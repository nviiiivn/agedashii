"""Home - the whole dashboard, and the compatibility layer under it.

The system moved out into two packages, ``agedashii_qi`` (the look) and
``agedashii_washi`` (the framework). Home still imports them under their
old names so that none of its seventeen pages had to change while the
split apps - Moto, Watudo, Foodzen - are being proven. Nothing is removed
from Home until those ship and work.

These are ALIASES, not copies. ``sys.modules["agedashii_tofu.api"]`` IS
``agedashii_washi.api``, the same object, so a test that patches
``agedashii_tofu.api.something`` patches the real module. An earlier version of
this file re-exported the names instead, which quietly gave every test a
private copy to patch and broke 156 of them at once.
"""
import sys as _sys

import agedashii_qi.themes as _themes
import agedashii_washi.api as _api
import agedashii_washi.config as _config
import agedashii_washi.foodzen as _foodzen
import agedashii_washi.foodzen_catalog as _foodzen_catalog
import agedashii_washi.mock as _mock
import agedashii_washi.panels as _panels
import agedashii_washi.prefs as _prefs
import agedashii_washi.routing as _routing
import agedashii_washi.store as _store
import agedashii_washi.tileserver as _tileserver

for _old, _new in (
    ("api", _api), ("config", _config), ("panels", _panels), ("prefs", _prefs),
    ("store", _store), ("tileserver", _tileserver), ("routing", _routing),
    ("mock", _mock), ("foodzen", _foodzen), ("foodzen_catalog", _foodzen_catalog),
    ("theme_data", _themes),
):
    _sys.modules[f"{__name__}.{_old}"] = _new
    globals()[_old] = _new
