#!/usr/bin/env python3
#
# Cube Libre Audio Lab
# --------------------
# Separate pygame-based poor-man's sound-design / 4-track piano-roll tool.
# It does not import or modify cube_libre_pygame.py.
#
# Put this file at:
#   tools/cube_libre_audio_lab.py
#
# Run from the repository root:
#   python tools/cube_libre_audio_lab.py
#
# Output dirs, created automatically:
#   tools/audio_lab_patches/   JSON patches / songs
#   tools/audio_lab_exports/   rendered WAV files
#

from __future__ import annotations

import copy
import json
import math
import os
import random
import sys
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

try:
    import pygame
except Exception as exc:
    print(f"[ERROR] pygame is required: {exc}", file=sys.stderr)
    print("Install repo requirements first, e.g.: python -m pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(1)

try:
    import numpy as np
except Exception:
    np = None

# -----------------------------------------------------------------------------
# Paths / constants
# -----------------------------------------------------------------------------

SAMPLE_RATE = 44100
CHANNELS = 2
WINDOW_W, WINDOW_H = 1280, 820
FPS = 60
TRACKS = 4
STEPS = 32
ROWS = 24
BASE_MIDI = 48  # C3
DEFAULT_BPM = 120
DEFAULT_SWING = 0.0
STEP_BEATS = 0.25  # 16th notes
MAX_POLY_PER_TRACK_STEP = 4

THIS_FILE = Path(__file__).resolve()
TOOLS_DIR = THIS_FILE.parent
REPO_ROOT = TOOLS_DIR.parent
PATCH_DIR = TOOLS_DIR / "audio_lab_patches"
EXPORT_DIR = TOOLS_DIR / "audio_lab_exports"
REAL_SFX_DIR = REPO_ROOT / "assets" / "sfx"
PATCH_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

ASSET_TARGETS = {
    "crash": "crash_collision.wav",
    "structure_alert": "structure_loss_dee_doo.wav",
    "ambient": "spaceship_engine_room_hum_loop.wav",
    "gamelan": "alien_gamelan_more_frequent_v3.wav",
    "gamelan_sourcegain": "alien_gamelan_more_frequent_v3_sourcegain.wav",
    "field": "field_wooom_loop.wav",
    "critical": "critical_beep_loop.wav",
    "portal": "portal_ethereal_entry.wav",
    "portal_wou": "portal_wou_wou_slow_loop.wav",
    "laser_reveal": "laser_grid_reveal_ominous_woosh.wav",
    "laser_dissipate": "laser_grid_dissipate_exhale_aaah.wav",
    "materialize": "course_materialize.wav",
    "death": "cube_death_whiteout.wav",
    "reassembly": "cube_reassembly.wav",
    "recouple": "cube_recoupling_request.wav",
    "collapse": "joint_cage_collapse_cashhh_octave_down.wav",
    "time_tick": "time_tick_tock_loop.wav",
    "time_buzzer": "time_buzzer_10sec_berrrrt.wav",
    "time_siren": "time_siren_5sec_wiuwiu_loop.wav",
}

WAVEFORMS = ["sine", "square", "saw", "triangle", "noise", "fm", "ring", "pluck"]
PARAMS = [
    "wave",
    "gain",
    "attack",
    "decay",
    "sustain",
    "release",
    "fm_ratio",
    "fm_index",
    "noise",
    "detune",
    "pan",
    "cutoff",
    "drive",
]

PRESET_PATCHES: Dict[str, dict] = {}

# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def db_to_amp(db: float) -> float:
    return 10.0 ** (float(db) / 20.0)


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def safe_name(name: str) -> str:
    out = []
    for ch in str(name).strip().lower():
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    s = "".join(out).strip("._-")
    return s or "cube_libre_patch"


def soft_clip(x):
    # Works for scalars and numpy arrays.
    return x / (1.0 + abs(x))


def equal_power_pan(mono, pan: float):
    pan = clamp(float(pan), -1.0, 1.0)
    angle = (pan + 1.0) * math.pi * 0.25
    l = math.cos(angle)
    r = math.sin(angle)
    return mono * l, mono * r


def normalize_stereo(stereo, peak: float = 0.92):
    if np is None:
        maxv = max(max(abs(x), abs(y)) for x, y in stereo) if stereo else 0.0
        if maxv <= 1e-9:
            return stereo
        g = min(1.0, peak / maxv)
        return [(x * g, y * g) for x, y in stereo]
    maxv = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    if maxv <= 1e-9:
        return stereo
    g = min(1.0, peak / maxv)
    return stereo * g


def float_to_pcm16_array(stereo) -> array:
    pcm = array("h")
    if np is not None and hasattr(stereo, "shape"):
        clipped = np.clip(stereo, -1.0, 1.0)
        ints = (clipped * 32767.0).astype("<i2")
        pcm.frombytes(ints.tobytes())
        return pcm
    for l, r in stereo:
        pcm.append(int(clamp(l, -1.0, 1.0) * 32767))
        pcm.append(int(clamp(r, -1.0, 1.0) * 32767))
    return pcm


def write_wav_stereo(path: Path, stereo, sample_rate: int = SAMPLE_RATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = float_to_pcm16_array(stereo)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def stereo_to_sound(stereo, sample_rate: int = SAMPLE_RATE) -> pygame.mixer.Sound:
    # pygame.sndarray.make_sound wants int16 array shaped (samples, channels).
    if np is not None:
        arr = np.clip(stereo, -1.0, 1.0)
        intarr = (arr * 32767.0).astype(np.int16)
        return pygame.sndarray.make_sound(intarr.copy())
    pcm = float_to_pcm16_array(stereo)
    return pygame.mixer.Sound(buffer=pcm.tobytes())


def blank_stereo(num_samples: int):
    if np is not None:
        return np.zeros((num_samples, 2), dtype=np.float32)
    return [(0.0, 0.0) for _ in range(num_samples)]


def add_stereo(dst, start: int, src):
    if np is not None:
        if start >= len(dst):
            return
        end = min(len(dst), start + len(src))
        if end <= start:
            return
        dst[start:end, :] += src[: end - start, :]
        return
    for i, (l, r) in enumerate(src):
        j = start + i
        if 0 <= j < len(dst):
            dl, dr = dst[j]
            dst[j] = (dl + l, dr + r)


def render_text(font, text, color=(220, 235, 235)):
    return font.render(str(text), True, color)

# -----------------------------------------------------------------------------
# Patch model
# -----------------------------------------------------------------------------


def default_track(index: int) -> dict:
    waves = ["fm", "saw", "noise", "triangle"]
    names = ["FM bite", "saw stab", "noise ash", "tri pulse"]
    pans = [-0.35, 0.35, 0.0, 0.0]
    gains = [0.45, 0.34, 0.28, 0.32]
    return {
        "name": names[index] if index < len(names) else f"track {index+1}",
        "muted": False,
        "solo": False,
        "params": {
            "wave": waves[index % len(waves)],
            "gain": gains[index % len(gains)],
            "attack": 0.006,
            "decay": 0.080,
            "sustain": 0.45,
            "release": 0.160,
            "fm_ratio": 2.0 + index * 0.5,
            "fm_index": 2.0 if index == 0 else 0.8,
            "noise": 0.0 if index != 2 else 0.75,
            "detune": 0.0,
            "pan": pans[index % len(pans)],
            "cutoff": 0.80,
            "drive": 0.10,
        },
        "notes": [],
    }


def default_patch() -> dict:
    patch = {
        "version": 1,
        "name": "cube-libre-audio-lab-default",
        "bpm": DEFAULT_BPM,
        "swing": DEFAULT_SWING,
        "steps": STEPS,
        "rows": ROWS,
        "base_midi": BASE_MIDI,
        "step_beats": STEP_BEATS,
        "loop": True,
        "tracks": [default_track(i) for i in range(TRACKS)],
    }
    # Starter pattern: ugly enough to be immediately editable, useful enough to prove it works.
    add_note(patch, 0, 0, 60, 2, 0.85)
    add_note(patch, 0, 4, 63, 2, 0.70)
    add_note(patch, 0, 8, 67, 2, 0.80)
    add_note(patch, 0, 12, 70, 2, 0.65)
    add_note(patch, 0, 16, 60, 2, 0.85)
    add_note(patch, 0, 20, 65, 2, 0.72)
    add_note(patch, 0, 24, 67, 2, 0.80)
    add_note(patch, 0, 28, 72, 3, 0.70)

    for s in range(0, STEPS, 4):
        add_note(patch, 1, s, 36 + (s // 8) * 5, 1, 0.60)
    for s in range(2, STEPS, 4):
        add_note(patch, 2, s, 72, 1, 0.45)
    for s in range(0, STEPS, 8):
        add_note(patch, 3, s, 48, 4, 0.45)
    return patch


def add_note(patch: dict, track: int, step: int, midi: int, length: int = 1, velocity: float = 0.8):
    track = int(clamp(track, 0, len(patch["tracks"]) - 1))
    step = int(clamp(step, 0, patch["steps"] - 1))
    midi = int(midi)
    length = int(clamp(length, 1, patch["steps"]))
    patch["tracks"][track]["notes"].append({
        "step": step,
        "midi": midi,
        "length": length,
        "velocity": float(clamp(velocity, 0.0, 1.0)),
    })


def remove_note_at(patch: dict, track: int, step: int, midi: int) -> bool:
    notes = patch["tracks"][track]["notes"]
    for i, n in enumerate(notes):
        if int(n.get("step", -1)) == int(step) and int(n.get("midi", -999)) == int(midi):
            notes.pop(i)
            return True
    return False


def find_note_at(patch: dict, track: int, step: int, midi: int) -> Optional[dict]:
    for n in patch["tracks"][track]["notes"]:
        if int(n.get("step", -1)) == int(step) and int(n.get("midi", -999)) == int(midi):
            return n
    return None


def validate_patch(patch: dict) -> dict:
    p = copy.deepcopy(patch)
    p.setdefault("version", 1)
    p.setdefault("name", "cube-libre-patch")
    p["bpm"] = int(clamp(int(p.get("bpm", DEFAULT_BPM)), 40, 260))
    p["swing"] = float(clamp(float(p.get("swing", 0.0)), -0.45, 0.45))
    p["steps"] = int(clamp(int(p.get("steps", STEPS)), 8, 128))
    p["rows"] = int(clamp(int(p.get("rows", ROWS)), 12, 60))
    p["base_midi"] = int(clamp(int(p.get("base_midi", BASE_MIDI)), 12, 96))
    p["step_beats"] = float(p.get("step_beats", STEP_BEATS))
    p.setdefault("loop", True)
    tracks = p.get("tracks") or []
    while len(tracks) < TRACKS:
        tracks.append(default_track(len(tracks)))
    p["tracks"] = tracks[:TRACKS]
    for ti, tr in enumerate(p["tracks"]):
        base = default_track(ti)
        tr.setdefault("name", base["name"])
        tr.setdefault("muted", False)
        tr.setdefault("solo", False)
        params = tr.setdefault("params", {})
        for k, v in base["params"].items():
            params.setdefault(k, v)
        if params.get("wave") not in WAVEFORMS:
            params["wave"] = "sine"
        tr["notes"] = list(tr.get("notes") or [])[:10000]
    return p

# -----------------------------------------------------------------------------
# Synth engine
# -----------------------------------------------------------------------------


def envelope(t, dur, attack, decay, sustain, release):
    attack = max(0.0005, float(attack))
    decay = max(0.0005, float(decay))
    sustain = clamp(float(sustain), 0.0, 1.0)
    release = max(0.0005, float(release))
    if dur <= 0.0:
        return 0.0
    if np is not None and hasattr(t, "shape"):
        env = np.zeros_like(t, dtype=np.float32)
        a = t < attack
        env[a] = t[a] / attack
        d = (t >= attack) & (t < attack + decay)
        env[d] = 1.0 - (1.0 - sustain) * ((t[d] - attack) / decay)
        mid = (t >= attack + decay) & (t <= max(0.0, dur - release))
        env[mid] = sustain
        r = t > max(0.0, dur - release)
        env[r] = sustain * np.maximum(0.0, (dur - t[r]) / release)
        return env
    if t < attack:
        return t / attack
    if t < attack + decay:
        return 1.0 - (1.0 - sustain) * ((t - attack) / decay)
    if t > dur - release:
        return sustain * max(0.0, (dur - t) / release)
    return sustain


def oscillator(wave: str, phase):
    # phase is cycles, not radians. Works for numpy/scalar.
    if np is not None and hasattr(phase, "shape"):
        frac = phase - np.floor(phase)
        if wave == "sine":
            return np.sin(math.tau * phase)
        if wave == "square":
            return np.where(frac < 0.5, 1.0, -1.0)
        if wave == "saw":
            return 2.0 * frac - 1.0
        if wave == "triangle":
            return 1.0 - 4.0 * np.abs(frac - 0.5)
        return np.sin(math.tau * phase)
    frac = phase - math.floor(phase)
    if wave == "sine":
        return math.sin(math.tau * phase)
    if wave == "square":
        return 1.0 if frac < 0.5 else -1.0
    if wave == "saw":
        return 2.0 * frac - 1.0
    if wave == "triangle":
        return 1.0 - 4.0 * abs(frac - 0.5)
    return math.sin(math.tau * phase)


def one_pole_lowpass(samples, cutoff_norm: float):
    cutoff_norm = clamp(float(cutoff_norm), 0.01, 1.0)
    # Not physically exact; a cheap musical smoothing knob.
    alpha = cutoff_norm ** 2
    if np is not None and hasattr(samples, "shape"):
        out = np.empty_like(samples)
        acc = 0.0
        for i, x in enumerate(samples):
            acc += alpha * (float(x) - acc)
            out[i] = acc
        return out
    out = []
    acc = 0.0
    for x in samples:
        acc += alpha * (float(x) - acc)
        out.append(acc)
    return out


def synth_note(params: dict, midi: int, duration: float, velocity: float, sample_rate: int = SAMPLE_RATE, seed: int = 0):
    duration = max(0.005, float(duration))
    n = max(1, int(duration * sample_rate))
    wave = params.get("wave", "sine")
    gain = float(params.get("gain", 0.4)) * float(velocity)
    attack = float(params.get("attack", 0.005))
    decay = float(params.get("decay", 0.07))
    sustain = float(params.get("sustain", 0.5))
    release = float(params.get("release", 0.15))
    fm_ratio = float(params.get("fm_ratio", 2.0))
    fm_index = float(params.get("fm_index", 0.0))
    noise_amt = float(params.get("noise", 0.0))
    detune = float(params.get("detune", 0.0))
    pan = float(params.get("pan", 0.0))
    cutoff = float(params.get("cutoff", 0.85))
    drive = float(params.get("drive", 0.0))
    freq = midi_to_hz(int(midi)) * (2.0 ** (detune / 1200.0))

    if np is not None:
        t = np.arange(n, dtype=np.float32) / float(sample_rate)
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        if wave == "noise":
            mono = rng.uniform(-1.0, 1.0, n).astype(np.float32)
        elif wave == "fm":
            mod = np.sin(math.tau * freq * fm_ratio * t)
            mono = np.sin(math.tau * freq * t + fm_index * mod).astype(np.float32)
        elif wave == "ring":
            a = np.sin(math.tau * freq * t)
            b = np.sin(math.tau * freq * fm_ratio * t)
            mono = (a * b).astype(np.float32)
        elif wave == "pluck":
            # Tiny Karplus-ish noise burst through feedback delay.
            period = max(2, int(sample_rate / max(1.0, freq)))
            buf = rng.uniform(-1.0, 1.0, period).astype(np.float32)
            mono = np.zeros(n, dtype=np.float32)
            idx = 0
            damp = 0.985 - clamp(cutoff, 0.0, 1.0) * 0.035
            for i in range(n):
                nxt = 0.5 * (buf[idx] + buf[(idx + 1) % period]) * damp
                mono[i] = buf[idx]
                buf[idx] = nxt
                idx = (idx + 1) % period
        else:
            phase = freq * t
            mono = oscillator(wave, phase).astype(np.float32)
        if noise_amt > 0.0001 and wave != "noise":
            mono = mono * (1.0 - noise_amt) + rng.uniform(-1.0, 1.0, n).astype(np.float32) * noise_amt
        env = envelope(t, duration, attack, decay, sustain, release)
        mono = mono * env * gain
        if cutoff < 0.995 and wave != "pluck":
            mono = one_pole_lowpass(mono, cutoff)
        if drive > 0.0001:
            mono = soft_clip(mono * (1.0 + drive * 12.0))
        left, right = equal_power_pan(mono, pan)
        return np.stack([left, right], axis=1).astype(np.float32)

    rng = random.Random(seed)
    out = []
    delay_buf = None
    delay_idx = 0
    if wave == "pluck":
        period = max(2, int(sample_rate / max(1.0, freq)))
        delay_buf = [rng.uniform(-1.0, 1.0) for _ in range(period)]
    for i in range(n):
        t = i / sample_rate
        if wave == "noise":
            mono = rng.uniform(-1.0, 1.0)
        elif wave == "fm":
            mod = math.sin(math.tau * freq * fm_ratio * t)
            mono = math.sin(math.tau * freq * t + fm_index * mod)
        elif wave == "ring":
            mono = math.sin(math.tau * freq * t) * math.sin(math.tau * freq * fm_ratio * t)
        elif wave == "pluck" and delay_buf is not None:
            nxt = 0.5 * (delay_buf[delay_idx] + delay_buf[(delay_idx + 1) % len(delay_buf)]) * 0.975
            mono = delay_buf[delay_idx]
            delay_buf[delay_idx] = nxt
            delay_idx = (delay_idx + 1) % len(delay_buf)
        else:
            mono = oscillator(wave, freq * t)
        if noise_amt > 0.0001 and wave != "noise":
            mono = mono * (1.0 - noise_amt) + rng.uniform(-1.0, 1.0) * noise_amt
        mono *= envelope(t, duration, attack, decay, sustain, release) * gain
        if drive > 0.0001:
            mono = soft_clip(mono * (1.0 + drive * 12.0))
        l, r = equal_power_pan(mono, pan)
        out.append((l, r))
    if cutoff < 0.995:
        # Lowpass mono-ish after panning for pure python simplicity.
        alpha = cutoff ** 2
        ll = rr = 0.0
        f = []
        for l, r in out:
            ll += alpha * (l - ll)
            rr += alpha * (r - rr)
            f.append((ll, rr))
        return f
    return out


def step_duration_seconds(patch: dict) -> float:
    return (60.0 / float(patch.get("bpm", DEFAULT_BPM))) * float(patch.get("step_beats", STEP_BEATS))


def step_start_seconds(patch: dict, step: int) -> float:
    base = step_duration_seconds(patch)
    swing = float(patch.get("swing", 0.0))
    start = step * base
    # Delay odd 16ths by swing fraction of a step. Negative swing pulls them earlier.
    if step % 2 == 1:
        start += base * swing
    return max(0.0, start)


def pattern_duration_seconds(patch: dict) -> float:
    steps = int(patch.get("steps", STEPS))
    if steps <= 0:
        return 0.0
    return step_start_seconds(patch, steps - 1) + step_duration_seconds(patch)


def format_transport_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    mins = int(seconds // 60)
    secs = seconds - mins * 60
    return f"{mins:02d}:{secs:05.2f}"


def render_patch(patch: dict, include_tail: bool = True):
    patch = validate_patch(patch)
    steps = int(patch["steps"])
    step_sec = step_duration_seconds(patch)
    pattern_len = step_start_seconds(patch, steps - 1) + step_sec
    tail = 1.0 if include_tail else 0.0
    n = int((pattern_len + tail) * SAMPLE_RATE)
    mix = blank_stereo(n)

    solos = [i for i, tr in enumerate(patch["tracks"]) if tr.get("solo")]
    for ti, tr in enumerate(patch["tracks"]):
        if tr.get("muted"):
            continue
        if solos and ti not in solos:
            continue
        params = tr.get("params", {})
        notes_by_step: Dict[int, int] = {}
        for ni, note in enumerate(tr.get("notes", [])):
            st = int(note.get("step", 0))
            notes_by_step[st] = notes_by_step.get(st, 0) + 1
            if notes_by_step[st] > MAX_POLY_PER_TRACK_STEP:
                continue
            midi = int(note.get("midi", BASE_MIDI))
            length = int(max(1, note.get("length", 1)))
            vel = float(note.get("velocity", 0.8))
            start = int(step_start_seconds(patch, st) * SAMPLE_RATE)
            dur = max(0.010, step_sec * length + float(params.get("release", 0.1)))
            seed = 1337 + ti * 100003 + ni * 313 + st * 17 + midi * 7
            snd = synth_note(params, midi, dur, vel, SAMPLE_RATE, seed)
            add_stereo(mix, start, snd)
    return normalize_stereo(mix, 0.92)

# -----------------------------------------------------------------------------
# GUI widgets
# -----------------------------------------------------------------------------


@dataclass
class Slider:
    name: str
    rect: pygame.Rect
    min_value: float
    max_value: float
    value: float
    step: float = 0.01
    int_mode: bool = False

    def set_from_x(self, x: int):
        f = clamp((x - self.rect.x) / max(1, self.rect.w), 0.0, 1.0)
        v = self.min_value + f * (self.max_value - self.min_value)
        if self.int_mode:
            v = int(round(v))
        elif self.step:
            v = round(v / self.step) * self.step
        self.value = clamp(v, self.min_value, self.max_value)

    def normalized(self):
        return (float(self.value) - self.min_value) / max(1e-9, self.max_value - self.min_value)


def draw_button(screen, rect, label, font, active=False, danger=False, disabled=False):
    if disabled:
        color = (28, 32, 34)
        border = (58, 66, 68)
        text_color = (112, 126, 126)
    else:
        color = (55, 78, 82) if not active else (50, 125, 105)
        border = (95, 150, 155) if not danger else (190, 85, 60)
        text_color = (225, 240, 238)
        if danger:
            color = (95, 48, 42)
    pygame.draw.rect(screen, color, rect, border_radius=6)
    pygame.draw.rect(screen, border, rect, width=1, border_radius=6)
    txt = font.render(label, True, text_color)
    screen.blit(txt, (rect.centerx - txt.get_width() // 2, rect.centery - txt.get_height() // 2))


def draw_transport_grabber(screen, rect):
    """Little draggable-looking handle at the left of the transport strip.

    It is only visual for now. It makes the transport look like a coherent widget
    rather than a row of random buttons bolted to the top of the window.
    """
    pygame.draw.rect(screen, (12, 19, 22), rect, border_radius=5)
    pygame.draw.rect(screen, (58, 88, 94), rect, width=1, border_radius=5)
    for x in (rect.centerx - 3, rect.centerx + 3):
        for y in range(rect.y + 7, rect.bottom - 5, 5):
            pygame.draw.circle(screen, (128, 162, 166), (x, y), 1)


def _icon_points(rect: pygame.Rect, pts):
    return [(rect.x + int(rect.w * x), rect.y + int(rect.h * y)) for x, y in pts]


def draw_transport_icon(screen, rect, icon: str, color, disabled=False, active=False):
    """Draw DAW/tape-deck style transport icons without relying on font glyphs."""
    cx, cy = rect.center
    w, h = rect.w, rect.h
    col = color
    red = (225, 70, 62) if not disabled else (108, 70, 68)

    if icon == "play":
        pygame.draw.polygon(screen, (95, 230, 120) if not disabled else col, _icon_points(rect, [(0.39, 0.29), (0.39, 0.71), (0.72, 0.50)]))
        pygame.draw.polygon(screen, (18, 55, 30), _icon_points(rect, [(0.39, 0.29), (0.39, 0.71), (0.72, 0.50)]), width=1)
    elif icon == "pause":
        bw = max(4, int(w * 0.12))
        bh = int(h * 0.42)
        gap = int(w * 0.08)
        pygame.draw.rect(screen, col, pygame.Rect(cx - gap - bw, cy - bh // 2, bw, bh), border_radius=1)
        pygame.draw.rect(screen, col, pygame.Rect(cx + gap, cy - bh // 2, bw, bh), border_radius=1)
    elif icon == "stop":
        side = int(min(w, h) * 0.34)
        pygame.draw.rect(screen, col, pygame.Rect(cx - side // 2, cy - side // 2, side, side), border_radius=1)
    elif icon == "home":
        # skip to start: vertical bar plus left-facing triangle
        pygame.draw.line(screen, col, (cx - int(w * 0.18), cy - int(h * 0.23)), (cx - int(w * 0.18), cy + int(h * 0.23)), 3)
        pygame.draw.polygon(screen, col, _icon_points(rect, [(0.66, 0.29), (0.66, 0.71), (0.37, 0.50)]))
    elif icon == "end":
        # skip to end: right-facing triangle plus vertical bar
        pygame.draw.polygon(screen, col, _icon_points(rect, [(0.34, 0.29), (0.34, 0.71), (0.63, 0.50)]))
        pygame.draw.line(screen, col, (cx + int(w * 0.18), cy - int(h * 0.23)), (cx + int(w * 0.18), cy + int(h * 0.23)), 3)
    elif icon == "rec":
        pygame.draw.circle(screen, red, (cx, cy), int(min(w, h) * 0.17))
    elif icon == "render":
        # Small rendered waveform into a tray. Think "bake audio", not play.
        amp = int(h * 0.13)
        pts = []
        for i in range(9):
            x = rect.x + int(w * (0.22 + 0.055 * i))
            y = cy - int(math.sin(i * 1.45) * amp)
            pts.append((x, y))
        if len(pts) > 1:
            pygame.draw.lines(screen, col, False, pts, 2)
        pygame.draw.line(screen, col, (cx + int(w * 0.18), rect.y + int(h * 0.34)), (cx + int(w * 0.18), rect.y + int(h * 0.65)), 2)
        pygame.draw.polygon(screen, col, _icon_points(rect, [(0.61, 0.63), (0.74, 0.63), (0.675, 0.76)]))
        pygame.draw.line(screen, col, (rect.x + int(w * 0.55), rect.y + int(h * 0.79)), (rect.x + int(w * 0.80), rect.y + int(h * 0.79)), 2)
    elif icon == "loop":
        # Two circular arrows. pygame arcs are primitive but good enough.
        lw = 2
        left = pygame.Rect(rect.x + int(w * 0.23), rect.y + int(h * 0.31), int(w * 0.26), int(h * 0.34))
        right = pygame.Rect(rect.x + int(w * 0.51), rect.y + int(h * 0.31), int(w * 0.26), int(h * 0.34))
        pygame.draw.arc(screen, col, left, math.radians(40), math.radians(330), lw)
        pygame.draw.arc(screen, col, right, math.radians(220), math.radians(150), lw)
        pygame.draw.polygon(screen, col, _icon_points(rect, [(0.45, 0.28), (0.53, 0.28), (0.50, 0.19)]))
        pygame.draw.polygon(screen, col, _icon_points(rect, [(0.55, 0.72), (0.47, 0.72), (0.50, 0.81)]))
    else:
        # Last-ditch fallback: tiny square, so a missing icon key is visible.
        side = int(min(w, h) * 0.30)
        pygame.draw.rect(screen, col, pygame.Rect(cx - side // 2, cy - side // 2, side, side), width=2)


def draw_transport_icon_button(screen, rect, icon: str, active=False, disabled=False):
    if disabled:
        fill = (25, 30, 32)
        border = (54, 62, 64)
        icon_color = (102, 118, 118)
    else:
        fill = (42, 61, 66) if not active else (50, 122, 104)
        border = (92, 145, 152) if not active else (115, 220, 185)
        icon_color = (226, 246, 242)

    # Flat transport buttons. The earlier translucent top highlight read as
    # accidental frost/ice over the icons, especially on small screenshots.
    pygame.draw.rect(screen, fill, rect, border_radius=7)
    pygame.draw.rect(screen, border, rect, width=1, border_radius=7)

    # A tiny bottom edge gives the button shape without covering the icon area.
    bottom = pygame.Rect(rect.x + 2, rect.bottom - 3, rect.w - 4, 1)
    pygame.draw.rect(screen, (4, 10, 12), bottom, border_radius=1)

    draw_transport_icon(screen, rect, icon, icon_color, disabled=disabled, active=active)

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------


class AudioLabApp:
    def __init__(self):
        pygame.mixer.pre_init(SAMPLE_RATE, -16, CHANNELS, 512)
        pygame.init()
        pygame.display.set_caption("Cube Libre Audio Lab - poor man's four-track beep crimes")
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 15)
        self.small = pygame.font.SysFont("consolas", 13)
        self.big = pygame.font.SysFont("consolas", 21, bold=True)
        self.patch = default_patch()
        self.selected_track = 0
        self.selected_param = "gain"
        self.selected_note_len = 1
        self.selected_velocity = 0.8
        self.status = "ready"
        self.current_sound: Optional[pygame.mixer.Sound] = None
        self.play_channel: Optional[pygame.mixer.Channel] = pygame.mixer.Channel(0)
        self.is_playing = False
        self.is_paused = False
        self.play_started_at = 0.0
        self.pause_started_at = 0.0
        self.pause_total = 0.0
        self.loop_playback = True
        self.transport_cursor_seconds = 0.0
        self.transport_grabber_rect = pygame.Rect(0, 0, 1, 1)
        self.last_render = None
        self.last_export_path: Optional[Path] = None
        self.dragging_slider: Optional[Slider] = None
        self.preview_dirty = True
        self.render_in_progress = False
        self.patch_files: List[Path] = []
        self.patch_file_index = 0
        self.sliders: Dict[str, Slider] = {}
        self.buttons: Dict[str, pygame.Rect] = {}
        self.grid_rect = pygame.Rect(0, 0, 1, 1)
        self.param_x = 850
        self.param_rect = pygame.Rect(835, 104, max(1, WINDOW_W - 835), max(1, WINDOW_H - 104))
        self.track_row_h = 30
        self.step_w = 1
        self.row_h = 1
        self.play_cursor_offset = 0.0
        self.realtime_preview = True
        self.last_realtime_refresh = 0.0
        self.realtime_refresh_interval = 0.12
        self.refresh_patch_files()
        self.load_presets_into_dir_once()

    def refresh_patch_files(self):
        PATCH_DIR.mkdir(parents=True, exist_ok=True)
        self.patch_files = sorted(PATCH_DIR.glob("*.json"))
        self.patch_file_index = int(clamp(self.patch_file_index, 0, max(0, len(self.patch_files) - 1)))

    def load_presets_into_dir_once(self):
        # Install a couple of starter JSONs into the patch dir if it is empty.
        if list(PATCH_DIR.glob("*.json")):
            return
        for name, p in make_builtin_patches().items():
            path = PATCH_DIR / f"{safe_name(name)}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(validate_patch(p), f, indent=2)
        self.refresh_patch_files()

    def stop(self, cursor: float = 0.0):
        if self.play_channel:
            self.play_channel.stop()
        self.is_playing = False
        self.is_paused = False
        self.pause_started_at = 0.0
        self.pause_total = 0.0
        self.play_cursor_offset = 0.0
        self.transport_cursor_seconds = clamp(float(cursor), 0.0, pattern_duration_seconds(self.patch))

    def rewind_to_start(self):
        self.transport_cursor_seconds = 0.0
        if self.is_playing:
            self.play(cursor=0.0, force_render=False)
        else:
            self.play_started_at = time.time()
            self.pause_started_at = 0.0
            self.pause_total = 0.0
            self.is_paused = False
            self.status = "transport at start"

    def skip_to_end(self):
        end = pattern_duration_seconds(self.patch)
        self.stop(cursor=end)
        self.status = "transport at end"

    def toggle_pause(self):
        if not self.is_playing or not self.play_channel:
            self.status = "nothing playing"
            return
        now = time.time()
        if self.is_paused:
            self.play_channel.unpause()
            self.pause_total += max(0.0, now - self.pause_started_at)
            self.pause_started_at = 0.0
            self.is_paused = False
            self.status = "playing"
        else:
            self.play_channel.pause()
            self.pause_started_at = now
            self.is_paused = True
            self.status = "paused"

    def render_current(self):
        self.status = "rendering..."
        pygame.display.set_caption("Cube Libre Audio Lab - rendering...")
        try:
            self.last_render = render_patch(self.patch, include_tail=True)
            # current_sound is a full exported/render-preview sound. Actual transport
            # playback is made from this buffer at the current cursor so tempo/synth
            # edits can be re-applied while the song is running.
            self.current_sound = stereo_to_sound(self.last_render)
            self.status = f"rendered {len(self.last_render) / SAMPLE_RATE:.2f}s"
            self.preview_dirty = False
        except Exception as exc:
            self.status = f"render failed: {exc}"
            print(f"[ERROR] render failed: {exc}", file=sys.stderr)
        finally:
            pygame.display.set_caption("Cube Libre Audio Lab - poor man's four-track beep crimes")

    def playback_sound_from_cursor(self, cursor: float):
        if self.last_render is None:
            return None
        dur = max(0.001, pattern_duration_seconds(self.patch))
        cursor = clamp(float(cursor), 0.0, dur)
        pattern_n = max(1, int(dur * SAMPLE_RATE))
        start = int(clamp(cursor / dur, 0.0, 0.999999) * pattern_n)

        if self.loop_playback:
            # Rotate the exact one-pattern buffer so the mixer can loop it forever
            # while the playhead math still reports the real song position.
            if np is not None and hasattr(self.last_render, "shape"):
                pat = self.last_render[:pattern_n]
                if len(pat) <= 0:
                    return None
                playbuf = np.concatenate((pat[start:], pat[:start]), axis=0)
            else:
                pat = self.last_render[:pattern_n]
                if not pat:
                    return None
                playbuf = pat[start:] + pat[:start]
        else:
            # Non-loop mode keeps the rendered tail after the pattern.
            if np is not None and hasattr(self.last_render, "shape"):
                playbuf = self.last_render[start:]
            else:
                playbuf = self.last_render[start:]
            if len(playbuf) <= 0:
                return None
        return stereo_to_sound(playbuf)

    def play(self, force_render: bool = False, cursor: Optional[float] = None):
        if cursor is None:
            cursor = self.transport_cursor_seconds
            if cursor >= pattern_duration_seconds(self.patch) - 1e-6:
                cursor = 0.0
        cursor = clamp(float(cursor), 0.0, pattern_duration_seconds(self.patch))
        if force_render or self.preview_dirty or self.last_render is None:
            self.render_current()
        sound = self.playback_sound_from_cursor(cursor)
        if not sound:
            return
        if self.play_channel:
            self.play_channel.stop()
        loops = -1 if self.loop_playback else 0
        self.play_channel.play(sound, loops=loops)
        self.is_playing = True
        self.is_paused = False
        self.play_cursor_offset = cursor
        self.transport_cursor_seconds = cursor
        self.play_started_at = time.time()
        self.pause_started_at = 0.0
        self.pause_total = 0.0
        self.status = "playing"

    def apply_live_changes_if_needed(self):
        if not (self.realtime_preview and self.is_playing and not self.is_paused and self.preview_dirty):
            return
        now = time.time()
        if now - self.last_realtime_refresh < self.realtime_refresh_interval:
            return
        cursor = self.current_position_seconds()
        self.render_current()
        self.play(cursor=cursor, force_render=False)
        self.last_realtime_refresh = now
        self.status = f"live-applied @ {format_transport_time(cursor)}"

    def change_bpm(self, delta: int):
        old_step = max(1e-6, step_duration_seconds(self.patch))
        old_pos = self.current_position_seconds()
        old_frac_step = old_pos / old_step
        old_bpm = int(self.patch.get("bpm", DEFAULT_BPM))
        new_bpm = int(clamp(old_bpm + int(delta), 40, 260))
        if new_bpm == old_bpm:
            return
        self.patch["bpm"] = new_bpm
        new_pos = clamp(old_frac_step * step_duration_seconds(self.patch), 0.0, pattern_duration_seconds(self.patch))
        self.transport_cursor_seconds = new_pos
        # While playing, update the timebase immediately so the playhead stays on
        # the same musical step/fraction under the new tempo. The audio buffer is
        # re-rendered/restarted by apply_live_changes_if_needed() on the next tick.
        if self.is_playing:
            now = time.time()
            self.play_cursor_offset = new_pos
            self.play_started_at = now
            self.pause_total = 0.0
            if self.is_paused:
                self.pause_started_at = now
        self.preview_dirty = True
        self.status = f"tempo {new_bpm} BPM"

    def export_wav(self, target_asset: Optional[str] = None, install: bool = False):
        if self.preview_dirty or self.last_render is None:
            self.render_current()
        if self.last_render is None:
            return
        name = safe_name(self.patch.get("name", "cube_libre_patch"))
        if install and target_asset:
            filename = ASSET_TARGETS.get(target_asset, f"{target_asset}.wav")
            path = REAL_SFX_DIR / filename
        else:
            path = EXPORT_DIR / f"{name}_{now_stamp()}.wav"
        try:
            write_wav_stereo(path, self.last_render)
            self.last_export_path = path
            self.status = f"exported {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}"
        except Exception as exc:
            self.status = f"export failed: {exc}"
            print(f"[ERROR] export failed: {exc}", file=sys.stderr)

    def save_patch(self):
        name = safe_name(self.patch.get("name", "cube_libre_patch"))
        path = PATCH_DIR / f"{name}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(validate_patch(self.patch), f, indent=2)
            self.status = f"saved {path.relative_to(REPO_ROOT)}"
            self.refresh_patch_files()
        except Exception as exc:
            self.status = f"save failed: {exc}"

    def save_patch_as(self):
        self.patch["name"] = f"{safe_name(self.patch.get('name','patch'))}_{now_stamp()}"
        self.save_patch()

    def load_selected_patch(self):
        if not self.patch_files:
            self.status = "no patches found"
            return
        path = self.patch_files[self.patch_file_index]
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.patch = validate_patch(json.load(f))
            self.selected_track = int(clamp(self.selected_track, 0, TRACKS - 1))
            self.status = f"loaded {path.name}"
            self.preview_dirty = True
            self.stop()
        except Exception as exc:
            self.status = f"load failed: {exc}"

    def duplicate_track(self):
        tr = copy.deepcopy(self.patch["tracks"][self.selected_track])
        tr["name"] = tr.get("name", "track") + " copy"
        self.patch["tracks"][self.selected_track] = tr
        self.preview_dirty = True
        self.status = f"duplicated track {self.selected_track + 1}"

    def clear_track_notes(self):
        self.patch["tracks"][self.selected_track]["notes"] = []
        self.preview_dirty = True
        self.status = f"cleared notes on track {self.selected_track + 1}"

    def randomize_track_params(self):
        tr = self.patch["tracks"][self.selected_track]
        p = tr["params"]
        p["wave"] = random.choice(WAVEFORMS)
        p["gain"] = round(random.uniform(0.16, 0.65), 2)
        p["attack"] = round(random.uniform(0.001, 0.04), 3)
        p["decay"] = round(random.uniform(0.02, 0.25), 3)
        p["sustain"] = round(random.uniform(0.05, 0.95), 2)
        p["release"] = round(random.uniform(0.04, 0.55), 3)
        p["fm_ratio"] = round(random.uniform(0.25, 8.0), 2)
        p["fm_index"] = round(random.uniform(0.0, 9.0), 2)
        p["noise"] = round(random.uniform(0.0, 0.8), 2)
        p["detune"] = round(random.uniform(-35.0, 35.0), 1)
        p["pan"] = round(random.uniform(-0.85, 0.85), 2)
        p["cutoff"] = round(random.uniform(0.12, 1.0), 2)
        p["drive"] = round(random.uniform(0.0, 0.8), 2)
        self.preview_dirty = True
        self.status = f"randomized track {self.selected_track + 1} params"

    def randomize_pattern(self):
        tr = self.patch["tracks"][self.selected_track]
        tr["notes"] = []
        root = random.choice([36, 38, 41, 43, 48, 50, 55, 60])
        scale = [0, 2, 3, 5, 7, 10]
        density = random.uniform(0.18, 0.38)
        for s in range(STEPS):
            if random.random() < density:
                midi = root + random.choice(scale) + random.choice([0, 12, 24])
                midi = int(clamp(midi, BASE_MIDI, BASE_MIDI + ROWS - 1))
                add_note(self.patch, self.selected_track, s, midi, random.choice([1, 1, 1, 2, 4]), random.uniform(0.35, 0.9))
        self.preview_dirty = True
        self.status = f"randomized track {self.selected_track + 1} pattern"

    def transpose_track(self, semitones: int):
        for n in self.patch["tracks"][self.selected_track]["notes"]:
            n["midi"] = int(clamp(int(n.get("midi", BASE_MIDI)) + semitones, BASE_MIDI, BASE_MIDI + ROWS - 1))
        self.preview_dirty = True

    def resize(self, size):
        global WINDOW_W, WINDOW_H
        WINDOW_W, WINDOW_H = max(1000, size[0]), max(700, size[1])
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)

    def current_params(self):
        return self.patch["tracks"][self.selected_track]["params"]

    def set_param_value(self, name: str, value):
        p = self.current_params()
        if name == "wave":
            p["wave"] = str(value)
        else:
            p[name] = float(value)
        self.preview_dirty = True

    def cycle_wave(self, delta: int):
        p = self.current_params()
        cur = p.get("wave", "sine")
        idx = WAVEFORMS.index(cur) if cur in WAVEFORMS else 0
        p["wave"] = WAVEFORMS[(idx + delta) % len(WAVEFORMS)]
        self.preview_dirty = True

    def current_position_seconds(self) -> float:
        dur = pattern_duration_seconds(self.patch)
        if not self.is_playing:
            return clamp(float(self.transport_cursor_seconds), 0.0, dur)
        now = self.pause_started_at if self.is_paused else time.time()
        elapsed = max(0.0, now - self.play_started_at - self.pause_total)
        pos = self.play_cursor_offset + elapsed
        if self.loop_playback and dur > 1e-9:
            pos = pos % dur
        else:
            pos = clamp(pos, 0.0, dur)
        return pos

    def current_step(self):
        pos = self.current_position_seconds()
        if not self.is_playing and pos <= 0.0:
            return -1
        step_sec = step_duration_seconds(self.patch)
        steps = int(self.patch.get("steps", STEPS))
        if not self.is_playing and pos >= pattern_duration_seconds(self.patch) - 1e-6:
            return steps - 1
        return int(pos / max(1e-6, step_sec)) % steps

    def layout(self):
        self.buttons = {}
        top = 10
        left = 12
        btn_h = 30
        gap = 8

        # Clean icon transport strip: tape-deck/DAW buttons, not text soup.
        transport_btn_h = 34
        transport_btn_w = 42
        icon_gap = 5
        self.transport_grabber_rect = pygame.Rect(left, top, 18, transport_btn_h)
        x = self.transport_grabber_rect.right + icon_gap
        transport_defs = [
            ("transport_pause", transport_btn_w),
            ("transport_play", transport_btn_w),
            ("transport_stop", transport_btn_w),
            ("transport_home", transport_btn_w),
            ("transport_end", transport_btn_w),
            ("transport_rec", transport_btn_w),  # placeholder; recording is intentionally not wired yet
            ("transport_render", transport_btn_w + 8),
            ("transport_loop", transport_btn_w + 8),
        ]
        for key, width in transport_defs:
            self.buttons[key] = pygame.Rect(x, top, width, transport_btn_h)
            x += width + icon_gap

        # Tempo belongs in the transport, not buried at the bottom. The counter is
        # clickable; the small stacked arrows are dedicated fine adjust buttons.
        x += 8
        self.buttons["bpm_display"] = pygame.Rect(x, top, 88, transport_btn_h)
        self.buttons["bpm_up"] = pygame.Rect(x + 92, top, 24, transport_btn_h // 2 - 1)
        self.buttons["bpm_down"] = pygame.Rect(x + 92, top + transport_btn_h // 2 + 1, 24, transport_btn_h // 2 - 1)
        x += 122

        # File buttons stay on the top row; edit/random actions go on row two.
        file_widths = [("save", 66), ("saveas", 78), ("load", 66), ("export", 96)]
        file_total = sum(w for _k, w in file_widths) + gap * (len(file_widths) - 1)
        fx = WINDOW_W - 12 - file_total
        for key, width in file_widths:
            self.buttons[key] = pygame.Rect(fx, top, width, btn_h)
            fx += width + gap

        action_y = top + 40
        self.buttons["random_params"] = pygame.Rect(12, action_y, 112, 26)
        self.buttons["random_pattern"] = pygame.Rect(132, action_y, 116, 26)
        self.buttons["clear_track"] = pygame.Rect(256, action_y, 92, 26)
        self.buttons["prev_patch"] = pygame.Rect(370, action_y, 30, 26)
        self.buttons["next_patch"] = pygame.Rect(406, action_y, 30, 26)
        self.buttons["asset_export"] = pygame.Rect(WINDOW_W - 184, action_y, 172, 26)

        track_top = 78
        for i in range(TRACKS):
            self.buttons[f"track_{i}"] = pygame.Rect(12 + i * 155, track_top, 145, 30)
            self.buttons[f"mute_{i}"] = pygame.Rect(625 + i * 48, track_top, 42, 30)
            self.buttons[f"solo_{i}"] = pygame.Rect(820 + i * 48, track_top, 42, 30)

        # Keep the right synth inspector in its own lane. This fixes the old fixed
        # x=850 spill where the inspector/status text could draw on top of the
        # piano roll or title when the window was resized.
        right_panel_w = 430
        param_x = max(570, WINDOW_W - right_panel_w)
        self.param_x = param_x
        self.param_rect = pygame.Rect(param_x - 10, 104, WINDOW_W - param_x + 10, WINDOW_H - 104)
        param_y = 164
        self.sliders = {}
        p = self.current_params()
        slider_defs = [
            ("gain", 0.0, 1.0, 0.01),
            ("attack", 0.001, 0.20, 0.001),
            ("decay", 0.001, 0.60, 0.001),
            ("sustain", 0.0, 1.0, 0.01),
            ("release", 0.005, 1.20, 0.005),
            ("fm_ratio", 0.10, 12.0, 0.01),
            ("fm_index", 0.0, 16.0, 0.01),
            ("noise", 0.0, 1.0, 0.01),
            ("detune", -120.0, 120.0, 0.5),
            ("pan", -1.0, 1.0, 0.01),
            ("cutoff", 0.01, 1.0, 0.01),
            ("drive", 0.0, 1.0, 0.01),
        ]
        for i, (name, lo, hi, step) in enumerate(slider_defs):
            rect = pygame.Rect(param_x + 90, param_y + i * 38 + 6, max(180, WINDOW_W - param_x - 118), 13)
            self.sliders[name] = Slider(name, rect, lo, hi, float(p.get(name, 0.0)), step)

        grid_left = 70
        grid_top = 136
        grid_right = param_x - 18
        grid_bottom = WINDOW_H - 130
        self.grid_rect = pygame.Rect(grid_left, grid_top, max(200, grid_right - grid_left), max(280, grid_bottom - grid_top))
        self.step_w = self.grid_rect.w / int(self.patch.get("steps", STEPS))
        self.row_h = self.grid_rect.h / int(self.patch.get("rows", ROWS))

        bottom_y = WINDOW_H - 112
        self.buttons["len_minus"] = pygame.Rect(70, bottom_y, 34, 26)
        self.buttons["len_plus"] = pygame.Rect(110, bottom_y, 34, 26)
        self.buttons["vel_minus"] = pygame.Rect(210, bottom_y, 34, 26)
        self.buttons["vel_plus"] = pygame.Rect(250, bottom_y, 34, 26)
        self.buttons["base_down"] = pygame.Rect(355, bottom_y, 34, 26)
        self.buttons["base_up"] = pygame.Rect(395, bottom_y, 34, 26)

    def note_at_mouse(self, pos) -> Optional[Tuple[int, int]]:
        if not self.grid_rect.collidepoint(pos):
            return None
        x, y = pos
        step = int((x - self.grid_rect.x) / max(1e-6, self.step_w))
        row = int((y - self.grid_rect.y) / max(1e-6, self.row_h))
        step = int(clamp(step, 0, int(self.patch.get("steps", STEPS)) - 1))
        rows = int(self.patch.get("rows", ROWS))
        midi = int(self.patch.get("base_midi", BASE_MIDI)) + (rows - 1 - row)
        midi = int(clamp(midi, self.patch.get("base_midi", BASE_MIDI), self.patch.get("base_midi", BASE_MIDI) + rows - 1))
        return step, midi

    def toggle_note(self, step: int, midi: int, erase: bool = False):
        tr = self.selected_track
        if erase:
            removed = remove_note_at(self.patch, tr, step, midi)
            if removed:
                self.preview_dirty = True
            return
        existing = find_note_at(self.patch, tr, step, midi)
        if existing:
            self.patch["tracks"][tr]["notes"].remove(existing)
        else:
            add_note(self.patch, tr, step, midi, self.selected_note_len, self.selected_velocity)
        self.preview_dirty = True

    def handle_button(self, key: str):
        if key in ("play", "transport_play"):
            self.play()
        elif key in ("stop", "transport_stop"):
            self.stop(); self.status = "stopped"
        elif key == "transport_home":
            self.rewind_to_start()
        elif key == "transport_end":
            self.skip_to_end()
        elif key == "transport_pause":
            self.toggle_pause()
        elif key == "transport_rec":
            self.status = "REC is a placeholder for later; no recording wired yet"
        elif key in ("render", "transport_render"):
            self.render_current()
        elif key == "save":
            self.save_patch()
        elif key == "saveas":
            self.save_patch_as()
        elif key == "load":
            self.load_selected_patch()
        elif key == "export":
            self.export_wav()
        elif key in ("loop", "transport_loop"):
            self.loop_playback = not self.loop_playback
            self.status = f"loop {'on' if self.loop_playback else 'off'}"
        elif key == "random_params":
            self.randomize_track_params()
        elif key == "random_pattern":
            self.randomize_pattern()
        elif key == "clear_track":
            self.clear_track_notes()
        elif key == "prev_patch":
            if self.patch_files:
                self.patch_file_index = (self.patch_file_index - 1) % len(self.patch_files)
                self.status = f"selected {self.patch_files[self.patch_file_index].name}"
        elif key == "next_patch":
            if self.patch_files:
                self.patch_file_index = (self.patch_file_index + 1) % len(self.patch_files)
                self.status = f"selected {self.patch_files[self.patch_file_index].name}"
        elif key == "asset_export":
            # Safer than overwriting blindly: export a copy using the first asset target name as an example.
            self.status = "asset install disabled in GUI; use exported WAV manually or patch the filename on purpose"
        elif key == "len_minus":
            self.selected_note_len = int(clamp(self.selected_note_len - 1, 1, 16))
        elif key == "len_plus":
            self.selected_note_len = int(clamp(self.selected_note_len + 1, 1, 16))
        elif key == "vel_minus":
            self.selected_velocity = round(clamp(self.selected_velocity - 0.05, 0.05, 1.0), 2)
        elif key == "vel_plus":
            self.selected_velocity = round(clamp(self.selected_velocity + 0.05, 0.05, 1.0), 2)
        elif key == "bpm_down":
            self.change_bpm(-1)
        elif key == "bpm_up":
            self.change_bpm(1)
        elif key == "bpm_display":
            self.change_bpm(1)
        elif key == "base_down":
            self.patch["base_midi"] = int(clamp(int(self.patch.get("base_midi", BASE_MIDI)) - 12, 12, 84)); self.preview_dirty = True
        elif key == "base_up":
            self.patch["base_midi"] = int(clamp(int(self.patch.get("base_midi", BASE_MIDI)) + 12, 12, 84)); self.preview_dirty = True
        elif key.startswith("track_"):
            self.selected_track = int(key.split("_")[1])
        elif key.startswith("mute_"):
            i = int(key.split("_")[1]); self.patch["tracks"][i]["muted"] = not self.patch["tracks"][i].get("muted"); self.preview_dirty = True
        elif key.startswith("solo_"):
            i = int(key.split("_")[1]); self.patch["tracks"][i]["solo"] = not self.patch["tracks"][i].get("solo"); self.preview_dirty = True

    def handle_keydown(self, event):
        mods = pygame.key.get_mods()
        ctrl = bool(mods & pygame.KMOD_CTRL)
        shift = bool(mods & pygame.KMOD_SHIFT)
        key = event.key
        if key == pygame.K_ESCAPE:
            raise SystemExit
        if key == pygame.K_SPACE:
            self.play()
        elif key == pygame.K_RETURN:
            self.render_current()
        elif key == pygame.K_s and ctrl:
            self.save_patch()
        elif key == pygame.K_o and ctrl:
            self.load_selected_patch()
        elif key == pygame.K_e and ctrl:
            self.export_wav()
        elif key == pygame.K_n and ctrl:
            self.patch = default_patch(); self.preview_dirty = True; self.status = "new default patch"
        elif key == pygame.K_BACKSPACE:
            self.clear_track_notes()
        elif pygame.K_1 <= key <= pygame.K_4:
            self.selected_track = key - pygame.K_1
        elif key == pygame.K_TAB:
            self.selected_track = (self.selected_track + ( -1 if shift else 1)) % TRACKS
        elif key == pygame.K_LEFTBRACKET:
            self.cycle_wave(-1)
        elif key == pygame.K_RIGHTBRACKET:
            self.cycle_wave(1)
        elif key == pygame.K_r and not ctrl:
            self.randomize_track_params()
        elif key == pygame.K_p and not ctrl:
            self.randomize_pattern()
        elif key == pygame.K_UP:
            self.transpose_track(12 if shift else 1)
        elif key == pygame.K_DOWN:
            self.transpose_track(-(12 if shift else 1))
        elif key == pygame.K_MINUS:
            self.selected_note_len = int(clamp(self.selected_note_len - 1, 1, 16))
        elif key == pygame.K_EQUALS:
            self.selected_note_len = int(clamp(self.selected_note_len + 1, 1, 16))
        elif key == pygame.K_COMMA:
            self.change_bpm(-5 if shift else -1)
        elif key == pygame.K_PERIOD:
            self.change_bpm(5 if shift else 1)

    def handle_mouse_down(self, event):
        pos = event.pos
        mods = pygame.key.get_mods()
        bpm_step = 5 if (mods & pygame.KMOD_SHIFT) else 1
        for key, rect in self.buttons.items():
            if rect.collidepoint(pos):
                if key == "bpm_display":
                    self.change_bpm(-bpm_step if event.button == 3 else bpm_step)
                elif key == "bpm_up":
                    self.change_bpm(bpm_step)
                elif key == "bpm_down":
                    self.change_bpm(-bpm_step)
                else:
                    self.handle_button(key)
                return
        for name, slider in self.sliders.items():
            if slider.rect.inflate(0, 12).collidepoint(pos):
                self.dragging_slider = slider
                slider.set_from_x(pos[0])
                self.set_param_value(name, slider.value)
                return
        n = self.note_at_mouse(pos)
        if n:
            self.toggle_note(n[0], n[1], erase=(event.button == 3))

    def handle_mouse_motion(self, event):
        if self.dragging_slider:
            self.dragging_slider.set_from_x(event.pos[0])
            self.set_param_value(self.dragging_slider.name, self.dragging_slider.value)

    def handle_mouse_up(self, event):
        self.dragging_slider = None

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise SystemExit
            if event.type == pygame.VIDEORESIZE:
                self.resize(event.size)
            elif event.type == pygame.KEYDOWN:
                self.handle_keydown(event)
            elif event.type == pygame.MOUSEWHEEL:
                pos = pygame.mouse.get_pos()
                bpm_rects = [self.buttons.get(k) for k in ("bpm_display", "bpm_up", "bpm_down")]
                if any(r and r.collidepoint(pos) for r in bpm_rects):
                    step = 5 if (pygame.key.get_mods() & pygame.KMOD_SHIFT) else 1
                    self.change_bpm(int(event.y) * step)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self.handle_mouse_down(event)
            elif event.type == pygame.MOUSEMOTION:
                self.handle_mouse_motion(event)
            elif event.type == pygame.MOUSEBUTTONUP:
                self.handle_mouse_up(event)


    def draw_bpm_widget(self):
        display = self.buttons.get("bpm_display")
        up = self.buttons.get("bpm_up")
        down = self.buttons.get("bpm_down")
        if not display or not up or not down:
            return
        bpm = int(self.patch.get("bpm", DEFAULT_BPM))
        pygame.draw.rect(self.screen, (12, 22, 25), display, border_radius=6)
        pygame.draw.rect(self.screen, (92, 145, 152), display, width=1, border_radius=6)
        txt = self.font.render(f"{bpm:03d} BPM", True, (226, 246, 242))
        self.screen.blit(txt, (display.centerx - txt.get_width() // 2, display.centery - txt.get_height() // 2))
        for rect, direction in ((up, 1), (down, -1)):
            pygame.draw.rect(self.screen, (34, 52, 56), rect, border_radius=4)
            pygame.draw.rect(self.screen, (82, 132, 138), rect, width=1, border_radius=4)
            if direction > 0:
                pts = [(rect.centerx, rect.y + 4), (rect.x + 6, rect.bottom - 5), (rect.right - 6, rect.bottom - 5)]
            else:
                pts = [(rect.centerx, rect.bottom - 4), (rect.x + 6, rect.y + 5), (rect.right - 6, rect.y + 5)]
            pygame.draw.polygon(self.screen, (210, 242, 236), pts)

    def draw_transport(self):
        keys = [
            "transport_pause", "transport_play", "transport_stop", "transport_home",
            "transport_end", "transport_rec", "transport_render", "transport_loop",
            "bpm_display", "bpm_up", "bpm_down",
        ]
        rects = [self.buttons[k] for k in keys if k in self.buttons]
        if not rects:
            return
        panel = self.transport_grabber_rect.unionall(rects) if rects else self.transport_grabber_rect.copy()
        panel.inflate_ip(12, 10)
        pygame.draw.rect(self.screen, (6, 11, 14), panel, border_radius=8)
        pygame.draw.rect(self.screen, (70, 118, 124), panel, width=1, border_radius=8)
        draw_transport_grabber(self.screen, self.transport_grabber_rect)

        icons = {
            "transport_pause": "pause",
            "transport_play": "play",
            "transport_stop": "stop",
            "transport_home": "home",
            "transport_end": "end",
            "transport_rec": "rec",
            "transport_render": "render",
            "transport_loop": "loop",
        }
        for key in [k for k in keys if k.startswith("transport_")]:
            rect = self.buttons.get(key)
            if not rect:
                continue
            draw_transport_icon_button(
                self.screen, rect, icons[key],
                active=(
                    (key == "transport_play" and self.is_playing and not self.is_paused) or
                    (key == "transport_pause" and self.is_paused) or
                    (key == "transport_loop" and self.loop_playback)
                ),
                disabled=(key == "transport_rec"),
            )

        self.draw_bpm_widget()

        dur = pattern_duration_seconds(self.patch)
        pos = self.current_position_seconds()
        step = self.current_step()
        if step < 0:
            step_text = "--"
        else:
            step_text = f"{step + 1:02d}/{int(self.patch.get('steps', STEPS)):02d}"
        state = "PAUSED" if self.is_paused else ("PLAYING" if self.is_playing else "STOPPED")
        info = f"{state}  {format_transport_time(pos)} / {format_transport_time(dur)}  step {step_text}"
        info_x = panel.right + 12
        file_left = self.buttons.get("save", pygame.Rect(WINDOW_W, 0, 0, 0)).x
        max_w = max(0, file_left - info_x - 10)
        txt = self.small.render(info, True, (168, 205, 205))
        if txt.get_width() > max_w:
            info = f"{state} {format_transport_time(pos)}"
            txt = self.small.render(info, True, (168, 205, 205))
        if txt.get_width() <= max_w:
            self.screen.blit(txt, (info_x, panel.y + 9))

    def draw_top(self):
        self.draw_transport()
        for key, rect in self.buttons.items():
            if (key.startswith("transport_") or key.startswith("track_") or key.startswith("mute_") or
                    key.startswith("solo_") or key in ("len_minus", "len_plus", "vel_minus", "vel_plus",
                    "bpm_display", "bpm_up", "bpm_down", "base_down", "base_up")):
                continue
            label = {
                "save": "SAVE", "saveas": "SAVE AS", "load": "LOAD", "export": "EXPORT WAV",
                "random_params": "RAND SOUND", "random_pattern": "RAND NOTES", "clear_track": "CLEAR TRK",
                "prev_patch": "<", "next_patch": ">", "asset_export": "INSTALL DISABLED",
            }.get(key, key)
            draw_button(self.screen, rect, label, self.small, danger=(key in ("clear_track", "asset_export")), disabled=(key == "asset_export"))

        title = self.big.render("CUBE LIBRE AUDIO LAB", True, (235, 255, 255))
        self.screen.blit(title, (12, 112))
        patch_name = self.patch.get("name", "patch")
        pfile = self.patch_files[self.patch_file_index].name if self.patch_files else "no json patches"
        dirty = "*" if self.preview_dirty else ""
        transport = f"{format_transport_time(self.current_position_seconds())}/{format_transport_time(pattern_duration_seconds(self.patch))}"
        text = self.small.render(f"patch: {patch_name}{dirty}   file: {pfile}   transport: {transport}   status: {self.status}", True, (180, 215, 218))
        old_clip = self.screen.get_clip()
        self.screen.set_clip(pygame.Rect(250, 110, max(20, self.param_rect.x - 260), 26))
        self.screen.blit(text, (250, 116))
        self.screen.set_clip(old_clip)

    def draw_tracks(self):
        for i in range(TRACKS):
            tr = self.patch["tracks"][i]
            rect = self.buttons[f"track_{i}"]
            label = f"{i+1}: {tr.get('name','track')}"
            draw_button(self.screen, rect, label, self.small, active=(i == self.selected_track))
            draw_button(self.screen, self.buttons[f"mute_{i}"], "M", self.small, active=tr.get("muted"), danger=tr.get("muted"))
            draw_button(self.screen, self.buttons[f"solo_{i}"], "S", self.small, active=tr.get("solo"))

    def draw_grid(self):
        gr = self.grid_rect
        pygame.draw.rect(self.screen, (8, 12, 15), gr)
        pygame.draw.rect(self.screen, (80, 135, 140), gr, width=1)
        steps = int(self.patch.get("steps", STEPS))
        rows = int(self.patch.get("rows", ROWS))
        base = int(self.patch.get("base_midi", BASE_MIDI))

        # Grid lines + bar accents.
        for s in range(steps + 1):
            x = gr.x + s * self.step_w
            if s % 8 == 0:
                col = (80, 130, 138)
                w = 2
            elif s % 4 == 0:
                col = (55, 85, 90)
                w = 1
            else:
                col = (30, 48, 52)
                w = 1
            pygame.draw.line(self.screen, col, (x, gr.y), (x, gr.bottom), w)
        for r in range(rows + 1):
            y = gr.y + r * self.row_h
            midi = base + (rows - r)
            is_c = midi % 12 == 0
            col = (45, 70, 74) if is_c else (25, 40, 44)
            pygame.draw.line(self.screen, col, (gr.x, y), (gr.right, y), 1)

        # Note names left.
        for r in range(rows):
            midi = base + (rows - 1 - r)
            if midi % 12 == 0 or r == rows - 1:
                txt = self.small.render(note_name(midi), True, (145, 180, 180))
                self.screen.blit(txt, (12, gr.y + r * self.row_h + self.row_h * 0.5 - txt.get_height() * 0.5))

        # Playhead.
        st = self.current_step()
        if st >= 0:
            x = gr.x + st * self.step_w
            pygame.draw.rect(self.screen, (190, 240, 230, 46), (x, gr.y, self.step_w, gr.h))

        # Draw all tracks faintly, selected track strongly.
        colors = [(255, 90, 75), (90, 200, 255), (255, 210, 80), (150, 255, 120)]
        for ti, tr in enumerate(self.patch["tracks"]):
            alpha_selected = ti == self.selected_track
            col = colors[ti % len(colors)]
            for n in tr.get("notes", []):
                step = int(n.get("step", 0))
                midi = int(n.get("midi", base))
                length = int(n.get("length", 1))
                if not (base <= midi <= base + rows - 1):
                    continue
                row = rows - 1 - (midi - base)
                x = gr.x + step * self.step_w + 2
                y = gr.y + row * self.row_h + 2
                w = max(3, self.step_w * length - 4)
                h = max(3, self.row_h - 4)
                if alpha_selected:
                    fill = col
                    border = (255, 255, 255)
                else:
                    fill = tuple(int(c * 0.35) for c in col)
                    border = tuple(int(c * 0.55) for c in col)
                rect = pygame.Rect(int(x), int(y), int(w), int(h))
                pygame.draw.rect(self.screen, fill, rect, border_radius=3)
                pygame.draw.rect(self.screen, border, rect, width=1, border_radius=3)

    def draw_params(self):
        x = self.param_x
        y = 114
        tr = self.patch["tracks"][self.selected_track]
        params = tr["params"]
        old_clip = self.screen.get_clip()
        self.screen.set_clip(self.param_rect.inflate(-4, -4))
        title = self.big.render(f"TRACK {self.selected_track+1} SYNTH", True, (235, 255, 255))
        self.screen.blit(title, (x, y))
        wave = params.get("wave", "sine")
        wtxt = self.font.render(f"wave: [{wave}]  ([ / ] cycles)", True, (220, 235, 235))
        self.screen.blit(wtxt, (x, y + 28))

        # Sync slider values from params before drawing.
        for name, slider in self.sliders.items():
            slider.value = float(params.get(name, slider.value))
            label = self.small.render(f"{name:>8} {slider.value:>7.3f}", True, (185, 215, 215))
            self.screen.blit(label, (x, slider.rect.y - 5))
            pygame.draw.rect(self.screen, (25, 42, 46), slider.rect, border_radius=4)
            pygame.draw.rect(self.screen, (70, 110, 115), slider.rect, width=1, border_radius=4)
            kx = slider.rect.x + slider.normalized() * slider.rect.w
            pygame.draw.circle(self.screen, (220, 245, 240), (int(kx), slider.rect.centery), 7)
            pygame.draw.line(self.screen, (90, 210, 190), (slider.rect.x, slider.rect.centery), (int(kx), slider.rect.centery), 3)

        help_lines = [
            "mouse L: toggle note   mouse R: erase note",
            "BPM: click counter / arrows / wheel; shift = x5",
            "space play   enter render   ctrl+s save   ctrl+e export",
            "1-4 track   tab next   [/] waveform   R/P randomize",
            "JSON: tools/audio_lab_patches/",
            "WAV:  tools/audio_lab_exports/",
        ]
        slider_bottom = max((sl.rect.bottom for sl in self.sliders.values()), default=y + 60)
        hy = slider_bottom + 18
        max_lines = max(0, (WINDOW_H - hy - 16) // 18)
        for line in help_lines[:max_lines]:
            s = self.small.render(line, True, (150, 185, 185))
            self.screen.blit(s, (x, hy)); hy += 18
        self.screen.set_clip(old_clip)

    def draw_bottom(self):
        y = WINDOW_H - 112
        draw_button(self.screen, self.buttons["len_minus"], "-", self.small)
        draw_button(self.screen, self.buttons["len_plus"], "+", self.small)
        draw_button(self.screen, self.buttons["vel_minus"], "-", self.small)
        draw_button(self.screen, self.buttons["vel_plus"], "+", self.small)
        draw_button(self.screen, self.buttons["base_down"], "-", self.small)
        draw_button(self.screen, self.buttons["base_up"], "+", self.small)
        items = [
            (12, f"note len: {self.selected_note_len} step(s)"),
            (160, f"velocity: {self.selected_velocity:.2f}"),
            (300, f"base: {note_name(int(self.patch.get('base_midi', BASE_MIDI)))}"),
            (470, f"selected track notes: {len(self.patch['tracks'][self.selected_track].get('notes', []))}"),
        ]
        for x, txt in items:
            s = self.font.render(txt, True, (195, 225, 225))
            self.screen.blit(s, (x, y + 35))

        p = self.patch.get("name", "")
        s = self.small.render("Rename by editing the JSON 'name' field for now. Text input can come later; this is the first non-dumb version.", True, (135, 165, 165))
        self.screen.blit(s, (12, WINDOW_H - 32))

    def draw_waveform(self):
        # Tiny waveform preview under grid.
        if self.last_render is None:
            return
        rect = pygame.Rect(self.grid_rect.x, WINDOW_H - 88, self.grid_rect.w, 46)
        pygame.draw.rect(self.screen, (8, 12, 15), rect)
        pygame.draw.rect(self.screen, (60, 100, 105), rect, width=1)
        if np is not None and hasattr(self.last_render, "shape"):
            data = self.last_render[:, 0]
            if len(data) <= 0:
                return
            idxs = np.linspace(0, len(data) - 1, rect.w).astype(int)
            vals = data[idxs]
        else:
            if not self.last_render:
                return
            step = max(1, len(self.last_render) // rect.w)
            vals = [self.last_render[i][0] for i in range(0, len(self.last_render), step)][:rect.w]
        mid = rect.centery
        pts = []
        for i, v in enumerate(vals):
            pts.append((rect.x + i, int(mid - float(v) * rect.h * 0.45)))
        if len(pts) >= 2:
            pygame.draw.lines(self.screen, (100, 220, 205), False, pts, 1)

    def draw(self):
        self.layout()
        self.screen.fill((3, 5, 7))
        # Background scan-ish panels. The right inspector gets a real panel and
        # clipping lane so its text never paints over the piano roll/status area.
        pygame.draw.rect(self.screen, (7, 13, 16), (0, 0, WINDOW_W, 132))
        pygame.draw.rect(self.screen, (7, 13, 16), self.param_rect)
        pygame.draw.line(self.screen, (45, 82, 88), (self.param_rect.x, self.param_rect.y), (self.param_rect.x, WINDOW_H), 1)
        self.draw_top()
        self.draw_tracks()
        self.draw_grid()
        self.draw_waveform()
        self.draw_params()
        self.draw_bottom()
        pygame.display.flip()

    def run(self):
        while True:
            self.handle_events()
            self.apply_live_changes_if_needed()
            self.draw()
            self.clock.tick(FPS)

# -----------------------------------------------------------------------------
# Built-in starter patches
# -----------------------------------------------------------------------------


def make_builtin_patches() -> Dict[str, dict]:
    patches = {}

    p = default_patch()
    p["name"] = "laser-grid-acid-sketch"
    p["bpm"] = 128
    p["tracks"][0]["params"].update({"wave": "fm", "fm_ratio": 3.0, "fm_index": 5.5, "gain": 0.42, "decay": 0.045, "sustain": 0.12, "release": 0.09, "drive": 0.3})
    p["tracks"][1]["params"].update({"wave": "ring", "fm_ratio": 1.5, "gain": 0.28, "release": 0.18, "pan": 0.45})
    p["tracks"][2]["params"].update({"wave": "noise", "gain": 0.18, "attack": 0.001, "decay": 0.02, "sustain": 0.05, "release": 0.05, "cutoff": 0.35})
    p["tracks"][3]["params"].update({"wave": "pluck", "gain": 0.30, "release": 0.55, "pan": -0.2})
    patches[p["name"]] = p

    q = default_patch()
    q["name"] = "portal-woowoo-pad-sketch"
    q["bpm"] = 72
    for tr in q["tracks"]:
        tr["notes"] = []
    q["tracks"][0]["params"].update({"wave": "sine", "gain": 0.38, "attack": 0.08, "decay": 0.30, "sustain": 0.78, "release": 0.70, "pan": -0.35})
    q["tracks"][1]["params"].update({"wave": "triangle", "gain": 0.30, "attack": 0.04, "decay": 0.22, "sustain": 0.70, "release": 0.80, "detune": 7.0, "pan": 0.35})
    q["tracks"][2]["params"].update({"wave": "fm", "gain": 0.22, "fm_ratio": 0.5, "fm_index": 1.8, "attack": 0.12, "release": 0.95, "pan": 0.0})
    q["tracks"][3]["params"].update({"wave": "noise", "gain": 0.10, "attack": 0.30, "decay": 0.50, "sustain": 0.5, "release": 1.0, "cutoff": 0.16})
    for s in [0, 8, 16, 24]:
        add_note(q, 0, s, 48, 8, 0.65)
        add_note(q, 1, s, 55, 8, 0.55)
        add_note(q, 2, s + 2 if s + 2 < STEPS else s, 67, 6, 0.45)
    add_note(q, 3, 0, 72, 32, 0.7)
    patches[q["name"]] = q

    r = default_patch()
    r["name"] = "collapse-noise-pulse-sketch"
    r["bpm"] = 96
    for tr in r["tracks"]:
        tr["notes"] = []
    r["tracks"][0]["params"].update({"wave": "noise", "gain": 0.55, "attack": 0.001, "decay": 0.08, "sustain": 0.05, "release": 0.14, "cutoff": 0.26, "drive": 0.45, "pan": -0.10})
    r["tracks"][1]["params"].update({"wave": "fm", "gain": 0.42, "fm_ratio": 0.25, "fm_index": 7.0, "attack": 0.001, "decay": 0.18, "sustain": 0.08, "release": 0.35, "drive": 0.35, "pan": 0.15})
    r["tracks"][2]["params"].update({"wave": "square", "gain": 0.20, "attack": 0.001, "decay": 0.03, "sustain": 0.01, "release": 0.04, "drive": 0.6})
    r["tracks"][3]["params"].update({"wave": "sine", "gain": 0.38, "attack": 0.001, "decay": 0.14, "sustain": 0.20, "release": 0.42, "detune": -12.0})
    for s in [0, 1, 2, 4, 8, 12, 16, 20, 24, 28]:
        add_note(r, 0, s, 72, 1, 0.6)
    for s in [0, 8, 16, 24]:
        add_note(r, 1, s, 36, 2, 0.9)
        add_note(r, 3, s, 24 + (s // 8) * 5, 4, 0.75)
    for s in range(0, 32, 2):
        add_note(r, 2, s, 84, 1, 0.35)
    patches[r["name"]] = r

    return patches

# -----------------------------------------------------------------------------
# CLI helpers
# -----------------------------------------------------------------------------


def render_json_to_wav(json_path: Path, wav_path: Optional[Path] = None):
    with open(json_path, "r", encoding="utf-8") as f:
        patch = validate_patch(json.load(f))
    audio = render_patch(patch, include_tail=True)
    if wav_path is None:
        wav_path = EXPORT_DIR / f"{safe_name(patch.get('name', json_path.stem))}_{now_stamp()}.wav"
    write_wav_stereo(wav_path, audio)
    print(wav_path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--render" in argv:
        i = argv.index("--render")
        try:
            json_path = Path(argv[i + 1]).expanduser().resolve()
        except Exception:
            print("usage: cube_libre_audio_lab.py --render patch.json [out.wav]", file=sys.stderr)
            return 2
        wav_path = None
        if len(argv) > i + 2:
            wav_path = Path(argv[i + 2]).expanduser().resolve()
        render_json_to_wav(json_path, wav_path)
        return 0
    if "--make-presets" in argv:
        PATCH_DIR.mkdir(parents=True, exist_ok=True)
        for name, p in make_builtin_patches().items():
            path = PATCH_DIR / f"{safe_name(name)}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(validate_patch(p), f, indent=2)
            print(path)
        return 0
    app = AudioLabApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
