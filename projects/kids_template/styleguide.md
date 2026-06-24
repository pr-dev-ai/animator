# Visual Style Guide - Kids Animation Template

## Overall Aesthetic
- **Style**: 2D cartoon, flat colors, bold black outlines, bright and cheerful
- **Mood**: Happy, playful, friendly, safe for all ages
- **Color Palette**: Primary colors (red, blue, yellow, green) plus pastels

## Character Design
- **Shape Language**: Simple rounded shapes throughout — no sharp edges
- **Eyes**: Large, expressive, clearly visible emotions
- **Faces**: Expressive, easy to read from a distance
- **Outlines**: Bold black outlines on all characters and objects

## Environment / Backgrounds
- **Complexity**: Simple, minimal detail — keep focus on characters
- **Colors**: Solid color gradients or flat fills, no photorealistic textures
- **Lighting**: Bright and even — no dramatic shadows or dark areas

## What to Avoid
- NO dark themes, shadows, or scary elements
- NO complex backgrounds with many distracting details
- NO realistic textures or photography-style lighting
- NO violence, frightening imagery, or adult content

## ComfyUI Prompt Template

### Base SD Keywords
```
cartoon, flat color, children's illustration, 2d, cute, bright, bold outlines, simple shapes, pastel colors
```

### Negative Prompt (use for all shots)
```
realistic, photo, dark, scary, complex background, watermark, text, logo
```

### Example Prompt (SH020 - singing character)
```
cartoon, flat color, children's illustration, 2d, cute, bright, bold outlines, simple shapes, pastel colors,
happy bunny character singing, open mouth smile, round body, large eyes, green meadow background, yellow sun in sky,
realistic, photo, dark, scary, complex background, watermark, text, logo
```

## Technical Notes
- **Resolution**: 512x512 (safe for 4GB VRAM RTX 3050)
- **Aspect Ratio**: 16:9 for final output (640x360 or 1280x720)
- **Model**: Stable Diffusion 1.5 (VRAM-safe for RTX 3050 4GB)
- **Steps**: 20-30
- **Sampler**: DPM++ 2M Karras or Euler a
- **CFG Scale**: 7-9 (higher values reinforce cartoon style)

## Animation Notes
- **Frame Rate**: 24 fps
- **Transitions**: Bright wipes or simple cuts — avoid dark fades mid-scene
- **Character Motion**: Bouncy, exaggerated, expressive — kids love big movements
