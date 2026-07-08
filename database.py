"""
Legacy compatibility shim — all implementation lives in database/__init__.py

This file is preserved for backward compatibility.  The database/ package
(database/__init__.py) takes precedence during import and is what every
existing ``from database import ...`` statement actually resolves to.
This module only activates in edge‑case scenarios where database.py is
loaded directly instead of the package.
"""

import sys as _sys


def __getattr__(name):
    """Forward attribute access to the real database/ package."""
    pkg = _sys.modules.get("database")
    if pkg is not None and hasattr(pkg, "__path__") and hasattr(pkg, name):
        return getattr(pkg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
