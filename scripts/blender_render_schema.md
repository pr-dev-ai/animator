# Layer animation spec (v2)

Executed by `scripts/blender_render.py`.  A shot is a stack of flat textured
planes ("2.5D paper cutouts") animated in front of an orthographic camera. All
positions are in **canvas pixel coordinates**: origin top-left, x right, y
**down** (ordinary image coordinates). The executor converts to Blender units;
the spec author never touches Blender.

> v2 adds **layer parenting** (§ Parenting) so a wheel can stay mounted on a bus
> and spin independently. Everything from v1 is unchanged and still valid.

```jsonc
{
  "fps": 24,
  "duration": 5.0,                  // seconds
  "resolution": [1152, 768],
  "layers": [
    {
      "name": "bg",
      "image": "bg.png",            // path, absolute or relative to the spec file
      "z": 0,                       // paint order; higher = nearer camera. bg = 0.
      "anchor": [576, 384],         // pixel in the IMAGE that the pivot sits on;
                                    // rotation and scale happen about this point.
      "keyframes": [
        // "pos"    = where the anchor lands on the canvas, in canvas pixels.
        // "rot"    = degrees, clockwise positive (like a wheel rolling right).
        // "scale"  = 1.0 is native size; float or [sx, sy].
        // "easing" = applies to the segment LEAVING this keyframe.
        {"t": 0.0, "pos": [576, 384], "rot": 0,   "scale": 1.0, "easing": "linear"},
        {"t": 5.0, "pos": [576, 384], "rot": 360, "scale": 1.0}
      ]
    }
  ]
}
```

## Fields

| field | required | notes |
|---|---|---|
| `fps` | no (24) | frames per second |
| `duration` | yes | seconds; last rendered frame = round(duration*fps)+1 |
| `resolution` | no ([1152,768]) | output pixels |
| `layers[].name` | yes | unique; used for object naming, parenting, logging |
| `layers[].image` | yes | PNG. RGBA gets alpha-blended; RGB is opaque. |
| `layers[].z` | no (index) | ABSOLUTE paint order, higher = in front (holds even when parented) |
| `layers[].anchor` | no (image centre) | pivot, in image pixels |
| `layers[].parent` | no | name of another layer; see § Parenting |
| `layers[].keyframes` | yes, >=1 | see § Keyframes |

## Keyframes

`t` seconds. `pos` `[x,y]`. `rot` degrees clockwise. `scale` float or `[sx,sy]`.

A single keyframe = a static layer. Omitted channels inherit the previous
keyframe's value (and default to pos = the layer's natural home, rot = 0,
scale = 1 at the first keyframe).

## Parenting  (v2)

Add `"parent": "<layer name>"` to make a layer a **child** of another. The child
rides along with the parent's position, rotation and scale, while keeping its own
independent motion on top. This is what makes "wheels on the bus" work: the bus
drives across and its wheels ride along, each wheel also spinning.

**Exact semantics** — read carefully, this is the fiddly part to author:

- **`pos` becomes an OFFSET, not an absolute position.** For a parented layer,
  `pos` is the offset of the child's anchor from the **parent's anchor**, in
  canvas pixels (same x-right / y-down convention), measured in the parent's
  *un-rotated, unscaled* local frame. `pos: [0, 0]` puts the child's anchor
  exactly on the parent's anchor. `pos: [140, 130]` puts it 140 px right and
  130 px below the parent anchor **at rest**; the parent's own rotation/scale
  then carry that offset around.
- **`rot` is the child's OWN spin**, about its own anchor, composed on top of any
  rotation inherited from the parent. A wheel with `anchor` at its geometric
  centre spins cleanly in place (no wobble).
- **`scale`** multiplies the parent's scale.
- **`z` stays an absolute paint order.** A child with `z: 3` renders at depth 3
  regardless of the parent's `z`; you do not offset it yourself.
- **Chains are allowed** (a child may parent another child). Cycles and unknown
  parent names are rejected with a clear error.

The offset is measured against the parent's **rest pose** (its transform at
`pos`/`rot`/`scale` = the first keyframe). Animating a child's `pos` moves it
relative to the parent, in the parent's local frame.

```jsonc
// A bus that drives left->right with two wheels mounted under it, each spinning
// 3 full turns. The wheels' pos never changes (constant offset) yet they ride
// along, because the parent bus is what translates.
{
  "name": "bus",  "z": 2, "anchor": [260, 120], "image": "bus.png",
  "keyframes": [
    {"t": 0.0, "pos": [300, 470], "easing": "ease_in_out"},
    {"t": 4.0, "pos": [860, 470]}
  ]
},
{
  "name": "wheel_front", "z": 3, "anchor": [80, 80], "image": "wheel.png",
  "parent": "bus",
  "keyframes": [
    {"t": 0.0, "pos": [140, 130], "rot": 0},
    {"t": 4.0, "pos": [140, 130], "rot": 1080}
  ]
}
```

## Easing

Applies to the interval *after* the keyframe it is written on.

- `linear` (default)
- `ease_in` — slow start
- `ease_out` — slow stop
- `ease_in_out` — slow both ends
- `constant` — hold, then jump (step). Use for snappy on-beat pops.

## Camera

Optional. Same coordinate convention; `pos` is the canvas point the camera
centres on, `zoom` 1.0 = fit canvas width.

```jsonc
"camera": {
  "keyframes": [
    {"t": 0.0, "pos": [576, 384], "zoom": 1.0, "easing": "ease_in_out"},
    {"t": 5.0, "pos": [700, 384], "zoom": 1.2}
  ]
}
```

## Rendering & the silent-failure guard

`scripts/blender_render.py` renders headless and, by default, on **CPU** (Cycles,
one sample — exact for flat emission art and it never touches the GPU). Pass
`--engine eevee` to use the GPU rasteriser instead.

After rendering, the module runs a **silent-failure guard**: it samples frames
and fails loudly (non-zero exit, no MP4 written) if the render is a single flat
colour (black/empty) or if the spec authored motion but the frames do not move.
Blender exits 0 even on an uncaught exception or an all-black render, so this
guard is what stops a silently-broken shot from ever shipping.

## On-beat authoring

`scripts/beat_timing.py` exposes a project's musical beat grid so a spec's
keyframes can land on the beat. For a shot occupying song-seconds `[start, end]`,
`accents_in_window(project, start, end)` returns the beat times **relative to the
shot start** — drop keyframes on those `t` values for on-beat motion.
```
