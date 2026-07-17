#!/usr/bin/env python3
"""depth_parallax.py — turn a flat storyboard still into genuine 2.5D parallax.

This is the GENERAL animation path: given any project's own storyboard image, it
estimates a monocular depth map, slices the image into a few depth layers
(near / mid / far paper cutouts), and authors a `blender_render` spec in which
each layer pans by a DIFFERENT amount over the shot.  Foreground and background
therefore shift at different rates as the (virtual) camera drifts through the
scene — real depth parallax, not a uniform Ken Burns pan/zoom of a flat frame.

Pipeline per image:
  1. estimate_depth()   Depth-Anything-V2-Small (ONNX, CPU via onnxruntime) ->
                        a normalised depth map (near = 1.0, far = 0.0).
  2. build_layers()     threshold depth into N bands; each near/mid band becomes
                        a full-canvas RGBA cutout with a feathered alpha edge,
                        and the far band becomes a fully opaque background with
                        the nearer bands INPAINTED away (so a moving foreground
                        never leaves a ghost of itself behind).
  3. author_shot_spec() every layer is a full-canvas plane, overscanned so it
                        never reveals an edge, and given differential pan
                        keyframes (near pans most, far least) = parallax.

Why multi-plane cutout (not mesh displacement): the production renderer
(`scripts/blender_render.py`) uses an ORTHOGRAPHIC camera, under which moving the
camera translates every plane by the same amount regardless of depth — so a
camera move alone yields NO parallax.  Differential per-layer panning of a few
cutout planes produces the parallax directly, reuses the existing flat-plane
renderer with zero changes, and is far more robust on soft cartoon art than mesh
displacement (which would need a perspective camera and tears on approximate
depth).  See the task notes / schema for the trade-off.

Runs entirely inside the shared `.venv` (onnxruntime + numpy + Pillow + scipy);
no torch, no GPU, no ComfyUI dependency.  The ONNX model is fetched once to a
user cache dir on first use.
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# --------------------------------------------------------------------------- #
# Depth model (Depth-Anything-V2-Small, ONNX) — fetched once to a user cache.  #
# --------------------------------------------------------------------------- #

_MODEL_URL = ("https://huggingface.co/onnx-community/depth-anything-v2-small/"
              "resolve/main/onnx/model.onnx")
_MODEL_DIR = Path.home() / ".cache" / "kids_studio_depth"
_MODEL_PATH = _MODEL_DIR / "depth_anything_v2_small.onnx"
# Depth-Anything wants the input side to be a multiple of 14.  518 = 37*14 is the
# model's native training size and a good speed/quality point on CPU.
_INPUT_SIDE = 518
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_SESSION = None  # lazily created onnxruntime session (heavy; reused across shots)


def log(msg: str):
    """Print a progress line and flush it (the SSE contract used across scripts/)."""
    print(f"[depth_parallax] {msg}", flush=True)


def ensure_model() -> Path:
    """Return the local ONNX path, downloading it once if missing."""
    if _MODEL_PATH.is_file() and _MODEL_PATH.stat().st_size > 1_000_000:
        return _MODEL_PATH
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    log(f"downloading depth model (~95 MB) -> {_MODEL_PATH} ...")
    tmp = _MODEL_PATH.with_suffix(".onnx.part")
    with urllib.request.urlopen(_MODEL_URL, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(_MODEL_PATH)
    log("depth model ready")
    return _MODEL_PATH


def _session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        ensure_model()
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
        _SESSION = ort.InferenceSession(str(_MODEL_PATH), sess_options=so,
                                        providers=["CPUExecutionProvider"])
    return _SESSION


# --------------------------------------------------------------------------- #
# 1. Depth estimation                                                          #
# --------------------------------------------------------------------------- #

def estimate_depth(image: Image.Image) -> np.ndarray:
    """Estimate a normalised depth map for an RGB image.

    Returns a float32 array shaped (H, W) with values in [0, 1] where **1.0 is
    nearest** the camera and 0.0 is farthest, resampled back to the image size.
    """
    rgb = image.convert("RGB")
    W, H = rgb.size

    small = rgb.resize((_INPUT_SIDE, _INPUT_SIDE), Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
    tensor = np.transpose(arr, (2, 0, 1))[None]  # (1,3,side,side)

    sess = _session()
    out = sess.run(None, {sess.get_inputs()[0].name: tensor})[0]
    depth = np.asarray(out, dtype=np.float32).squeeze()  # (h', w')

    # Depth-Anything-V2 emits a disparity-like map: LARGER = NEARER.  Normalise to
    # [0,1] robustly (ignore a few outlier pixels so one bright speck can't crush
    # the range), then keep the "near = 1" convention.
    lo, hi = np.percentile(depth, 1.0), np.percentile(depth, 99.0)
    if hi - lo < 1e-6:
        hi = lo + 1e-6
    depth = np.clip((depth - lo) / (hi - lo), 0.0, 1.0)

    depth_img = Image.fromarray((depth * 255).astype(np.uint8)).resize(
        (W, H), Image.BILINEAR)
    return np.asarray(depth_img, dtype=np.float32) / 255.0


# --------------------------------------------------------------------------- #
# 2. Layer extraction (multi-plane cutouts + inpainted background)             #
# --------------------------------------------------------------------------- #

def _nearest_inpaint(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Fill `hole` pixels of `rgb` from their nearest non-hole neighbour.

    A cheap, dependency-light inpaint (scipy's exact Euclidean distance transform
    returns, for every pixel, the index of the nearest FALSE pixel).  For the
    small reveals a gentle parallax exposes behind a moving foreground layer this
    is visually clean once softened, and needs no OpenCV/torch.
    """
    from scipy import ndimage

    if not hole.any() or hole.all():
        # Nothing to fill, or (degenerate flat depth) nothing to fill FROM — the
        # distance transform has no source pixel, so leave the image untouched.
        return rgb.copy()
    # indices of nearest non-hole pixel for every pixel
    idx = ndimage.distance_transform_edt(
        hole, return_distances=False, return_indices=True)
    filled = rgb[tuple(idx)]
    # Soften the filled region a touch so nearest-neighbour blocks don't read as
    # hard facets when they briefly peek out.
    blurred = np.asarray(
        Image.fromarray(filled).filter(ImageFilter.GaussianBlur(4)),
        dtype=np.uint8)
    out = filled.copy()
    m = hole[..., None]
    return np.where(m, blurred, out)


def _feather(mask: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian-feather a boolean/float mask into a smooth [0,1] alpha."""
    m = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))
    m = m.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(m, dtype=np.float32) / 255.0


def build_layers(image: Image.Image, depth: np.ndarray, out_dir: Path,
                 stem: str, n_bands: int = 3, feather_px: float = 6.0):
    """Slice `image` into `n_bands` depth layers, write RGBA PNGs to `out_dir`.

    Returns a list of dicts ordered FAR -> NEAR:
        [{"name","path","depth"}, ...]
    where `depth` is the band's mean normalised depth (0 far .. 1 near), used by
    the spec author to scale each layer's pan (nearer = pans more).

    Construction:
      * The FAR layer is the whole image, fully opaque, with the nearer bands
        inpainted out — so it always covers the canvas (no edge reveal) and holds
        no ghost of the moved-away foreground.
      * Each nearer band is a full-canvas RGBA cutout (feathered alpha) so it
        registers 1:1 with the background and reveals the background cleanly as
        it parallax-pans.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    H, W = depth.shape

    # Band edges by depth quantiles so every band actually holds content (a flat
    # sky-heavy frame would starve fixed thresholds).  edges: [0=far .. 1=near].
    qs = np.linspace(0.0, 1.0, n_bands + 1)
    edges = [float(np.quantile(depth, q)) for q in qs]
    edges[0], edges[-1] = -1e-3, 1.0 + 1e-3

    # Hard band masks (band 0 = farthest).
    band_masks = []
    for b in range(n_bands):
        lo, hi = edges[b], edges[b + 1]
        band_masks.append((depth >= lo) & (depth < hi))

    layers = []

    # --- FAR layer: full frame, nearer bands inpainted away, opaque ---
    nearer = np.zeros((H, W), dtype=bool)
    for b in range(1, n_bands):
        nearer |= band_masks[b]
    # Dilate the hole a little so feathered edges of the cutouts don't double up.
    nearer_soft = _feather(nearer, feather_px) > 0.25
    far_rgb = _nearest_inpaint(rgb, nearer_soft)
    far_rgba = np.dstack([far_rgb, np.full((H, W), 255, np.uint8)])
    far_path = out_dir / f"{stem}_L0_far.png"
    Image.fromarray(far_rgba, "RGBA").save(far_path)
    layers.append({"name": "L0_far", "path": far_path,
                   "depth": float(depth[band_masks[0]].mean()
                                  if band_masks[0].any() else 0.0)})

    # --- nearer bands: feathered RGBA cutouts over the full canvas ---
    for b in range(1, n_bands):
        alpha = _feather(band_masks[b].astype(np.float32), feather_px)
        # Keep the band's own pixels fully opaque at the core; feather only rolls
        # off the border, so the cutout stays crisp where it matters.
        alpha = np.clip(alpha * 1.35, 0.0, 1.0)
        rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
        name = f"L{b}_" + ("near" if b == n_bands - 1 else f"mid{b}")
        path = out_dir / f"{stem}_{name}.png"
        Image.fromarray(rgba, "RGBA").save(path)
        layers.append({"name": name, "path": path,
                       "depth": float(depth[band_masks[b]].mean()
                                      if band_masks[b].any() else float(b) / n_bands)})

    return layers


# --------------------------------------------------------------------------- #
# 3. Spec authoring (differential pan = parallax)                              #
# --------------------------------------------------------------------------- #

# A small palette of camera drifts, cycled per shot so the motion never looks
# mechanical.  Each is a unit direction (dx, dy) for the NEAR layer's travel;
# farther layers travel a fraction of it.  Kept gentle — big moves tear soft
# depth and blow past the overscan margin.
_DRIFTS = [
    (1.0, 0.0), (-1.0, 0.0), (0.7, 0.5), (-0.7, 0.5),
    (0.0, 1.0), (0.8, -0.4), (-0.8, -0.4), (0.5, 0.8),
]


def author_shot_spec(layers, duration, canvas, drift_index=0,
                     near_pan_frac=0.11, overscan=1.22, push=0.05, fps=24):
    # near_pan_frac tuned up from 0.055: at 0.055 the near/far differential
    # measured only ~2px (imperceptible — read as a flat pan/zoom).  0.11 gives a
    # clearly-visible ~25px differential; 0.18 is dramatic (~48px) but the
    # inpaint seams around foreground cutouts start to ghost on approximate
    # cartoon depth.  0.11 + overscan 1.22 is the balance: alive, minimal seams.
    """Author a `blender_render` spec whose layers parallax-pan over the shot.

    `layers` is the FAR->NEAR list from build_layers().  Every layer is a
    full-canvas plane (anchor = centre); it is overscanned by `overscan` so the
    opaque far layer never reveals a canvas edge, then panned by an amount
    proportional to its depth (near pans `near_pan_frac`*W, far a small fraction
    of that) in the shot's drift direction.  A slight shared `push` (zoom) adds
    life on top without touching the parallax.

    Returns a spec dict ready for scripts/blender_render.py.
    """
    W, H = canvas
    cx, cy = W / 2.0, H / 2.0
    dx, dy = _DRIFTS[drift_index % len(_DRIFTS)]
    # Normalise the drift so diagonal moves aren't longer than axis-aligned ones.
    mag = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    dx, dy = dx / mag, dy / mag
    max_pan = near_pan_frac * W

    n = len(layers)
    spec_layers = []
    for i, layer in enumerate(layers):
        # Depth-scaled pan: farthest layer barely moves, nearest moves fully.
        # Blend the model's mean-depth with the band index for a monotonic,
        # well-separated spread even when the depth map is soft.
        idx_frac = i / max(1, n - 1)              # 0 far .. 1 near
        d = 0.5 * idx_frac + 0.5 * float(layer["depth"])
        # Keep a floor so even the far layer drifts a hair (alive, not frozen),
        # and a clear gap between bands so parallax is visible.
        pan = max_pan * (0.15 + 0.85 * d)
        ox, oy = dx * pan, dy * pan
        # Pan symmetric about centre so the layer is centred mid-shot (maximises
        # usable overscan margin at both ends).
        start = [cx - ox, cy - oy]
        end = [cx + ox, cy + oy]
        # Gentle shared push-in; nearer layers push a touch more (parallax dolly).
        s0 = overscan * (1.0 + push * 0.15 * d)
        s1 = overscan * (1.0 + push * (0.4 + 0.6 * d))
        spec_layers.append({
            "name": layer["name"],
            "image": str(Path(layer["path"]).resolve()),
            "z": i,  # far first, near last -> correct paint order
            "anchor": [W / 2.0, H / 2.0],
            "keyframes": [
                {"t": 0.0, "pos": start, "scale": round(s0, 4),
                 "easing": "ease_in_out"},
                {"t": round(duration, 4), "pos": end, "scale": round(s1, 4)},
            ],
        })

    return {
        "fps": fps,
        "duration": round(duration, 4),
        "resolution": [W, H],
        "layers": spec_layers,
    }


# --------------------------------------------------------------------------- #
# Convenience: one call from image -> spec.json on disk                         #
# --------------------------------------------------------------------------- #

def build_shot(image_path, work_dir, duration, drift_index=0, n_bands=3,
               fps=24, canvas=None):
    """Full per-shot build: depth -> layers -> spec.json.  Returns the spec path.

    Layer PNGs and the spec land in `work_dir`.  `canvas` defaults to the image's
    own pixel size.  Depth is cached (keyed on the image bytes) so a re-run with
    an unchanged image skips re-inference.
    """
    image_path = Path(image_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(image_path).convert("RGB")
    if canvas is None:
        canvas = img.size
    W, H = canvas
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)

    stem = image_path.stem
    # Cache the depth map on the image content AND the canvas size (the stored
    # map is resampled to the canvas, so a different canvas needs its own entry).
    key = hashlib.sha256(
        image_path.read_bytes() + f"|{W}x{H}".encode()).hexdigest()[:16]
    depth_cache = work_dir / f"{stem}_{key}_depth.npy"
    if depth_cache.is_file():
        depth = np.load(depth_cache)
    else:
        log(f"estimating depth: {image_path.name}")
        depth = estimate_depth(img)
        np.save(depth_cache, depth)

    layers = build_layers(img, depth, work_dir, stem, n_bands=n_bands)
    spec = author_shot_spec(layers, duration, (W, H), drift_index=drift_index,
                            fps=fps)
    import json
    spec_path = work_dir / f"{stem}_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return spec_path


def _save_depth_preview(depth: np.ndarray, path: Path):
    """Write a grayscale depth preview (near = bright) for eyeball inspection."""
    Image.fromarray((depth * 255).astype(np.uint8)).save(path)


def main():
    p = argparse.ArgumentParser(description="Depth-parallax layer/spec builder.")
    p.add_argument("--image", required=True)
    p.add_argument("--out", default=None, help="work dir (default: alongside image)")
    p.add_argument("--duration", type=float, default=4.0)
    p.add_argument("--bands", type=int, default=3)
    p.add_argument("--drift", type=int, default=0)
    p.add_argument("--depth-preview", action="store_true",
                   help="also write <stem>_depthmap.png")
    args = p.parse_args()

    image_path = Path(args.image)
    work_dir = Path(args.out) if args.out else image_path.parent / "_parallax"
    if args.depth_preview:
        img = Image.open(image_path).convert("RGB")
        depth = estimate_depth(img)
        work_dir.mkdir(parents=True, exist_ok=True)
        _save_depth_preview(depth, work_dir / f"{image_path.stem}_depthmap.png")
        log(f"wrote depth preview -> {work_dir / (image_path.stem + '_depthmap.png')}")
    spec = build_shot(image_path, work_dir, args.duration,
                      drift_index=args.drift, n_bands=args.bands)
    log(f"spec -> {spec}")


if __name__ == "__main__":
    main()
