from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eslint import setup
from src.eslint.extension import ESLint


class TestESLintExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = ESLint(app_instance.api)
        issues = ext.lint_code("var a == 1;\ndebugger;")
        assert len(issues) >= 2
        assert any(i["rule"] == "no-debugger" for i in issues)
        fixed = ext.fix_code("var b = 2;")
        assert "let b = 2;" in fixed
