# Piper TTS Setup

Piper TTS is used for offline text-to-speech generation.

## Voice Models

Download voice models and place them in this directory (`./piper/`).

### Download Voice Models

1. Visit: https://github.com/rhasspy/piper/releases
2. Download voice model files (`.onnx` format)
3. Place in `./piper/` directory

### Example Voice Models

- `en_US-lessac-medium.onnx` - US English, medium quality
- `en_GB-alba-medium.onnx` - British English, medium quality

### Usage

The TTS script (`scripts/gen_tts.py`) will use these models via docker exec:

```bash
docker exec -it piper python3 -m piper_tts --model /config/en_US-lessac-medium.onnx --output_file /voices/output.wav
```

Or using the automation script:

```bash
python3 scripts/gen_tts.py --project night_shift
```

## Notes

- Voice models are typically 5-50MB each
- Models are gitignored (too large for version control)
- Place models directly in `./piper/` directory
