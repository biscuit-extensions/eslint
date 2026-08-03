from __future__ import annotations

import typing

from .extension import ESLint

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def setup(api: ExtensionsAPI) -> None:
    """Entrypoint for Biscuit to load the extension."""
    ext = ESLint(api)
    api.register("eslint", ext)
    ext.install()
