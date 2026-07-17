"""Generate synthetic test assets for the Blender render module's parenting proof.

Draws deterministic shapes (no AI, no external art) so the parenting + wheel-spin
tests are reproducible: a gradient background, a road strip, a rectangular bus
body, and a circular wheel with a bright spoke so rotation is unmistakable.

Run with the app venv Python (has PIL): .venv/Scripts/python.exe
"""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "test_assets"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1152, 768


def save(im, name):
    im.save(OUT / name)
    print(f"{name}: {im.size} {im.mode}")


# --- background: vertical sky gradient (opaque RGB) -------------------------
grad = np.zeros((H, W, 3), dtype=np.uint8)
top = np.array([120, 190, 255])      # sky blue
bot = np.array([225, 245, 255])      # pale horizon
for y in range(H):
    t = y / (H - 1)
    grad[y, :, :] = (top * (1 - t) + bot * t).astype(np.uint8)
save(Image.fromarray(grad, "RGB"), "bg.png")

# --- road: dark strip along the bottom (RGBA, so it sits over the bg) -------
road = Image.new("RGBA", (W, 160), (60, 60, 68, 255))
rd = ImageDraw.Draw(road)
for x in range(0, W, 120):                       # dashed centre line
    rd.rectangle([x + 20, 74, x + 80, 86], fill=(240, 220, 90, 255))
save(road, "road.png")

# --- bus body: rounded rectangle with windows (RGBA) ------------------------
BW, BH = 520, 240
bus = Image.new("RGBA", (BW, BH), (0, 0, 0, 0))
bd = ImageDraw.Draw(bus)
bd.rounded_rectangle([4, 4, BW - 4, BH - 4], radius=40, fill=(230, 70, 60, 255),
                     outline=(120, 30, 25, 255), width=6)
for i in range(4):                               # windows
    x0 = 40 + i * 110
    bd.rounded_rectangle([x0, 40, x0 + 80, 120], radius=12, fill=(180, 225, 245, 255))
bd.rectangle([0, BH - 40, BW, BH], fill=(0, 0, 0, 0))   # clear space for wheels
save(bus, "bus.png")

# --- wheel: black disc with a bright spoke, anchor = geometric centre -------
D = 160
wheel = Image.new("RGBA", (D, D), (0, 0, 0, 0))
wd = ImageDraw.Draw(wheel)
c = D / 2.0
wd.ellipse([4, 4, D - 4, D - 4], fill=(25, 25, 30, 255), outline=(15, 15, 18, 255), width=3)
wd.ellipse([c - 22, c - 22, c + 22, c + 22], fill=(90, 90, 100, 255))   # hub
# One bright spoke (12 o'clock) so any rotation is obvious frame-to-frame.
wd.rectangle([c - 7, 12, c + 7, c], fill=(255, 210, 40, 255))
# A second, dimmer spoke at 3 o'clock to disambiguate spin direction.
wd.rectangle([c, c - 6, D - 14, c + 6], fill=(255, 120, 40, 255))
save(wheel, "wheel.png")

print("DONE synthetic assets ->", OUT)
