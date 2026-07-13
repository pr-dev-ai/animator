#!/usr/bin/env python3
"""
Project scaffolding script for the AI animation pipeline.

Usage:
    python3 scripts/create_project.py --name my_kids_show --type kids
    python3 scripts/create_project.py --name my_story --type story
    python3 scripts/create_project.py --name custom_film            # defaults to story
"""

import argparse
import logging
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
KIDS_TEMPLATE_DIR = PROJECTS_DIR / "kids_template"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_VALID_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+')


def validate_project_name(name: str) -> None:
    """Raise SystemExit if *name* is not a valid directory name."""
    if not _VALID_NAME_RE.fullmatch(name):
        logger.error(
            "Invalid project name %r — only letters, numbers, underscores, "
            "and hyphens are allowed.",
            name,
        )
        sys.exit(1)


def check_project_does_not_exist(project_dir: Path) -> None:
    """Raise SystemExit if *project_dir* already exists."""
    if project_dir.exists():
        logger.error(
            "Project directory already exists: %s",
            project_dir,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Inline template content
# ---------------------------------------------------------------------------

def _kids_script_md(name: str) -> str:
    return f"""\
# {name.replace('_', ' ').replace('-', ' ').title()}

## Episode 1

[Write your story or song lyrics here]

### Scene 1
*Description of what happens in this scene.*

CHARACTER: "Example dialogue or song lyric."
"""


def _kids_shotlist_csv() -> str:
    return (
        "shot_id,description,camera,duration,notes\n"
        "SH010,Opening title card,Wide,3.0,Bright colours with title\n"
        "SH020,Main character is introduced,Medium,4.0,Happy expression\n"
        "SH030,Close-up of character reacting,Close-up,2.5,Big eyes surprise expression\n"
    )


def _kids_dialogue_csv() -> str:
    return (
        "shot_id,character,text,voice_id,language\n"
        "SH020,Hero,Hello everyone welcome to the show!,en_US-lessac-medium,en\n"
        "SH030,Hero,La la la let's go on an adventure,en_US-lessac-medium,en\n"
    )


def _kids_styleguide_md(name: str) -> str:
    return f"""\
# Visual Style Guide — {name.replace('_', ' ').replace('-', ' ').title()}

## Overall Aesthetic
- **Mood**: Bright, cheerful, imaginative
- **Color Palette**: Saturated primary colors, soft pastels
- **Lighting**: Bright and even, minimal harsh shadows

## Character Design
- **Hero**:
  - Round shapes, large expressive eyes
  - Colorful clothing
  - Simple, readable silhouette

## Environment
- **Main Setting**:
  - Simplified backgrounds, flat or slightly textured
  - Warm, inviting spaces

## Camera Language
- **Wide shots**: Establish world, invite the audience in
- **Close-ups**: Character emotion and reaction beats
- **Medium shots**: Action and interaction

## ComfyUI Prompt Template

### Base Template
```
[shot description], [camera angle], [character description],
kids animation style, bright colors, cartoon, cheerful mood,
simple clean backgrounds, cel-shaded, no text, no captions,
no logos, digital art
```

### Example (SH020)
```
Medium shot, colorful cartoon character waving hello,
bright cheerful background, kids animation style, big expressive eyes,
saturated colors, cel-shaded, no text, no captions, no logos, digital art
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
- **Lighting**: Bright 3-point setup, soft shadows
- **Materials**: Flat / toon-shaded, bold outlines
"""


def _kids_storyboards_md(name: str) -> str:
    return f"""\
# Storyboard Prompts — {name.replace('_', ' ').replace('-', ' ').title()}

## Style prefix (add to every prompt)
```
kids animation style, bright colors, cartoon, cheerful mood,
simple clean backgrounds, cel-shaded, no text, no captions,
no logos, digital art
```

## SH010 - Opening Title Card
```
Wide shot, colorful title card background, stars and balloons,
bright cheerful mood, kids animation style, saturated colors,
cel-shaded, no text, no captions, no logos, digital art
```

## SH020 - Character Introduction
```
Medium shot, colorful cartoon character waving hello,
bright cheerful background, kids animation style, big expressive eyes,
saturated colors, cel-shaded, no text, no captions, no logos, digital art
```

## SH030 - Character Reaction
```
Close-up, cartoon character with big surprised eyes,
bright lighting, cheerful indoor setting, kids animation style,
saturated colors, cel-shaded, no text, no captions, no logos, digital art
```

## [Add more shots here — one section per row in shotlist.csv]
"""


# ---------------------------------------------------------------------------
# Story (generic) templates
# ---------------------------------------------------------------------------

def _story_script_md(name: str) -> str:
    return f"""\
# {name.replace('_', ' ').replace('-', ' ').title()}

## Logline
[One sentence description of your story.]

## Characters
- **Protagonist**: [Description]

## Locations
- [Main location]

## Script

**SH010** - EXT. LOCATION - DAY/NIGHT
*[Opening scene description.]*

**SH020** - INT. LOCATION - DAY/NIGHT
*[Scene description.]*

PROTAGONIST: "[Example dialogue.]"

---

**Duration**: ~60 seconds
**Style**: [Describe the look and feel]
"""


def _story_shotlist_csv() -> str:
    return (
        "shot_id,description,camera,duration,notes\n"
        "SH010,Establishing shot of main location,Wide,3.0,Set the scene\n"
        "SH020,Protagonist introduced,Medium,4.0,Main character revealed\n"
        "SH030,Key moment close-up,Close-up,2.5,Emotional beat\n"
    )


def _story_dialogue_csv() -> str:
    return (
        "shot_id,character,text,voice_id,language\n"
        "SH020,Protagonist,Example dialogue line here.,en_US-lessac-medium,en\n"
    )


def _story_styleguide_md(name: str) -> str:
    return f"""\
# Visual Style Guide — {name.replace('_', ' ').replace('-', ' ').title()}

## Overall Aesthetic
- **Mood**: [Describe the mood]
- **Color Palette**: [Describe the palette]
- **Lighting**: [Describe the lighting approach]

## Character Design
- **Protagonist**:
  - [Physical description]
  - [Clothing description]

## Environment
- **Main Location**:
  - [Environment description]

## Camera Language
- **Wide shots**: Establish location and context
- **Close-ups**: Emotion and detail
- **Medium shots**: Character and action

## ComfyUI Prompt Template

### Base Template
```
[shot description], [camera angle], [lighting], [mood],
[location keywords], cinematic lighting, high quality,
no text, no captions, no logos, professional photography
```

### Example (SH020)
```
Medium shot, protagonist in main location,
natural lighting, [mood] mood, cinematic lighting,
high contrast, no text, no captions, no logos,
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
- **Lighting**: 3-point setup appropriate to mood
- **Materials**: Keep complexity low for fast renders
"""


def _story_storyboards_md(name: str) -> str:
    return f"""\
# Storyboard Prompts — {name.replace('_', ' ').replace('-', ' ').title()}

## Style prefix (add to every prompt)
```
[your style keywords here], cinematic lighting, high quality,
no text, no captions, no logos, professional photography, 4k
```

## SH010 - Establishing Shot
```
Wide shot, [location description],
[time of day] lighting, [mood] mood,
cinematic lighting, high quality,
no text, no captions, no logos, professional photography, 4k
```

## SH020 - Protagonist Introduction
```
Medium shot, [protagonist description] in [location],
[lighting description], [mood] mood,
cinematic lighting, high quality,
no text, no captions, no logos, professional photography, 4k
```

## SH030 - Key Moment
```
Close-up, [subject of close-up],
[lighting description], [emotional quality],
cinematic lighting, high quality,
no text, no captions, no logos, professional photography, 4k
```

## [Add more shots here — one section per row in shotlist.csv]
"""


# ---------------------------------------------------------------------------
# Template creation
# ---------------------------------------------------------------------------

def create_from_kids_template(project_dir: Path, name: str) -> None:
    """Copy kids_template dir and replace placeholder text, or use inline fallback."""
    if KIDS_TEMPLATE_DIR.exists():
        logger.info("Copying from kids_template: %s", KIDS_TEMPLATE_DIR)
        try:
            shutil.copytree(KIDS_TEMPLATE_DIR, project_dir)
            # Replace placeholder in all files
            _replace_placeholder(project_dir, "kids_template", name)
        except Exception as exc:
            logger.error("Failed to copy kids_template: %s", exc)
            _cleanup(project_dir)
            sys.exit(1)
    else:
        logger.info("kids_template not found — using inline fallback.")
        _create_kids_inline(project_dir, name)


def _create_kids_inline(project_dir: Path, name: str) -> None:
    """Write kids template files from inline content."""
    (project_dir / "prompts").mkdir(parents=True, exist_ok=True)
    _write(project_dir / "script.md", _kids_script_md(name))
    _write(project_dir / "shotlist.csv", _kids_shotlist_csv())
    _write(project_dir / "dialogue.csv", _kids_dialogue_csv())
    _write(project_dir / "styleguide.md", _kids_styleguide_md(name))
    _write(project_dir / "prompts" / "storyboards.md", _kids_storyboards_md(name))


def create_story_template(project_dir: Path, name: str) -> None:
    """Write generic story template files inline."""
    (project_dir / "prompts").mkdir(parents=True, exist_ok=True)
    _write(project_dir / "script.md", _story_script_md(name))
    _write(project_dir / "shotlist.csv", _story_shotlist_csv())
    _write(project_dir / "dialogue.csv", _story_dialogue_csv())
    _write(project_dir / "styleguide.md", _story_styleguide_md(name))
    _write(project_dir / "prompts" / "storyboards.md", _story_storyboards_md(name))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    logger.debug("Wrote: %s", path)


def _replace_placeholder(project_dir: Path, placeholder: str, replacement: str) -> None:
    """Recursively replace *placeholder* with *replacement* in all text files."""
    for path in project_dir.rglob("*"):
        try:
            if path.is_file():
                original = path.read_text(encoding="utf-8")
                updated = original.replace(placeholder, replacement)
                if updated != original:
                    path.write_text(updated, encoding="utf-8")
                    logger.debug("Updated placeholder in: %s", path)
        except (UnicodeDecodeError, PermissionError):
            # Skip binary or unreadable/unwritable files
            logger.warning("Skipping unwritable file: %s", path)


def _cleanup(project_dir: Path) -> None:
    """Remove a partially-created project directory."""
    if project_dir.exists():
        logger.warning("Cleaning up partial directory: %s", project_dir)
        shutil.rmtree(project_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Quickstart checklist
# ---------------------------------------------------------------------------

QUICKSTART_TEMPLATE = """\
Quickstart:
  1. Edit {rel}/script.md — write your story/lyrics
  2. Edit {rel}/shotlist.csv — define your shots
  3. Edit {rel}/dialogue.csv — add dialogue/song lines
  4. Edit {rel}/prompts/storyboards.md — add ComfyUI prompts
  5. Start services: ./app.sh start
  6. Generate storyboards in ComfyUI: http://localhost:8188
  7. Import your recorded audio: python3 scripts/import_audio.py --project {name} --list
  8. Generate lip-sync: python3 scripts/gen_lipsync.py --project {name}
  9. Create animatic: python3 scripts/make_dailies.py --project {name}
"""


def print_quickstart(name: str, project_dir: Path) -> None:
    rel = project_dir.relative_to(REPO_ROOT)
    print(f"\n✅ Created project: {rel}/\n")
    print(QUICKSTART_TEMPLATE.format(rel=rel, name=name))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new AI animation project from a template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/create_project.py --name my_kids_show --type kids\n"
            "  python3 scripts/create_project.py --name my_story --type story\n"
            "  python3 scripts/create_project.py --name custom_film\n"
        ),
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Project name (letters, numbers, underscores, hyphens only).",
    )
    parser.add_argument(
        "--type",
        choices=["kids", "story"],
        default="story",
        help="Template type: 'kids' or 'story' (default: story).",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    args = parse_args()
    name: str = args.name
    project_type: str = args.type

    # 1. Validate name
    validate_project_name(name)

    # 2. Check project does not already exist
    project_dir = PROJECTS_DIR / name
    check_project_does_not_exist(project_dir)

    # 3. Create project
    logger.info("Creating project '%s' (type=%s) at %s", name, project_type, project_dir)
    try:
        if project_type == "kids":
            create_from_kids_template(project_dir, name)
        else:
            try:
                project_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                logger.error("Project directory already exists: %s", project_dir)
                sys.exit(1)
            create_story_template(project_dir, name)
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Unexpected error during project creation: %s", exc)
        _cleanup(project_dir)
        sys.exit(1)

    # 4. Print quickstart
    print_quickstart(name, project_dir)


if __name__ == "__main__":
    main()
