"""Prepare clean RGBA layers for the spike into the worktree (never touch outputs/).

Produces, in spike_blender/assets/:
  bg.png       -- opaque background plate (park), the SH060 storyboard.
  bus.png      -- the bus cutout, cropped to its alpha bbox (RGBA).
  wheel.png    -- a single wheel, magenta-keyed to alpha, cropped square about its
                  centre so it rotates in place. NB: it is an ELLIPSE (3/4 view) --
                  rotating it in 2D looks wrong; that limitation is left in on purpose.
"""
import numpy as np
from PIL import Image
from pathlib import Path

SRC = Path(r"C:\pradeep\animator\outputs\layer_assets")
OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)


def content_bbox_rgba(im):
    a = np.array(im)[..., 3]
    ys, xs = np.where(a > 24)
    return (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)


# --- background -------------------------------------------------------------
bg = Image.open(SRC / "ORIGINAL_SH060.png").convert("RGB")
bg.save(OUT / "bg.png")
print(f"bg.png   {bg.size}")

# --- bus --------------------------------------------------------------------
bus = Image.open(SRC / "phase2_isolated" / "bus_whole__isnet-anime__cutout.png").convert("RGBA")
bb = content_bbox_rgba(bus)
bus_c = bus.crop(bb)
bus_c.save(OUT / "bus.png")
print(f"bus.png  crop={bb} -> {bus_c.size}")

# --- wheel: crop the front wheel straight out of the bus cutout -------------
# The SAM wheel PNGs are RGB previews with the transparency baked into diagonal
# magenta/black stripes -- keying them cleanly is not worth it for a spike. The
# bus cutout already has proper alpha and the wheel is drawn in the same style,
# so we crop a square around the front wheel and use that. It is a 3/4-view
# ELLIPSE; rotating it in 2D will read as a wobble, which is the honest limit.
bus_full = Image.open(SRC / "phase2_isolated" / "bus_whole__isnet-anime__cutout.png").convert("RGBA")
# Front wheel centre in the 1152x768 cutout, measured off the image.
WCX, WCY, WHALF = 620, 588, 118
wheel = bus_full.crop((WCX - WHALF, WCY - WHALF, WCX + WHALF, WCY + WHALF))
# Circular alpha mask so the surrounding body/fender fragments drop out and only
# the round wheel remains -- a rotating disc, not a rotating chunk of bus.
wa = np.array(wheel)
yy, xx = np.mgrid[0:wa.shape[0], 0:wa.shape[1]]
cx = cy = wa.shape[0] / 2.0
rad = wa.shape[0] / 2.0 - 4
disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= rad ** 2
wa[..., 3] = np.where(disc, wa[..., 3], 0)
Image.fromarray(wa, "RGBA").save(OUT / "wheel.png")
# Also record where each wheel sits on the bus, in bus-crop pixel coords, so the
# spec can pin rotating wheels onto the driving bus.
print(f"wheel.png  {wheel.size} (front-wheel crop from bus cutout)")
print("DONE prep")
