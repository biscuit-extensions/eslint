from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from biscuit.extensions import Extension

if TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def parse_eslint_json(json_output: str) -> list[dict[str, Any]]:
    """Parse ESLint JSON format output into normalized diagnostic list."""
    diagnostics: list[dict[str, Any]] = []
    if not json_output.strip():
        return diagnostics

    try:
        data = json.loads(json_output)
    except Exception:
        return diagnostics

    if not isinstance(data, list):
        return diagnostics

    for file_entry in data:
        messages = file_entry.get("messages", [])
        for msg in messages:
            sev_num = msg.get("severity", 1)
            severity = "error" if sev_num == 2 else "warning"
            diag = {
                "line": msg.get("line", 1),
                "column": msg.get("column", 1),
                "rule": msg.get("ruleId") or "syntax-error",
                "severity": severity,
                "message": msg.get("message", ""),
                "fix": msg.get("fix"),
            }
            diagnostics.append(diag)
    return diagnostics


def parse_eslint_compact(compact_output: str) -> list[dict[str, Any]]:
    """Parse ESLint compact format output into normalized diagnostic list."""
    diagnostics: list[dict[str, Any]] = []
    # format: <file>: line <line>, col <col>, <Error|Warning> - <msg> (<rule>)
    pattern = re.compile(
        r"^(?:(?P<file>.+?):\s*)?line\s+(?P<line>\d+),\s*col\s+(?P<col>\d+),\s*(?P<severity>Error|Warning)\s*-\s*(?P<msg>.+?)(?:\s*\((?P<rule>[^)]+)\))?$",
        re.IGNORECASE,
    )

    for line in compact_output.splitlines():
        line = line.strip()
        match = pattern.match(line)
        if match:
            diag = {
                "line": int(match.group("line")),
                "column": int(match.group("col")),
                "severity": match.group("severity").lower(),
                "message": match.group("msg").strip(),
                "rule": match.group("rule") or "unknown",
                "fix": None,
            }
            diagnostics.append(diag)
    return diagnostics


def find_eslint_config(start_path: Path | str | None) -> Path | None:
    """Locate nearest ESLint configuration file."""
    if not start_path:
        return None

    curr = Path(start_path).resolve()
    if curr.is_file():
        curr = curr.parent

    config_names = [
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.yaml",
        ".eslintrc.yml",
        ".eslintrc",
        "package.json",
    ]

    while True:
        for name in config_names:
            candidate = curr / name
            if candidate.is_file():
                if name == "package.json":
                    try:
                        data = json.loads(candidate.read_text(encoding="utf-8"))
                        if "eslintConfig" in data:
                            return candidate
                    except Exception:
                        pass
                else:
                    return candidate

        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    return None


class ESLint(Extension):
    """JavaScript and TypeScript linter, problem scanner, and diagnostic annotator."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.enabled = True
        self.auto_fix_on_save = False
        self.status_button = None
        self.last_diagnostics: list[dict[str, Any]] = []

        self._load_settings()
        self.install()
        self.api.logger.info("ESLint extension initialised.")

    def _load_settings(self) -> None:
        if hasattr(self.api, "settings") and self.api.settings:
            self.enabled = self.api.settings.get("eslint.enable", self.enabled)
            self.auto_fix_on_save = self.api.settings.get("eslint.autoFixOnSave", self.auto_fix_on_save)

    def install(self) -> None:
        """Register commands, settings, and UI controls."""
        if hasattr(self.api, "settings") and self.api.settings:
            self.api.settings.register_setting(
                "eslint.enable", type="boolean", default=True, description="Enable or disable ESLint linting"
            )
            self.api.settings.register_setting(
                "eslint.autoFixOnSave", type="boolean", default=False, description="Automatically apply fixes on document save"
            )

        if hasattr(self.api, "commands") and self.api.commands:
            self.api.commands.register_command("eslint.lint_document", self.lint_document)
            self.api.commands.register_command("eslint.fix_document", self.fix_document)
            self.api.commands.register_command("eslint.clear_diagnostics", self.clear_diagnostics)

        if hasattr(self.api, "statusbar") and self.api.statusbar:
            if not self.status_button:
                self.status_button = self.api.statusbar.add_button(
                    text="ESLint: 0 problems",
                    icon="shield",
                    callback=self.lint_document,
                    description="ESLint code quality status",
                )
                if hasattr(self.status_button, "show"):
                    self.status_button.show()

        self.api.logger.info("ESLint extension installed.")

    def find_config(self, start_dir: Path | str | None = None) -> Path | None:
        return find_eslint_config(start_dir)

    def lint_with_cli(self, code: str, filename: str = "index.js") -> tuple[bool, list[dict[str, Any]]]:
        """Lint code using eslint CLI runner with JSON output format."""
        eslint_bin = shutil.which("eslint")
        cmd: list[str] = []
        if eslint_bin:
            cmd = [eslint_bin, "--format", "json", "--stdin", "--stdin-filename", filename]
        else:
            npx_bin = shutil.which("npx")
            if npx_bin:
                cmd = [npx_bin, "--no-install", "eslint", "--format", "json", "--stdin", "--stdin-filename", filename]

        if not cmd:
            return False, []

        try:
            proc = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            # ESLint returns 1 when lint errors are present, 0 when clean
            if proc.returncode in (0, 1) and proc.stdout.strip():
                diags = parse_eslint_json(proc.stdout)
                return True, diags
        except Exception as e:
            if hasattr(self.api, "logger"):
                self.api.logger.warning(f"ESLint CLI execution error: {e}")

        return False, []

    def lint_pure(self, code: str, filename: str = "index.js") -> list[dict[str, Any]]:
        """Static lint scanner for common ESLint rules without external dependencies."""
        issues: list[dict[str, Any]] = []
        lines = code.splitlines()

        for i, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            # rule: no-debugger
            if "debugger" in line:
                col = raw_line.find("debugger") + 1
                issues.append({
                    "line": i,
                    "column": col,
                    "rule": "no-debugger",
                    "severity": "error",
                    "message": "Unexpected 'debugger' statement.",
                    "fix": None,
                })

            # rule: eqeqeq (== vs ===, != vs !==)
            if "==" in line and "===" not in line:
                col = raw_line.find("==") + 1
                issues.append({
                    "line": i,
                    "column": col,
                    "rule": "eqeqeq",
                    "severity": "warning",
                    "message": "Expected '===' and instead saw '=='.",
                    "fix": {"line": i, "search": "==", "replace": "==="},
                })
            elif "!=" in line and "!==" not in line:
                col = raw_line.find("!=") + 1
                issues.append({
                    "line": i,
                    "column": col,
                    "rule": "eqeqeq",
                    "severity": "warning",
                    "message": "Expected '!==' and instead saw '!='.",
                    "fix": {"line": i, "search": "!=", "replace": "!=="},
                })

            # rule: no-var
            if re.search(r"\bvar\s+", raw_line):
                match = re.search(r"\bvar\s+", raw_line)
                col = match.start() + 1 if match else 1
                issues.append({
                    "line": i,
                    "column": col,
                    "rule": "no-var",
                    "severity": "warning",
                    "message": "Unexpected var, use let or const instead.",
                    "fix": {"line": i, "search": "var ", "replace": "let "},
                })

            # rule: no-console
            if re.search(r"\bconsole\.(log|warn|error|info|debug)\(", raw_line):
                col = raw_line.find("console.") + 1
                issues.append({
                    "line": i,
                    "column": col,
                    "rule": "no-console",
                    "severity": "warning",
                    "message": "Unexpected console statement.",
                    "fix": None,
                })

            # rule: no-constant-condition
            if re.search(r"\b(?:if|while)\s*\(\s*(true|false|1|0)\s*\)", line):
                issues.append({
                    "line": i,
                    "column": 1,
                    "rule": "no-constant-condition",
                    "severity": "error",
                    "message": "Unexpected constant condition.",
                    "fix": None,
                })

            # rule: no-empty
            if re.search(r"\b(?:catch|if|for|while)\s*\([^)]*\)\s*\{\s*\}", line):
                issues.append({
                    "line": i,
                    "column": 1,
                    "rule": "no-empty",
                    "severity": "warning",
                    "message": "Empty block statement.",
                    "fix": None,
                })

        return issues

    def lint_code(self, code: str, filename: str = "index.js") -> list[dict[str, Any]]:
        """Perform static lint analysis on JavaScript/TypeScript code using CLI or fallback."""
        success, cli_diags = self.lint_with_cli(code, filename)
        if success:
            self.last_diagnostics = cli_diags
            return cli_diags

        diags = self.lint_pure(code, filename)
        self.last_diagnostics = diags
        return diags

    def lint_file(self, filepath: str) -> list[dict[str, Any]]:
        """Lint a file from disk."""
        path = Path(filepath)
        if not path.exists():
            return []
        try:
            content = path.read_text(encoding="utf-8")
            return self.lint_code(content, filename=path.name)
        except Exception:
            return []

    def apply_fixes(self, code: str, fixes: list[dict[str, Any]]) -> str:
        """Apply automatic text fixes to code."""
        lines = code.splitlines()

        # Handle line-based replacements
        for fix in fixes:
            if not fix or not isinstance(fix, dict):
                continue

            # Standard ESLint range-based fix: {"range": [start, end], "text": "..."}
            if "range" in fix and "text" in fix:
                continue

            # Line-replacement fix: {"line": i, "search": "...", "replace": "..."}
            line_idx = fix.get("line", 1) - 1
            if 0 <= line_idx < len(lines):
                search_val = fix.get("search", "")
                replace_val = fix.get("replace", "")
                if search_val in lines[line_idx]:
                    lines[line_idx] = lines[line_idx].replace(search_val, replace_val, 1)

        result = "\n".join(lines)
        if code.endswith("\n") and not result.endswith("\n"):
            result += "\n"
        return result

    def fix_code(self, code: str) -> str:
        """Automatically fix trivial lint issues in code buffer."""
        diagnostics = self.lint_pure(code)
        fixes = [d.get("fix") for d in diagnostics if d.get("fix")]
        return self.apply_fixes(code, fixes)

    def _get_active_text_widget(self) -> Any:
        if not hasattr(self.api, "editors") or not self.api.editors:
            return None
        active = getattr(self.api.editors, "active_editor", None)
        if not active:
            return None
        return getattr(active, "text", None) or getattr(getattr(active, "content", None), "text", None)

    def _update_statusbar(self, errors: int, warnings: int) -> None:
        if not self.status_button:
            return
        if errors == 0 and warnings == 0:
            self.status_button.change_text("ESLint: 0 problems")
            self.status_button.change_icon("shield")
        else:
            self.status_button.change_text(f"ESLint: {errors} errors, {warnings} warnings")
            self.status_button.change_icon("alert-circle" if errors > 0 else "alert-triangle")

    def annotate_editor(self, text_widget: Any, diagnostics: list[dict[str, Any]]) -> None:
        """Apply diagnostic line annotations / tags to editor buffer."""
        if not text_widget or not hasattr(text_widget, "tag_add"):
            return

        # Clear existing tags
        if hasattr(text_widget, "tag_remove"):
            try:
                text_widget.tag_remove("eslint_error", "1.0", "end")
                text_widget.tag_remove("eslint_warning", "1.0", "end")
            except Exception:
                pass

        for diag in diagnostics:
            line = diag.get("line", 1)
            col = max(0, diag.get("column", 1) - 1)
            sev = diag.get("severity", "warning")
            tag_name = "eslint_error" if sev == "error" else "eslint_warning"

            start_idx = f"{line}.{col}"
            end_idx = f"{line}.end"
            try:
                text_widget.tag_add(tag_name, start_idx, end_idx)
            except Exception:
                pass

    def lint_document(self) -> list[dict[str, Any]]:
        """Biscuit Command: Lint active editor document and show annotations."""
        text_widget = self._get_active_text_widget()
        if not text_widget:
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.warning("ESLint: No active document to lint.")
            return []

        content = text_widget.get("1.0", "end-1c")
        diagnostics = self.lint_code(content)

        errors = sum(1 for d in diagnostics if d.get("severity") == "error")
        warnings = sum(1 for d in diagnostics if d.get("severity") == "warning")

        self.annotate_editor(text_widget, diagnostics)
        self._update_statusbar(errors, warnings)

        if hasattr(self.api, "notifications") and self.api.notifications:
            if errors > 0 or warnings > 0:
                self.api.notifications.warning(f"ESLint found {errors} errors, {warnings} warnings.")
            else:
                self.api.notifications.info("ESLint: No problems found.")

        return diagnostics

    def fix_document(self) -> bool:
        """Biscuit Command: Fix auto-fixable problems in current active editor."""
        text_widget = self._get_active_text_widget()
        if not text_widget:
            return False

        content = text_widget.get("1.0", "end-1c")
        fixed = self.fix_code(content)
        if fixed != content:
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", fixed)
            self.lint_document()
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info("ESLint: Auto-fixes applied.")
            return True
        return False

    def clear_diagnostics(self) -> None:
        """Biscuit Command: Clear diagnostic highlights and reset statusbar."""
        text_widget = self._get_active_text_widget()
        if text_widget and hasattr(text_widget, "tag_remove"):
            try:
                text_widget.tag_remove("eslint_error", "1.0", "end")
                text_widget.tag_remove("eslint_warning", "1.0", "end")
            except Exception:
                pass
        self._update_statusbar(0, 0)
        self.last_diagnostics = []
