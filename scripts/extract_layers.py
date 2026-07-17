"""Turn an object rendered isolated-on-white into clean 2.5D RGBA layers.

Motivation
----------
SD 1.5 cannot render object *parts* (ask for "a bus with no wheels" and you get
wheels anyway). The working pattern for a 2.5D pipeline is:

    1. Generate the WHOLE object isolated on a plain white background
       (strict side / profile view if the part must rotate as a true circle).
    2. Key out the white background -> clean RGBA body layer.
    3. Extract each rotating part (a wheel, a propeller, ...) with a circular
       mask centred on its pivot -> a rotation-invariant part layer whose
       bounding box does not change as it spins.

This module implements steps 2 and 3. It deliberately does NOT use a neural
matte (rembg/isnet) for the body: on a flat plain-white render a border
flood-fill white-key is both cleaner and more predictable (isnet-anime chewed
most of a bright bus). Neural mattes remain the right tool for busy backgrounds.

Requires: pillow, numpy (both already pinned in the repo .venv). No new deps.

CLI
---
  # body layer (white background -> transparent), trimmed to content:
  python extract_layers.py whitekey in.png out_rgba.png [--thresh 232] \
      [--drop-interior-white] [--checker chk.png] [--trim]

  # circular part (e.g. a wheel), pivot at image centre:
  python extract_layers.py circle in.png out_rgba.png --cx 240 --cy 515 \
      --r 60 [--pad 5] [--checker chk.png]
"""
from __future__ import annotations
import argparse
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _near_white(arr: np.ndarray, thresh: int) -> np.ndarray:
    return (arr[:, :, 0] > thresh) & (arr[:, :, 1] > thresh) & (arr[:, :, 2] > thresh)


def white_key(
    img: Image.Image,
    thresh: int = 232,
    drop_interior_white: bool = False,
    feather: float = 0.6,
) -> Image.Image:
    """Make a plain white/near-white background transparent.

    Border flood-fill: only near-white pixels connected to the image edge become
    transparent, so interior near-white regions (e.g. window glass, chrome) are
    preserved. Pass ``drop_interior_white=True`` to additionally clear ALL
    near-white pixels (useful when enclosed ground-shadow or hub highlights must
    also go, and any interior holes will be covered by another layer).
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    arr = np.asarray(rgb).astype(np.int16)
    nw = _near_white(arr, thresh)

    bg = np.zeros((h, w), dtype=bool)
    dq: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if nw[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if nw[y, x] and not bg[y, x]:
                bg[y, x] = True
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and nw[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                dq.append((nx, ny))

    remove = bg | nw if drop_interior_white else bg
    alpha = np.where(remove, 0, 255).astype(np.uint8)
    alpha_img = Image.fromarray(alpha, "L")
    if feather:
        alpha_img = alpha_img.filter(ImageFilter.GaussianBlur(feather))
    out = rgb.convert("RGBA")
    out.putalpha(alpha_img)
    return out


def trim_to_content(img: Image.Image, alpha_min: int = 20) -> tuple[Image.Image, tuple[int, int]]:
    """Crop to the opaque bounding box. Returns (cropped, (x0, y0) origin)."""
    a = np.asarray(img)[:, :, 3]
    ys, xs = np.where(a > alpha_min)
    if len(xs) == 0:
        return img, (0, 0)
    x0, y0 = int(xs.min()), int(ys.min())
    return img.crop((x0, y0, int(xs.max()) + 1, int(ys.max()) + 1)), (x0, y0)


def extract_circle(
    img: Image.Image, cx: int, cy: int, r: int, pad: int = 5, feather: float = 1.0
) -> Image.Image:
    """Crop a circular part centred on (cx, cy). Pivot = centre of the result.

    Result is a (2*(r+pad))^2 RGBA image; the circular alpha makes the part
    rotation-invariant (its bounding box is unchanged by any rotation about the
    centre), which is exactly what a spinning wheel needs.
    """
    R = r + pad
    size = 2 * R
    crop = img.convert("RGBA").crop((cx - R, cy - R, cx + R, cy + R))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((R - r, R - r, R + r, R + r), fill=255)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    crop.putalpha(mask)
    return crop


def checker_preview(img: Image.Image, sq: int = 24) -> Image.Image:
    """Composite over a magenta/charcoal checkerboard to eyeball the alpha."""
    w, h = img.size
    cb = Image.new("RGB", (w, h))
    px = cb.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (255, 0, 255) if ((x // sq + y // sq) % 2 == 0) else (30, 30, 30)
    cb.paste(img, (0, 0), img.convert("RGBA"))
    return cb


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("whitekey", help="key out a plain white background")
    w.add_argument("inp"); w.add_argument("out")
    w.add_argument("--thresh", type=int, default=232)
    w.add_argument("--drop-interior-white", action="store_true")
    w.add_argument("--trim", action="store_true")
    w.add_argument("--checker")

    c = sub.add_parser("circle", help="extract a circular part, pivot at centre")
    c.add_argument("inp"); c.add_argument("out")
    c.add_argument("--cx", type=int, required=True)
    c.add_argument("--cy", type=int, required=True)
    c.add_argument("--r", type=int, required=True)
    c.add_argument("--pad", type=int, default=5)
    c.add_argument("--checker")

    a = ap.parse_args()
    src = Image.open(a.inp)
    if a.cmd == "whitekey":
        out = white_key(src, a.thresh, a.drop_interior_white)
        if a.trim:
            out, origin = trim_to_content(out)
            print(f"trimmed origin (x,y) = {origin}")
        out.save(a.out)
    else:
        out = extract_circle(src, a.cx, a.cy, a.r, a.pad)
        print(f"pivot = ({out.width // 2}, {out.height // 2}), radius = {a.r}")
    out.save(a.out)
    print("saved", a.out, out.size)
    if a.checker:
        checker_preview(out).save(a.checker)
        print("saved", a.checker)


if __name__ == "__main__":
    _main()
