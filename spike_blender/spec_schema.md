# Layer animation spec (v1)

A shot is a stack of flat textured planes ("2.5D paper cutouts") animated in front of an
orthographic camera. All positions are in **canvas pixel coordinates**: origin top-left,
x right, y **down** (i.e. ordinary image coordinates). The executor converts to Blender
units; the spec author never touches Blender.

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
      "anchor": [576, 384],         // pixel in the IMAGE that the pivot sits on.
                                    // rotation and scale happen about this point.
      "keyframes": [
        // "pos" = where the anchor lands on the canvas, in canvas pixels.
        // "rot" = degrees, clockwise positive (screen-space, like a wheel rolling right).
        // "scale" = 1.0 is native size.
        // "easing" applies to the segment LEAVING this keyframe.
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
| `duration` | yes | seconds; frame count = round(duration*fps) |
| `resolution` | no ([1152,768]) | output pixels |
| `layers[].name` | yes | unique; used for object naming + logging |
| `layers[].image` | yes | PNG. RGBA gets alpha-blended; RGB is opaque. |
| `layers[].z` | no (index) | paint order, higher = in front |
| `layers[].anchor` | no (image centre) | pivot, in image pixels |
| `layers[].keyframes` | yes, >=1 | see below |

## Keyframes

`t` seconds. `pos` `[x,y]` canvas px. `rot` degrees clockwise. `scale` float or `[sx,sy]`.

A single keyframe = a static layer. Omitted channels inherit the previous keyframe's value
(and default to pos=anchor's natural home, rot=0, scale=1 at the first keyframe).

## Easing

Applies to the interval *after* the keyframe it is written on.

- `linear` (default)
- `ease_in` — slow start (Blender SINE/EASE_IN)
- `ease_out` — slow stop
- `ease_in_out` — slow both ends
- `constant` — hold, then jump (step). Use for snappy on-beat pops.

## Camera

Optional. Same coordinate convention; `pos` is the canvas point the camera centres on,
`zoom` 1.0 = fit canvas width.

```jsonc
"camera": {
  "keyframes": [
    {"t": 0.0, "pos": [576, 384], "zoom": 1.0, "easing": "ease_in_out"},
    {"t": 5.0, "pos": [700, 384], "zoom": 1.2}
  ]
}
```
