# Ubuntu Music Tools for Kids Animation Audio

For most users, start with **Audacity** for recording vocals and **LMMS** for composing music tracks. These two tools cover 80% of what you need and are easy to install on Ubuntu.

## Tool Overview

| Tool | What it's for | Install | Best for |
|------|--------------|---------|---------|
| **Audacity** | Record & edit vocals/singing, trim, clean noise | `sudo apt install audacity` | Recording your singing, exporting WAV |
| **LMMS** | Full DAW — compose backing tracks, beats, arrange | `sudo apt install lmms` | Creating music beds for kids songs |
| **Ardour** | Professional multi-track recording & mixing | `sudo apt install ardour` | Mixing final audio with multiple tracks |
| **MuseScore** | Write & print sheet music, chord charts | `sudo apt install musescore3` | Planning your song structure |
| **Hydrogen** | Drum machine — program rhythms and beats | `sudo apt install hydrogen` | Adding rhythm/percussion |
| **Carla** | VST/LV2 plugin host for synths & effects | `sudo apt install carla` | Adding reverb, EQ, pitch correction |
| **Kdenlive** | Video editor (also for audio sync) | `sudo apt install kdenlive` | Final video assembly with audio |

## Recommended Workflow for Kids Animation Audio

```
Step 1 — Compose the music bed in LMMS
  → Export as WAV (e.g., song_verse1.wav)

Step 2 — Record your singing/narration in Audacity
  → Import the LMMS track as a guide
  → Record vocals on a new track
  → Export combined result as a single WAV

Step 3 — Place your WAV in the voices directory, then generate lip-sync
  # Copy your recorded/exported WAV into the project voices folder:
  cp ~/music/verse1_final.wav voices/my_kids_show/SH020_Narrator.wav

  # Generate lip-sync data from the WAV:
  python3 scripts/gen_lipsync.py --project my_kids_show

Step 4 — Generate animatic with your audio
  # Activate the virtualenv first (or use ./run_script.sh):
  source .venv/bin/activate
  python3 scripts/make_dailies.py --project my_kids_show

Step 5 — Final assembly in Kdenlive or Blender VSE
```

## Audacity Tips for Clean Vocal Recording

- Use Effect → Noise Reduction to clean background noise
- Use Effect → Amplify to normalize volume
- Export as WAV, 16-bit PCM (Rhubarb accepts 22050 Hz or 44100 Hz; either works)
- Keep recordings under 10 seconds per shot for best lip-sync accuracy

## LMMS Quick Start for Kids Music

- Use the Beat+Bassline editor for simple rhythmic tracks
- The SF2 Player supports free soundfonts (search for "kids music soundfont")
- Export: File → Export → As WAV

## System Audio Setup

This is an important prerequisite before recording.

```bash
# Install JACK audio for low-latency recording
sudo apt install jackd2 qjackctl

# Or use PulseAudio (simpler, usually already installed)
pactl info | grep "Server Name"
```
