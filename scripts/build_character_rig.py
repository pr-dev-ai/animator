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


_MALE_WORDS = {"boy", "brother", "man", "father", "dad", "grandfather", "grandpa",
               "uncle", "son", "guy", "bhaiya", "papa"}
_FEMALE_WORDS = {"girl", "sister", "woman", "mother", "mom", "mum", "grandmother",
                 "grandma", "aunt", "daughter", "lady", "bahan", "didi", "mummy"}


def _is_human(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in _HUMAN_WORDS)


def _gender(name: str):
    """'male' / 'female' / None from the character name (None = leave unforced)."""
    n = (name or "").lower()
    if any(w in n for w in _MALE_WORDS):
        return "male"
    if any(w in n for w in _FEMALE_WORDS):
        return "female"
    return None


def _prompt(name, culture=None):
    if _is_human(name):
        # culture is set per-project (e.g. "Indian" for Hindi songs). We add the
        # cultural cue + traditional clothing but do NOT force a skin tone — Indians
        # (and everyone) span a wide range, so let SD vary it naturally. Without a
        # culture, stay neutral (no ethnicity forced) and just avoid the anime default.
        who = f"{culture} {name}" if culture else name
        gender = _gender(name)
        # Clothing carries a strong gender signal: 'traditional Indian clothes'
        # alone renders female (a lehenga/saree), which is why 'big brother' used to
        # come out a girl. Pick attire by gender so boys read as boys.
        if culture == "Indian":
            if gender == "male":
                details = "black hair, wearing a colourful kurta pajama, "
            elif gender == "female":
                details = "black hair, wearing a colourful traditional Indian lehenga dress, "
            else:
                details = "black hair, wearing colourful traditional Indian clothes, "
        else:
            details = ""
        return (
            "cartoon, flat color, children's storybook illustration, 2d, bold clean outlines, "
            f"simple shapes, cute, ONE single solo cartoon {who}, {details}"
            "big friendly eyes, alone, a single pose, full body, standing, facing forward, "
            "a distinct visible mouth, happy, plain solid white background, "
            "no scenery, centered"
        )
    return (
        "cartoon, flat color, children's illustration, 2d, bold clean outlines, simple shapes, cute, "
        f"a single adorable chubby baby {name}, clear side view profile facing left, round body, "
        "a distinct visible mouth, two tiny feet, one big friendly eye, happy, "
        "plain solid white background, no scenery, centered, full body"
    )


def _neg(name):
    # flat2DAnimerge IS an anime-based flat model, so we no longer negate "anime"
    # (that fought the very style we want). Instead kill its bad habits: companion
    # creatures, paint-splatter backdrops, and extreme chibi.
    base = ("back view, multiple characters, two figures, scenery, background, realistic, "
            "photo, dark, cropped, extra limbs, extra character, companion, mascot, "
            "creature, pet, ball, extra object, paint splatter, paint splash, "
            "messy background, colored background, brush strokes, sketchy lines, "
            "swirls, ribbons, banners, flames, aura, glow, magic effects, floating petals, "
            "watermark, text")
    if _is_human(name):
        neg = (base + ", character sheet, reference sheet, model sheet, turnaround, "
               "multiple views, multiple poses, grid, duplicate, chibi, super deformed, "
               "japanese, pale skin, white skin, blonde hair, blue eyes")
        gender = _gender(name)
        if gender == "male":      # keep a 'brother'/'boy' from drifting female
            neg += ", girl, woman, dress, saree, lehenga, skirt, female"
        elif gender == "female":
            neg += ", beard, moustache, man, male"
        return neg
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


def _mouth_region(cut_rgba, bbox):
    """Locate the mouth box on the character's FACE, whichever way it faces.

    The old heuristic hard-coded the front of the head to the LEFT, so a
    right-facing figure got its lip-sync region in empty space. Instead we find the
    face by skin colour inside the head band (skin is warm: R>=G>=B with a clear
    R-B margin, which excludes black hair where R≈G≈B) and put the mouth in the
    lower-centre of that face box — no assumption about facing direction. Falls back
    to a centred lower-head ellipse (still orientation-agnostic) if skin isn't found.
    """
    import numpy as np
    from scipy import ndimage
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0, y1 - y0
    arr = np.asarray(cut_rgba).astype(int)
    R, G, B, A = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    # 1) work on the MAIN body blob only, so detached checkpoint artifacts (stray
    #    shapes the model paints beside the character) can't hijack the face search.
    solid = A > 40
    lbl, n = ndimage.label(solid)
    if n > 1:
        sizes = ndimage.sum(solid, lbl, range(1, n + 1))
        solid = lbl == (int(np.argmax(sizes)) + 1)
    mys = np.where(solid.any(axis=1))[0]
    by0, by1 = int(mys.min()), int(mys.max())
    body_h = max(1, by1 - by0)
    # 2) find the face = skin blob in the head band nearest the body's central axis
    #    (not the topmost — a stray orange artifact beside the head reads as skin
    #    and often sits higher, but the real face straddles the body centreline).
    skin = (A > 60) & (R >= G - 4) & (G >= B - 4) & (R - B > 10) & (R < 250) & solid
    band = skin.copy()
    band[by0 + int(body_h * 0.30):, :] = False
    head_solid = solid.copy(); head_solid[by0 + int(body_h * 0.30):, :] = False
    hxs0 = np.where(head_solid.any(axis=0))[0]
    axis_x = float(np.median(hxs0)) if hxs0.size else (x0 + x1) / 2
    fl, fn = ndimage.label(band)
    if fn:
        def _axis_dist(c):
            cxs = np.where((fl == c).any(axis=0))[0]
            return abs(float(np.median(cxs)) - axis_x)
        face = fl == min(range(1, fn + 1), key=_axis_dist)
        fys, fxs = np.where(face)
        if fxs.size >= 40:
            fy0, fy1f = int(fys.min()), int(fys.max())
            mcy = int(fy0 + (fy1f - fy0) * 0.80)           # mouth = lower face
            lvl = face[max(by0, mcy - 5):mcy + 6, :]
            xs_lvl = np.where(lvl.any(axis=0))[0]
            mcx = int(np.median(xs_lvl)) if xs_lvl.size else int(np.median(fxs))
            fw = int(fxs.max() - fxs.min()) or int(bw * 0.4)
            mw = max(10, int(fw * 0.8))
            mh = max(8, int((fy1f - fy0) * 0.28))
            return mcx - mw // 2, mcy - mh // 2, mcx + mw // 2, mcy + mh // 2
    # 3) fallback: front-lower of the head silhouette, side chosen by where skin sits.
    head_y1 = by0 + int(body_h * 0.28)
    hmask = solid.copy(); hmask[head_y1:, :] = False
    hxs = np.where(hmask.any(axis=0))[0]
    hx0, hx1 = (int(hxs.min()), int(hxs.max())) if hxs.size else (x0, x1)
    hw = max(1, hx1 - hx0)
    sk = skin & hmask
    faces_left = sk[:, hx0:hx0 + hw // 3].sum() >= sk[:, hx1 - hw // 3:hx1 + 1].sum()
    mcx = hx0 + int(hw * 0.25) if faces_left else hx1 - int(hw * 0.25)
    mcy = by0 + int((head_y1 - by0) * 0.72)
    mw, mh = max(12, int(hw * 0.4)), max(10, int((head_y1 - by0) * 0.22))
    return mcx - mw // 2, mcy - mh // 2, mcx + mw // 2, mcy + mh // 2


def _largest_component(rgba):
    """Keep only the largest connected opaque blob (the character), zeroing the
    alpha of floating decorations the flat model likes to scatter around."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    arr = np.asarray(rgba).copy()
    solid = arr[:, :, 3] > 40
    lbl, n = ndimage.label(solid)
    if n <= 1:
        return rgba
    sizes = ndimage.sum(solid, lbl, range(1, n + 1))
    keep = int(np.argmax(sizes)) + 1
    arr[:, :, 3] = np.where(lbl == keep, arr[:, :, 3], 0)
    return Image.fromarray(arr, "RGBA")


def cut(name, candidate, out=None):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    from rembg import remove, new_session

    out = Path(out) if out else (LIB / name)
    out.mkdir(parents=True, exist_ok=True)
    img = Image.open(candidate).convert("RGB")
    W, H = img.size
    # crop out any left-edge UI junk the checkpoint sometimes adds
    img = img.crop((int(W * 0.30), 0, W, H))
    cw, ch = img.size

    cut_rgba = remove(img, session=new_session("isnet-anime"))
    # the flat model already draws flat colour + bold outlines (cartoonify would only
    # muddy it), but it likes to add floating decorations (swirls/ribbons); keep just
    # the main connected figure so those drop out.
    cut_rgba = _largest_component(cut_rgba)
    cut_rgba.save(out / "body.png")
    alpha = np.asarray(cut_rgba)[:, :, 3]
    ys, xs = np.where(alpha > 30)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    bw, bh = x1 - x0, y1 - y0
    body_anchor = ((x0 + x1) // 2, (y0 + y1) // 2)

    # mouth region on the face, orientation-agnostic (see _mouth_region).
    mx0, my0, mx1, my1 = _mouth_region(cut_rgba, (x0, y0, x1, y1))
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


def _generate_one(name, seed, culture=None, out=None):
    """Generate a single isolated-on-white candidate; return its path."""
    sys.path.insert(0, r"C:\pradeep\animator\web_ui")
    import pipeline_api as P
    ckpt = P._find_checkpoint()
    out = Path(out) if out else (LIB / name)
    out.mkdir(parents=True, exist_ok=True)
    wf = P._comfyui_workflow(_prompt(name, culture), _neg(name), f"char_{name}_{seed}", ckpt)
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
    # a clean standing/side figure is TALLER than wide (aspect ~0.4-0.75); wings,
    # turnarounds and multi-figure blobs are wide (aspect >~0.9) — penalise hard.
    width_pen = max(0.0, aspect - 0.78)
    return single_share - 2.2 * width_pen


def _rig_dir(name, culture=None) -> Path:
    """Library dir for a character, namespaced by culture so a Hindi project's
    Indian 'girl' and an English project's neutral 'girl' don't collide."""
    slug = f"{name}__{culture.lower()}" if culture else name
    return LIB / slug


def ensure_rig(name, culture=None, char_dir=None) -> Path:
    """Return a ready rig dir for *name*, building it if absent.

    *culture* (e.g. "Indian" for Hindi projects) steers human characters and
    namespaces the library dir. Generates a few candidates and AUTO-PICKS the
    cleanest single figure (rejects turnaround sheets / multi-figure), then cuts +
    rigs it. Idempotent: an existing rig.json is reused, so cost is paid once.
    """
    d = Path(char_dir) if char_dir else _rig_dir(name, culture)
    if (d / "rig.json").is_file():
        return d
    seeds = [7, 42, 101, 250] if _is_human(name) else [7, 88, 205]  # humans are pickier
    best, best_score = None, -1e9
    for s in seeds:
        cand = _generate_one(name, s, culture=culture, out=d)
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
    cut(name, str(best), out=d)
    return d


if __name__ == "__main__":
    if sys.argv[1] == "generate":
        generate(sys.argv[2])
    elif sys.argv[1] == "cut":
        cut(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "ensure":
        culture = sys.argv[3] if len(sys.argv) > 3 else None
        print(ensure_rig(sys.argv[2], culture=culture))
