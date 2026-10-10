"""
Every module under src/ must import cleanly.

Most modules don't use `from __future__ import annotations`, so their function
annotations are evaluated at import time. An annotation that names something
imported only under `if TYPE_CHECKING:` passes mypy but raises NameError the
first time the module is imported, and a module no other test imports would
ship broken.
"""

import importlib
import pkgutil

import pytest

import src


def _all_modules() -> list[str]:
    return sorted(m.name for m in pkgutil.walk_packages(src.__path__, prefix="src."))


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _all_modules())
def test_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)
