#!/usr/bin/env python3
"""Generate home-assistant/brands assets from brand/source.png.

Outputs to brand/out/: icon.png (256), icon@2x.png (512), logo.png, logo@2x.png.
Trims transparent (or near-white) borders, keeps aspect, centers on a square
transparent canvas for the icons.

    pip install pillow
    python brand/make_brand_assets.py [path/to/source.png]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "source.png"
OUT = HERE / "out"

_WHITE_TOL = 12  # treat pixels within this of pure white as background


def _to_rgba_trimmed(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    # if the image has no real transparency, knock out a white background
    if im.getextrema()[3] == (255, 255):
        for y in range(h):
            for x in range(w):
                r, g, b, _a = px[x, y]
                if r >= 255 - _WHITE_TOL and g >= 255 - _WHITE_TOL and b >= 255 - _WHITE_TOL:
                    px[x, y] = (r, g, b, 0)
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def _square(im: Image.Image, size: int) -> Image.Image:
    im = im.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return canvas


def _wide(im: Image.Image, max_w: int, max_h: int) -> Image.Image:
    im = im.copy()
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    return im


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"missing source image: {SRC}")
    OUT.mkdir(exist_ok=True)
    base = _to_rgba_trimmed(Image.open(SRC))

    _square(base, 256).save(OUT / "icon.png")
    _square(base, 512).save(OUT / "icon@2x.png")
    _wide(base, 256, 256).save(OUT / "logo.png")
    _wide(base, 512, 512).save(OUT / "logo@2x.png")
    for p in sorted(OUT.glob("*.png")):
        with Image.open(p) as chk:
            print(f"{p.name:14} {chk.size[0]}x{chk.size[1]}")


if __name__ == "__main__":
    main()
