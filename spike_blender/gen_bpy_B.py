"""Approach B: ask Claude to write a complete raw bpy script, run it headless, retry.

This is the fair test the user asked for. Claude is told:
  * the EXACT Blender version (5.2.0),
  * the exact asset paths and sizes,
  * where to write frames,
and gets a validation/retry loop: if the script raises, the traceback is fed back
and Claude tries again, up to MAX_RETRIES.

Crucially we then check for SILENT failure -- a script that exits 0 but renders an
empty or static scene -- by diffing the rendered frames. exit 0 is NOT success here.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "web_ui"))

from anthropic import Anthropic
from dotenv import load_dotenv

from check_frames import verdict

load_dotenv(HERE.parent / ".env")
MODEL = "claude-haiku-4-5-20251001"
BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
MAX_RETRIES = 5
FRAMES_DIR = HERE / "frames_B"
ASSETS = HERE / "assets"

SYSTEM = "You are an expert Blender technical artist who writes correct, headless bpy scripts."

TASK = f"""Write a COMPLETE Python script using bpy that renders a "Wheels on the Bus" shot.

EXACT ENVIRONMENT (do not guess):
- Blender 5.2.0 LTS. Run as: blender --background --python yourscript.py
- Target this version's API precisely. Note the bpy API has changed across recent
  releases, so names you remember from older Blender may be wrong here.
- Python is bundled; numpy is available; PIL is NOT.

ASSETS (PNG, already on disk, use these absolute paths):
- Background (opaque, 1152x768):  {ASSETS / 'bg.png'}
- Bus cutout (RGBA, 1057x677):    {ASSETS / 'bus.png'}
- Wheel cutout (RGBA, 236x236):   {ASSETS / 'wheel.png'}

SHOT (5 seconds, 24 fps, 1152x768):
- Orthographic camera looking down -Z at flat textured planes (a 2.5D paper-cutout look).
- bg fills the frame, static, furthest from camera.
- The bus drives left-to-right across the frame over the 5 seconds and bobs up and down
  gently, landing a bob on every beat. Tempo is 120 BPM, so a beat every 0.5 seconds.
- A wheel plane spins (rotates, several full turns) and stays on top of the bus's front
  wheel as the bus moves.
- Scale the bus to roughly half size so it fits the frame.

OUTPUT:
- Render the animation as a PNG sequence to this directory (create it if needed):
  {FRAMES_DIR}
- Use filepath ending in "frame_" so frames are frame_0001.png ... frame_0120.png.
- Set scene.frame_start=1 and scene.frame_end=120 and render with bpy.ops.render.render(animation=True).
- Use view_settings.view_transform = 'Standard' so the flat art is not colour-graded.

IMPORTANT:
- Start from an empty scene: bpy.ops.wm.read_factory_settings(use_empty=True).
- Load each image with bpy.data.images.load(<absolute path>).
- Use image-texture nodes so the PNGs actually appear (bg colour, bus/wheel with alpha).
- The wheel and bus must MOVE via keyframes (keyframe_insert), not stay static.

Output ONLY the Python script. No markdown fences, no commentary."""


def strip_fences(t):
    t = t.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3].rstrip()
    return t


def ask_claude(client, messages):
    resp = client.messages.create(model=MODEL, max_tokens=4096, system=SYSTEM, messages=messages)
    return next(b.text for b in resp.content if hasattr(b, "text"))


def run_script(script_path):
    """Run the generated script headless; return (returncode, combined_output)."""
    proc = subprocess.run(
        [BLENDER, "--background", "--python", str(script_path)],
        capture_output=True, text=True, timeout=600,
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def main():
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    FRAMES_DIR.mkdir(exist_ok=True)
    # clear any stale frames so we judge THIS run
    for f in FRAMES_DIR.glob("*.png"):
        f.unlink()

    messages = [{"role": "user", "content": TASK}]
    script_path = HERE / "claude_bpy_B.py"

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n===== ATTEMPT {attempt}/{MAX_RETRIES} =====")
        raw = ask_claude(client, messages)
        code = strip_fences(raw)
        script_path.write_text(code, encoding="utf-8")
        print(f"  Claude wrote {len(code)} chars of bpy")

        # Clear frames BEFORE each attempt so a crash cannot inherit the previous
        # attempt's frames and masquerade as a (black) success.
        for f in FRAMES_DIR.glob("frame_*.png"):
            f.unlink()

        rc, out = run_script(script_path)
        n_frames = len(list(FRAMES_DIR.glob("frame_*.png")))
        crashed = "Traceback (most recent call last)" in out
        print(f"  exit={rc}  frames_written={n_frames}  crashed={crashed}")

        # Surface the tail of Blender's output for the log.
        tail = "\n".join(out.strip().splitlines()[-8:])
        print("  --- blender tail ---")
        for line in tail.splitlines():
            print("    " + line)

        # exit 0 + frames is NOT success: an uncaught exception still exits 0 here,
        # and even a clean exit can render pure black. Only trust the PIXELS.
        v = None
        if not crashed and n_frames >= 120:
            v = verdict(FRAMES_DIR)
            print(f"  pixel check: {v}")
            if v["ok"]:
                print(f"  RESULT: real moving render on attempt {attempt} "
                      f"(brightness={v['brightness']}, motion={v['motion']})")
                return 0

        # Build feedback: crash traceback OR the silent-failure verdict.
        err = out.strip().splitlines()
        err_tail = "\n".join(err[-25:])
        if v is not None and not v["ok"]:
            feedback = (
                f"The script ran without crashing and wrote {n_frames} frames, but the "
                f"RENDER IS WRONG: {v['reason']} (mean brightness={v['brightness']} on a "
                f"0-255 scale, inter-frame motion={v['motion']}). Diagnose why the textured "
                "planes are not visibly rendering and moving (candidate causes include "
                "lighting, the shader type, camera framing/clipping, or the render engine), "
                "fix it, and output the COMPLETE corrected script only."
            )
        else:
            feedback = (
                f"That script failed. Exit code {rc}, {n_frames} frames written "
                f"(need 120). Here is the end of Blender's output:\n\n{err_tail}\n\n"
                "Fix the script and output the COMPLETE corrected script only."
            )
        messages = [
            {"role": "user", "content": TASK},
            {"role": "assistant", "content": code},
            {"role": "user", "content": feedback},
        ]

    print(f"\nRESULT: FAILED after {MAX_RETRIES} attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
