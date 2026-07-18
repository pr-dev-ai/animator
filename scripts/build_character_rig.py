#!/usr/bin/env python3
"""build_character_rig.py — generate + cut + rig a character for the puppet library.

Two steps (run generate, pick the best candidate by eye, then cut):
  generate <name>                 -> outputs/char_lib/<name>/cand_seedN.png (several)
  cut <name> <candidate.png>      -> outputs/char_lib/<name>/{body,mouth}.png + rig.json

The cut is automated: rembg isnet-anime isolates the character, then a heuristic
region on the head-front (works for a side-view character facing left) becomes the
"mouth" part that opens for lip-sync.  Same rig.json shape as the duck de-risk, so
scripts/scene_director.py drives any library character unchanged.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

COMFY = "http://localhost:8188"
LIB = Path(r"C:\pradeep\animator\outputs\char_lib")


_HUMAN_WORDS = {
    "child", "kid", "baby", "boy", "girl", "toddler", "mother", "mom", "mum",
    "father", "dad", "grandmother", "grandma", "grandpa", "grandfather",
    "grandparent", "man", "woman", "person", "family", "parent", "sister",
    "brother", "lady", "guy",
}


def _is_human(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in _HUMAN_WORDS)


def _prompt(name):
    if _is_human(name):
        return (
            "cartoon, flat color, children's storybook illustration, 2d, bold clean outlines, "
            f"simple shapes, cute, ONE single solo cartoon {name}, alone, a single pose, full body, "
            "strict side view profile facing left, standing, a distinct visible mouth, happy, "
            "simple clothes, plain solid white background, no scenery, centered"
        )
    return (
        "cartoon, flat color, children's illustration, 2d, bold clean outlines, simple shapes, cute, "
        f"a single adorable chubby baby {name}, clear side view profile facing left, round body, "
        "a distinct visible mouth, two tiny feet, one big friendly eye, happy, "
        "plain solid white background, no scenery, centered, full body"
    )


def _neg(name):
    base = ("front view, three-quarter, 3/4 view, back view, multiple characters, two figures, "
            "scenery, background, realistic, photo, dark, cropped, extra limbs")
    if _is_human(name):
        # humans need arms/hands, but SD loves turnaround sheets — kill those hard
        return (base + ", character sheet, reference sheet, model sheet, turnaround, "
                "multiple views, multiple poses, three views, front and back, grid, duplicate")
    return base + ", human, humanoid, person, arms, hands"


NEG = _neg("")  # animal default (back-compat for module-level references)


def generate(name):
    sys.path.insert(0, r"C:\pradeep\animator\web_ui")
    import pipeline_api as P
    ckpt = P._find_checkpoint()
    out = LIB / name
    out.mkdir(parents=True, exist_ok=True)
    for seed in [7, 21, 88, 130, 205, 302]:
        wf = P._comfyui_workflow(_prompt(name), _neg(name), f"char_{name}_{seed}", ckpt)
        for node in wf.values():
            if node.get("class_type") == "KSampler":
                node["inputs"]["seed"] = seed
        req = urllib.request.Request(f"{COMFY}/prompt",
                                     data=json.dumps({"prompt": wf}).encode(),
                                     headers={"Content-Type": "application/json"})
        pid = json.loads(urllib.request.urlopen(req, timeout=30).read())["prompt_id"]
        for _ in range(120):
            time.sleep(3)
            h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10).read())
            if pid in h:
                for node in h[pid]["outputs"].values():
                    for img in node.get("images", []):
                        url = f"{COMFY}/view?" + urllib.parse.urlencode(
                            {"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                             "type": img["type"]})
                        raw = urllib.request.urlopen(url, timeout=30).read()
                        p = out / f"cand_seed{seed}.png"
                        p.write_bytes(raw)
                        print("saved", p)
                break


def cut(name, candidate):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    from rembg import remove, new_session

    out = LIB / name
    out.mkdir(parents=True, exist_ok=True)
    img = Image.open(candidate).convert("RGB")
    W, H = img.size
    # crop out any left-edge UI junk the checkpoint sometimes adds
    img = img.crop((int(W * 0.30), 0, W, H))
    cw, ch = img.size

    cut_rgba = remove(img, session=new_session("isnet-anime"))
    cut_rgba.save(out / "body.png")
    alpha = np.asarray(cut_rgba)[:, :, 3]
    ys, xs = np.where(alpha > 30)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bw, bh = x1 - x0, y1 - y0
    body_anchor = ((x0 + x1) // 2, (y0 + y1) // 2)

    # heuristic mouth region: front (left) of the head, upper-third of the body.
    mx0, my0 = x0 + int(bw * 0.00), y0 + int(bh * 0.24)
    mx1, my1 = x0 + int(bw * 0.26), y0 + int(bh * 0.52)
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).ellipse((mx0, my0, mx1, my1), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(6))
    m = np.asarray(mask, float) / 255.0
    src = np.asarray(cut_rgba).astype(float)
    mouth = src.copy()
    mouth[:, :, 3] = src[:, :, 3] * m
    Image.fromarray(mouth.astype(np.uint8), "RGBA").save(out / "mouth.png")
    mouth_anchor = ((mx0 + mx1) // 2, (my0 + my1) // 2)

    rig = {"name": name, "frame": [cw, ch], "body_anchor": list(body_anchor),
           "parts": {"body": {"image": "body.png", "anchor": list(body_anchor), "z": 2},
                     "mouth": {"image": "mouth.png", "anchor": list(mouth_anchor), "z": 4}}}
    (out / "rig.json").write_text(json.dumps(rig, indent=2))

    # checker preview of body + mouth region marker
    def checker(rgba, path):
        c = Image.new("RGBA", rgba.size)
        d = c.load()
        for yy in range(rgba.size[1]):
            for xx in range(rgba.size[0]):
                v = 210 if ((xx // 22 + yy // 22) % 2 == 0) else 110
                d[xx, yy] = (v, 30, v, 255)
        comp = Image.alpha_composite(c, rgba)
        dr = ImageDraw.Draw(comp)
        dr.ellipse((mx0, my0, mx1, my1), outline=(0, 255, 0, 255), width=4)
        comp.convert("RGB").save(path)

    checker(cut_rgba, str(out / "rig_preview.png"))
    print(f"{name}: bbox ({x0},{y0})-({x1},{y1}) body_anchor {body_anchor} "
          f"mouth_anchor {mouth_anchor}  -> {out/'rig.json'}")


def _generate_one(name, seed):
    """Generate a single isolated-on-white candidate; return its path."""
    sys.path.insert(0, r"C:\pradeep\animator\web_ui")
    import pipeline_api as P
    ckpt = P._find_checkpoint()
    out = LIB / name
    out.mkdir(parents=True, exist_ok=True)
    wf = P._comfyui_workflow(_prompt(name), _neg(name), f"char_{name}_{seed}", ckpt)
    for node in wf.values():
        if node.get("class_type") == "KSampler":
            node["inputs"]["seed"] = seed
    req = urllib.request.Request(f"{COMFY}/prompt", data=json.dumps({"prompt": wf}).encode(),
                                 headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=30).read())["prompt_id"]
    for _ in range(120):
        time.sleep(3)
        h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10).read())
        if pid in h:
            for node in h[pid]["outputs"].values():
                for img in node.get("images", []):
                    url = f"{COMFY}/view?" + urllib.parse.urlencode(
                        {"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                         "type": img["type"]})
                    p = out / f"cand_seed{seed}.png"
                    p.write_bytes(urllib.request.urlopen(url, timeout=30).read())
                    return p
    return None


def _score_candidate(path) -> float:
    """Higher = a cleaner SINGLE figure. Penalises turnarounds/multi-figure (low
    largest-component share) and extreme/too-wide bounding boxes."""
    import numpy as np
    from PIL import Image
    from rembg import remove, new_session
    from scipy import ndimage
    img = Image.open(path).convert("RGB")
    W, H = img.size
    img = img.crop((int(W * 0.30), 0, W, H))
    alpha = np.asarray(remove(img, session=new_session("isnet-anime")))[:, :, 3]
    solid = alpha > 40
    if not solid.any():
        return 0.0
    lbl, n = ndimage.label(solid)
    sizes = ndimage.sum(solid, lbl, range(1, n + 1))
    largest = float(sizes.max()) if n else 0.0
    single_share = largest / max(1.0, solid.sum())     # 1.0 = one blob (single figure)
    ys, xs = np.where(solid)
    bw, bh = xs.max() - xs.min(), ys.max() - ys.min()
    aspect = bw / max(1, bh)
    width_pen = max(0.0, aspect - 1.1)                 # penalise very wide (turnaround)
    return single_share - 0.6 * width_pen


def ensure_rig(name, char_dir=None) -> Path:
    """Return a ready rig dir for *name*, building it if absent.

    Generates a few candidates and AUTO-PICKS the cleanest single figure (rejects
    turnaround sheets / multi-figure), then cuts + rigs it. Idempotent: an existing
    rig.json is reused, so the cost is paid once per character.
    """
    d = Path(char_dir) if char_dir else (LIB / name)
    if (d / "rig.json").is_file():
        return d
    seeds = [7, 42, 101, 250] if _is_human(name) else [7, 88, 205]  # humans are pickier
    best, best_score = None, -1e9
    for s in seeds:
        cand = _generate_one(name, s)
        if not cand:
            continue
        try:
            sc = _score_candidate(cand)
        except Exception:  # noqa: BLE001
            sc = 0.0
        if sc > best_score:
            best, best_score = cand, sc
        if best_score >= 0.9:      # a clean single figure — stop early
            break
    if not best:
        raise RuntimeError(f"could not generate character '{name}'")
    cut(name, str(best))
    return LIB / name


if __name__ == "__main__":
    if sys.argv[1] == "generate":
        generate(sys.argv[2])
    elif sys.argv[1] == "cut":
        cut(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "ensure":
        print(ensure_rig(sys.argv[2]))
