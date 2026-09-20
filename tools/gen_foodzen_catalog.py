#!/usr/bin/env python3
"""Generate flet_app/foodzen_catalog.py from the dashboard's FZ_CATALOG.

The website categorises a grocery item by name (Produce / Meat / Dairy / ...)
using a 231-entry lookup table in its JS. The app has to categorise
*identically*, or an item added on the phone lands in a different aisle than
the same item added in the browser, and the two clients quietly disagree about
data they both write to the same file.

Rather than copy the table and let it rot, generate it — the same approach
tools/gen-themes.py already takes for the 21 themes.

    python3 tools/gen_foodzen_catalog.py [path/to/dashbored-web/app.js]

Default source: ~/www/dashbored/dashbored-web/app.js
"""
import re
import sys
from pathlib import Path

DEFAULT_SRC = Path.home() / "www/dashbored/dashbored-web/app.js"
OUT = Path(__file__).resolve().parent.parent / "flet_app" / "foodzen_catalog.py"


def extract(js_path):
    src = js_path.read_text(encoding="utf-8")
    start = src.find("const FZ_CATALOG")
    if start == -1:
        raise SystemExit(f"FZ_CATALOG not found in {js_path}")
    open_brace = src.find("{", start)
    close = src.find("};", open_brace)
    if open_brace == -1 or close == -1:
        raise SystemExit("could not delimit the FZ_CATALOG object literal")
    blob = src[open_brace:close + 1]

    pairs = re.findall(r"'([^']+)'\s*:\s*'([^']+)'", blob)
    if not pairs:
        raise SystemExit("FZ_CATALOG matched no 'key': 'value' pairs")
    catalog = dict(pairs)
    if len(catalog) != len(pairs):
        print(f"note: {len(pairs) - len(catalog)} duplicate keys collapsed", file=sys.stderr)
    return catalog


def render(catalog, source):
    lines = [
        '"""Grocery item -> aisle category. GENERATED — do not edit by hand.',
        "",
        f"Source: {source}  (FZ_CATALOG)",
        "Regenerate: python3 tools/gen_foodzen_catalog.py",
        "",
        "Kept in lockstep with the website so both clients file the same item",
        "under the same category.",
        "",
        "ORDER IS SIGNIFICANT and is preserved from the JS object literal.",
        "fzAutoCat walks the table and takes the FIRST substring match, so",
        "sorting these keys would silently change how items are categorised.",
        '"""',
        "",
        "CATALOG = {",
    ]
    for k, v in catalog.items():
        lines.append(f"    {k!r}: {v!r},")
    lines.append("}")
    lines.append("")
    lines.append(f"CATEGORIES = {sorted(set(catalog.values()))!r}")
    lines.append("")
    return "\n".join(lines)


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_file():
        raise SystemExit(f"source not found: {src}")
    catalog = extract(src)
    OUT.write_text(render(catalog, src), encoding="utf-8")
    cats = sorted(set(catalog.values()))
    print(f"wrote {OUT} — {len(catalog)} items, {len(cats)} categories: {', '.join(cats)}")


if __name__ == "__main__":
    main()
