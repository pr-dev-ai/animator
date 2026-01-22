#!/usr/bin/env python3
"""
Assemble storyboard frames + audio into an animatic MP4 using FFmpeg.
"""

import argparse
import csv
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
OUTPUTS_DIR = REPO_ROOT / "outputs"
VOICES_DIR = REPO_ROOT / "voices"


def check_ffmpeg():
    """Check if FFmpeg is available."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("FFmpeg not found. Install with: sudo apt install ffmpeg")
        return False


def get_shot_duration(shot_id: str, shotlist_csv: Path) -> float:
    """Get duration for a shot from shotlist.csv (in seconds)."""
    if not shotlist_csv.exists():
        return 3.0  # Default duration

    with open(shotlist_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("shot_id", "").strip() == shot_id:
                duration_str = row.get("duration", "3.0").strip()
                try:
                    return float(duration_str)
                except ValueError:
                    return 3.0
    return 3.0  # Default if not found


def create_animatic(project_name: str, output_path: Path):
    """Create animatic from storyboard images and audio."""
    project_dir = PROJECTS_DIR / project_name
    shotlist_csv = project_dir / "shotlist.csv"
    dialogue_csv = project_dir / "dialogue.csv"
    prompts_dir = project_dir / "prompts"
    voices_dir = VOICES_DIR / project_name

    if not shotlist_csv.exists():
        logger.error(f"shotlist.csv not found: {shotlist_csv}")
        sys.exit(1)

    # Read shotlist to get shot order
    shots = []
    with open(shotlist_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            shot_id = row.get("shot_id", "").strip()
            if shot_id:
                shots.append(shot_id)

    if not shots:
        logger.error("No shots found in shotlist.csv")
        sys.exit(1)

    # Create temporary directory for FFmpeg input files
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        concat_file = Path(temp_dir) / "concat.txt"
        
        with open(concat_file, "w") as f:
            for shot_id in shots:
                # Look for storyboard image (try common locations/extensions)
                storyboard_path = None
                for ext in [".png", ".jpg", ".jpeg"]:
                    candidate = OUTPUTS_DIR / f"{project_name}_storyboards" / f"{shot_id}{ext}"
                    if candidate.exists():
                        storyboard_path = candidate
                        break
                
                if not storyboard_path:
                    logger.warning(f"Storyboard not found for {shot_id}, using placeholder")
                    # Create a simple placeholder (black frame)
                    placeholder = Path(temp_dir) / f"{shot_id}.png"
                    subprocess.run([
                        "ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=3",
                        "-frames:v", "1", "-y", str(placeholder)
                    ], capture_output=True)
                    storyboard_path = placeholder

                duration = get_shot_duration(shot_id, shotlist_csv)
                
                # Check for dialogue audio
                audio_path = None
                if dialogue_csv.exists():
                    with open(dialogue_csv, "r", encoding="utf-8") as df:
                        d_reader = csv.DictReader(df)
                        for d_row in d_reader:
                            if d_row.get("shot_id", "").strip() == shot_id:
                                character = d_row.get("character", "").strip()
                                audio_candidate = voices_dir / f"{shot_id}_{character}.wav"
                                if audio_candidate.exists():
                                    audio_path = audio_candidate
                                    break

                # Write concat entry
                f.write(f"file '{storyboard_path}'\n")
                f.write(f"duration {duration}\n")
                
                if audio_path:
                    logger.info(f"Shot {shot_id}: image + audio ({duration}s)")
                else:
                    logger.info(f"Shot {shot_id}: image only ({duration}s)")

            # Repeat last frame duration for concat
            f.write(f"file '{storyboard_path}'\n")

        # Build FFmpeg command
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "24",  # 24 fps
            "-y",  # Overwrite output
            str(output_path)
        ]

        # Add audio if available (mix all dialogue files)
        audio_files = []
        for shot_id in shots:
            if dialogue_csv.exists():
                with open(dialogue_csv, "r", encoding="utf-8") as df:
                    d_reader = csv.DictReader(df)
                    for d_row in d_reader:
                        if d_row.get("shot_id", "").strip() == shot_id:
                            character = d_row.get("character", "").strip()
                            audio_candidate = voices_dir / f"{shot_id}_{character}.wav"
                            if audio_candidate.exists():
                                audio_files.append(str(audio_candidate))

        if audio_files:
            # Create audio concat file
            audio_concat = Path(temp_dir) / "audio_concat.txt"
            with open(audio_concat, "w") as f:
                for audio_file in audio_files:
                    f.write(f"file '{audio_file}'\n")
            
            cmd.extend(["-f", "concat", "-safe", "0", "-i", str(audio_concat)])
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        logger.info(f"Creating animatic: {output_path}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"✅ Animatic created: {output_path}")
        else:
            logger.error(f"❌ FFmpeg failed: {result.stderr}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Create animatic from storyboards and audio")
    parser.add_argument("--project", required=True, help="Project name")
    args = parser.parse_args()

    if not check_ffmpeg():
        sys.exit(1)

    if not (PROJECTS_DIR / args.project).exists():
        logger.error(f"Project directory not found: {PROJECTS_DIR / args.project}")
        sys.exit(1)

    output_path = OUTPUTS_DIR / f"{args.project}_animatic.mp4"
    create_animatic(args.project, output_path)


if __name__ == "__main__":
    main()
