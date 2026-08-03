from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eslint import setup
from src.eslint.extension import (
    ESLint,
    find_eslint_config,
    parse_eslint_compact,
    parse_eslint_json,
)


class TestESLintExtension:
    def test_extension_setup_and_registration(self, mock_api) -> None:
        setup(mock_api)
        assert "eslint" in mock_api.registered_extensions
        ext = mock_api.registered_extensions["eslint"]
        assert isinstance(ext, ESLint)

        commands = mock_api.commands.commands
        assert "eslint.lint_document" in commands
        assert "eslint.fix_document" in commands
        assert "eslint.clear_diagnostics" in commands

        settings = mock_api.settings.get_registered_settings()
        assert "eslint.enable" in settings
        assert "eslint.autoFixOnSave" in settings

        assert len(mock_api.statusbar.buttons) == 1
        assert "ESLint" in mock_api.statusbar.buttons[0].text
        assert mock_api.statusbar.buttons[0].visible is True

    def test_manifest_metadata_and_dependencies(self) -> None:
        import toml
        manifest_path = Path(__file__).parent.parent / "extension.toml"
        assert manifest_path.exists()
        manifest = toml.load(manifest_path)

        assert manifest["extension"]["author"] == "tomlin7"
        assert manifest["extension"]["version"] == "0.2.0"
        assert manifest["dependencies"]["tools"] == ["node", "eslint", "npx"]
        cmd_ids = [cmd["id"] for cmd in manifest["contributes"]["commands"]]
        assert "eslint.lint_document" in cmd_ids
        assert "eslint.fix_document" in cmd_ids

    def test_parse_eslint_json(self) -> None:
        sample_json = json.dumps([
            {
                "filePath": "src/index.js",
                "messages": [
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 2,
                        "message": "'x' is defined but never used.",
                        "line": 10,
                        "column": 5,
                    },
                    {
                        "ruleId": "eqeqeq",
                        "severity": 1,
                        "message": "Expected '===' and instead saw '=='.",
                        "line": 15,
                        "column": 8,
                    },
                ],
            }
        ])
        diags = parse_eslint_json(sample_json)
        assert len(diags) == 2
        assert diags[0]["rule"] == "no-unused-vars"
        assert diags[0]["severity"] == "error"
        assert diags[0]["line"] == 10
        assert diags[1]["severity"] == "warning"

    def test_parse_eslint_compact(self) -> None:
        sample_compact = (
            "index.js: line 4, col 2, Error - Unexpected 'debugger' statement. (no-debugger)\n"
            "index.js: line 7, col 5, Warning - Unexpected var, use let or const instead. (no-var)\n"
        )
        diags = parse_eslint_compact(sample_compact)
        assert len(diags) == 2
        assert diags[0]["line"] == 4
        assert diags[0]["column"] == 2
        assert diags[0]["severity"] == "error"
        assert diags[0]["rule"] == "no-debugger"
        assert diags[1]["severity"] == "warning"
        assert diags[1]["rule"] == "no-var"

    def test_find_eslint_config(self, tmp_path: Path) -> None:
        sub = tmp_path / "app" / "deep"
        sub.mkdir(parents=True)
        config_file = tmp_path / ".eslintrc.json"
        config_file.write_text("{}", encoding="utf-8")

        found = find_eslint_config(sub / "index.js")
        assert found == config_file

    def test_lint_pure_rules(self, mock_api) -> None:
        ext = ESLint(mock_api)
        code = (
            "var total = 100;\n"
            "if (total == 100) {\n"
            "    debugger;\n"
            "}\n"
            "if (true) {}\n"
            "console.log(total);\n"
        )
        issues = ext.lint_pure(code)
        rules = [i["rule"] for i in issues]
        assert "no-var" in rules
        assert "eqeqeq" in rules
        assert "no-debugger" in rules
        assert "no-constant-condition" in rules
        assert "no-empty" in rules
        assert "no-console" in rules

    def test_fix_code_pure(self, mock_api) -> None:
        ext = ESLint(mock_api)
        code = "var a = 1;\nif (a == 1) {\n    var b = 2;\n}"
        fixed = ext.fix_code(code)
        assert "let a = 1;" in fixed
        assert "let b = 2;" in fixed
        assert "a === 1" in fixed

    def test_lint_file_reading(self, tmp_path: Path, mock_api) -> None:
        ext = ESLint(mock_api)
        sample = tmp_path / "sample.js"
        sample.write_text("debugger;\n", encoding="utf-8")
        issues = ext.lint_file(str(sample))
        assert len(issues) == 1
        assert issues[0]["rule"] == "no-debugger"

    def test_lint_document_with_editor(self, mock_api) -> None:
        ext = ESLint(mock_api)
        mock_text = MagicMock()
        mock_text.get.return_value = "var foo = 1;\ndebugger;\n"
        mock_api.editors.active_editor = MagicMock(text=mock_text)

        diags = ext.lint_document()
        assert len(diags) >= 2
        mock_text.tag_add.assert_called()
        assert "ESLint: 1 errors, 1 warnings" in ext.status_button.text
        mock_api.notifications.warning.assert_called_once()

    def test_lint_document_no_editor(self, mock_api) -> None:
        ext = ESLint(mock_api)
        mock_api.editors.active_editor = None
        diags = ext.lint_document()
        assert diags == []
        mock_api.notifications.warning.assert_called_once()

    def test_fix_document(self, mock_api) -> None:
        ext = ESLint(mock_api)
        mock_text = MagicMock()
        mock_text.get.return_value = "var x = 1;\nif (x == 1) {}"
        mock_api.editors.active_editor = MagicMock(text=mock_text)

        res = ext.fix_document()
        assert res is True
        mock_text.delete.assert_called_with("1.0", "end")
        mock_text.insert.assert_called()

    def test_clear_diagnostics(self, mock_api) -> None:
        ext = ESLint(mock_api)
        mock_text = MagicMock()
        mock_api.editors.active_editor = MagicMock(text=mock_text)
        ext.status_button.change_text("ESLint: 5 errors")

        ext.clear_diagnostics()
        mock_text.tag_remove.assert_called()
        assert "0 problems" in ext.status_button.text
        assert ext.last_diagnostics == []

    def test_app_instance_compatibility(self, app_instance) -> None:
        ext = ESLint(app_instance.api)
        issues = ext.lint_code("debugger;")
        assert any(i["rule"] == "no-debugger" for i in issues)
