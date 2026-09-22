"""The dashboard's surfaces.

``_forms`` is an alias for ``agedashii_washi.forms`` for the same reason
the aliases in ``flet_app/__init__.py`` exist, and with the same caveat:
it is the same module object, so patching it in a test patches the real
one.
"""
import sys as _sys

import agedashii_washi.forms as _forms

_sys.modules[f"{__name__}._forms"] = _forms
globals()["_forms"] = _forms
