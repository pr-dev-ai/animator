# Piper TTS Setup

Piper TTS is used for offline text-to-speech generation.

## Voice Models

Download voice models and place them in this directory (`./piper/`).

### Download Voice Models

**Option 1: Automated download (recommended)**
```bash
./scripts/download_piper_voice.sh en_US-lessac-medium
```

**Option 2: Manual download**
1. Visit: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium
2. Download voice model files (`.onnx` format)
3. Place in `./piper/` directory

**Option 3: Using wget**
```bash
cd piper/
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
```

### Example Voice Models

- `en_US-lessac-medium.onnx` - US English, medium quality
- `en_GB-alba-medium.onnx` - British English, medium quality

### Usage

The TTS script (`scripts/gen_tts.py`) will use these models via docker exec:

```bash
echo "Hello world" | docker exec -i piper piper -m /config/en_US-lessac-medium.onnx -f /voices/output.wav
```

Or using the automation script:

```bash
python3 scripts/gen_tts.py --project night_shift
```

## Notes

- Voice models are typically 5-50MB each
- Models are gitignored (too large for version control)
- Place models directly in `./piper/` directory
