# Visual Style Guide - The Night Shift

## Overall Aesthetic
- **Mood**: Tense, isolated, surveillance
- **Color Palette**: Cool blues, harsh monitor whites, deep shadows
- **Lighting**: Practical (monitor glow), high contrast

## Character Design
- **Operator**: 
  - Mid-30s, tired but alert
  - Simple clothing: dark shirt, practical
  - Facial features: readable but not overly detailed

## Environment
- **Control Room**:
  - Multiple monitors (3-5 visible)
  - Console desk with buttons/switches
  - Minimal props
  - Dark walls, monitor glow as primary light source

## Camera Language
- **Wide shots**: Establish isolation
- **Close-ups**: Build tension, focus on reactions
- **Medium shots**: Show operator's relationship to the space

## ComfyUI Prompt Template

### Base Template
```
[shot description], [camera angle], [lighting], [mood], 
control room, security monitors, surveillance aesthetic, 
cinematic lighting, high contrast, cool color palette, 
no text, no captions, no logos, professional photography
```

### Example (SH020)
```
Medium shot, security operator at console, multiple monitors, 
dim monitor glow lighting, tense mood, control room, 
surveillance aesthetic, cinematic lighting, high contrast, 
cool blue color palette, no text, no captions, no logos, 
professional photography, 4k
```

## Technical Notes
- **Resolution**: 512x512 for storyboards (SD 1.5)
- **Aspect Ratio**: 16:9 for final (640x360 or 1280x720)
- **Model**: Stable Diffusion 1.5 (VRAM-safe for RTX 3050 4GB)
- **Steps**: 20-30
- **Sampler**: DPM++ 2M Karras or Euler a

## Blender Notes
- **Render Engine**: Eevee (fast iteration)
- **Frame Rate**: 24 fps
- **Lighting**: 3-point setup with practical monitor lights
- **Materials**: Simple, readable, low complexity
