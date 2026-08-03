from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class ESLint(Extension):
    """JavaScript and TypeScript linter and code quality scanner."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("ESLint extension initialised.")

    def lint_code(self, code: str, filename: str = "index.js") -> list[dict]:
        """Perform static lint analysis on JavaScript/TypeScript code."""
        issues = []
        for i, line in enumerate(code.splitlines(), start=1):
            if "debugger" in line:
                issues.append({"line": i, "rule": "no-debugger", "severity": "error", "message": "Unexpected debugger statement."})
            if "==" in line and "===" not in line:
                issues.append({"line": i, "rule": "eqeqeq", "severity": "warning", "message": "Expected '===' and instead saw '=='."})
            if "var " in line:
                issues.append({"line": i, "rule": "no-var", "severity": "warning", "message": "Unexpected var, use let or const instead."})
        return issues

    def fix_code(self, code: str) -> str:
        """Automatically fix trivial lint issues."""
        fixed_lines = []
        for line in code.splitlines():
            line = line.replace("var ", "let ")
            fixed_lines.append(line)
        return "\n".join(fixed_lines)

    def install(self) -> None:
        self.api.logger.info("ESLint extension installed.")
