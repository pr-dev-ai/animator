#!/usr/bin/env python3
"""gen_fx.py — procedural animated-overlay sprites for the 'living world' pass.

The puppet video used to be a rigid cutout on a STATIC painted plate, which reads
as a moving sticker.  These cheap PIL sprites (no Stable Diffusion) are added as
extra Blender layers with their own drift/twinkle keyframes, so the world itself
moves: clouds drift across the sky, sparkles float, stars twinkle.  Deterministic,
cached once in outputs/fx/, reused across every scene.
"""
from pathlib import Path

FX_DIR = Path(r"C:\pradeep\animator\outputs\fx")


def _soft_cloud(w=480, h=260):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # overlapping white lobes -> one puffy cloud, kept well inside the sprite so the
    # blur fades fully to transparent (no clipped bounding-box seam).
    lobes = [(0.30, 0.46, 0.16), (0.45, 0.40, 0.19), (0.60, 0.45, 0.17),
             (0.50, 0.52, 0.20), (0.38, 0.53, 0.15), (0.68, 0.52, 0.13)]
    for cx, cy, r in lobes:
        x, y, rr = cx * w, cy * h, r * min(w, h)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, 200))
    return img.filter(ImageFilter.GaussianBlur(16))


def _sparkle(s=64):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2.0
    # soft round glow
    for rr, a in ((s * 0.30, 90), (s * 0.20, 150), (s * 0.10, 235)):
        d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=(255, 252, 220, a))
    # 4-point star cross
    d.line((c, s * 0.10, c, s * 0.90), fill=(255, 255, 240, 220), width=max(2, s // 22))
    d.line((s * 0.10, c, s * 0.90, c), fill=(255, 255, 240, 220), width=max(2, s // 22))
    return img.filter(ImageFilter.GaussianBlur(1.2))


def _star(s=40):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2.0
    d.ellipse((c - s * 0.12, c - s * 0.12, c + s * 0.12, c + s * 0.12), fill=(255, 250, 210, 255))
    for a in (0, 45):
        import math
        for k in (1, -1):
            dx, dy = math.cos(math.radians(a)) * s * 0.42, math.sin(math.radians(a)) * s * 0.42
            d.line((c, c, c + k * dx, c + k * dy), fill=(255, 250, 220, 200), width=max(1, s // 26))
    return img.filter(ImageFilter.GaussianBlur(0.8))


def _flat_cloud(w=440, h=220):
    """Flat cartoon cloud: white fill with a bold dark rim (matches the flat art)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lobes = [(0.30, 0.50, 0.17), (0.45, 0.42, 0.20), (0.60, 0.48, 0.18),
             (0.50, 0.56, 0.21), (0.38, 0.57, 0.16), (0.70, 0.55, 0.14)]
    for cx, cy, r in lobes:                       # dark outline pass (larger)
        x, y, rr = cx * w, cy * h, r * min(w, h) + 6
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(70, 80, 100, 255))
    for cx, cy, r in lobes:                       # white fill pass (smaller)
        x, y, rr = cx * w, cy * h, r * min(w, h)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(250, 252, 255, 255))
    return img


def _bird(w=72, h=38):
    """A tiny flat gull silhouette (a distant bird), for the sky."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (45, 50, 65, 255)
    d.line([(3, h * 0.7), (w * 0.28, h * 0.22), (w * 0.5, h * 0.62)], fill=col, width=5, joint="curve")
    d.line([(w * 0.5, h * 0.62), (w * 0.72, h * 0.22), (w - 3, h * 0.7)], fill=col, width=5, joint="curve")
    return img


def _petal(s=26):
    """A small flat pink petal for festival/garden drift."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((s * 0.22, s * 0.05, s * 0.78, s * 0.95), fill=(232, 96, 128, 255))
    d.ellipse((s * 0.22, s * 0.05, s * 0.78, s * 0.95), outline=(150, 40, 70, 255), width=2)
    return img


def _shadow(w=340, h=110):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.08, h * 0.18, w * 0.92, h * 0.82), fill=(20, 25, 30, 140))
    return img.filter(ImageFilter.GaussianBlur(14))


_MAKERS = {"cloud": _soft_cloud, "sparkle": _sparkle, "star": _star, "shadow": _shadow,
           "flatcloud": _flat_cloud, "bird": _bird, "petal": _petal}


def ensure_fx(name: str) -> Path:
    """Return a cached FX sprite path, generating it once if absent."""
    FX_DIR.mkdir(parents=True, exist_ok=True)
    p = FX_DIR / f"{name}.png"
    if not p.is_file():
        _MAKERS[name]().save(p)
    return p


if __name__ == "__main__":
    for n in _MAKERS:
        print(ensure_fx(n))
