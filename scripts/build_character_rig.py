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


def _prompt(name):
    return (
        "cartoon, flat color, children's illustration, 2d, bold clean outlines, simple shapes, cute, "
        f"a single adorable chubby baby {name}, clear side view profile facing left, round body, "
        "a distinct visible mouth, two tiny feet, one big friendly eye, happy, "
        "plain solid white background, no scenery, centered, full body"
    )


NEG = ("front view, three-quarter, 3/4 view, back view, human, humanoid, person, arms, hands, "
       "multiple characters, two animals, scenery, background, realistic, photo, dark, cropped")


def generate(name):
    sys.path.insert(0, r"C:\pradeep\animator\web_ui")
    import pipeline_api as P
    ckpt = P._find_checkpoint()
    out = LIB / name
    out.mkdir(parents=True, exist_ok=True)
    for seed in [7, 21, 88, 130, 205, 302]:
        wf = P._comfyui_workflow(_prompt(name), NEG, f"char_{name}_{seed}", ckpt)
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


if __name__ == "__main__":
    if sys.argv[1] == "generate":
        generate(sys.argv[2])
    elif sys.argv[1] == "cut":
        cut(sys.argv[2], sys.argv[3])
