"""Silent-failure detector: is a PNG frame sequence non-empty AND moving?

Returns a verdict used both as a standalone check and by the B harness.
  - brightness: mean luma of the mid frame. ~0 => empty/black render.
  - motion: mean abs pixel diff between two spread-apart frames. ~0 => static.
"""
import sys
from pathlib import Path
import numpy as np
from PIL import Image


def verdict(frames_dir):
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if len(frames) < 2:
        return {"ok": False, "reason": f"only {len(frames)} frames", "brightness": 0, "motion": 0}
    a = np.asarray(Image.open(frames[len(frames) // 2]).convert("RGB"), dtype=np.float64)
    b = np.asarray(Image.open(frames[len(frames) // 5]).convert("RGB"), dtype=np.float64)
    c = np.asarray(Image.open(frames[4 * len(frames) // 5]).convert("RGB"), dtype=np.float64)
    brightness = float(a.mean())
    motion = float(np.abs(b - c).mean())
    ok = brightness > 5.0 and motion > 0.5
    reason = "ok" if ok else (
        "render is essentially BLACK/EMPTY" if brightness <= 5.0 else "render is STATIC (no motion)"
    )
    return {"ok": ok, "reason": reason, "n": len(frames),
            "brightness": round(brightness, 3), "motion": round(motion, 3)}


if __name__ == "__main__":
    v = verdict(sys.argv[1])
    print(v)
    sys.exit(0 if v["ok"] else 2)
