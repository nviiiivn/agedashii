# Agedashii

Four offline-first apps built on two shared packages. Written in
[Flet](https://flet.dev) (Python → Flutter); one codebase builds an Android
APK, a Linux desktop app and a browser PWA.

```
packages/
  agedashii-qi        the look     24 themes, corner radius, accent glow, CRT
  agedashii-washi     the system   store, api, panels, prefs, surface,
                                   widgets, forms, motion, routing, tileserver
apps/
  moto                Trips + Garage + XNAV
  watudo              Wat2Do + Future Finds
  foodzen             Grocery + EatZen + BPM
agedashii_tofu/       Tofu — the whole dashboard, every surface on one home
```

Qi does not import Washi: a theme has no opinion about cards, and a card
cannot be drawn without a theme, so the arrow points one way. The three
apps do not import each other, and none of them imports Tofu. Both rules
are enforced by `tests/test_package_boundaries.py` rather than remembered.

**Nothing has been removed from Tofu.** It still shows all nine surfaces.
The split apps are additive until each has been used and confirmed; what
exists meanwhile is the ability to *hide* a surface, which is reversible
and deletes nothing.

| | web | Android | Linux x64 |
|---|---|---|---|
| Tofu | [tofu.alola.lol](https://tofu.alola.lol) | 212 MB | 108 MB |
| Moto | [moto.alola.lol](https://moto.alola.lol) | 212 MB | 108 MB |
| Watudo | [watudo.alola.lol](https://watudo.alola.lol) | 135 MB | 32 MB |
| Foodzen | [foodzen.alola.lol](https://foodzen.alola.lol) | 134 MB | 32 MB |

Watudo and Foodzen are a third the size because they do not carry the
120 MB offline map they would never open.

[agedashii.alola.lol](https://agedashii.alola.lol) documents Qi and Washi,
and `/tofu`, `/moto`, `/watudo`, `/foodzen` there are the per-app pages.
The app subdomains serve the running app and nothing else.

## Local first, and meant literally

`agedashii_washi.store` is the only module in the system that talks to a
network, and it is built to be told no: writes queue offline, an
unreachable server burns no retry attempt, failures park rather than
vanish, and the outbox drains on resume.

Everything else is local and works with the radio off — themes, layouts,
preferences, the 86 MB routing graph, the 39 MB tile pack, the
7,709-food nutrition table.

Two deliberate exceptions, both because there is nothing to replay later:
EatZen's photo analysis needs the vision model live, and Valhalla /
Overpass / elevation refuse with a reason rather than queueing.

## Pointing it at a server

The app is a client. Set the address in **Settings → Server**; it is saved
on the device and reused next launch, no rebuild. On startup it tries each
known address and adopts the first that answers, so a stale saved address
falls back to the defaults in
`packages/agedashii-washi/agedashii_washi/config.py` rather than hanging.

| Where you are | Works | How |
|---|---|---|
| On the LAN | yes | the LAN address, direct |
| On WireGuard | yes | same address, through the tunnel |
| Anywhere else | no | the public hostname is behind a login the app cannot pass |
| In a browser | no | an https page cannot fetch an http LAN address — mixed content |

The web builds say so plainly rather than looking like a server that is
merely down.

## Running from source

```sh
pip install -e packages/agedashii-qi -e packages/agedashii-washi \
            -e apps/moto -e apps/watudo -e apps/foodzen
python main.py                 # Tofu
python apps/moto/main.py       # Moto, Watudo, Foodzen likewise
```

On the Void laptop all four are in the desktop launcher as
**Agedashii Tofu / Moto / Watudo / Foodzen**, run from `~/agedashii-src`.

## Building

```sh
tools/build_app.sh <tofu|moto|watudo|foodzen> [apk] [linux] [web]
```

Runs on Tower against the toolchain at `/home/nvii/flet-toolchain`. It
stages each app into its own directory with the shared packages vendored
in, because a relative path dependency does not survive `flet build`
copying the tree; it strips `agedashii-*` out of the staged pyproject,
because pip would otherwise try to fetch them from PyPI where they do not
exist; and it drops `assets/maps` from the web target, because a browser
build must not be a 120 MB download.

GitHub Actions is not used — the minutes are exhausted and a macOS runner
bills at 10x. `.github/workflows/build.yml` is kept for reference.

iOS is not built. `flutter build ipa` needs macOS, and while a Sonoma
guest under KVM on Tower does work, Xcode itself is behind an Apple ID.
The free route onto an iPhone is Safari → Share → Add to Home Screen from
`tofu.alola.lol`.

## Tests

```sh
python -m unittest discover -s tests                       # 764, Tofu
python -m unittest discover -s apps/moto/tests -t .        # 120
python -m unittest discover -s apps/watudo/tests -t .      # 115
python -m unittest discover -s apps/foodzen/tests -t .     # 112
```

`tests/test_package_boundaries.py` holds the rules the split depends on:
no relative import escaping its package, Qi never importing Washi, no app
importing another, every module parsing, and no `expand=True` inside a
wrapping Row.

## Flet 0.86.5 traps, all of which shipped broken here first

Each of these is valid Python, valid in a control tree, and wrong only on
a real device.

- `expand=True` inside `ft.Row(wrap=True)` — a `Wrap` hands children
  **unbounded constraints on both axes**, so a `filled=True` TextField
  with no height paints grey to the bottom of the screen
- `ft.Icon` exposes `.icon`, not `.name`, and is not slotted — assigning
  to `.name` binds a stray attribute and silently does nothing
- `animate_position` outside a `Stack` makes Flutter refuse to draw the
  control at all
- `Dropdown.on_select`, not `on_change`; `FilledButton.content`, not
  `.text`; `page.show_dialog`/`pop_dialog`, not `open`/`close`
- `Page.padding` defaults to `Padding.all(10)`, outside `SafeArea` and any
  overlay
- `strftime("%-d")` is a glibc extension and prints literally on Android
- a slow synchronous handler on Flet's event loop **drops taps**, and a
  CPU-bound worker thread starves the UI anyway because of the GIL
- under Pyodide there are no sockets: a loopback tile server and a urllib
  probe both hang forever
