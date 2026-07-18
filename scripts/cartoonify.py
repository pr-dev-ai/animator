#!/usr/bin/env python3
"""cartoonify.py — flatten painterly art into flat-cartoon art.

The image model paints soft, shaded, anime-illustration frames; a real 2D cartoon
is flat colour + bold outlines.  This posterises to a few solid colours, draws dark
cel-lines where colour regions meet, and (for characters) a bold dark silhouette
outline — turning a moving photo into something that reads as drawn.  Pure PIL +
numpy + scipy, no model calls.
"""
from pathlib import Path


def cartoonify(img, colors: int = 14, silhouette: bool = False,
               edges: bool = True, outline_width: int = 6):
    import numpy as np
    from PIL import Image, ImageFilter
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba)
    alpha = arr[:, :, 3]

    # smooth away painterly speckle FIRST, so posterising yields clean flat regions
    # (not a grainy field of tiny colour patches that then spawn edge noise).
    pil_rgb = Image.fromarray(arr[:, :, :3], "RGB").filter(ImageFilter.MedianFilter(5))
    pal = pil_rgb.quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.NONE)
    # despeckle the palette-index map, then map indices back through the palette
    idx = np.asarray(Image.fromarray(np.asarray(pal).astype("uint8"), "L")
                     .filter(ImageFilter.MedianFilter(5)))
    palette = np.array(pal.getpalette(), dtype=np.uint8).reshape(-1, 3)
    flat = palette[idx].copy()

    if edges:                                 # dark cel-line where regions meet
        e = np.zeros(idx.shape, bool)
        e[:, :-1] |= idx[:, :-1] != idx[:, 1:]
        e[:-1, :] |= idx[:-1, :] != idx[1:, :]
        e &= alpha > 20                       # only inside the subject
        flat[e] = (38, 28, 28)

    out = np.dstack([flat, alpha]).astype(np.uint8)
    if silhouette:
        from scipy import ndimage
        mask = alpha > 30
        ring = ndimage.binary_dilation(mask, iterations=outline_width) & ~mask
        out[ring] = (30, 22, 22, 255)         # bold dark border around the figure
    return Image.fromarray(out, "RGBA")


def cartoonify_file(src, dst=None, **kw):
    from PIL import Image
    src = Path(src)
    dst = Path(dst) if dst else src
    cartoonify(Image.open(src), **kw).save(dst)
    return dst


if __name__ == "__main__":
    import sys
    sil = "--silhouette" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cartoonify_file(args[0], args[1] if len(args) > 1 else None, silhouette=sil)
    print("cartoonified", args[0])
