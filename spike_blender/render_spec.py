"""Execute a layer-animation JSON spec in Blender (see spec_schema.md).

Run headless:
    blender --background --python render_spec.py -- <spec.json> <output_dir>

Design notes
------------
Deliberately confined to the oldest, most stable corners of bpy: primitive_plane_add,
image texture nodes, location/rotation_euler/scale, keyframe_insert, and an orthographic
camera. Those have barely moved since 2.8, which is the whole point -- this module is the
thing that absorbs Blender API churn so that generated content (the JSON) never has to.

Coordinate mapping: 1 Blender unit = 1 canvas pixel. The camera is orthographic at
+Z looking down -Z, ortho_scale = canvas width. Canvas pixel (x, y) with y DOWN maps to
Blender (x - W/2, H/2 - y). Layers sit at increasing Z toward the camera.

Two version-sensitive spots are handled defensively, because they are exactly where
Blender 4.x -> 5.x drifted:
  * render engine enum: 5.x calls EEVEE-Next "BLENDER_EEVEE"; 4.2-4.5 called it
    "BLENDER_EEVEE_NEXT". We pick whichever the running build actually offers.
  * alpha blending: 4.2+ replaced Material.blend_method with surface_render_method.
"""

import json
import math
import os
import sys

import bpy


# --- version-drift shims ----------------------------------------------------

def pick_eevee():
    """Return the EEVEE engine id this build actually exposes.

    5.2 offers ('BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES'); 4.2-4.5 offered
    'BLENDER_EEVEE_NEXT' instead. Reading the enum beats hardcoding either name.
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


# --- helpers ----------------------------------------------------------------

EASING = {
    "linear": ("LINEAR", None),
    "ease_in": ("SINE", "EASE_IN"),
    "ease_out": ("SINE", "EASE_OUT"),
    "ease_in_out": ("SINE", "EASE_IN_OUT"),
    "constant": ("CONSTANT", None),
}


def log(msg):
    print(f"[render_spec] {msg}", flush=True)


def canvas_to_blender(x, y, W, H):
    """Canvas pixel (y down, origin top-left) -> Blender XY (y up, origin centre)."""
    return (x - W / 2.0, H / 2.0 - y)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


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

    # Move the mesh so the anchor pixel sits exactly on the object origin. Rotation
    # and scale then pivot about the anchor for free, with no empties or constraints.
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
    # Flat cutout art: no filtering halo, no colour management surprises.
    tex.interpolation = "Linear"
    tex.extension = "CLIP"
    img.alpha_mode = "STRAIGHT"

    emit = nt.nodes.new("ShaderNodeEmission")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")

    # Emission + Transparent + MixShader is used instead of Principled on purpose:
    # these node types and their socket names have been stable for many releases,
    # whereas Principled's sockets were renamed in 4.0 ("Emission" -> "Emission Color").
    nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    nt.links.new(transp.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emit.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    set_alpha_blend(mat)
    obj.data.materials.append(mat)

    obj.location.z = z
    return obj, (iw, ih)


def apply_keyframes(obj, keyframes, W, H, fps, default_pos):
    """Key location/rotation/scale, then stamp per-segment interpolation."""
    prev = {"pos": default_pos, "rot": 0.0, "scale": [1.0, 1.0]}
    stamped = []

    for kf in sorted(keyframes, key=lambda k: k["t"]):
        frame = round(kf["t"] * fps) + 1

        pos = kf.get("pos", prev["pos"])
        rot = kf.get("rot", prev["rot"])
        scale = kf.get("scale", prev["scale"])
        if isinstance(scale, (int, float)):
            scale = [float(scale), float(scale)]

        bx, by = canvas_to_blender(pos[0], pos[1], W, H)
        obj.location.x = bx
        obj.location.y = by
        # Canvas rot is CLOCKWISE positive (a wheel rolling right); Blender's Z
        # rotation is counter-clockwise positive, hence the negation.
        obj.rotation_euler.z = -math.radians(rot)
        obj.scale.x = scale[0]
        obj.scale.y = scale[1]

        obj.keyframe_insert("location", frame=frame)
        obj.keyframe_insert("rotation_euler", frame=frame)
        obj.keyframe_insert("scale", frame=frame)

        stamped.append((frame, kf.get("easing", "linear")))
        prev = {"pos": pos, "rot": rot, "scale": scale}

    # Interpolation must be set AFTER all keys exist: inserting a later key would
    # otherwise inherit/overwrite the flags we just set on the earlier one.
    if obj.animation_data and obj.animation_data.action:
        for fc in _fcurves(obj.animation_data.action):
            for kp in fc.keyframe_points:
                ease = next((e for f, e in stamped if f == round(kp.co.x)), "linear")
                interp, mode = EASING.get(ease, EASING["linear"])
                kp.interpolation = interp
                if mode:
                    kp.easing = mode
    return len(stamped)


def _fcurves(action):
    """Yield an action's fcurves on both the 4.4+ slotted and legacy layouts.

    Blender 4.4 moved fcurves behind action.layers[].strips[].channelbag(slot);
    action.fcurves still exists but is empty for slotted actions. Try the new
    location first and fall back, so this works either side of the change.
    """
    yielded = False
    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            for strip in layer.strips:
                for cbag in getattr(strip, "channelbags", []) or []:
                    for fc in cbag.fcurves:
                        yielded = True
                        yield fc
    # Fall back to the legacy flat list if the slotted layout yielded nothing.
    # An early return in the slotted branch would silently skip easing on any
    # build where `channelbags` is absent/empty, leaving default interpolation.
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

    for holder in (cam, cam.data):
        if holder.animation_data and holder.animation_data.action:
            for fc in _fcurves(holder.animation_data.action):
                for kp in fc.keyframe_points:
                    ease = next((e for f, e in stamped if f == round(kp.co.x)), "linear")
                    interp, mode = EASING.get(ease, EASING["linear"])
                    kp.interpolation = interp
                    if mode:
                        kp.easing = mode
    return cam


def build(spec, spec_dir):
    fps = int(spec.get("fps", 24))
    W, H = spec.get("resolution", [1152, 768])
    duration = float(spec["duration"])

    clear_scene()
    scene = bpy.context.scene
    scene.render.engine = pick_eevee()
    scene.render.resolution_x = W
    scene.render.resolution_y = H
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start = 1
    # +1 so the keyframe at t=duration (which apply_keyframes maps to
    # round(duration*fps)+1) is actually inside the rendered range; without it the
    # final authored pose lands one frame past frame_end and never renders.
    scene.frame_end = max(1, round(duration * fps) + 1)
    scene.render.film_transparent = False
    # Flat 2D art is authored in sRGB; the filmic/AgX view transform would wash it
    # out. 'Standard' plays the pixels back as painted.
    try:
        scene.view_settings.view_transform = "Standard"
    except TypeError:
        pass

    layers = spec["layers"]
    for i, layer in enumerate(layers):
        z = float(layer.get("z", i))
        obj, (iw, ih) = make_layer_plane(layer, W, H, spec_dir, z)
        default_pos = layer.get("anchor", [iw / 2.0, ih / 2.0])
        n = apply_keyframes(obj, layer["keyframes"], W, H, fps, default_pos)
        log(f"layer '{layer['name']}' {iw}x{ih} z={z} keys={n}")

    setup_camera(spec, W, H, fps)
    log(f"scene: {W}x{H} @{fps}fps frames 1..{scene.frame_end} engine={scene.render.engine}")
    return scene


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    spec_path, out_dir = argv[0], argv[1]

    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    spec_dir = os.path.dirname(os.path.abspath(spec_path))

    scene = build(spec, spec_dir)
    os.makedirs(out_dir, exist_ok=True)
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = os.path.join(out_dir, "frame_")
    bpy.ops.render.render(animation=True)
    log(f"DONE wrote frames to {out_dir}")


if __name__ == "__main__":
    main()
