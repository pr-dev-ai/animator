#!/usr/bin/env python3
"""Render a layer-animation JSON spec to a video, headless via Blender.

This is the production home of the Blender "2.5D paper cutout" renderer (the
spike lives on in spike_blender/ for reference).  It runs in TWO modes from the
one file:

  * CLI mode (ordinary Python, no bpy): locate Blender, drive it headless,
    encode the rendered frames to MP4 with ffmpeg, optionally mux an audio bed.
        python scripts/blender_render.py --spec shot.json --out shot.mp4
        python scripts/blender_render.py --spec shot.json --out shot.mp4 \
               --audio song.wav --audio-start 24.0

  * Executor mode (inside Blender's bundled Python, bpy importable): build the
    scene from the spec, render PNG frames, and run a silent-failure guard.
    Blender invokes this file itself:
        blender --background --factory-startup --python blender_render.py -- \
                --render --spec shot.json --frames <dir> --engine cpu

Why one file two modes: the executor needs bpy (only available inside Blender)
and the CLI needs ffmpeg + subprocess (only sensible outside).  Keeping them
together means the schema, the coordinate convention and the parenting rules
have exactly one authoritative implementation.

CPU rendering
-------------
The default engine is Cycles on the CPU.  A sibling process owns the GPU, and
the art is flat emission (no lighting / GI), so Cycles at one sample is exact
*and* costs the GPU nothing.  `--engine eevee` is available but uses the GPU
rasteriser; prefer the default unless you know the GPU is free.

See scripts/blender_render_schema.md for the full spec, including the parenting
semantics.
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

try:
    import bpy  # only importable inside Blender
    IN_BLENDER = True
except ImportError:
    bpy = None
    IN_BLENDER = False


def log(msg):
    """Print a progress line and flush it (the SSE contract used across scripts/)."""
    print(f"[blender_render] {msg}", flush=True)


# ===========================================================================
# EXECUTOR MODE  (runs inside Blender; needs bpy)
# ===========================================================================

# --- version-drift shims ---------------------------------------------------

def pick_eevee():
    """Return the EEVEE engine id this build actually exposes.

    5.x offers 'BLENDER_EEVEE'; 4.2-4.5 offered 'BLENDER_EEVEE_NEXT'.  Reading
    the enum beats hardcoding either name.
    """
    items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    for candidate in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        if candidate in items:
            return candidate
    raise RuntimeError(f"No EEVEE engine found; available: {list(items)}")


def set_alpha_blend(mat):
    """Enable alpha blending across the 4.x/5.x material API split."""
    if hasattr(mat, "surface_render_method"):      # 4.2+ / 5.x
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):             # <= 4.1
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False


def configure_engine(scene, engine):
    """Set up the render engine for a flat-emission cutout scene.

    'cpu'   -> Cycles on the CPU at one sample (exact for flat emission, and it
               never touches the contended GPU).
    'eevee' -> EEVEE-Next (GPU rasteriser); faster but wants the GPU.
    """
    engine = (engine or "cpu").lower()
    if engine in ("cpu", "cycles"):
        scene.render.engine = "CYCLES"
        cy = getattr(scene, "cycles", None)
        if cy is not None:
            cy.device = "CPU"
            cy.samples = 1                     # flat emission: 1 sample is exact
            cy.use_adaptive_sampling = False
            if hasattr(cy, "use_denoising"):
                cy.use_denoising = False
        return "CYCLES/CPU"
    if engine == "eevee":
        scene.render.engine = pick_eevee()
        return scene.render.engine
    raise RuntimeError(f"unknown engine '{engine}' (use 'cpu' or 'eevee')")


# --- coordinate helpers ----------------------------------------------------

EASING = {
    "linear": ("LINEAR", None),
    "ease_in": ("SINE", "EASE_IN"),
    "ease_out": ("SINE", "EASE_OUT"),
    "ease_in_out": ("SINE", "EASE_IN_OUT"),
    "constant": ("CONSTANT", None),
}


def canvas_to_blender(x, y, W, H):
    """Canvas pixel (y down, origin top-left) -> Blender XY (y up, origin centre)."""
    return (x - W / 2.0, H / 2.0 - y)


def offset_to_blender(dx, dy):
    """Canvas-pixel OFFSET (y down) -> Blender XY offset (y up).

    Used for parented layers, whose `pos` is an offset from the parent anchor
    rather than an absolute canvas position, so there is no W/2,H/2 centring --
    only the y axis flips.
    """
    return (float(dx), -float(dy))


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _as_scale(scale):
    if isinstance(scale, (int, float)):
        return [float(scale), float(scale)]
    return [float(scale[0]), float(scale[1])]


def make_layer_plane(layer, W, H, spec_dir, z):
    """Build one textured, alpha-blended plane whose origin sits on its anchor."""
    name = layer["name"]
    img_path = layer["image"]
    if not os.path.isabs(img_path):
        img_path = os.path.join(spec_dir, img_path)
    if not os.path.exists(img_path):
        raise FileNotFoundError(f"layer '{name}': image not found: {img_path}")

    img = bpy.data.images.load(img_path)
    iw, ih = img.size
    if iw == 0 or ih == 0:
        raise RuntimeError(f"layer '{name}': image has zero size: {img_path}")

    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name

    # Plane is 1x1 centred; stretch to the image's pixel size.
    for v in obj.data.vertices:
        v.co.x *= iw
        v.co.y *= ih

    # Move the mesh so the anchor pixel sits exactly on the object origin, so
    # rotation and scale pivot about the anchor for free (no empties/constraints).
    # For a wheel with anchor = geometric centre this makes the spin wobble-free.
    ax, ay = layer.get("anchor", [iw / 2.0, ih / 2.0])
    off_x = iw / 2.0 - ax     # anchor measured from image left, y down
    off_y = ay - ih / 2.0
    for v in obj.data.vertices:
        v.co.x += off_x
        v.co.y += off_y

    mat = bpy.data.materials.new(f"M_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.extension = "CLIP"
    img.alpha_mode = "STRAIGHT"

    emit = nt.nodes.new("ShaderNodeEmission")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")

    # Emission + Transparent + MixShader (not Principled): these node/socket
    # names have been stable for many releases, and this composites the same in
    # both Cycles and EEVEE, so the CPU/GPU choice never changes the picture.
    nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    nt.links.new(transp.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emit.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    set_alpha_blend(mat)
    obj.data.materials.append(mat)

    obj.location.z = z
    return obj, (iw, ih)


def setup_parenting(layers, objs, abs_z):
    """Wire up `parent` relationships between built layer objects.

    Semantics (documented in spec_schema.md):
      * A child's object origin is parented to its parent's object origin (=
        the parent's anchor).  matrix_parent_inverse is forced to identity so
        the child's local transform is measured directly against the parent's
        anchor -- no hidden offset baked in at parent time.
      * The child's `pos` is therefore an OFFSET (canvas px, y-down) from the
        parent anchor, applied in the parent's *un-rotated* local frame; the
        parent's own rotation/scale then carry the child along.
      * `z` stays an ABSOLUTE paint order: we set the child's local z to
        (child_z - parent_z) so its world z equals the authored z regardless of
        where the parent sits.

    Validates that every named parent exists and that there are no cycles.
    """
    import mathutils

    names = {l["name"] for l in layers}
    parent_of = {l["name"]: l.get("parent") for l in layers}

    for name, p in parent_of.items():
        if p is None:
            continue
        if p not in names:
            raise RuntimeError(f"layer '{name}': parent '{p}' not found")
        if p == name:
            raise RuntimeError(f"layer '{name}': cannot be its own parent")
        # Walk the chain to the root, detecting cycles.
        seen, cur = {name}, p
        while cur is not None:
            if cur in seen:
                raise RuntimeError(f"parenting cycle involving layer '{name}'")
            seen.add(cur)
            cur = parent_of.get(cur)

    for l in layers:
        p = l.get("parent")
        if not p:
            continue
        child, parent = objs[l["name"]], objs[p]
        child.parent = parent
        child.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        child.location.z = abs_z[l["name"]] - abs_z[p]
        log(f"parented '{l['name']}' -> '{p}'")


def _stamp_easing(holder, stamped):
    """Apply per-keyframe interpolation flags to an object's fcurves.

    Must run AFTER every key exists: inserting a later key would otherwise
    inherit/overwrite flags already set on earlier ones.
    """
    if holder.animation_data and holder.animation_data.action:
        for fc in _fcurves(holder.animation_data.action):
            for kp in fc.keyframe_points:
                ease = next((e for f, e in stamped if f == round(kp.co.x)), "linear")
                interp, mode = EASING.get(ease, EASING["linear"])
                kp.interpolation = interp
                if mode:
                    kp.easing = mode


def apply_keyframes(obj, keyframes, W, H, fps, default_pos, parented=False):
    """Key location/rotation/scale, then stamp per-segment interpolation.

    `parented` switches `pos` from an absolute canvas position to an offset from
    the parent anchor (see setup_parenting).
    """
    prev = {"pos": list(default_pos), "rot": 0.0, "scale": [1.0, 1.0]}
    stamped = []

    for kf in sorted(keyframes, key=lambda k: k["t"]):
        frame = round(kf["t"] * fps) + 1

        pos = kf.get("pos", prev["pos"])
        rot = kf.get("rot", prev["rot"])
        scale = _as_scale(kf.get("scale", prev["scale"]))

        if parented:
            bx, by = offset_to_blender(pos[0], pos[1])
        else:
            bx, by = canvas_to_blender(pos[0], pos[1], W, H)
        obj.location.x = bx
        obj.location.y = by
        # Canvas rot is CLOCKWISE positive (a wheel rolling right); Blender's Z
        # rotation is counter-clockwise positive, hence the negation.  For a
        # parented child this Z rotation is in parent-local space, so the wheel
        # spins about its own anchor AND inherits the parent's rotation.
        obj.rotation_euler.z = -math.radians(rot)
        obj.scale.x = scale[0]
        obj.scale.y = scale[1]

        obj.keyframe_insert("location", frame=frame)
        obj.keyframe_insert("rotation_euler", frame=frame)
        obj.keyframe_insert("scale", frame=frame)

        stamped.append((frame, kf.get("easing", "linear")))
        prev = {"pos": pos, "rot": rot, "scale": scale}

    _stamp_easing(obj, stamped)
    return len(stamped)


def _fcurves(action):
    """Yield an action's fcurves on both the 4.4+ slotted and legacy layouts."""
    yielded = False
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                for cbag in getattr(strip, "channelbags", []) or []:
                    for fc in cbag.fcurves:
                        yielded = True
                        yield fc
    if not yielded:
        for fc in getattr(action, "fcurves", []):
            yield fc


def setup_camera(spec, W, H, fps):
    bpy.ops.object.camera_add(location=(0, 0, 100), rotation=(0, 0, 0))
    cam = bpy.context.active_object
    cam.name = "Camera"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = W
    bpy.context.scene.camera = cam

    cam_spec = spec.get("camera")
    if not cam_spec or not cam_spec.get("keyframes"):
        return cam

    prev = {"pos": [W / 2.0, H / 2.0], "zoom": 1.0}
    stamped = []
    for kf in sorted(cam_spec["keyframes"], key=lambda k: k["t"]):
        frame = round(kf["t"] * fps) + 1
        pos = kf.get("pos", prev["pos"])
        zoom = kf.get("zoom", prev["zoom"])
        bx, by = canvas_to_blender(pos[0], pos[1], W, H)
        cam.location.x = bx
        cam.location.y = by
        cam.data.ortho_scale = W / float(zoom)
        cam.keyframe_insert("location", frame=frame)
        cam.data.keyframe_insert("ortho_scale", frame=frame)
        stamped.append((frame, kf.get("easing", "linear")))
        prev = {"pos": pos, "zoom": zoom}

    _stamp_easing(cam, stamped)
    _stamp_easing(cam.data, stamped)
    return cam


def build(spec, spec_dir, engine):
    fps = int(spec.get("fps", 24))
    W, H = spec.get("resolution", [1152, 768])
    duration = float(spec["duration"])

    clear_scene()
    scene = bpy.context.scene
    engine_label = configure_engine(scene, engine)
    scene.render.resolution_x = W
    scene.render.resolution_y = H
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start = 1
    # +1 so the keyframe at t=duration (mapped to round(duration*fps)+1) lands
    # inside the rendered range instead of one frame past the end.
    scene.frame_end = max(1, round(duration * fps) + 1)
    scene.render.film_transparent = False
    # Flat 2D art is authored in sRGB; the filmic/AgX view transform would wash
    # it out.  'Standard' plays the pixels back as painted.
    try:
        scene.view_settings.view_transform = "Standard"
    except TypeError:
        pass

    layers = spec["layers"]
    if not layers:
        raise RuntimeError("spec has no layers")

    objs, abs_z, sizes = {}, {}, {}
    for i, layer in enumerate(layers):
        name = layer["name"]
        if name in objs:
            raise RuntimeError(f"duplicate layer name '{name}'")
        z = float(layer.get("z", i))
        obj, (iw, ih) = make_layer_plane(layer, W, H, spec_dir, z)
        objs[name], abs_z[name], sizes[name] = obj, z, (iw, ih)

    setup_parenting(layers, objs, abs_z)

    for layer in layers:
        name = layer["name"]
        obj = objs[name]
        parented = bool(layer.get("parent"))
        if parented:
            default_pos = [0.0, 0.0]            # sit on the parent anchor
        else:
            iw, ih = sizes[name]
            default_pos = layer.get("anchor", [iw / 2.0, ih / 2.0])
        n = apply_keyframes(obj, layer["keyframes"], W, H, fps, default_pos, parented)
        pinfo = f" parent={layer['parent']}" if parented else ""
        log(f"layer '{name}' {sizes[name][0]}x{sizes[name][1]} z={abs_z[name]} "
            f"keys={n}{pinfo}")

    setup_camera(spec, W, H, fps)
    log(f"scene: {W}x{H} @{fps}fps frames 1..{scene.frame_end} engine={engine_label}")
    return scene


# --- silent-failure guard --------------------------------------------------

def spec_has_motion(spec):
    """True if the spec authors any change over time (so frames should differ).

    A static child on a MOVING parent still counts: the parent's own keyframes
    change, so this returns True and the guard rightly expects motion.
    """
    def poses(keyframes):
        kfs = sorted(keyframes, key=lambda k: k["t"])
        if not kfs:
            return set()
        # Seed the inherited baseline from the FIRST keyframe's own values, so an
        # omitted channel on the first key cannot masquerade as a change (which
        # would wrongly make the guard demand motion from a static shot).
        first = kfs[0]
        prev = {
            "pos": tuple(first.get("pos", (0.0, 0.0))),
            "rot": float(first.get("rot", 0.0)),
            "scale": tuple(_as_scale(first.get("scale", 1.0))),
        }
        seen = set()
        for kf in kfs:
            pos = tuple(kf.get("pos", prev["pos"]))
            rot = float(kf.get("rot", prev["rot"]))
            scale = tuple(_as_scale(kf.get("scale", prev["scale"])))
            seen.add((pos, rot, scale))
            prev = {"pos": pos, "rot": rot, "scale": scale}
        return seen

    for layer in spec.get("layers", []):
        if len(poses(layer.get("keyframes", []))) > 1:
            return True
    cam = spec.get("camera") or {}
    cam_seen = set()
    prev = {"pos": (0.0, 0.0), "zoom": 1.0}
    for kf in sorted(cam.get("keyframes", []), key=lambda k: k["t"]):
        pos = tuple(kf.get("pos", prev["pos"]))
        zoom = float(kf.get("zoom", prev["zoom"]))
        cam_seen.add((pos, zoom))
        prev = {"pos": pos, "zoom": zoom}
    return len(cam_seen) > 1


def sanity_check_frames(frames_dir, expect_motion):
    """Fail loudly if the render is silently empty or (wrongly) static.

    Blender exits 0 even when it renders pure black or throws inside a handler,
    so this reads the frames back and asserts:
      (a) no sampled frame is a single flat colour (black/empty), and
      (b) there IS inter-frame change when the spec authored motion.
    Raises RuntimeError on failure; the caller turns that into a non-zero exit.
    """
    import numpy as np

    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if len(frames) < 1:
        raise RuntimeError("guard: no frames were written")

    # Sample a spread of frames rather than all of them.
    n = len(frames)
    idxs = sorted({0, n // 5, n // 2, (4 * n) // 5, n - 1})
    samples = []
    for i in idxs:
        img = bpy.data.images.load(str(frames[i]), check_existing=False)
        buf = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(buf)
        bpy.data.images.remove(img)
        rgb = buf.reshape(-1, 4)[:, :3]
        samples.append((i, rgb))

    # (a) flatness: a single-colour frame (black or otherwise) has ~zero spread.
    FLAT_EPS = 0.01
    for i, rgb in samples:
        spread = float(rgb.max() - rgb.min())
        if spread < FLAT_EPS:
            raise RuntimeError(
                f"guard: frame {i + 1} is a single flat colour "
                f"(spread={spread:.4f}) -- render is empty/black")

    # (b) motion: when the spec moves, consecutive samples must differ somewhere.
    if expect_motion and len(samples) >= 2:
        MOTION_EPS = 1e-3
        diffs = [float(np.abs(samples[k][1] - samples[k - 1][1]).mean())
                 for k in range(1, len(samples))]
        if max(diffs) < MOTION_EPS:
            raise RuntimeError(
                f"guard: spec authors motion but frames are static "
                f"(max inter-frame diff={max(diffs):.2e})")
        log(f"guard: motion ok (max inter-frame diff={max(diffs):.4f})")

    log(f"guard: {n} frames, all non-empty"
        f"{' and moving' if expect_motion else ''} -- OK")


def executor_main(argv):
    """Entry point when Blender runs this file.  Any failure -> non-zero exit."""
    try:
        p = argparse.ArgumentParser(prog="blender_render(executor)")
        p.add_argument("--render", action="store_true")
        p.add_argument("--spec", required=True)
        p.add_argument("--frames", required=True)
        p.add_argument("--engine", default="cpu")
        p.add_argument("--no-guard", action="store_true")
        args = p.parse_args(argv)

        with open(args.spec, "r", encoding="utf-8") as f:
            spec = json.load(f)
        spec_dir = os.path.dirname(os.path.abspath(args.spec))

        scene = build(spec, spec_dir, args.engine)

        frames_dir = os.path.abspath(args.frames)
        os.makedirs(frames_dir, exist_ok=True)
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = os.path.join(frames_dir, "frame_")
        bpy.ops.render.render(animation=True)
        log(f"rendered frames -> {frames_dir}")

        if not args.no_guard:
            sanity_check_frames(frames_dir, spec_has_motion(spec))
        log("DONE (executor)")
        sys.exit(0)
    except SystemExit:
        raise
    except BaseException as exc:
        # Blender exits 0 on an uncaught exception, which is the exact silent
        # failure this module exists to prevent -- so convert it into a loud,
        # non-zero exit that the CLI can see.
        import traceback
        traceback.print_exc()
        log(f"FATAL: {type(exc).__name__}: {exc}")
        sys.exit(2)


# ===========================================================================
# CLI MODE  (ordinary Python; needs ffmpeg + subprocess)
# ===========================================================================

def resolve_blender():
    """Find the Blender executable, mirroring make_dailies' ffmpeg resolution.

    A long-running server may have started before Blender was on PATH, so PATH
    alone is not enough; fall back to the standard Windows install dirs.
    """
    import shutil
    found = shutil.which("blender")
    if found:
        return found

    def _ver_key(p):
        # Order 'Blender 5.10' above 'Blender 5.2' numerically, not lexically.
        import re
        return [int(n) for n in re.findall(r"\d+", p.parent.name)]

    candidates = []
    for base in (Path("C:/Program Files/Blender Foundation"),
                 Path("C:/Program Files (x86)/Blender Foundation")):
        if base.is_dir():
            candidates.extend(sorted(base.glob("Blender */blender.exe"),
                                     key=_ver_key, reverse=True))
    local = Path.home() / "AppData" / "Local"
    candidates.append(local / "Microsoft" / "WinGet" / "Links" / "blender.exe")
    pkgs = local / "Microsoft" / "WinGet" / "Packages"
    if pkgs.is_dir():
        candidates.extend(sorted(pkgs.glob("BlenderFoundation.Blender*/**/blender.exe"),
                                 reverse=True))
    candidates.append(Path("/usr/local/bin/blender"))
    candidates.append(Path("/usr/bin/blender"))

    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def resolve_ffmpeg():
    """Reuse make_dailies' tool resolver so both scripts find ffmpeg the same way."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_dailies import _resolve_tool
    return _resolve_tool("ffmpeg")


def run_blender_headless(blender, spec_path, frames_dir, engine, no_guard):
    """Drive Blender headless, streaming its output line-by-line (SSE contract)."""
    import subprocess
    cmd = [
        blender, "--background", "--factory-startup",
        "--python", os.path.abspath(__file__), "--",
        "--render", "--spec", str(spec_path), "--frames", str(frames_dir),
        "--engine", engine,
    ]
    if no_guard:
        cmd.append("--no-guard")
    log(f"launching Blender: {Path(blender).name} (engine={engine})")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    proc.wait()
    return proc.returncode


def _frame_pattern(frames_dir):
    """Return (pattern, start_number) for the rendered PNG sequence."""
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"no frames were rendered in {frames_dir}")
    first = frames[0].stem.split("_")[-1]
    width = len(first)
    return str(Path(frames_dir) / f"frame_%0{width}d.png"), int(first)


def encode_video(ffmpeg, frames_dir, fps, out_path, audio=None, audio_start=0.0):
    """Encode the PNG frames to H.264 MP4, optionally muxing an audio bed.

    `audio_start` is the offset (seconds) into the audio at which this shot
    begins, i.e. audio time = video time + audio_start.  The audio is seeked to
    that point; the VIDEO length is authoritative -- the audio is silence-padded
    (apad) so that a shot landing near the end of the song is not truncated.
    """
    import subprocess
    pattern, start = _frame_pattern(frames_dir)
    # yuv420p needs even dimensions; guard against odd-sized specs.
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=neighbor"

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-framerate", str(fps), "-start_number", str(start), "-i", pattern]
    if audio:
        # Seek the audio input to audio_start so the shot lines up with the song.
        cmd += ["-ss", f"{max(0.0, float(audio_start)):.5f}", "-i", str(audio)]
    cmd += ["-vf", even,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p"]
    if audio:
        # apad makes the audio effectively infinite; -shortest then bounds the
        # output to the finite VIDEO stream, so short remaining audio pads with
        # silence rather than cutting the animation off.
        cmd += ["-af", "apad", "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-movflags", "+faststart", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"ffmpeg failed:\n{result.stderr[-2000:]}")
        raise RuntimeError("video encode failed")


def cli_main():
    import shutil
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(
        description="Render a layer-animation JSON spec to MP4 via Blender.")
    parser.add_argument("--spec", required=True, help="path to the spec JSON")
    parser.add_argument("--out", required=True, help="output MP4 path")
    parser.add_argument("--engine", default="cpu", choices=["cpu", "eevee"],
                        help="cpu = Cycles/CPU (default, GPU-free); eevee = GPU raster")
    parser.add_argument("--audio", help="optional audio bed to mux (wav/mp3/...)")
    parser.add_argument("--audio-start", type=float, default=0.0,
                        help="seconds into the audio at which this shot begins")
    parser.add_argument("--frames-dir", help="keep rendered PNGs here (default: temp)")
    parser.add_argument("--no-guard", action="store_true",
                        help="skip the silent-failure guard (not recommended)")
    args = parser.parse_args()

    spec_path = Path(args.spec).resolve()
    if not spec_path.is_file():
        log(f"spec not found: {spec_path}")
        sys.exit(1)
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    fps = int(spec.get("fps", 24))

    blender = resolve_blender()
    if not blender:
        log("Blender not found. Install it (winget install BlenderFoundation.Blender) "
            "or put 'blender' on PATH.")
        sys.exit(1)

    if args.audio and not Path(args.audio).is_file():
        log(f"audio not found: {args.audio}")
        sys.exit(1)

    ffmpeg = resolve_ffmpeg()
    try:
        subprocess.run([ffmpeg, "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        log("ffmpeg not found. Install it (winget install Gyan.FFmpeg) and retry.")
        sys.exit(1)

    keep = bool(args.frames_dir)
    frames_dir = Path(args.frames_dir).resolve() if keep else Path(tempfile.mkdtemp(prefix="blender_frames_"))
    frames_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale frames from any previous render into a reused --frames-dir: a
    # shorter new render would otherwise leave higher-numbered old frames behind
    # for ffmpeg's %04d glob to splice onto the end of the new video.
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()

    try:
        rc = run_blender_headless(blender, spec_path, frames_dir, args.engine, args.no_guard)
        if rc != 0:
            # A non-zero code here means the render errored OR the guard caught a
            # silent black/empty/static render.  Do NOT produce an MP4: the whole
            # point of the guard is that a broken render never ships.
            log(f"Blender exited {rc} -- render failed or the silent-failure guard "
                f"tripped (see the log above). No video written.")
            sys.exit(rc)

        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"encoding -> {out_path}")
        encode_video(ffmpeg, frames_dir, fps, out_path,
                     audio=args.audio, audio_start=args.audio_start)
        log(f"DONE {out_path}")
    finally:
        if not keep:
            shutil.rmtree(frames_dir, ignore_errors=True)


# ===========================================================================

if __name__ == "__main__":
    if IN_BLENDER and "--" in sys.argv:
        executor_main(sys.argv[sys.argv.index("--") + 1:])
    else:
        cli_main()
