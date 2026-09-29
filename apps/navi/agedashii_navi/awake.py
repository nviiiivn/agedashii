"""Keep the screen on: whether the phone may sleep in the middle of a ride.

It never worked until this module. main.py set `page.wakelock_enabled = True`,
and Flet 0.86's Page has no such field - the assignment landed on nothing, no
error was raised, and the phone slept on its own timeout whatever the switch
in Settings said. It was also read once, at startup, so turning the switch off
or on changed nothing until the app was next opened.

A phone that sleeps is worse than dark: the Flet app stops guiding and stops
recording when the screen locks, so a sleeping screen is a silent ride and a
straight line in the log.

In Flet 0.86 the wakelock is a *service*, `ft.Wakelock`, with async
`enable()`/`disable()` (wakelock_plus underneath, which sets
FLAG_KEEP_SCREEN_ON on the activity window). `follow` makes the screen do
whatever `prefs.keep_awake()` says now, and is called at startup, after every
rebuild - which is how a switch in Settings reaches it - and when the app comes
back to the front, because a window that was rebuilt behind her back has lost
the flag.

The service lives on the page, not in this module: when Android keeps the
process but not the activity, `main()` runs again with a new page, and a
service registered to the old one would be talking to nothing.
"""
import flet as ft

from agedashii_navi import prefs


def _service(page):
    """This page's Wakelock, made and registered once.

    Flet drops a service once nothing strongly holds it; `page.services` is the
    root view's own list, so one parked there lives as long as the page.
    """
    service = getattr(page, "_navi_wakelock", None)
    if service is None:
        service = ft.Wakelock()
        services = getattr(page, "services", None)
        if isinstance(services, list):
            services.append(service)
        page._navi_wakelock = service        # noqa: SLF001 - our own attribute
    return service


async def follow(page, force=False):
    """Hold the screen on, or let it go, as the pref says.

    Asks the phone only when the wanted state differs from the last one it
    accepted, so calling this after every rebuild costs nothing. `force` asks
    regardless - the app coming back to the front, when the window may have
    been rebuilt without the flag.

    Returns what the screen is now held to (True/False), or None when the
    phone refused - in which case the next call tries again rather than
    believing it worked.
    """
    want = prefs.keep_awake()
    if not force and getattr(page, "_navi_awake", None) == want:
        return want
    service = _service(page)
    try:
        if want:
            await service.enable()
        else:
            await service.disable()
    except Exception as exc:                 # noqa: BLE001 - never fatal
        page._navi_awake = None              # noqa: SLF001 - try again next time
        print(f"[navi] keep the screen on: {'enable' if want else 'disable'} "
              f"failed: {exc!r}")
        return None
    page._navi_awake = want                  # noqa: SLF001 - our own attribute
    return want
