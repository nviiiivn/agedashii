#!/usr/bin/env python3
"""Rasterise the four app icons from their SVG masters.

The SVGs are the source of truth; every PNG in this directory is produced
by this script and nothing in here should ever be hand-edited in a bitmap
editor. Run it after touching any master:

    /home/nvii/venvs/flet/bin/python assets/icons/render.py

Backends, in the order it tries them: rsvg-convert, then Inkscape, then
cairosvg. On this machine only cairosvg is present (there is a librsvg
shared library but no CLI for it), so that is what actually runs; the two
CLI branches are there so a different machine with librsvg-bin or Inkscape
installed does not need cairosvg pulled in. Pillow is not a candidate -
it has no SVG parser at all, it only shells out to a rasteriser.

Options:

    --check [dir]   also write greyscale contact sheets, so the silhouettes
                    can be compared with the colour taken out. They go to a
                    temp directory by default and print their path, rather
                    than into this one - they are review artefacts and must
                    not end up in the commit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The four apps. The id is the filename stem and matches apps/<id>/.
ICONS = ("tofu", "moto", "watudo", "foodzen")

# 1024 is what `flet build` wants for an app icon; the rest are the launcher
# sizes Android asks for, plus 48 because 48 is the size the icon is actually
# judged at on a phone home screen.
SIZES = (1024, 512, 192, 48)


def _rsvg(src: Path, dst: Path, size: int) -> None:
    subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(dst), str(src)],
        check=True,
    )


def _inkscape(src: Path, dst: Path, size: int) -> None:
    subprocess.run(
        ["inkscape", str(src), "--export-type=png", f"--export-filename={dst}",
         f"--export-width={size}", f"--export-height={size}"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _cairosvg(src: Path, dst: Path, size: int) -> None:
    import cairosvg  # imported lazily so the CLI backends need no install

    cairosvg.svg2png(url=str(src), write_to=str(dst),
                     output_width=size, output_height=size)


def pick_backend():
    """Return (name, fn) for the first rasteriser this machine actually has."""
    if shutil.which("rsvg-convert"):
        return "rsvg-convert", _rsvg
    if shutil.which("inkscape"):
        return "inkscape", _inkscape
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        pass
    else:
        return "cairosvg", _cairosvg
    sys.exit(
        "No SVG rasteriser available. Install one of:\n"
        "  sudo apt install librsvg2-bin        (gives rsvg-convert)\n"
        "  /home/nvii/venvs/flet/bin/python -m pip install cairosvg\n"
    )


def greyscale_sheet(out: Path) -> None:
    """One strip of all four at 48px, desaturated, on a neutral ground.

    The rule this exists to enforce: if two apps are not telling apart in
    this strip, the design is wrong, not the viewer. Colour is identical
    across the family by design - the brand gradient is on every glyph -
    so silhouette is the only thing doing the work.
    """
    from PIL import Image

    out.mkdir(parents=True, exist_ok=True)
    for size in (48, 192):
        tiles = [Image.open(HERE / f"{i}-{size}.png").convert("L") for i in ICONS]
        gap = max(4, size // 8)
        sheet = Image.new("L", (size * 4 + gap * 5, size + gap * 2), 128)
        for n, tile in enumerate(tiles):
            sheet.paste(tile, (gap + n * (size + gap), gap))
        sheet.save(out / f"greyscale-{size}.png")
        # And each one on its own, large enough to actually look at.
        for name, tile in zip(ICONS, tiles):
            if size == 48:
                tile.resize((384, 384), Image.NEAREST).save(out / f"{name}-48-grey-4x.png")


def main() -> int:
    name, render = pick_backend()
    print(f"renderer: {name}")
    for icon in ICONS:
        src = HERE / f"{icon}.svg"
        if not src.exists():
            sys.exit(f"missing master: {src}")
        for size in SIZES:
            dst = HERE / f"{icon}-{size}.png"
            render(src, dst, size)
            print(f"  {dst.name}  {dst.stat().st_size:>7} bytes")
    if "--check" in sys.argv:
        argv = sys.argv[sys.argv.index("--check") + 1:]
        out = Path(argv[0]) if argv else Path(tempfile.mkdtemp(prefix="icon-check-"))
        greyscale_sheet(out)
        print(f"greyscale sheets: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
