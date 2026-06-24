#!/usr/bin/env python3
"""
Import user-recorded audio into the pipeline.

Copies a user-provided WAV file into the voices/<project>/ directory
using the standard naming convention, and optionally runs lip-sync generation.
"""

import argparse
import csv
import importlib.util
import logging
import shutil
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


def import_audio(project: str, shot: str, character: str, audio_src: Path, run_lipsync: bool = False) -> bool:
    """Copy a WAV file into the pipeline's voices directory."""
    # Validate source file
    if not audio_src.exists():
        logger.error(f"Source audio file not found: {audio_src}")
        return False

    if audio_src.suffix.lower() != ".wav":
        logger.error(f"Source file must be a .wav file, got: {audio_src.suffix}")
        return False

    # Build destination path
    output_dir = VOICES_DIR / project
    output_dir.mkdir(parents=True, exist_ok=True)

    output_filename = f"{shot}_{character}.wav"
    output_path = output_dir / output_filename

    # Copy the file
    shutil.copy2(audio_src, output_path)
    logger.info(f"Imported: {audio_src} -> {output_path}")

    # Optionally run lip-sync generation
    if run_lipsync:
        gen_lipsync_script = Path(__file__).parent / "gen_lipsync.py"
        json_path = output_path.with_suffix(".json")
        logger.info(f"Running lip-sync generation for {output_filename}...")

        # Load gen_lipsync.py directly to call generate_lipsync() for this single file
        # rather than processing the whole project directory.
        try:
            spec = importlib.util.spec_from_file_location("gen_lipsync", gen_lipsync_script)
            gen_lipsync = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gen_lipsync)
            success = gen_lipsync.generate_lipsync(output_path, json_path, force=True)
            if success:
                logger.info(f"Lip-sync JSON generated: {json_path}")
            else:
                logger.warning("Lip-sync generation failed (see above for details)")
                logger.info(f"Audio was imported successfully: {output_path}")
                return False
        except Exception as e:
            logger.error(f"Failed to run lip-sync generation: {e}")
            logger.info(f"Audio was imported successfully: {output_path}")
            return False

    return True


def list_missing_audio(project: str):
    """Show shots in dialogue.csv that do not yet have a WAV file.

    Labels each missing entry with the action needed:
      [import_audio] — type=song or type=custom (user must record and import)
      [gen_tts]      — type=dialogue (gen_tts.py will generate this automatically)
    """
    dialogue_csv = PROJECTS_DIR / project / "dialogue.csv"
    if not dialogue_csv.exists():
        logger.error(f"dialogue.csv not found: {dialogue_csv}")
        sys.exit(1)

    voices_dir = VOICES_DIR / project
    missing = []

    with open(dialogue_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            shot_id = row.get("shot_id", "").strip()
            character = row.get("character", "").strip()
            row_type = row.get("type", "dialogue").strip()
            if not shot_id:
                continue
            wav_path = voices_dir / f"{shot_id}_{character}.wav"
            if not wav_path.exists():
                missing.append((shot_id, character, row_type, row.get("text", "").strip()))

    if not missing:
        logger.info(f"All shots in {project} have audio files.")
        return

    logger.info(f"Shots missing audio in project '{project}':")
    for shot_id, character, row_type, text in missing:
        if row_type in ("song", "custom"):
            action = "import_audio"
        else:
            action = "gen_tts"
        logger.info(f"  [{action}] {shot_id}_{character}.wav  [{character}]: {text[:60]}")


def main():
    parser = argparse.ArgumentParser(
        description="Import a user-recorded WAV into the animation pipeline."
    )
    parser.add_argument("--project", required=True, help="Project name (subdirectory in voices/ and projects/)")
    parser.add_argument("--shot", help="Shot ID (e.g. SH010)")
    parser.add_argument("--character", help="Character name (e.g. Narrator)")
    parser.add_argument("--audio", help="Path to source WAV file")
    parser.add_argument("--lipsync", action="store_true", help="Run lip-sync generation after import")
    parser.add_argument("--list", action="store_true", help="List shots missing audio and exit")
    args = parser.parse_args()

    if args.list:
        list_missing_audio(args.project)
        return

    # In import mode, --shot, --character, and --audio are required
    missing_args = [name for name, val in [("--shot", args.shot), ("--character", args.character), ("--audio", args.audio)] if val is None]
    if missing_args:
        parser.error(f"The following arguments are required in import mode: {', '.join(missing_args)}")

    audio_src = Path(args.audio)
    success = import_audio(
        project=args.project,
        shot=args.shot,
        character=args.character,
        audio_src=audio_src,
        run_lipsync=args.lipsync,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
