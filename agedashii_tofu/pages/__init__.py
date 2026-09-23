"""The dashboard's surfaces - Home's own, plus the three apps' by alias.

Nine of these pages now live in their own app packages: Trips, Garage and
XNAV in ``agedashii_moto``, Wat2Do and Future Finds in
``agedashii_watudo``, FoodZen and EatZen in ``agedashii_foodzen``. Home
keeps showing all of them until each split app has shipped and been
confirmed working - her sequencing, and the safe one: nothing is deleted
on the strength of a refactor that has not been used yet.

The aliases are the same module objects, not copies, for the reason given
in ``agedashii_tofu/__init__.py``: a test that patches one of these patches the
real module.
"""
import sys as _sys

import agedashii_foodzen.pages.eatzen as _eatzen
import agedashii_foodzen.pages.foodzen as _foodzen
import agedashii_moto.pages._trips_parts as _trips_parts
import agedashii_moto.pages.garage as _garage
import agedashii_moto.pages.trips as _trips
import agedashii_moto.pages.xnav as _xnav
import agedashii_washi.forms as _forms
import agedashii_watudo.pages._wat2do_parts as _wat2do_parts
import agedashii_watudo.pages.future as _future
import agedashii_watudo.pages.wat2do as _wat2do

for _old, _new in (
    ("_forms", _forms), ("trips", _trips), ("_trips_parts", _trips_parts),
    ("garage", _garage), ("xnav", _xnav), ("wat2do", _wat2do),
    ("_wat2do_parts", _wat2do_parts), ("future", _future),
    ("foodzen", _foodzen), ("eatzen", _eatzen),
):
    _sys.modules[f"{__name__}.{_old}"] = _new
    globals()[_old] = _new
