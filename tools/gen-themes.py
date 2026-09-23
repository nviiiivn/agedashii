#!/usr/bin/env python3
"""Push the app's palettes into the website, so the two never drift.

agedashii_tofu/theme_data.py is the one place themes are defined: alola.lol's
eight values per theme, plus the rule that derives the seven the app also
needs. The website carries the same palettes twice over - once as CSS custom
properties in dashbored-web/app.css, once as the THEME_NAMES / THEME_COLORS /
THEME_BG maps in dashbored-web/app.js - and hand-editing three copies of
twenty-four palettes is how they end up disagreeing.

This rewrites both website blocks in place. It used to read the themes out of
dashbored-v3.py's CSS, which was the source of truth before alola became one.

    python3 tools/gen-themes.py [--check] [--web DIR]

--check exits non-zero if the website is out of date instead of writing.
Changing app.css or app.js changes the page digest that
dashbored/tests/test_frontend_assets.py pins, so update EXPECTED_PAGE_SHA256
in the same commit.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agedashii_tofu.theme_data import (  # noqa: E402
    THEMES, THEME_NAMES, KEYS, _rgb as to_rgb)

DEFAULT_WEB = Path.home() / "www/dashbored/dashbored-web"

# The first theme doubles as the bare-body default, for the instant before
# app.js applies a class.
CSS_START = "/* ===== THEMES ===== */"
CSS_END = "/* ===== LOLCAT / NYAN CAT MODE ===== */"
JS_START = "const THEME_NAMES = {"
JS_END = "const TABS ="


def rgb_triplet(colour):
    """'#1e1e2e' -> '30,30,46', for the rgba() uses in app.css."""
    if not colour.startswith("#"):
        # A few local themes keep an rgba() border; it has no -rgb companion.
        return None
    return ",".join(str(round(v * 255)) for v in to_rgb(colour))


def css_block(key, first):
    p = THEMES[key]
    sel = f"body, body.theme-{key}" if first else f"body.theme-{key}"
    return "\n".join([
        sel + " {",
        f"  --bg:{p['bg']};--bg2:{p['bg2']};--surface:{p['surface']};--border:{p['border']};",
        f"  --text:{p['text']};--text2:{p['text2']};--text3:{p['text3']};",
        f"  --accent:{p['accent']};--accent-rgb:{rgb_triplet(p['accent'])};"
        f"--surface-rgb:{rgb_triplet(p['surface'])};",
        f"  --green:{p['green']};--green-rgb:{rgb_triplet(p['green'])};--yellow:{p['yellow']};",
        f"  --red:{p['red']};--red-rgb:{rgb_triplet(p['red'])};--orange:{p['orange']};"
        f"--purple:{p['purple']};--pink:{p['pink']};--cyan:{p['cyan']};",
        "}",
    ])


def css_text():
    blocks = [css_block(k, i == 0) for i, k in enumerate(KEYS)]
    return CSS_START + "\n" + "\n".join(blocks) + "\n\n"


def js_key(key):
    return f"'{key}'" if "-" in key else key


def js_map(name, value_of, per_line=3):
    rows, line = [], []
    for i, k in enumerate(KEYS, 1):
        line.append(f"{js_key(k)}:{value_of(k)!r}")
        if i % per_line == 0:
            rows.append("  " + ", ".join(line) + ",")
            line = []
    if line:
        rows.append("  " + ", ".join(line) + ",")
    rows[-1] = rows[-1].rstrip(",")
    return f"const {name} = {{\n" + "\n".join(rows) + "\n};"


def js_text():
    return "\n".join([
        js_map("THEME_NAMES", lambda k: THEME_NAMES[k]),
        "const THEMES = Object.keys(THEME_NAMES);",
        js_map("THEME_COLORS", lambda k: THEMES[k]["accent"]),
        js_map("THEME_BG", lambda k: THEMES[k]["bg"]),
        "",
    ])


def splice(text, start, end, new, what):
    i = text.find(start)
    j = text.find(end, i + 1)
    if i < 0 or j < 0:
        raise SystemExit(f"could not find the {what} block ({start!r} .. {end!r})")
    return text[:i] + new + text[j:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", type=Path, default=DEFAULT_WEB)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    edits = []
    css_path = args.web / "app.css"
    css = css_path.read_text(encoding="utf-8")
    edits.append((css_path, css, splice(css, CSS_START, CSS_END, css_text(), "CSS theme")))

    js_path = args.web / "app.js"
    js = js_path.read_text(encoding="utf-8")
    edits.append((js_path, js, splice(js, JS_START, JS_END, js_text(), "JS theme map")))

    stale = [p for p, old, new in edits if old != new]
    if args.check:
        for p in stale:
            print(f"out of date: {p}")
        return 1 if stale else 0
    for p, _old, new in edits:
        p.write_text(new, encoding="utf-8")
    print(f"{len(THEMES)} themes -> {', '.join(str(p) for p in (css_path, js_path))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
