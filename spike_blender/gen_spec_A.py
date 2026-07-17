"""Approach A: ask Claude for a JSON keyframe SPEC (not code), then render it.

Claude never touches bpy or the Blender API. It only fills in the small, stable
schema in spec_schema.md, timed to the song's 120 BPM beat grid. The hand-written
render_spec.py is the only thing that talks to Blender.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "web_ui"))

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(HERE.parent / ".env")
MODEL = "claude-haiku-4-5-20251001"

SCHEMA = (HERE / "spec_schema.md").read_text(encoding="utf-8")

# The shot + its musical timing. 120 BPM -> 0.5 s/beat, 2 s/bar. This is exactly
# what lyric_sync.py / arrangement.json provide for a real project.
SHOT = {
    "description": (
        "A yellow school bus drives across a sunny park from left to right while its "
        "front wheel spins. The bus bobs up and down gently, landing a bob on every beat."
    ),
    "bpm": 120,
    "beat_seconds": 0.5,
    "duration": 5.0,
    "fps": 24,
    "resolution": [1152, 768],
    "layers_available": [
        {"name": "bg", "image": "assets/bg.png", "size": [1152, 768],
         "note": "sunny park plate, fills the frame, static"},
        {"name": "bus", "image": "assets/bus.png", "size": [1057, 677],
         "note": "school bus cutout, drive it across; scale ~0.5 fits nicely; "
                 "its front wheel sits at image pixel (476, 486)"},
        {"name": "wheel", "image": "assets/wheel.png", "size": [236, 236],
         "note": "a single round wheel to overlay on the bus front wheel and spin; "
                 "anchor its centre (118,118); keep it moving locked to the bus front "
                 "wheel; scale ~0.5 to match the bus"},
    ],
}

PROMPT = f"""You are animating one shot of a "Wheels on the Bus" kids video by emitting a JSON spec.

Here is the spec schema you must produce (do NOT write any Blender/Python code, ONLY the JSON spec):

{SCHEMA}

Shot to animate:
{json.dumps(SHOT, indent=2)}

Requirements:
- Use all three layers (bg, bus, wheel) in paint order bg < bus < wheel.
- The bus enters from the left edge and exits toward the right over the {SHOT['duration']}s.
- Add a gentle vertical bob that lands on every beat ({SHOT['beat_seconds']}s apart) using ease_in_out.
- The wheel must SPIN (increasing rotation) and stay locked on top of the bus front wheel as the bus moves. Roughly 3-6 full turns over the shot.
- Keep everything on-screen (resolution {SHOT['resolution']}).

Output ONLY the JSON spec object, no prose, no markdown fences."""


def strip_fences(t):
    t = t.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3].rstrip()
    return t


def main():
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": PROMPT}],
    )
    text = next(b.text for b in resp.content if hasattr(b, "text"))
    spec = json.loads(strip_fences(text))
    out = HERE / "claude_spec_A.json"
    out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"Claude produced a valid spec: {len(spec.get('layers', []))} layers, "
          f"{sum(len(l.get('keyframes', [])) for l in spec.get('layers', []))} keyframes total")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
