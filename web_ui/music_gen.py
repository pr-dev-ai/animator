"""Chord synthesizer and audio mixer — no external tools required, pure numpy."""

from __future__ import annotations

import io
import math
import re
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100

_NOTE_FREQS: dict[str, float] = {
    "C": 261.63, "C#": 277.18, "Db": 277.18,
    "D": 293.66, "D#": 311.13, "Eb": 311.13,
    "E": 329.63, "F": 349.23, "F#": 369.99, "Gb": 369.99,
    "G": 392.00, "G#": 415.30, "Ab": 415.30,
    "A": 440.00, "A#": 466.16, "Bb": 466.16,
    "B": 493.88,
}

# Semitone intervals above root for each chord quality
_INTERVALS: dict[str, list[int]] = {
    "":     [0, 4, 7],        # major
    "m":    [0, 3, 7],        # minor
    "7":    [0, 4, 7, 10],    # dominant 7
    "m7":   [0, 3, 7, 10],    # minor 7
    "maj7": [0, 4, 7, 11],    # major 7
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "dim":  [0, 3, 6],
    "aug":  [0, 4, 8],
}

_CHORD_RE = re.compile(r"^([A-G][#b]?)(m7|maj7|sus2|sus4|dim|aug|m|7)?$")


def _parse_chord(name: str) -> tuple[str | None, str]:
    m = _CHORD_RE.match(name.strip())
    if not m:
        return None, ""
    return m.group(1), m.group(2) or ""


def _freq(root: str, semitones: int) -> float:
    return _NOTE_FREQS.get(root, 261.63) * (2 ** (semitones / 12.0))


def _synth_note(freq: float, duration_s: float, vol: float = 0.3) -> np.ndarray:
    n = int(SAMPLE_RATE * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Layered harmonics for a piano-like tone
    sig = (
        np.sin(2 * math.pi * freq * t)
        + 0.35 * np.sin(4 * math.pi * freq * t)
        + 0.12 * np.sin(6 * math.pi * freq * t)
        + 0.05 * np.sin(8 * math.pi * freq * t)
    )
    # ADSR
    attack = min(int(0.015 * SAMPLE_RATE), n)
    decay = min(int(0.06 * SAMPLE_RATE), n)
    release = min(int(0.12 * SAMPLE_RATE), n)
    sustain_level = 0.75
    env = np.ones(n) * sustain_level
    if attack:
        env[:attack] = np.linspace(0, 1, attack)
    if decay and attack + decay < n:
        env[attack: attack + decay] = np.linspace(1, sustain_level, decay)
    if release and n > release:
        env[-release:] = np.linspace(sustain_level, 0, release)
    return (sig * env * vol).astype(np.float64)


def synth_chord(chord_name: str, duration_s: float) -> np.ndarray:
    """Synthesize a chord as overlapping sine waves."""
    root, quality = _parse_chord(chord_name)
    n = int(SAMPLE_RATE * duration_s)
    if root is None:
        return np.zeros(n, dtype=np.float64)

    intervals = _INTERVALS.get(quality, [0, 4, 7])
    base = _NOTE_FREQS.get(root, 261.63)
    combined = np.zeros(n, dtype=np.float64)

    # Bass note one octave down
    combined += _synth_note(base / 2, duration_s, vol=0.22)
    # Chord tones
    vol_each = 0.52 / len(intervals)
    for semitones in intervals:
        combined += _synth_note(_freq(root, semitones), duration_s, vol=vol_each)

    peak = np.max(np.abs(combined))
    if peak > 0:
        combined = combined / peak * 0.88
    return combined


def arrangement_to_wav(bars: list[dict], tempo_bpm: float) -> bytes:
    """Render a list of {chord, beats} bars to 16-bit mono WAV bytes."""
    if not bars:
        return _ndarray_to_wav(np.zeros(SAMPLE_RATE, dtype=np.float64))
    beat_s = 60.0 / max(float(tempo_bpm), 40)
    chunks = [
        synth_chord(bar.get("chord", "C"), float(bar.get("beats", 4)) * beat_s)
        for bar in bars
    ]
    return _ndarray_to_wav(np.concatenate(chunks))


def _ndarray_to_wav(audio: np.ndarray) -> bytes:
    int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


def _wav_to_ndarray(wav_bytes: bytes) -> np.ndarray:
    """Load WAV bytes → float64 mono array normalised to ±1, resampled to SAMPLE_RATE."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(sw, np.int16)
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if sw == 1:
        samples = samples / 128.0 - 1.0
    elif sw == 2:
        samples /= 32768.0
    else:
        samples /= 2147483648.0

    if n_ch > 1:
        samples = samples.reshape(-1, n_ch).mean(axis=1)

    if rate != SAMPLE_RATE:
        old_n = len(samples)
        new_n = int(old_n * SAMPLE_RATE / rate)
        samples = np.interp(
            np.linspace(0, old_n - 1, new_n), np.arange(old_n), samples
        )
    return samples


def mix_tracks(
    instrumental_path: Path,
    vocal_path: Path,
    instrumental_vol: float = 0.7,
    vocal_vol: float = 1.0,
) -> bytes:
    """Overlay vocals on top of instrumental. Returns WAV bytes."""
    instr = _wav_to_ndarray(instrumental_path.read_bytes()) * instrumental_vol
    vocal = _wav_to_ndarray(vocal_path.read_bytes()) * vocal_vol

    max_len = max(len(instr), len(vocal))
    instr = np.pad(instr, (0, max_len - len(instr)))
    vocal = np.pad(vocal, (0, max_len - len(vocal)))

    mixed = instr + vocal
    peak = np.max(np.abs(mixed))
    if peak > 1.0:
        mixed = mixed / peak * 0.95

    return _ndarray_to_wav(mixed)
