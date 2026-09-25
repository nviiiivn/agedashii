"""The dashboard's surfaces.

Home, Lab, Tools, Weather, Extras and Settings. That is all of them.

Until 2026-09-25 this module also installed aliases so that nine pages
living in the split apps - Trips, Garage and XNAV in ``agedashii_moto``,
Wat2Do and Future Finds in ``agedashii_watudo``, FoodZen and EatZen in
``agedashii_foodzen`` - could still be imported as
``agedashii_tofu.pages.trips`` and friends. That was deliberate and
temporary: her rule was that nothing came out of Home until each split app
had shipped and been used. They have, so the aliases are gone and those
surfaces are reached in their own apps.

One alias remains, and only because it is not a page: ``_forms`` is
Washi's shared write-form plumbing, which the surfaces that stayed still
use. It is the same module object, not a copy - a test that patches it
patches the real thing. See ``agedashii_tofu/__init__.py`` for why that
distinction matters; re-exporting names instead once gave every test a
private copy and broke 156 of them at once.
"""
import sys as _sys

import agedashii_washi.forms as _forms

_sys.modules[f"{__name__}._forms"] = _forms
globals()["_forms"] = _forms
