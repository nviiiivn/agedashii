# Known, and not yet fixed

Things that are true of the Flet app today, written down so they are not
rediscovered the hard way. Each says what, why it matters, and what would close
it. Remove an entry in the same commit that fixes it.

## Something in the app asks for background location, and nobody knows what

On v1.19.0 (emulator-5556, 2026-09-28 21:40:42), at launch, the app's own uid
raised a system permission request for ACCESS_BACKGROUND_LOCATION
(GrantPermissionsViewModel: result=2). No code of ours requests it. It landed at
the same moment as our notification request, and flutter permission_handler
logged "onRequestPermissionsResult is called without results ... interfering
request codes": no dialog appeared, and the service ran with its notification
hidden. The suspects are flet_geolocator or geolocator_android reacting to the
foreground-service settings (location.py), but that is a guess.

What protects her meanwhile: the notification ask waits ASK_DELAY_S after a
route or a recording starts and is only spent once it gets an answer
(location.ask_notifications). On v1.19.1 the dialog appeared on the first ask.
But the retry path has only been proven by a unit test; nothing interfered
on the device that run.

Closes when: logcat around a stream start with the foreground settings names
the caller (flet_geolocator logs "Geolocator.<method>" on invoke). Then either
stop the request or, if it is flet_geolocator, add it to the upstream issue
drafted beside the nested-keys bug (~/Sandbox/tmp/minion3/flet-geolocator-issue.md).

## Whether the notification shows the moment she taps Allow is unverified

The service is started before she answers the prompt, so its notification is
posted while notifications are still denied, and Android drops it. On v1.19.1 it
was confirmed on the NEXT stream start (recording: "Agedashii Navi - Using
GPS...", flags 0x62). Whether it also appears straight after Allow was not
checked: the probe that looked grepped too few lines of dumpsys.

Closes when: a device run checks `dumpsys notification` right after Allow. If it
is missing, restart the stream when the permission comes back GRANTED.

## The cough has never been heard on a device

COUGH_RATE is 0.08 of far/near prompts with a distance piece, and the v1.19.1
by-ear drives had too few of those to expect one. The placement (between the
distance and the turn, never on "now", never twice running) is proven by unit
tests only.

Closes when: a longer drive's "[navi] speech: playing" log shows an
[interject-cough...] between a distance and its turn.
