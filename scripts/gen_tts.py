#!/usr/bin/env python3
"""
Generate TTS audio files from dialogue.csv using Piper container.
"""

import argparse
import csv
import logging
import os
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
VOICES_DIR = REPO_ROOT / "voices"


def generate_tts_via_service(text: str, voice_id: str, output_path: Path, language: str = "en") -> bool:
    """
    Generate TTS using Piper service via HTTP API.
    Falls back to docker exec if service is unavailable.
    """
    # Try HTTP API first
    try:
        import requests
        response = requests.post(
            "http://localhost:10200/api/tts",
            json={
                "text": text,
                "voice": voice_id,
                "language": language
            },
            timeout=30
        )
        if response.status_code == 200:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(response.content)
            return True
    except Exception as e:
        logger.debug(f"HTTP API failed: {e}, trying docker exec")

    # Fallback to docker exec (CLI mode)
    # Note: This requires piper binary and voice models in ./piper/ directory
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        container_output = f"/voices/{output_path.relative_to(VOICES_DIR)}"
        model_path = f"/config/{voice_id}.onnx"
        
        # Check if model exists in container
        check_cmd = ["docker", "exec", "piper", "test", "-f", model_path]
        check_result = subprocess.run(check_cmd, capture_output=True)
        if check_result.returncode != 0:
            logger.error(f"Voice model not found: {voice_id}.onnx")
            logger.info(f"Expected location in container: {model_path}")
            logger.info("Download models from: https://github.com/rhasspy/piper/releases")
            logger.info("Place model files in ./piper/ directory on host")
            return False
        
        # Use piper binary with stdin input (properly handles quotes and special chars)
        # piper reads from stdin by default if no -i flag is provided
        cmd = [
            "docker", "exec", "-i", "piper",
            "piper",
            "-m", model_path,
            "-f", container_output
        ]
        
        result = subprocess.run(
            cmd,
            input=text,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            logger.info(f"Generated via docker exec: {output_path}")
            return True
        else:
            logger.error(f"Piper exec failed: {result.stderr}")
            logger.info("Note: Ensure voice models are in ./piper/ directory")
            logger.info("Download models from: https://github.com/rhasspy/piper/releases")
            return False
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        logger.info("Note: Piper TTS container must be running. Start with: ./app.sh start")
        return False


def process_dialogue_csv(project_name: str, force: bool = False):
    """Process dialogue.csv and generate WAV files."""
    dialogue_csv = PROJECTS_DIR / project_name / "dialogue.csv"
    
    if not dialogue_csv.exists():
        logger.error(f"dialogue.csv not found: {dialogue_csv}")
        sys.exit(1)

    output_dir = VOICES_DIR / project_name
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0
    failed = 0

    with open(dialogue_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            shot_id = row.get("shot_id", "").strip()
            character = row.get("character", "").strip()
            text = row.get("text", "").strip()
            voice_id = row.get("voice_id", "en_US-lessac-medium").strip()
            language = row.get("language", "en").strip()
            row_type = row.get("type", "dialogue").strip()

            if not shot_id or not text:
                logger.warning(f"Skipping row with missing shot_id or text")
                continue

            output_filename = f"{shot_id}_{character}.wav"
            output_path = output_dir / output_filename

            # Song and custom tracks are always skipped — user must supply recording via import_audio.py
            if row_type in ("song", "custom"):
                logger.info(f"Skipping TTS for {row_type} track (use import_audio.py to add your recording): {shot_id}")
                skipped += 1
                continue

            if output_path.exists() and not force:
                logger.info(f"Skipping {output_filename} (already exists)")
                skipped += 1
                continue

            logger.info(f"Generating {output_filename}...")
            if generate_tts_via_service(text, voice_id, output_path, language):
                logger.info(f"✅ Generated: {output_path}")
                generated += 1
            else:
                logger.error(f"❌ Failed: {output_filename}")
                failed += 1

    logger.info(f"\nSummary: {generated} generated, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Generate TTS audio from dialogue.csv")
    parser.add_argument("--project", required=True, help="Project name (directory in projects/)")
    parser.add_argument("--force", action="store_true", help="Regenerate existing files")
    args = parser.parse_args()

    if not (PROJECTS_DIR / args.project).exists():
        logger.error(f"Project directory not found: {PROJECTS_DIR / args.project}")
        sys.exit(1)

    process_dialogue_csv(args.project, force=args.force)


if __name__ == "__main__":
    main()
