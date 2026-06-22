#!/usr/bin/env python3
#
# cube_libre_audio_generator.py
#
# Standalone procedural audio helper/lab for Cube Libre.
# Drop this in: tools/cube_libre_audio_generator.py
#
# It intentionally does NOT import cube_libre_pygame.py.  The point is to let you
# sketch/render/test WAVs outside the game loop and only copy/install them into
# assets/sfx when you explicitly ask for that.
#
# Pure stdlib for rendering. Optional pygame is used only for --play.

from __future__ import annotations

import argparse
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
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SAMPLE_RATE = 44100
TAU = math.tau

# These are the current game-facing asset keys/filenames.  The helper can render
# experimental versions to tools/audio_lab_out by default, or write directly to
# assets/sfx with --install after you decide a sound belongs in the game.
ASSET_FILENAMES: Dict[str, str] = {
    "crash": "crash_collision.wav",
    "structure_alert": "structure_loss_dee_doo.wav",
    "ambient": "spaceship_engine_room_hum_loop.wav",
    "gamelan": "alien_gamelan_more_frequent_v3_sourcegain.wav",
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

ASSET_TO_PRESET: Dict[str, str] = {
    "crash": "impact-crash",
    "structure_alert": "structure-alert",
    "ambient": "engine-hum-loop",
    "gamelan": "alien-gamelan-loop",
    "field": "field-wooom-loop",
    "critical": "critical-beep-loop",
    "portal": "portal-entry",
    "portal_wou": "portal-wou-loop",
    "laser_reveal": "laser-reveal",
    "laser_dissipate": "laser-dissipate",
    "materialize": "course-materialize",
    "death": "cube-death",
    "reassembly": "cube-reassembly",
    "recouple": "cube-recoup",
    "collapse": "cage-collapse",
    "time_tick": "time-tick-loop",
    "time_buzzer": "time-buzzer",
    "time_siren": "time-siren-loop",
}


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    duration: float
    loop: bool
    func: Callable[[float, int, int, random.Random, float], float]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def script_path() -> Path:
    return Path(__file__).resolve()


def repo_root() -> Path:
    # Expected location: <repo>/tools/cube_libre_audio_generator.py
    p = script_path()
    if p.parent.name == "tools":
        return p.parent.parent
    return Path.cwd().resolve()


def default_lab_dir() -> Path:
    return repo_root() / "tools" / "audio_lab_out"


def default_asset_dir() -> Path:
    return repo_root() / "assets" / "sfx"


# ---------------------------------------------------------------------------
# Small DSP helpers.  Deliberately stdlib-only, because this should run even on
# a plain install and not drag numpy into a sound doodle script.
# ---------------------------------------------------------------------------


def clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def smoothstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def soft_clip(x: float) -> float:
    # Cheap tanh-ish limiter.  Good enough for stacked osc garbage without
    # cooking square-wave digital death unless you intentionally push it.
    return x / (1.0 + abs(x))


def foldback(x: float, amount: float = 1.0) -> float:
    # Useful for nastier arcade-metal effects.  Keep amount subtle unless you
    # want broken toaster mythology.
    x *= max(0.0, amount)
    if x > 1.0 or x < -1.0:
        x = abs(abs((x - 1.0) % 4.0) - 2.0) - 1.0
    return clamp(x)


def adsr(t: float, dur: float, attack: float = 0.01, release: float = 0.08) -> float:
    if dur <= 0.0:
        return 0.0
    if t < attack:
        return t / max(attack, 1e-9)
    if t > dur - release:
        return max(0.0, (dur - t) / max(release, 1e-9))
    return 1.0


def exp_decay(t: float, rate: float) -> float:
    return math.exp(-max(0.0, t) * rate)


def sine(freq: float, t: float, phase: float = 0.0) -> float:
    return math.sin(TAU * freq * t + phase)


def tri(freq: float, t: float, phase: float = 0.0) -> float:
    # phase in radians; convert into cycle offset
    x = (freq * t + phase / TAU) % 1.0
    return 4.0 * abs(x - 0.5) - 1.0


def square(freq: float, t: float, duty: float = 0.5, phase: float = 0.0) -> float:
    x = (freq * t + phase / TAU) % 1.0
    return 1.0 if x < duty else -1.0


def noise(rng: random.Random) -> float:
    return rng.random() * 2.0 - 1.0


def noise_gate_tick(t: float, rate: float, width: float) -> float:
    ph = (t * rate) % 1.0
    if ph >= width:
        return 0.0
    return 1.0 - ph / max(width, 1e-9)


def normalize_peak(samples: Sequence[float], peak: float = 0.92) -> List[float]:
    peak = clamp(float(peak), 0.01, 0.999)
    current = max((abs(v) for v in samples), default=0.0)
    if current <= 1e-12:
        return list(samples)
    g = peak / current
    return [clamp(v * g, -peak, peak) for v in samples]


def apply_gain(samples: Sequence[float], gain_db: float, peak_ceiling: float = 0.98) -> List[float]:
    gain = db_to_gain(gain_db)
    peak_ceiling = clamp(float(peak_ceiling), 0.05, 0.999)
    out = [v * gain for v in samples]
    peak = max((abs(v) for v in out), default=0.0)
    if peak > peak_ceiling:
        out = [v * (peak_ceiling / peak) for v in out]
    return [clamp(v, -peak_ceiling, peak_ceiling) for v in out]


def fade_loop_edges(samples: List[float], sr: int, fade_ms: float = 12.0) -> List[float]:
    # For loop beds.  Not a perfect loop crossfade, just avoids stupid edge clicks.
    n = len(samples)
    fade_n = int(sr * fade_ms / 1000.0)
    fade_n = max(0, min(fade_n, n // 2))
    if fade_n <= 0:
        return samples
    out = list(samples)
    for i in range(fade_n):
        a = i / max(1, fade_n - 1)
        out[i] *= smoothstep(a)
        out[n - 1 - i] *= smoothstep(a)
    return out


# ---------------------------------------------------------------------------
# WAV IO
# ---------------------------------------------------------------------------


def pcm16(v: float) -> int:
    return int(clamp(v, -1.0, 1.0) * 32767)


def write_wav_mono(path: Path, samples: Sequence[float], sr: int = SAMPLE_RATE, force: bool = False) -> None:
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {path}; pass --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    data = array("h", (pcm16(v) for v in samples))
    try:
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(data.tobytes())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def wav_info(path: Path) -> Dict[str, object]:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        sr = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
    return {
        "path": str(path),
        "channels": channels,
        "sample_width_bytes": width,
        "sample_rate": sr,
        "frames": frames,
        "duration_seconds": frames / float(sr or 1),
    }


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def p_impact_crash(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.002, 0.12)
    thump = sine(82.0 - 40.0 * smoothstep(p), t) * exp_decay(t, 7.6) * 0.95
    metal = (
        sine(277.0, t, 1.7) * 0.30
        + sine(431.0, t, 0.2) * 0.18
        + sine(733.0, t, 2.8) * 0.10
        + sine(1190.0, t, 0.9) * 0.045
    ) * exp_decay(t, 10.5)
    grit = noise(rng) * exp_decay(t, 16.0) * 0.52
    return soft_clip((thump + metal + grit) * env) * 0.80


def p_structure_alert(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    note_len = 0.135
    gap = 0.020
    notes = [660.0, 440.0, 660.0, 440.0, 660.0, 440.0]
    idx = int(t / (note_len + gap))
    if idx < 0 or idx >= len(notes):
        return 0.0
    local = t - idx * (note_len + gap)
    if local > note_len:
        return 0.0
    gate = math.sin(math.pi * local / note_len) ** 0.42
    freq = notes[idx]
    vibr = 1.0 + 0.012 * sine(7.0, local)
    sig = sine(freq * vibr, t) * 0.70
    sig += sine(freq * 2.0, t) * 0.12
    sig += sine(freq * 0.5, t, 0.4) * 0.10
    return soft_clip(sig * gate) * 0.62


def p_engine_hum(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    breath = 0.5 + 0.5 * math.sin(TAU * p - math.pi / 2.0)
    slow = breath ** 1.85
    micro = 0.5 + 0.5 * sine(0.125, t, 1.1)
    f0 = 36.0 + 3.0 * sine(1.0 / dur, t)
    body = sine(f0, t) * 0.46
    body += sine(54.0, t, 0.8 + 0.25 * sine(1.0 / dur, t)) * 0.28
    body += sine(72.0, t, 1.7) * 0.18
    body += sine(108.0, t, 2.2) * 0.08
    air = sine(216.0, t, sine(0.25, t) * 0.6) * 0.025
    amp = 0.10 + 0.26 * slow + 0.035 * micro
    return soft_clip((body + air) * amp) * 0.78


def p_alien_gamelan(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    # Loopable ritual-engine-room/gamelan bed.  Stable partials, deterministic
    # strike pattern, not actual sampled instruments.
    p = t / dur
    breath = 0.55 + 0.45 * sine(1.0 / dur, t, -math.pi / 2.0)
    base = 54.0 + 2.0 * sine(0.05, t)
    drone = sine(base, t) * 0.22 + sine(base * 1.5, t, 0.7) * 0.12 + sine(base * 2.01, t, 1.9) * 0.07
    scale = [147.0, 165.0, 196.0, 220.0, 247.0, 294.0, 330.0, 392.0]
    bells = 0.0
    for k, freq in enumerate(scale):
        rate = 0.071 + k * 0.006
        ph = (t * rate + k * 0.137) % 1.0
        hit = max(0.0, 1.0 - ph / 0.08)
        if hit > 0.0:
            detune = 1.0 + 0.003 * sine(0.09 + k * 0.013, t)
            local_t = ph / rate
            decay = math.exp(-local_t * (2.5 + k * 0.21))
            bells += sine(freq * detune, t, k * 0.3) * hit * decay * (0.040 + k * 0.004)
            bells += sine(freq * 2.01, t, 1.2 + k) * hit * decay * 0.018
    shimmer = sine(880.0 + 22.0 * sine(0.031, t), t, 0.5) * 0.012
    return soft_clip((drone * (0.70 + 0.30 * breath) + bells + shimmer) * adsr(t, dur, 0.30, 0.60)) * 0.80


def p_field_wooom(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    sweep = 58.0 + 62.0 * (0.5 + 0.5 * sine(1.0 / dur, t - dur * 0.25))
    wob = 0.5 + 0.5 * sine(0.42, t)
    body = sine(sweep, t, 1.3 * sine(0.19, t)) * 0.42
    body += sine(sweep * 1.5, t, 0.6) * 0.16
    body += sine(240.0 + 50.0 * wob, t) * 0.045
    return soft_clip(body * (0.34 + 0.36 * wob) * adsr(t, dur, 0.10, 0.18)) * 0.75


def p_critical_beep(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    # One-second loop with a nasty-ish double blink.
    sig = 0.0
    for start in (0.06, 0.48):
        local = t - start
        if 0.0 <= local <= 0.145:
            env = math.sin(math.pi * local / 0.145) ** 0.33
            freq = 1180.0 + 60.0 * sine(18.0, local)
            sig += (square(freq, local, duty=0.48) * 0.34 + sine(freq * 1.997, local) * 0.16) * env
    sig += sine(54.0, t) * 0.012
    return soft_clip(sig) * 0.58


def p_portal_entry(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.015, 0.48)
    rise = smoothstep(p)
    freq = 110.0 + 780.0 * (rise ** 1.8)
    sig = sine(freq, t, 5.0 * p) * (0.20 + 0.36 * rise)
    sig += sine(freq * 1.5, t, 0.8) * 0.16
    sig += sine(880.0 + 220.0 * sine(2.0, t), t) * 0.055 * rise
    sparkle = 0.0
    tick = noise_gate_tick(t, 18.0 + 22.0 * p, 0.018)
    if tick > 0.0:
        sparkle = sine(1600.0 + 900.0 * p, t) * tick * 0.13
    return soft_clip((sig + sparkle) * env) * 0.78


def p_portal_wou(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    lfo = 0.5 + 0.5 * sine(2.0 / dur, t)
    freq = 74.0 + 78.0 * (lfo ** 1.2)
    sig = sine(freq, t, 0.8 * sine(0.31, t)) * 0.42
    sig += sine(freq * 2.01, t, 1.1) * 0.12
    sig += sine(310.0 + 60.0 * lfo, t) * 0.035
    return soft_clip(sig * adsr(t, dur, 0.06, 0.08)) * 0.70


def p_laser_reveal(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.010, 0.32)
    swell = smoothstep(p)
    low_freq = 46.0 + 74.0 * swell
    low = sine(low_freq, t, 2.4 * p * p) * 0.42
    mid = sine(138.0 + 170.0 * p, t, 7.0 * p) * 0.20
    bite = sine(510.0 + 210.0 * sine(1.1, t), t) * 0.055 * swell
    hiss = noise(rng) * (0.18 * swell * math.exp(-max(0.0, t - 0.18) * 0.7))
    pulse = (0.5 + 0.5 * sine(2.0 + 3.2 * p, t)) ** 2.0
    return soft_clip((low + mid + bite + hiss * pulse) * env * (0.25 + 0.90 * swell)) * 0.72


def p_laser_dissipate(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.035, 0.95)
    sigh = math.exp(-t * 0.62)
    f0 = 132.0 - 55.0 * smoothstep(p)
    body = sine(f0, t, 0.55 * sine(0.37, t)) * 0.34
    body += sine(f0 * 1.49, t, 1.1) * 0.17
    form1 = 420.0 - 160.0 * smoothstep(p)
    form2 = 730.0 - 230.0 * smoothstep(p)
    vowel = sine(form1, t, 0.6) * 0.11 + sine(form2, t, 1.9) * 0.065
    ember = sine(980.0 - 360.0 * p, t) * 0.030 * (1.0 - p)
    hiss = noise(rng) * (0.16 * exp_decay(t, 0.72) + 0.035 * exp_decay(t, 3.4))
    wobble = 0.72 + 0.28 * sine(1.05 - 0.35 * p, t, 0.4)
    return soft_clip((body + vowel + ember + hiss) * env * sigh * wobble) * 0.74


def p_course_materialize(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = min(1.0, t / max(0.001, dur))
    env = adsr(t, dur, 0.02, 0.30)
    rise_freq = 90.0 + 420.0 * (p ** 1.6)
    body = sine(rise_freq, t, p * 7.0) * (0.18 + 0.18 * p)
    body += sine(rise_freq * 1.503, t) * 0.12
    tick = 0.0
    tick_phase = (t * 12.0) % 1.0
    if tick_phase < 0.045:
        tick = sine(800.0 + 900.0 * p, t) * (1.0 - tick_phase / 0.045) * 0.28
    return soft_clip((body + tick) * env) * 0.72


def p_cube_death(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.004, 0.18)
    drop = sine(180.0 - 135.0 * p, t) * (1.0 - p) * 0.55
    crackle = noise(rng) * exp_decay(t, 8.0) * 0.35
    white = sine(1200.0 + 900.0 * p, t) * (p ** 1.7) * 0.18
    return soft_clip((drop + crackle + white) * env) * 0.78


def p_cube_reassembly(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.03, 0.18)
    zipf = 140.0 + 620.0 * smoothstep(p)
    sig = sine(zipf, t, p * 10.0) * 0.28
    sig += sine(zipf * 1.5, t, 1.2) * 0.14
    rate = 5.0 + 24.0 * p
    click_phase = (t * rate) % 1.0
    click = 0.0
    if click_phase < 0.035:
        click = sine(650.0 + 900.0 * p, t) * (1.0 - click_phase / 0.035) * 0.20
    return soft_clip((sig + click) * env) * 0.72


def p_cube_recoup(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = min(1.0, t / max(0.001, dur))
    env = adsr(t, dur, 0.012, 0.20)
    sweep = 120.0 + 510.0 * smoothstep(p)
    sig = sine(sweep, t, 6.0 * p) * 0.30
    sig += sine(sweep * 1.505, t, 0.7) * 0.15
    sig += sine(sweep * 2.02, t, 1.4) * 0.07
    rate = 7.0 + 21.0 * p
    ph = (t * rate) % 1.0
    tick = 0.0
    if ph < 0.035:
        tick = sine(720.0 + 520.0 * p, t) * (1.0 - ph / 0.035) * 0.20
    return soft_clip((sig + tick) * env) * 0.62


def p_cage_collapse(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    p = t / dur
    env = adsr(t, dur, 0.002, 0.50)
    scrape_env = exp_decay(t, 1.38)
    low = sine(44.0 - 16.0 * p, t) * 0.50 * scrape_env
    sub_grind = sine(31.0 + 5.0 * sine(0.72, t), t) * 0.28 * exp_decay(t, 1.05)
    metal = (
        sine(205.0 + 65.0 * sine(1.27, t), t) * 0.24
        + sine(365.0 - 140.0 * p, t, 1.1) * 0.18
        + sine(610.0 + 265.0 * p, t, 2.4) * 0.10
    ) * scrape_env
    hiss = noise(rng) * (0.22 * exp_decay(t, 0.95) + 0.15 * exp_decay(t, 4.6))
    flash = 0.0
    if t < 0.18:
        flash = sine(925.0, t) * (1.0 - t / 0.18) * 0.20
    return soft_clip((low + sub_grind + metal + hiss + flash) * env) * 0.86


def p_time_tick(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    sig = 0.0
    for start, freq, amp in ((0.02, 1180.0, 0.34), (1.02, 760.0, 0.30)):
        local = t - start
        if local < 0.0 or local > 0.20:
            continue
        env = exp_decay(local, 22.0) * adsr(local, 0.20, 0.001, 0.06)
        click = noise(rng) * exp_decay(local, 60.0) * 0.18
        tone = sine(freq, local) * 0.55 + sine(freq * 2.01, local, 0.4) * 0.13
        sig += (tone + click) * env * amp
    sig += sine(46.0, t) * 0.018 * (0.6 + 0.4 * sine(0.5, t))
    return soft_clip(sig) * 0.82


def p_time_buzzer(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    env = adsr(t, dur, 0.006, 0.11)
    wob = 1.0 + 0.018 * sine(31.0, t) + 0.009 * sine(53.0, t, 0.6)
    base = 104.0 * wob
    sig = sine(base, t) * 0.58
    sig += sine(base * 2.02, t, 0.4) * 0.36
    sig += sine(base * 3.01, t, 1.2) * 0.21
    sig += sine(base * 4.04, t, 2.0) * 0.12
    sig += noise(rng) * 0.055
    gate = 0.76 + 0.24 * (1.0 if sine(18.0, t) > -0.15 else 0.30)
    return soft_clip(sig * env * gate) * 0.88


def p_time_siren(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    lfo = 0.5 + 0.5 * sine(4.0, t)
    freq = 780.0 + 620.0 * (lfo ** 0.80)
    sig = sine(freq, t) * 0.52
    sig += sine(freq * 1.995, t, 0.8) * 0.18
    sig += sine(freq * 0.502, t, 1.3) * 0.12
    amp = 0.72 + 0.28 * sine(4.0, t, 0.3)
    return soft_clip(sig * amp) * 0.64


def p_test_sweep(t: float, i: int, sr: int, rng: random.Random, dur: float) -> float:
    # Useful sanity check for pygame playback / volume / clipping.
    p = t / dur
    f0, f1 = 80.0, 2400.0
    # Exponential sweep phase approximation.
    k = math.log(f1 / f0) / dur
    phase = TAU * f0 * (math.exp(k * t) - 1.0) / k
    return math.sin(phase) * adsr(t, dur, 0.01, 0.15) * 0.55


PRESETS: Dict[str, Preset] = {
    "impact-crash": Preset("impact-crash", "cube/body collision crash with thump, metal partials and shrapnel noise", 0.72, False, p_impact_crash),
    "structure-alert": Preset("structure-alert", "DEE-DOO structural loss alarm", 0.98, False, p_structure_alert),
    "engine-hum-loop": Preset("engine-hum-loop", "slow breathing title/level engine-room ambience loop", 24.0, True, p_engine_hum),
    "alien-gamelan-loop": Preset("alien-gamelan-loop", "ritual engine-room/gamelan drone loop", 48.0, True, p_alien_gamelan),
    "field-wooom-loop": Preset("field-wooom-loop", "low woom-woom force-field loop", 6.0, True, p_field_wooom),
    "critical-beep-loop": Preset("critical-beep-loop", "critical cube count warning beep loop", 1.0, True, p_critical_beep),
    "portal-entry": Preset("portal-entry", "portal entry shimmer/rise", 2.2, False, p_portal_entry),
    "portal-wou-loop": Preset("portal-wou-loop", "slow portal wou-wou loop", 4.0, True, p_portal_wou),
    "laser-reveal": Preset("laser-reveal", "ominous laser-grid reveal woosh", 1.55, False, p_laser_reveal),
    "laser-dissipate": Preset("laser-dissipate", "dying red-grid exhale / aaah", 2.85, False, p_laser_dissipate),
    "course-materialize": Preset("course-materialize", "course geometry materialization shimmer", 2.35, False, p_course_materialize),
    "cube-death": Preset("cube-death", "cube death whiteout drop/crackle", 0.78, False, p_cube_death),
    "cube-reassembly": Preset("cube-reassembly", "cube reassembly rebuild sweep and lock clicks", 3.75, False, p_cube_reassembly),
    "cube-recoup": Preset("cube-recoup", "C-key recoupling request sweep and ticks", 1.38, False, p_cube_recoup),
    "cage-collapse": Preset("cage-collapse", "low octave-dropped CASHHH cage collapse", 2.35, False, p_cage_collapse),
    "time-tick-loop": Preset("time-tick-loop", "slow tick-tock countdown loop", 2.0, True, p_time_tick),
    "time-buzzer": Preset("time-buzzer", "last-10-seconds BERRRRT buzzer", 0.68, False, p_time_buzzer),
    "time-siren-loop": Preset("time-siren-loop", "last-5-seconds WIUWIU siren loop", 1.0, True, p_time_siren),
    "test-sweep": Preset("test-sweep", "plain exponential test sweep", 3.0, False, p_test_sweep),
}


# ---------------------------------------------------------------------------
# Rendering / playback
# ---------------------------------------------------------------------------


def render_preset(
    preset_name: str,
    *,
    duration: Optional[float] = None,
    seed: int = 24601,
    variant: int = 0,
    sr: int = SAMPLE_RATE,
    gain_db: float = 0.0,
    peak: Optional[float] = None,
) -> Tuple[List[float], Dict[str, object]]:
    if preset_name not in PRESETS:
        raise KeyError(f"unknown preset {preset_name!r}")
    preset = PRESETS[preset_name]
    dur = float(duration if duration is not None else preset.duration)
    if dur <= 0:
        raise ValueError("duration must be positive")

    # Variant shifts seed and a tiny hidden tuning factor.  It is meant for fast
    # auditioning: same preset, same family, different mutant.
    render_seed = int(seed) + int(variant) * 100_003
    rng = random.Random(render_seed)
    n = int(round(dur * sr))
    samples: List[float] = []

    # Some presets look at duration, sample index and rng.  Keeping this simple
    # makes adding another one-liner preset painless.
    for i in range(n):
        t = i / sr
        v = preset.func(t, i, sr, rng, dur)
        samples.append(clamp(v, -1.0, 1.0))

    if preset.loop:
        samples = fade_loop_edges(samples, sr, fade_ms=10.0)

    if gain_db:
        samples = apply_gain(samples, gain_db, peak_ceiling=0.98)
    if peak is not None:
        samples = normalize_peak(samples, peak=peak)

    actual_peak = max((abs(v) for v in samples), default=0.0)
    rms = math.sqrt(sum(v * v for v in samples) / max(1, len(samples)))
    meta = {
        "preset": preset.name,
        "description": preset.description,
        "duration_seconds": dur,
        "sample_rate": sr,
        "samples": len(samples),
        "loop": preset.loop,
        "seed": seed,
        "variant": variant,
        "render_seed": render_seed,
        "gain_db": gain_db,
        "normalised_peak": peak,
        "actual_peak": actual_peak,
        "rms": rms,
    }
    return samples, meta


def play_wav(path: Path) -> None:
    try:
        import pygame  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"pygame is not available for --play: {exc}") from exc

    pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1)
    snd = pygame.mixer.Sound(str(path))
    ch = snd.play()
    if ch is None:
        return
    while ch.get_busy():
        time.sleep(0.025)
    pygame.mixer.quit()


def preset_output_name(preset_name: str, seed: int, variant: int) -> str:
    return f"{preset_name}_seed{seed}_v{variant}.wav"


def resolve_output_path(args: argparse.Namespace, preset_name: str, asset_key: Optional[str]) -> Path:
    if args.output:
        return Path(args.output).expanduser().resolve()

    out_dir = default_asset_dir() if args.install else Path(args.out_dir).expanduser().resolve()

    if asset_key:
        filename = ASSET_FILENAMES[asset_key]
    elif args.name:
        filename = args.name if args.name.lower().endswith(".wav") else f"{args.name}.wav"
    else:
        filename = preset_output_name(preset_name, args.seed, args.variant)
    return out_dir / filename


def write_metadata(path: Path, meta: Dict[str, object], force: bool) -> None:
    meta_path = path.with_suffix(path.suffix + ".json")
    if meta_path.exists() and not force:
        # Metadata should not be the thing that aborts a render.  Add a timestamp
        # suffix instead of stomping the old note.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        meta_path = path.with_suffix(path.suffix + f".{stamp}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")


def render_one(args: argparse.Namespace, preset_name: str, asset_key: Optional[str] = None) -> Path:
    samples, meta = render_preset(
        preset_name,
        duration=args.duration,
        seed=args.seed,
        variant=args.variant,
        sr=args.sample_rate,
        gain_db=args.gain_db,
        peak=args.peak,
    )
    path = resolve_output_path(args, preset_name, asset_key)
    meta["asset_key"] = asset_key
    meta["output_path"] = str(path)
    meta["generated_by"] = "tools/cube_libre_audio_generator.py"
    write_wav_mono(path, samples, sr=args.sample_rate, force=args.force)
    if args.write_meta:
        write_metadata(path, meta, force=args.force)
    print(f"[OK] wrote {path}")
    print(f"     preset={preset_name} duration={meta['duration_seconds']:.3f}s peak={meta['actual_peak']:.3f} rms={meta['rms']:.3f}")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_presets() -> None:
    print("Presets:")
    for name in sorted(PRESETS):
        p = PRESETS[name]
        loop = "loop" if p.loop else "one-shot"
        print(f"  {name:22s} {p.duration:6.2f}s  {loop:8s}  {p.description}")
    print("\nAsset keys:")
    for key in sorted(ASSET_FILENAMES):
        preset = ASSET_TO_PRESET.get(key, "?")
        print(f"  {key:16s} -> {ASSET_FILENAMES[key]:45s} preset={preset}")


def scan_assets() -> None:
    adir = default_asset_dir()
    print(f"Asset dir: {adir}")
    for key, filename in ASSET_FILENAMES.items():
        path = adir / filename
        if path.exists():
            try:
                info = wav_info(path)
                print(f"  [OK]      {key:16s} {filename:45s} {info['duration_seconds']:.2f}s {info['sample_rate']} Hz")
            except Exception as exc:
                print(f"  [BROKEN]  {key:16s} {filename:45s} {exc}")
        else:
            print(f"  [MISSING] {key:16s} {filename}")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Standalone Cube Libre procedural WAV generator / audio lab.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # render a safe preview outside the game assets:
  python tools/cube_libre_audio_generator.py --preset laser-reveal --play

  # render a few variants without touching assets/sfx:
  python tools/cube_libre_audio_generator.py --preset cage-collapse --variant 3 --gain-db -1 --peak 0.92

  # overwrite the actual game asset only when you mean it:
  python tools/cube_libre_audio_generator.py --asset collapse --install --force --write-meta

  # rebuild all current game-facing WAV names into assets/sfx:
  python tools/cube_libre_audio_generator.py --all-assets --install --force
""".strip(),
    )
    ap.add_argument("--preset", choices=sorted(PRESETS), help="render this named sound recipe")
    ap.add_argument("--asset", choices=sorted(ASSET_FILENAMES), help="render the preset mapped to this game asset key")
    ap.add_argument("--all-assets", action="store_true", help="render all game-facing asset keys")
    ap.add_argument("--list", action="store_true", help="list presets and asset mappings")
    ap.add_argument("--scan-assets", action="store_true", help="show which assets/sfx WAVs currently exist")

    ap.add_argument("--out-dir", default=str(default_lab_dir()), help="output directory for preview renders; default: tools/audio_lab_out")
    ap.add_argument("--output", help="exact output WAV path for single render")
    ap.add_argument("--name", help="output filename for preview render, e.g. my_zap.wav")
    ap.add_argument("--install", action="store_true", help="write to assets/sfx using the canonical game filename")
    ap.add_argument("--force", action="store_true", help="overwrite existing WAV")
    ap.add_argument("--write-meta", action="store_true", help="write a JSON sidecar with render settings")

    ap.add_argument("--duration", type=float, help="override preset duration in seconds")
    ap.add_argument("--sample-rate", type=int, default=SAMPLE_RATE, help="sample rate; default 44100")
    ap.add_argument("--seed", type=int, default=24601, help="base random seed")
    ap.add_argument("--variant", type=int, default=0, help="variant number; shifts seed but keeps same recipe")
    ap.add_argument("--gain-db", type=float, default=0.0, help="post-render gain in dB with peak ceiling")
    ap.add_argument("--peak", type=float, help="normalize output to this peak, e.g. 0.92")
    ap.add_argument("--play", action="store_true", help="play the written WAV using pygame.mixer")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.list:
        print_presets()
        return 0

    if args.scan_assets:
        scan_assets()
        return 0

    if args.all_assets:
        if args.output or args.name or args.duration is not None:
            print("[ERROR] --all-assets does not use --output, --name or --duration", file=sys.stderr)
            return 2
        written: List[Path] = []
        for asset_key in ASSET_FILENAMES:
            preset_name = ASSET_TO_PRESET[asset_key]
            written.append(render_one(args, preset_name, asset_key=asset_key))
        print(f"[OK] rendered {len(written)} files")
        return 0

    asset_key = args.asset
    preset_name = args.preset
    if asset_key:
        preset_name = ASSET_TO_PRESET[asset_key]
    if not preset_name:
        print("[ERROR] choose --preset, --asset, --all-assets, --list, or --scan-assets", file=sys.stderr)
        return 2

    path = render_one(args, preset_name, asset_key=asset_key if args.install or args.asset else None)

    if args.play:
        print(f"[PLAY] {path}")
        play_wav(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
