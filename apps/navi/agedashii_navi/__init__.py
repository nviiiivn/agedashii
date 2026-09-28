"""Agedashii Navi - turn-by-turn and telemetry for a motorcycle.

Deliberately thin. Everything lives in a module of its own so that the
import of one surface cannot drag the rest of the app - and in particular
so `theme` and `prefs` can be imported by a test with no Flet page and no
device attached.
"""
