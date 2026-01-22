# Voices Directory

Generated TTS audio files and lip-sync JSON data are stored here.

## Structure

```
voices/
├── <project_name>/
│   ├── SH020_Character.wav    # TTS audio files
│   └── SH020_Character.json   # Rhubarb lip-sync data
└── ...
```

## Generation

1. Generate TTS: `python3 scripts/gen_tts.py --project <name>`
2. Generate lip-sync: `python3 scripts/gen_lipsync.py --project <name>`

## Notes

- Audio files are in WAV format
- JSON files contain phoneme timing data for Blender
- Files are gitignored (generated content)
