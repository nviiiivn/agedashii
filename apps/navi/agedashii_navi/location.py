"""The GPS stream's Android settings - and the fix that keeps it alive with
the screen off.

v1.17.0 went SILENT the moment her screen locked: no prompts, and the ride log
became a straight line between two points (minion 2's ride test: 114 fixes
fed with the screen asleep, "2 samples, 0.9 mi"). Android stops feeding fixes
to an app with no foreground service once the screen is off.

flet_geolocator already carries one - geolocator_android's own location
foreground service, declared foregroundServiceType="location", with a wake
lock - switched on by `GeolocatorAndroidConfiguration.foreground_notification_config`.
But in Flet 0.86.5 that setting is a NO-OP: the Python dataclass sends it
NESTED ({"foreground_notification_config": {"notification_title": ...}})
while the Dart side (flet_geolocator lib/src/utils/geolocator.dart:64-82)
reads FLAT top-level keys ("foreground_notification_title", ...). Verified
with Flet's own msgpack encoder, then measured on emulator-5556 (API 34) with
dumpsys power reporting mWakefulness=Asleep: the plain config got 1 of 60
fixes while asleep; these flat keys got 58-59 of 60, isForeground=true.

So `android_config` sends the flat keys itself. When Flet fixes the nesting
this subclass still works - the Dart side reads the flat keys either way.

The service runs while a ride RECORDS or a route is ACTIVE (`wants_service`),
the state the Kotlin app keys its RideService on - not "the Ride page is
open", because she opens that page to look at the map at a stop. Its ongoing
notification is how she sees that her position is being taken. flet_geolocator
restarts the stream on every update with the configuration it then has, so
switching the service on or off is a configuration change (ride._sync_location).
"""
import asyncio
from typing import Optional

import flet as ft
import flet_geolocator as fgeo

#: What the ongoing notification says. It is visible on purpose: a phone
#: reporting its position with nothing on screen saying so would be both a
#: battery surprise and a privacy one (POST_NOTIFICATIONS, pyproject.toml).
TITLE = "Agedashii Navi"
TEXT = "Using GPS - guidance and the ride log keep going with the screen off"
CHANNEL = "Navigation"


@ft.value
class _FlatForeground(fgeo.GeolocatorAndroidConfiguration):
    """GeolocatorAndroidConfiguration plus the flat keys its Dart side reads."""

    foreground_notification_title: Optional[str] = None
    foreground_notification_text: Optional[str] = None
    foreground_notification_channel_name: Optional[str] = None
    foreground_notification_enable_wake_lock: bool = False
    foreground_notification_set_ongoing: bool = False


#: Whether this launch has had an ANSWER to the notification permission. Once
#: is enough: a refusal is hers, and asking on every route would be nagging.
_ASKED = {}
#: Seconds to wait before asking. The trigger (a route, a recording) is also
#: when the location stream restarts, and on v1.19.0 the two permission
#: requests collided: permission_handler logged "onRequestPermissionsResult is
#: called without results ... interfering request codes", no dialog showed,
#: and the ask was spent.
ASK_DELAY_S = 2.0


async def ask_notifications(page):
    """Ask for POST_NOTIFICATIONS, once per launch, on Android only.

    Android 13+ runs the location service without it but HIDES the ongoing
    notification - GPS on with nothing on screen saying so, the one failure
    this has to avoid. Called when she starts a route or a ride, so the request
    arrives with its reason, never at launch. Returns the status, or None when
    it was not asked. Never raises: a permission it cannot ask for is printed
    and the ride goes on.
    """
    if _ASKED.get("done") or _ASKED.get("asking"):
        return None
    if getattr(page, "web", False) or getattr(page, "platform", None) != ft.PagePlatform.ANDROID:
        return None
    _ASKED["asking"] = True
    try:
        if ASK_DELAY_S:
            await asyncio.sleep(ASK_DELAY_S)
        import flet_permission_handler as fph
        handler = _ASKED.get("handler")
        if handler is None:
            # A Flet service lives exactly as long as something holds it.
            handler = _ASKED["handler"] = fph.PermissionHandler()
            if isinstance(getattr(page, "services", None), list):
                page.services.append(handler)
        status = await handler.get_status(fph.Permission.NOTIFICATION)
        if status != fph.PermissionStatus.GRANTED:
            status = await handler.request(fph.Permission.NOTIFICATION)
        if status is not None:
            _ASKED["done"] = True              # answered: never nag again
        else:
            print("[navi] location: the notification request came back with no "
                  "answer - asking again next time", flush=True)
        return status
    except Exception as exc:                            # noqa: BLE001
        _ASKED["done"] = True                  # cannot ask here: stop trying
        print(f"[navi] location: could not ask for notifications: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return None
    finally:
        _ASKED["asking"] = False


#: The one live GPS stream and the (service on?, hz) it was last given. Held
#: here, not in a page, so whatever changes the route - Where-to clearing it,
#: a route coming back, arriving - switches the service without waiting for
#: the Ride page to be rebuilt (v1.19.1 on 5556: cleared on Where-to, the
#: service ran on until she happened to open Ride again).
_STREAM = {}


def register(geo, mode):
    """Hold the live stream built with `mode` = (service on?, hz)."""
    _STREAM.update(geo=geo, mode=mode)


def adopt(geo):
    """Hold `geo` as the live stream if it is not already (a render that
    reuses the stream built by an earlier one)."""
    if _STREAM.get("geo") is not geo:
        _STREAM.update(geo=geo, mode=None)


def forget():
    """The stream is gone (GPS switched off)."""
    _STREAM.clear()


def current_mode():
    from . import prefs
    return (wants_service(), prefs.hz())


def sync():
    """Point the live stream at what this moment needs: the service while a
    ride records or a route is being followed, none otherwise; and her sample
    rate. flet_geolocator restarts the stream with its configuration on every
    update, so this is a configuration change - no second service. True if
    anything changed."""
    geo = _STREAM.get("geo")
    if geo is None:
        return False
    wanted = current_mode()
    if _STREAM.get("mode") == wanted:
        return False
    geo.configuration = android_config(wanted[1], foreground=wanted[0])
    _STREAM["mode"] = wanted
    try:
        geo.update()
    except Exception as exc:                            # noqa: BLE001
        # Not on a live page (a test, a render being torn down): the next
        # page update carries the new configuration anyway.
        print(f"[navi] location: stream not updated yet: {type(exc).__name__}: {exc}",
              flush=True)
    return True


def wants_service():
    """Whether the screen-off location service should run: a ride recording,
    or a ROUTE up. Not while she only looks at the map.

    A route, not a destination: main.py restores her last destination at
    launch WITHOUT routing it, and keyed on that, v1.19.0 ran the service from
    the moment the app opened (emulator-5556).
    """
    from . import live
    if live.recording():
        return True
    if live.LIVE.get("route") is None:
        return False
    # Arrived: nothing left to guide her to, so GPS stops until she routes
    # again (AGEDASHII). A recording keeps it, above.
    follower = live.LIVE.get("follower")
    return not getattr(follower, "arrived", False)


def android_config(hz=1, foreground=True):
    """The stream's settings: HIGH accuracy, her sample rate, the service on.

    HIGH is the same satellite fix without the framework nagging for network
    assistance, which is right for an app whose point is working with the
    radio off; `distance_filter` of 3 m stops the stream firing while the bike
    is parked (a stationary GPS wanders, and every wander was a message, a log
    line and a repaint); the interval follows her rate, not the 5 s default.
    """
    common = dict(accuracy=fgeo.GeolocatorPositionAccuracy.HIGH,
                  distance_filter=3,
                  interval_duration=ft.Duration(milliseconds=int(1000 / max(1, hz))))
    if not foreground:
        return _FlatForeground(**common)        # no keys: no service, no notification
    return _FlatForeground(
        **common,
        foreground_notification_title=TITLE,
        foreground_notification_text=TEXT,
        foreground_notification_channel_name=CHANNEL,
        foreground_notification_enable_wake_lock=True,
        foreground_notification_set_ongoing=True)
