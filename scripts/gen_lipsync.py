#!/usr/bin/env python3
"""
Generate lip-sync JSON files from WAV files using Rhubarb container.
"""

import argparse
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
VOICES_DIR = REPO_ROOT / "voices"


def generate_lipsync(wav_path: Path, json_path: Path, force: bool = False) -> bool:
    """Generate lip-sync JSON from WAV using Rhubarb container."""
    if json_path.exists() and not force:
        logger.info(f"Skipping {json_path.name} (already exists)")
        return True

    json_path.parent.mkdir(parents=True, exist_ok=True)

    # Use docker exec to run rhubarb
    # Map the voices directory so paths work inside container
    container_wav = f"/voices/{wav_path.relative_to(VOICES_DIR)}"
    container_json = f"/voices/{json_path.relative_to(VOICES_DIR)}"

    cmd = [
        "docker", "exec", "rhubarb",
        "rhubarb",
        "-f", "json",
        "-o", container_json,
        container_wav
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            logger.info(f"✅ Generated: {json_path}")
            return True
        else:
            logger.error(f"❌ Rhubarb failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"❌ Rhubarb timed out for {wav_path.name}")
        return False
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False


def process_project(project_name: str, force: bool = False):
    """Process all WAV files in project directory."""
    project_dir = VOICES_DIR / project_name

    if not project_dir.exists():
        logger.error(f"Project voices directory not found: {project_dir}")
        sys.exit(1)

    wav_files = list(project_dir.glob("*.wav"))
    if not wav_files:
        logger.warning(f"No WAV files found in {project_dir}")
        return

    generated = 0
    skipped = 0
    failed = 0

    for wav_path in wav_files:
        json_path = wav_path.with_suffix(".json")
        if generate_lipsync(wav_path, json_path, force):
            if json_path.exists():
                generated += 1
            else:
                skipped += 1
        else:
            failed += 1

    logger.info(f"\nSummary: {generated} generated, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Generate lip-sync JSON from WAV files")
    parser.add_argument("--project", required=True, help="Project name (directory in voices/)")
    parser.add_argument("--force", action="store_true", help="Regenerate existing files")
    args = parser.parse_args()

    process_project(args.project, force=args.force)


if __name__ == "__main__":
    main()
