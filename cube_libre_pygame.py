#!/usr/bin/env python3
#
# ~~~~~~~~~~~~~~~~~~~~
# cube_libre_pygame.py
# ~~~~~~~~~~~~~~~~~~~~
#
# "Cube Libre" - rotating-field maze prototype game
#
# A cubistic puzzle/adventure game where the player is a cube made of smaller cubes.
# The player's goal is to get to the portal at the end of the maze with whatever pieces they have.
# Hits against rotating laser grids chip away the body. Broken chunks become independent
# debris; intact cells remain part of the controlled body.
#
# Original concept/code by FlyingFathead (w/ imaginary digital friends), Dec 2023-Dec 2024
# This version keeps the rotating playfield / rotating laser-grid concept while fixing the
# state model, broken delta-time, negative-index topology bug, and collision animation mess.
# by FlyingFathead, 2026
#
# this version: june 22, 2026

version_number = "0.15.65-first-run-setup-lower"

import colorsys
import importlib.util
import json
import math
import os
import platform as stdlib_platform
import random
import shlex
import shutil
import subprocess
import sys
import threading
import wave
from array import array
from dataclasses import dataclass

# -----------------------------------------------------------------------------
# Startup preflight: fail early and usefully instead of exploding at first import.
# -----------------------------------------------------------------------------

_REQUIRED_PYTHON_MODULES = (
    ("pygame", "pygame"),
    ("OpenGL", "PyOpenGL"),
    ("numpy", "numpy"),
)


def _quote_cmd_part(value) -> str:
    return shlex.quote(str(value))


def _requirements_path() -> str:
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "requirements.txt")


def _print_dependency_install_help(missing_packages=None, broken_exception=None):
    missing_packages = list(dict.fromkeys(missing_packages or []))
    exe = sys.executable or "python"
    req = _requirements_path()

    print("[ERROR] Cube Libre cannot start because required Python dependencies are missing or broken.", file=sys.stderr)
    if missing_packages:
        print("[ERROR] Missing package(s): " + ", ".join(missing_packages), file=sys.stderr)
    if broken_exception is not None:
        print(f"[ERROR] Import failure: {broken_exception.__class__.__name__}: {broken_exception}", file=sys.stderr)

    print("", file=sys.stderr)
    print("Install the repository requirements with:", file=sys.stderr)
    if os.path.exists(req):
        print(f"  {_quote_cmd_part(exe)} -m pip install -r {_quote_cmd_part(req)}", file=sys.stderr)
    else:
        print(f"  {_quote_cmd_part(exe)} -m pip install -r requirements.txt", file=sys.stderr)
        print("  (run this from the Cube Libre repository root)", file=sys.stderr)

    packages = missing_packages or [pip_name for _module_name, pip_name in _REQUIRED_PYTHON_MODULES]
    print("", file=sys.stderr)
    print("Or install the Python packages directly:", file=sys.stderr)
    print(f"  {_quote_cmd_part(exe)} -m pip install " + " ".join(_quote_cmd_part(pkg) for pkg in packages), file=sys.stderr)
    print("", file=sys.stderr)
    print("Required packages: pygame, PyOpenGL, numpy", file=sys.stderr)
    raise SystemExit(1)


def _preflight_required_python_modules():
    missing = []
    for module_name, pip_name in _REQUIRED_PYTHON_MODULES:
        if importlib.util.find_spec(module_name) is None:
            missing.append(pip_name)
    if missing:
        _print_dependency_install_help(missing_packages=missing)


_preflight_required_python_modules()

try:
    import pygame
    from pygame.locals import DOUBLEBUF, OPENGL, RESIZABLE, FULLSCREEN
except Exception as exc:
    _print_dependency_install_help(missing_packages=["pygame"], broken_exception=exc)

try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
except Exception as exc:
    _print_dependency_install_help(missing_packages=["PyOpenGL"], broken_exception=exc)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

DISPLAY = (1000, 760)
WINDOWED_DISPLAY = DISPLAY
IS_FULLSCREEN = False
LAST_GL_VIEWPORT_SIZE = None
FPS_LIMIT = 120

# Camera / playability. The default preserves the original whole-maze camera,
# but L can toggle player-centered tracking for long wonky levels.
AUTO_CENTER_ON_PLAYER = False
PLAYER_CENTER_ZOOM = 48.0
# From level 3 onward, do not even draw infinite grey future plumbing; keep a
# small preview window ahead and let the rest materialize/reveal later. Stars are
# deliberately left alone; the draw-call tax is mostly geometry, not 900 points.
PREVIEW_MODULES_AHEAD = 1

# Reset safety. Plain R used to restart the whole run from level 1, which is
# too easy to hit accidentally while playing near E/D/Q/W. During an active
# run, require Ctrl+R for the destructive full-run reset. Title/result screens
# can still start/reset normally.
RUN_RESET_REQUIRES_CTRL_DURING_PLAY = True

CUBE_SIZE = 5                 # odd number; 5 -> local coords -2..2 on each axis
CELL_SPACING = 1.0
CELL_HALF = 0.46

START_ORIGIN = (-18.0, 0.0, 0.0)
PORTAL_POSITION = (20.0, 0.0, 0.0)
PORTAL_SIZE = 5.8

MOVE_SPEED = 6.0              # units/sec
FAST_MULT = 2.6

# Course bounds in the unrotated game/course coordinate system.
COURSE_X_MIN, COURSE_X_MAX = -23.0, 23.0
COURSE_Y_MIN, COURSE_Y_MAX = -7.0, 7.0
COURSE_Z_MIN, COURSE_Z_MAX = -7.0, 7.0

# Visual rotation of the whole playing field. This is deliberately part of the feel.
SCENE_ROT_SPEED_X = 7.5       # deg/sec
SCENE_ROT_SPEED_Y = 13.0
SCENE_ROT_SPEED_Z = 4.5

DAMAGE_COOLDOWN = 0.16        # seconds between damage events; slightly slower so hits are readable
MAX_DAMAGE_PER_EVENT = 2
FRAGMENT_LIFETIME = 9.0

SCREEN_SHAKE_DURATION = 0.22
FLASH_DURATION = 0.20
IMPACT_SCREEN_FLASH_ENABLED = False   # Hit feedback belongs on the struck laser/cage, not the whole screen.

BACKGROUND = (0.0, 0.0, 0.0, 1.0)
MAX_CELLS = CUBE_SIZE ** 3

# UI / transitions
LOW_CUBE_WARNING = 42
CRITICAL_CUBE_WARNING = 18
DEATH_DISSOLVE_SECONDS = 0.48
REASSEMBLY_SECONDS = 3.75
REASSEMBLY_COMPLETE_HOLD_SECONDS = 0.50
# Old white-void pacing: pieces visibly converge into a complete cube, then the cube holds before fade-back.
REASSEMBLY_RECALL_DONE_AT = 0.72
# After the pieces have reached their slots, let the mini-cubes visibly rotate
# in-place for a short beat, then lock them into a non-rotating complete cube
# before the black/white inversion fade releases back to the level.
REASSEMBLY_SETTLE_DONE_AT = 0.88
# End of reassembly: fade the white void into a black/white inverted version
# of itself, then fade that inversion back into the actual game scene.
REASSEMBLY_INVERT_FLASH_ENABLED = True
REASSEMBLY_INVERT_START_AT = 0.91
REASSEMBLY_FLASH_SECONDS = 1.10
COURSE_MATERIALIZE_SECONDS = 2.20
PORTAL_WARP_SECONDS = 3.4
TRANSCENDENCE_WHITE_SECONDS = 4.25
LEVEL_READY_FADE_IN_SECONDS = 0.95

# Procedural audio. Generated as tiny WAV assets on first run; the game still runs
# silently if pygame cannot open an audio device.
AUDIO_ENABLED = True
AUDIO_SAMPLE_RATE = 44100
AUDIO_DIR_NAME = os.path.join("assets", "sfx")
AUDIO_DIR_LABEL = "assets/sfx"
# First-run audio rendering is CPU-heavy pure Python. Keep it out of the
# Pygame process so Linux/Wayland/GNOME does not think the window has frozen
# while the WAV cache is being built.
AUDIO_BUILDER_ARG = "--cube-libre-build-audio-cache-worker"
AUDIO_BUILDER_NICE = 5
# On first run, the procedural audio WAVs may take a while to synthesize,
# especially on Windows. Keep the title screen alive, show a clear setup
# notice, and do not allow starting the run until the missing audio cache has
# either loaded successfully or failed gracefully.
AUDIO_BLOCK_START_WHILE_GENERATING = True
SAVE_FILE_NAME = "cube_libre_scores.json"
PORTAL_LOCAL_X = 20.0
# The portal is drawn as a square plane, so the gameplay capture area should
# also be square-ish and cell-based. The old origin+circle test made it
# possible to visually touch the portal edge/corner without transcending.
PORTAL_CAPTURE_HALF = PORTAL_SIZE * 0.5 + CELL_HALF * 1.35
PORTAL_CAPTURE_X_BEFORE = 1.15
PORTAL_CAPTURE_X_AFTER = 2.40
# Portal commit logic: visible charging starts only once a meaningful part of
# the body has crossed into the portal. Full transcendence requires committing
# the whole surviving cube, instead of rewarding a one-cell accidental nick.
PORTAL_CHARGE_RATIO = 0.50
PORTAL_STRONG_RATIO = 2.0 / 3.0
PORTAL_TRANSCEND_RATIO = 0.985
PORTAL_ABSORB_X = -0.30
LEVEL_READY_SECONDS = 1.65

# Level/module generation. Level 1 is the exact original straight tunnel.
# Every later level adds one more same-style tunnel module after a 90-degree turn,
# with the portal moved to the end of the final module.
LEVEL_DIRECTIONS = ((1, 0), (0, 1), (1, 0), (0, -1))

# L-turn geometry. A turn is not a huge extra box and it does not hijack
# controls. It is just a same-cross-section joint cube with two openings, plus
# the two corridor tubes that meet inside it.
TUNNEL_HALF = max(abs(COURSE_Z_MIN), abs(COURSE_Z_MAX))
TURN_JOINT_HALF = TUNNEL_HALF
BOUNDARY_DAMAGE_PAD = 0.25
# Small laser mercy only inside the actual same-size joint cube. This prevents
# seam shaving without deleting obstacle grids from the corridor modules.
TURN_JOINT_LASER_PAD = 0.0

# Collision visuals
SPARK_LIFETIME = 0.30
SPARK_GRAVITY = -1.2
LASER_GLOW_LIFETIME = 0.22
BOUND_GLOW_LIFETIME = 0.22
# Simple antique collision feedback: the thing hit flashes red/white or
# blue/white, and the player cube flashes the same way for a short beat.
CUBE_IMPACT_FLASH_SECONDS = 0.22
CUBE_IMPACT_FLASH_HZ = 17.0

# Trail culling / collapse. Higher levels should not keep drawing the whole
# damn history of the maze forever. Once the player has properly entered the
# next module, the previous pipe/joint flashes away behind them and is no
# longer drawn or checked for laser hits.
# Keep at most two turn joints worth of history visible. Level 2 has only one
# joint, so nothing collapses yet. From level 3 onward, the oldest pipe section
# gets eaten out of existence behind the player after they enter the next module.
TRAIL_KEEP_JOINTS = 2
TRAIL_FADE_START_INTO_MODULE = 0.35
# Start collapse almost immediately after the player has exited into the newer
# module. The old red grids should not keep spinning two corners behind the
# player just because the local-X fade band was too far inside the next pipe.
# Make the visible effect last long enough to read, but let the hazard geometry
# die quickly for GPU sanity.
TRAIL_FADE_SECONDS_AT_RUSH = 1.0
TRAIL_FADE_DISTANCE = MOVE_SPEED * FAST_MULT * TRAIL_FADE_SECONDS_AT_RUSH
TRAIL_FLASH_HZ = 11.0
COLLAPSE_DEBRIS_COUNT = 70
# Once a collapse starts, keep the visible eat-away effect time-based. The old
# distance-only fade could vanish too subtly or draw the curtain at the wrong
# joint, especially after the portal/commit changes.
COLLAPSE_VISUAL_SECONDS = 1.25

# Starting at level 3, do not render the whole future murder-plumbing at full
# detail. Future modules are grey wireframe previews until the player commits
# into the preceding joint. This is both a performance cull and a readable
# "the maze is assembling ahead of you" language.
PREVIEW_CULL_START_LEVEL = 3
PREVIEW_WIREFRAME_ALPHA = 0.22
PREVIEW_FAR_WIREFRAME_ALPHA = 0.10
# Newly revealed red hazard grids should not pop in as finished geometry.
# From level 3 onward, future modules are grey previews until revealed; when a
# module wakes up its laser grids simmer inward from the outer edges, glow faintly,
# then fortify into the normal red rotating grid.
LASER_REVEAL_SECONDS = 1.45
LASER_REVEAL_COLLISION_ARM_RATIO = 0.78
LASER_REVEAL_SOUND_COOLDOWN = 0.35
LASER_REVEAL_AT = {}
LASER_REVEAL_SOUND_PLAYED = set()
# Passed laser grids should not just pop out of existence with the old pipe.
# During trail collapse they thin into orange/red embers, then grey ash-points,
# with a slow exhale-like audio tail.
LASER_DISSIPATE_SOUND_COOLDOWN = 0.28
LASER_EMBER_POINT_COUNT = 62

# Zap barriers / joint closures behind the player. These are visual first, but
# they also make the collapse read as a one-way field rather than silent deletion.
ZAP_BARRIER_SECONDS = 1.35
ZAP_BARRIER_FLASH_HZ = 13.0
ZAP_BARRIERS = {}

# Re-coupling mechanic. Press C to pull still-existing loose debris
# chunks back into missing cube cells. This is intentionally not free healing:
# only recoverable fragments can be reclaimed, so waiting too long costs you
# for the rest of the level/attempt. Near expiry they blink, grey out, then vanish.
RECOUPLING_SECONDS = 1.18
RECOUPLING_NOTICE_SECONDS = 1.65
RECOUPLING_MAX_PER_REQUEST = MAX_CELLS
# Fraction of available recoverable loose cubes that a C request actually gathers.
# 0.90 means re-coupling is intentionally lossy: a request usually reclaims about
# 90% of eligible debris instead of vacuum-cleaning every loose cube perfectly.
# Set to 1.0 for perfect capture, or lower values for nastier later-level tuning.
RECOUPLING_GATHER_RATE = 0.90
RECOUPLING_FRAGMENT_RECOVERABLE_SECONDS = 8.0
RECOUPLING_FRAGMENT_EXPIRY_BLINK_SECONDS = 1.75
# Bottom-screen recovery prompt. This appears briefly after damage and again
# as a last-chance warning just before loose cells expire. It is deliberately
# bottom-center so it does not fight the HUD or top danger warning.
RECOUPLING_PROMPT_SECONDS = 3.0
RECOUPLING_PROMPT_FADE_SECONDS = 0.55
# Re-coupling request limiter. Default: 5 successful requests per 10 seconds.
# If RECOUPLING_REQUEST_WINDOW_SECONDS is set to 0, the same limit becomes a
# per-level-attempt pool instead of a rolling cooldown window. The level-scaling
# knobs are intentionally dormant by default, but make later difficulty tuning easy.
RECOUPLING_REQUEST_LIMIT = 5
RECOUPLING_REQUEST_WINDOW_SECONDS = 10.0
RECOUPLING_MIN_REQUEST_LIMIT = 1
RECOUPLING_REQUEST_LIMIT_DROP_PER_LEVEL = 0
RECOUPLING_COOLDOWN_NOTICE_SECONDS = 1.45
# If true, hammering C while a re-coupling animation is already running still
# burns request quota. This makes spam punishable instead of turning C into a
# free "maybe later" button during the active reclaim animation.
RECOUPLING_ACTIVE_SPAM_CONSUMES_QUOTA = True

# Time pressure phase. Starting at level 5, the game becomes leg-timed:
# every corridor stretch gets its own countdown. Exiting an L-pocket/joint into
# the next tunnel resets the timer. If the timer runs out while you are still in
# that stretch, the collapsing pipe kills you.
TIME_MODE_START_LEVEL = 5
TIME_PER_LEG_SECONDS = 30.0
TIME_INTRO_SECONDS = 3.20
TIME_INTRO_FADE_IN_SECONDS = 1.10
TIME_TIMER_WARNING_SECONDS = 8.0
TIME_BUZZER_START_SECONDS = 10.0
TIME_SIREN_START_SECONDS = 5.0
TIME_BUZZER_COOLDOWN_SECONDS = 0.82

# Audio mix tuning. Keep runtime mixer gain separate from source-file gain.
# The alien-gamelan WAV is deliberately generated/copied from the safe old synth
# and then raised as a source asset with a peak ceiling, instead of rewriting the
# instrument into a clipped/distorted mess.
GAMELAN_GAIN_DB = 0.0
GAMELAN_GAIN = 10.0 ** (GAMELAN_GAIN_DB / 20.0)
GAMELAN_SOURCE_GAIN_DB = 12.0
GAMELAN_SOURCE_GAIN = 10.0 ** (GAMELAN_SOURCE_GAIN_DB / 20.0)
GAMELAN_SOURCE_TARGET_PEAK = 0.86


# -----------------------------------------------------------------------------
# Small math helper; kept dependency-free, because numpy for this would be silly.
# -----------------------------------------------------------------------------

@dataclass
class Vec3:
    x: float
    y: float
    z: float

    def __add__(self, other):
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float):
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def dot(self, other) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other):
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalized(self):
        l = self.length()
        if l <= 1e-9:
            return Vec3(0.0, 1.0, 0.0)
        return Vec3(self.x / l, self.y / l, self.z / l)

    def as_tuple(self):
        return (self.x, self.y, self.z)


def rotate_axis(v: Vec3, axis: Vec3, angle_deg: float) -> Vec3:
    """Rotate vector v around a normalized axis by angle_deg using Rodrigues."""
    a = axis.normalized()
    r = math.radians(angle_deg)
    c = math.cos(r)
    s = math.sin(r)
    return (v * c) + (a.cross(v) * s) + (a * (a.dot(v) * (1.0 - c)))


def centered_coords(size: int):
    if size % 2 == 0:
        raise ValueError("CUBE_SIZE should be odd so there is a real center cell.")
    half = size // 2
    return range(-half, half + 1)


def dist_to_grid_line(value: float, spacing: float) -> float:
    """Distance from value to nearest regularly spaced line centered around 0."""
    nearest = round(value / spacing) * spacing
    return abs(value - nearest)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def random_vec(scale=1.0):
    return Vec3(
        random.uniform(-1.0, 1.0) * scale,
        random.uniform(-1.0, 1.0) * scale,
        random.uniform(-1.0, 1.0) * scale,
    )


def gl_apply_basis(basis_x: Vec3, basis_y: Vec3, basis_z: Vec3):
    """Apply a local-to-world 3D basis for immediate-mode OpenGL drawing.

    The matrix columns are the world-space local X/Y/Z basis vectors. This lets
    later tunnel modules turn 90 degrees while the existing local drawing code
    still thinks it is drawing the original straight level.
    """
    glMultMatrixf([
        basis_x.x, basis_x.y, basis_x.z, 0.0,
        basis_y.x, basis_y.y, basis_y.z, 0.0,
        basis_z.x, basis_z.y, basis_z.z, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ])

# -----------------------------------------------------------------------------
# GL drawing helpers
# -----------------------------------------------------------------------------

CUBE_FACES = [
    # front
    [(-CELL_HALF, -CELL_HALF,  CELL_HALF), ( CELL_HALF, -CELL_HALF,  CELL_HALF),
     ( CELL_HALF,  CELL_HALF,  CELL_HALF), (-CELL_HALF,  CELL_HALF,  CELL_HALF)],
    # back
    [( CELL_HALF, -CELL_HALF, -CELL_HALF), (-CELL_HALF, -CELL_HALF, -CELL_HALF),
     (-CELL_HALF,  CELL_HALF, -CELL_HALF), ( CELL_HALF,  CELL_HALF, -CELL_HALF)],
    # top
    [(-CELL_HALF,  CELL_HALF,  CELL_HALF), ( CELL_HALF,  CELL_HALF,  CELL_HALF),
     ( CELL_HALF,  CELL_HALF, -CELL_HALF), (-CELL_HALF,  CELL_HALF, -CELL_HALF)],
    # bottom
    [(-CELL_HALF, -CELL_HALF,  CELL_HALF), ( CELL_HALF, -CELL_HALF,  CELL_HALF),
     ( CELL_HALF, -CELL_HALF, -CELL_HALF), (-CELL_HALF, -CELL_HALF, -CELL_HALF)],
    # left
    [(-CELL_HALF, -CELL_HALF,  CELL_HALF), (-CELL_HALF,  CELL_HALF,  CELL_HALF),
     (-CELL_HALF,  CELL_HALF, -CELL_HALF), (-CELL_HALF, -CELL_HALF, -CELL_HALF)],
    # right
    [( CELL_HALF, -CELL_HALF,  CELL_HALF), ( CELL_HALF,  CELL_HALF,  CELL_HALF),
     ( CELL_HALF,  CELL_HALF, -CELL_HALF), ( CELL_HALF, -CELL_HALF, -CELL_HALF)],
]

CUBE_EDGES = [
    (-CELL_HALF, -CELL_HALF, -CELL_HALF), ( CELL_HALF, -CELL_HALF, -CELL_HALF),
    ( CELL_HALF, -CELL_HALF, -CELL_HALF), ( CELL_HALF,  CELL_HALF, -CELL_HALF),
    ( CELL_HALF,  CELL_HALF, -CELL_HALF), (-CELL_HALF,  CELL_HALF, -CELL_HALF),
    (-CELL_HALF,  CELL_HALF, -CELL_HALF), (-CELL_HALF, -CELL_HALF, -CELL_HALF),
    (-CELL_HALF, -CELL_HALF,  CELL_HALF), ( CELL_HALF, -CELL_HALF,  CELL_HALF),
    ( CELL_HALF, -CELL_HALF,  CELL_HALF), ( CELL_HALF,  CELL_HALF,  CELL_HALF),
    ( CELL_HALF,  CELL_HALF,  CELL_HALF), (-CELL_HALF,  CELL_HALF,  CELL_HALF),
    (-CELL_HALF,  CELL_HALF,  CELL_HALF), (-CELL_HALF, -CELL_HALF,  CELL_HALF),
    (-CELL_HALF, -CELL_HALF, -CELL_HALF), (-CELL_HALF, -CELL_HALF,  CELL_HALF),
    ( CELL_HALF, -CELL_HALF, -CELL_HALF), ( CELL_HALF, -CELL_HALF,  CELL_HALF),
    ( CELL_HALF,  CELL_HALF, -CELL_HALF), ( CELL_HALF,  CELL_HALF,  CELL_HALF),
    (-CELL_HALF,  CELL_HALF, -CELL_HALF), (-CELL_HALF,  CELL_HALF,  CELL_HALF),
]


def gl_color(color, alpha=1.0):
    if len(color) == 4:
        glColor4f(color[0], color[1], color[2], color[3])
    else:
        glColor4f(color[0], color[1], color[2], alpha)


def draw_unit_cube(color, alpha=1.0, outline=True, outline_color=None):
    gl_color(color, alpha)
    glBegin(GL_QUADS)
    for face in CUBE_FACES:
        for vx, vy, vz in face:
            glVertex3f(vx, vy, vz)
    glEnd()

    if outline:
        if outline_color is None:
            outline_color = (0.0, 0.0, 0.0)
        glColor4f(outline_color[0], outline_color[1], outline_color[2], min(alpha, 0.55))
        glLineWidth(1.0)
        glBegin(GL_LINES)
        for vx, vy, vz in CUBE_EDGES:
            glVertex3f(vx, vy, vz)
        glEnd()


def draw_wire_box(xmin, xmax, ymin, ymax, zmin, zmax):
    corners = [
        (xmin, ymin, zmin), (xmax, ymin, zmin), (xmax, ymax, zmin), (xmin, ymax, zmin),
        (xmin, ymin, zmax), (xmax, ymin, zmax), (xmax, ymax, zmax), (xmin, ymax, zmax),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    glBegin(GL_LINES)
    for a, b in edges:
        glVertex3fv(corners[a])
        glVertex3fv(corners[b])
    glEnd()


_font_cache = {}
_tech_font_path = None

# Built-in font files cannot be bundled here, so prefer whatever futuristic /
# terminal / dot-matrix-ish fonts exist on the player's system and fall back safely.
TECH_FONT_CANDIDATES = (
    "Orbitron", "Eurostile", "Microgramma", "Bank Gothic", "OCR A Extended",
    "Consolas", "Lucida Console", "Cascadia Mono", "DejaVu Sans Mono",
    "Liberation Mono", "Courier New", "Fixedsys", "Terminal",
)


def _resolve_tech_font_path():
    global _tech_font_path
    if _tech_font_path is not None:
        return _tech_font_path
    _tech_font_path = False
    try:
        for name in TECH_FONT_CANDIDATES:
            path = pygame.font.match_font(name, bold=False)
            if path:
                _tech_font_path = path
                break
    except Exception:
        _tech_font_path = False
    return _tech_font_path


def get_font(size: int, bold: bool = False):
    key = (size, bold)
    font = _font_cache.get(key)
    if font is None:
        path = _resolve_tech_font_path()
        try:
            if path:
                font = pygame.font.Font(path, size)
                font.set_bold(bool(bold))
            else:
                font = pygame.font.SysFont("consolas", size, bold=bold)
        except Exception:
            font = pygame.font.Font(None, size)
        _font_cache[key] = font
    return font


# -----------------------------------------------------------------------------
# Optional Cube Libre dot-matrix font.
# -----------------------------------------------------------------------------
# The game should be able to use the procedural dot font when the companion files
# are present, but never fail to boot because one asset/editor file was not copied.
# Missing/broken dotmatrix support prints one warning and falls back to get_font().

DOTMATRIX_FONT_FILE = os.path.join("assets", "fonts", "cube_libre_5x7.json")
DOTMATRIX_EDITOR_FILE = os.path.join("tools", "dotmatrix_font_editor.py")
DOTMATRIX_REQUIRED_CHARS = "".join(dict.fromkeys("SPACE / ENTER = NEW RUNRENDERING AUDIO ASSETS - PLEASE WAITAUDIO DISABLEDÅÄÖÜåäöü?".replace(" ", "")))

_dotmatrix_font = None
_dotmatrix_checked = False
_dotmatrix_warning_printed = False


def _repo_path(*parts) -> str:
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, *parts)


def _warn_dotmatrix(message: str):
    global _dotmatrix_warning_printed
    if not _dotmatrix_warning_printed:
        print(f"[WARN] Cube Libre dot-matrix font unavailable: {message}", file=sys.stderr)
        print("[WARN] Falling back to normal pygame UI font for that text.", file=sys.stderr)
        _dotmatrix_warning_printed = True


def get_dotmatrix_font():
    """Load the optional procedural dot-matrix font once, or return None.

    Runtime files expected in the repository root/layout:
      - _cube_libre/dotmatrix_font.py
      - assets/fonts/cube_libre_5x7.json

    The standalone editor is also checked so local builds warn if the generator
    utility was not copied, but the editor is not required for gameplay.
    """
    global _dotmatrix_font, _dotmatrix_checked
    if _dotmatrix_checked:
        return _dotmatrix_font
    _dotmatrix_checked = True

    try:
        from _cube_libre.dotmatrix_font import DotMatrixFont
    except Exception as exc:
        _warn_dotmatrix(f"could not import dotmatrix_font.py ({exc.__class__.__name__}: {exc})")
        return None

    editor_path = _repo_path(DOTMATRIX_EDITOR_FILE)
    if not os.path.exists(editor_path):
        print(f"[WARN] Dot-matrix editor/generator not found at {editor_path}; gameplay can continue.", file=sys.stderr)

    font_path = _repo_path(DOTMATRIX_FONT_FILE)
    try:
        if os.path.exists(font_path):
            font = DotMatrixFont.load(font_path)
        else:
            print(f"[WARN] Dot-matrix JSON font not found at {font_path}; using built-in starter glyphs.", file=sys.stderr)
            font = DotMatrixFont.from_builtin()

        missing = []
        for ch in DOTMATRIX_REQUIRED_CHARS:
            glyph = font.get_glyph(ch)
            if not glyph or not any("1" in row for row in glyph):
                missing.append(ch)
        if missing:
            raise RuntimeError("missing/blank glyphs: " + "".join(dict.fromkeys(missing)))

        _dotmatrix_font = font
        return _dotmatrix_font
    except Exception as exc:
        _warn_dotmatrix(f"could not load/use {font_path} ({exc.__class__.__name__}: {exc})")
        _dotmatrix_font = None
        return None


def make_dotmatrix_title_prompt(label: str, t: float, color) -> object:
    """Return a transparent pygame Surface for the title start prompt, or None.

    Long setup/failure notices are allowed to fall back to the normal pygame font
    if the dot-matrix version would need to become unreadably tiny.
    """
    font = get_dotmatrix_font()
    if font is None:
        return None

    label = str(label)
    # First config is the intended look for "SPACE / ENTER = NEW RUN".
    # This must be actually smaller than the old pygame prompt: reduce dot_size
    # from 4 to 3. Merely tightening the gap did not visibly change enough.
    # Smaller fallbacks are kept for long first-run audio setup notices.
    configs = (
        (3, 1, 2),
        (3, 1, 1),
        (2, 1, 1),
    )
    chosen = None
    for dot_size, gap, char_spacing in configs:
        w, h = font.measure(label, dot_size=dot_size, gap=gap, char_spacing=char_spacing, line_spacing=dot_size * 2)
        if w <= 742 and h <= 58:
            chosen = (dot_size, gap, char_spacing, w, h)
            break
    if chosen is None:
        return None

    dot_size, gap, char_spacing, _w, _h = chosen
    surf = pygame.Surface((760, 70), pygame.SRCALPHA)
    cx, cy = surf.get_width() // 2, surf.get_height() // 2
    r, g, b = [int(v) for v in color[:3]]

    # Cheap glow: draw the dot glyphs a few times with alpha before the main pass.
    # Keep the glow slightly tighter too, otherwise the smaller prompt still
    # looks bloated because of the halo.
    for ox, oy, alpha in ((-1, 0, 32), (1, 0, 32), (0, -1, 32), (0, 1, 32), (0, 0, 56)):
        font.draw(
            surf, label, (cx + ox, cy + oy),
            dot_size=dot_size, gap=gap, char_spacing=char_spacing,
            color=(r, g, b, alpha), align="center", valign="middle",
            dot_shape="square", blink_phase=t, blink_rate=5.2, blink_depth=0.18,
        )

    font.draw(
        surf, label, (cx + 1, cy + 1),
        dot_size=dot_size, gap=gap, char_spacing=char_spacing,
        color=(0, 0, 0, 165), align="center", valign="middle",
        dot_shape="square",
    )
    font.draw(
        surf, label, (cx, cy),
        dot_size=dot_size, gap=gap, char_spacing=char_spacing,
        color=(r, g, b, 255), align="center", valign="middle",
        dot_shape="square", border_color=(255, 255, 255, 65),
        blink_phase=t, blink_rate=5.2, blink_depth=0.10,
    )
    return surf


def make_fallback_title_prompt(label: str, t: float, color) -> object:
    """Original pygame-font title prompt, kept as fallback."""
    prompt = pygame.Surface((760, 70), pygame.SRCALPHA)
    big = get_font(32, True)
    tr, tg, tb = [int(v) for v in color[:3]]
    for ox, oy, alpha in [(-3, 0, 60), (3, 0, 60), (0, -3, 60), (0, 3, 60), (0, 0, 95)]:
        glow = big.render(label, True, (tr, tg, tb))
        glow.set_alpha(alpha)
        prompt.blit(glow, ((prompt.get_width() - glow.get_width()) // 2 + ox, 16 + oy))
    shadow = big.render(label, True, (0, 0, 0))
    shadow.set_alpha(165)
    prompt.blit(shadow, ((prompt.get_width() - shadow.get_width()) // 2 + 2, 18))
    text_surf = big.render(label, True, (tr, tg, tb))
    prompt.blit(text_surf, ((prompt.get_width() - text_surf.get_width()) // 2, 16))
    return prompt


def make_text_panel(lines, title=None, footer=None, width=720):
    """Create a transparent pygame Surface for a centered overlay panel."""
    title_font = get_font(54, True)
    body_font = get_font(31, False)
    footer_font = get_font(23, False)

    rendered = []
    if title:
        rendered.append((title_font.render(title, True, (235, 255, 255)), 18))
    for line in lines:
        rendered.append((body_font.render(line, True, (230, 240, 245)), 10))
    if footer:
        rendered.append((footer_font.render(footer, True, (170, 210, 220)), 0))

    pad_x = 34
    pad_y = 28
    inner_w = max([surf.get_width() for surf, _ in rendered] + [width - pad_x * 2])
    panel_w = min(max(inner_w + pad_x * 2, 560), DISPLAY[0] - 80)
    panel_h = pad_y * 2 + sum(surf.get_height() + gap for surf, gap in rendered)

    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    # Dark translucent slab with a cyan edge; primitive but readable over OpenGL chaos.
    pygame.draw.rect(panel, (0, 0, 0, 190), panel.get_rect(), border_radius=16)
    pygame.draw.rect(panel, (0, 225, 230, 170), panel.get_rect(), width=2, border_radius=16)

    y = pad_y
    for surf, gap in rendered:
        x = (panel_w - surf.get_width()) // 2
        panel.blit(surf, (x, y))
        y += surf.get_height() + gap
    return panel


def draw_surface_2d(surface, x_center, y_center):
    """Draw a pygame surface as a textured 2D overlay in screen pixel coordinates."""
    width, height = surface.get_size()
    rgba = pygame.image.tostring(surface, "RGBA", True)

    tex_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba)

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, DISPLAY[0], 0, DISPLAY[1])

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glDisable(GL_DEPTH_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_TEXTURE_2D)

    x0 = x_center - width / 2
    y0 = DISPLAY[1] - y_center - height / 2
    x1 = x0 + width
    y1 = y0 + height

    glColor4f(1.0, 1.0, 1.0, 1.0)
    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(x0, y0)
    glTexCoord2f(1.0, 0.0); glVertex2f(x1, y0)
    glTexCoord2f(1.0, 1.0); glVertex2f(x1, y1)
    glTexCoord2f(0.0, 1.0); glVertex2f(x0, y1)
    glEnd()

    glDisable(GL_TEXTURE_2D)
    glDisable(GL_BLEND)
    glEnable(GL_DEPTH_TEST)

    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)

    glDeleteTextures([tex_id])


def render_win_overlay(escape_count: int, score: int, best_escape: int, timer: float, level: int, highest_level: int):
    """End-of-level transcendence card: black text in a white void.

    The old panel over the still-visible game scene made the level ending feel like
    normal UI. This deliberately cuts to a blank white space, prints the score in
    black, then lets the text fade toward white before the next level fades in.
    """
    if timer <= 0.0:
        return

    total = max(0.001, TRANSCENDENCE_WHITE_SECONDS)
    elapsed = clamp(total - timer, 0.0, total)
    progress = elapsed / total

    # Actual blank white void, not a translucent overlay over the game scene.
    inv = reassembly_inversion_amount(progress)
    void_level = 1.0 - inv
    glClearColor(void_level, void_level, void_level, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glClearColor(*BACKGROUND)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    # Hold black text for readability, then fade it to white.
    fade_to_white = smoothstep((progress - 0.56) / 0.40)
    shade = int(255 * fade_to_white)
    text_color = (shade, shade, shade)

    pct = escape_count / MAX_CELLS
    bonus = int(round(escape_count * 100))
    panel = pygame.Surface((900, 300), pygame.SRCALPHA)

    title_font = get_font(58, True)
    level_font = get_font(38, True)
    body_font = get_font(25, False)
    small_font = get_font(21, False)
    footer_font = get_font(14, False)

    lines = [
        (title_font.render("TRANSCENDENCE", True, text_color), 18),
        (level_font.render(f"LEVEL {level} COMPLETE", True, text_color), 88),
        (body_font.render(f"CUBES INTACT: {escape_count}/{MAX_CELLS}  ({pct:.0%})", True, text_color), 150),
        (body_font.render(f"SCORE +{bonus}   ·   TOTAL SCORE {score}", True, text_color), 190),
        (small_font.render(f"BEST ESCAPE {best_escape}/{MAX_CELLS}   ·   HIGHEST LEVEL {highest_level}", True, text_color), 244),
    ]

    # Subtle scale/breath before the fade, still black-on-white and clean.
    breath = 1.0 + 0.012 * math.sin(elapsed * 4.0)
    for surf, y in lines:
        if breath != 1.0 and y < 120:
            w = max(1, int(surf.get_width() * breath))
            h = max(1, int(surf.get_height() * breath))
            surf = pygame.transform.smoothscale(surf, (w, h))
        panel.blit(surf, ((panel.get_width() - surf.get_width()) // 2, y))

    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2 - 14)

    footer_text = "SPACE / ENTER = next level   ·   ESC = title   ·   H = help"
    footer = footer_font.render(footer_text, True, text_color)
    footer_panel = pygame.Surface((footer.get_width() + 20, footer.get_height() + 10), pygame.SRCALPHA)
    footer_panel.blit(footer, (10, 4))
    draw_surface_2d(footer_panel, DISPLAY[0] // 2, DISPLAY[1] - 24)

def _blink(t: float, hz: float = 4.0) -> bool:
    return math.sin(t * math.tau * hz) > 0.0


def _hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return r, g, b


def level_ready_palette(level: int):
    """Deterministic bright-ish solid panel color per level.

    It should feel random across levels, but remain stable so level 7 does not
    become a different nightclub every restart.
    """
    rng = random.Random(0xC0BE + int(level) * 7919)
    # Avoid too-dark mud because the ready card uses a solid fill.
    h = rng.random()
    s = rng.uniform(0.50, 0.82)
    v = rng.uniform(0.62, 0.92)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def render_outlined_text(font, text: str, fill, outline=(0, 0, 0), width: int = 3):
    """Pygame text surface with a thick pixel-ish black outline."""
    base = font.render(text, True, fill)
    surf = pygame.Surface((base.get_width() + width * 2, base.get_height() + width * 2), pygame.SRCALPHA)
    for dy in range(-width, width + 1):
        for dx in range(-width, width + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy <= width * width + 1:
                shadow = font.render(text, True, outline)
                surf.blit(shadow, (dx + width, dy + width))
    surf.blit(base, (width, width))
    return surf


def render_hud(player: "PlayerCube", t: float, score: int, best_escape: int, game_state: str, level: int, highest_level: int):
    """Animated top-left HUD: cube count, score, warning blink at low integrity."""
    intact = player.intact_count()
    pct = intact / MAX_CELLS

    warning = ""
    if intact <= CRITICAL_CUBE_WARNING:
        warning = "CRITICAL CUBE LOSS"
    elif intact <= LOW_CUBE_WARNING:
        warning = "WARNING: STRUCTURE FAILING"

    # animated/pulsing panel; when low, blink the border/background a little.
    # Wider than earlier versions because the level/score stats were getting clipped.
    w, h = 625, 142 if warning else 112
    surf = pygame.Surface((w, h), pygame.SRCALPHA)

    pulse = 0.5 + 0.5 * math.sin(t * 5.5)
    danger_blink = bool(warning) and _blink(t, 4.0)
    bg_alpha = 132 + int(34 * pulse)
    border = (0, 230, 240, 160)
    if warning:
        border = (255, 80, 40, 235 if danger_blink else 90)
        bg_alpha = 170 if danger_blink else 122

    pygame.draw.rect(surf, (0, 0, 0, bg_alpha), surf.get_rect(), border_radius=12)
    pygame.draw.rect(surf, border, surf.get_rect(), width=2, border_radius=12)

    title_font = get_font(22, True)
    small_font = get_font(15, False)
    warn_font = get_font(19, True)

    if intact <= CRITICAL_CUBE_WARNING:
        count_color = (255, 80, 45) if danger_blink else (255, 190, 150)
    elif intact <= LOW_CUBE_WARNING:
        count_color = (255, 215, 70) if danger_blink else (255, 245, 185)
    else:
        count_color = (220, 255, 255)

    surf.blit(title_font.render(f"LVL {level:02d}  ·  CUBES {intact:03d}/{MAX_CELLS}", True, count_color), (16, 12))
    surf.blit(small_font.render(f"INTEGRITY {pct:5.1%}   ·   SCORE {score}", True, (195, 225, 230)), (16, 43))
    surf.blit(small_font.render(f"BEST ESCAPE {best_escape}/{MAX_CELLS}   ·   HIGHEST LEVEL {highest_level}", True, (165, 210, 220)), (16, 66))

    if warning and danger_blink:
        surf.blit(warn_font.render(warning, True, (255, 70, 35)), (16, 101))
    elif warning:
        surf.blit(warn_font.render(warning, True, (255, 180, 95)), (16, 101))

    # slight animated drift, because the old game already lives in rotating-axis hell
    x_center = 26 + w / 2 + math.sin(t * 2.1) * (2.0 if warning else 0.8)
    y_center = 24 + h / 2 + math.cos(t * 1.7) * (1.6 if warning else 0.6)
    draw_surface_2d(surf, x_center, y_center)


def render_fullscreen_overlay(color, alpha: float):
    """Full-screen 2D color overlay. Used for whiteout death fade and warp tint."""
    alpha = clamp(alpha, 0.0, 1.0)
    if alpha <= 0.0:
        return

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(-1, 1, -1, 1)

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glDisable(GL_DEPTH_TEST)
    glColor4f(color[0], color[1], color[2], alpha)
    glBegin(GL_QUADS)
    glVertex2f(-1, -1)
    glVertex2f( 1, -1)
    glVertex2f( 1,  1)
    glVertex2f(-1,  1)
    glEnd()
    glEnable(GL_DEPTH_TEST)

    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glDisable(GL_BLEND)


def smoothstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def render_portal_warp(t: float, progress: float, escaped_cubes: int):
    """Psychedelic warp-drive/star-woosh overlay for portal entry."""
    p = smoothstep(progress)
    cx, cy = DISPLAY[0] * 0.5, DISPLAY[1] * 0.5
    max_r = math.sqrt(DISPLAY[0] ** 2 + DISPLAY[1] ** 2) * 0.62

    # Dark/colored veil first, then star streaks and tunnel rings.
    r, g, b = _hsv(t * 0.08 + p * 0.24, 0.75, 0.45)
    render_fullscreen_overlay((r, g, b), 0.20 + 0.35 * p)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(0, DISPLAY[0], 0, DISPLAY[1])

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()
    glDisable(GL_DEPTH_TEST)

    # Expanding chromatic tunnel rings.
    for ring in range(11):
        frac = ((ring / 11.0) + p * 1.55 + t * 0.18) % 1.0
        radius = 18.0 + frac * max_r
        hue = t * 0.10 + frac * 0.85
        rr, gg, bb = _hsv(hue, 0.90, 1.0)
        alpha = (1.0 - frac) * (0.10 + 0.35 * p)
        glColor4f(rr, gg, bb, alpha)
        glLineWidth(2.0 + 6.0 * p)
        glBegin(GL_LINE_LOOP)
        # deliberately polygonal/cubistic rings rather than smooth circles
        segs = 28
        wobble = math.sin(t * 6.0 + ring) * 0.08
        for i in range(segs):
            a = (i / segs) * math.tau + t * (0.35 + ring * 0.015)
            squash = 0.70 + 0.20 * math.sin(t * 2.0 + ring)
            x = cx + math.cos(a) * radius * (1.0 + wobble)
            y = cy + math.sin(a) * radius * squash
            glVertex2f(x, y)
        glEnd()

    # Warp-drive star streaks: lines shoot outward from near the center.
    glLineWidth(1.3 + 4.7 * p)
    random.seed(1337)  # deterministic star tunnel; keeps it stable frame-to-frame
    for i in range(170):
        base = random.random() * math.tau
        speed = 0.35 + random.random() * 1.55
        phase = (random.random() + t * speed + p * 2.2) % 1.0
        angle = base + math.sin(t * 0.9 + i) * 0.18 + p * 0.55
        r0 = (phase ** 2.1) * max_r * 0.18
        r1 = r0 + (42.0 + 330.0 * p) * (0.35 + phase)
        x0 = cx + math.cos(angle) * r0
        y0 = cy + math.sin(angle) * r0 * 0.78
        x1 = cx + math.cos(angle) * min(max_r, r1)
        y1 = cy + math.sin(angle) * min(max_r, r1) * 0.78
        rr, gg, bb = _hsv((i * 0.013 + t * 0.16 + p * 0.4), 0.35 + 0.60 * p, 1.0)
        alpha = 0.12 + 0.72 * p * (1.0 - abs(phase - 0.55))
        glColor4f(rr, gg, bb, alpha)
        glBegin(GL_LINES)
        glVertex2f(x0, y0)
        glVertex2f(x1, y1)
        glEnd()
    random.seed()

    # Brightening singularity toward the end.
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(1.0, 1.0, 1.0, max(0.0, (p - 0.70) / 0.30) * 0.95)
    glBegin(GL_QUADS)
    glVertex2f(0, 0)
    glVertex2f(DISPLAY[0], 0)
    glVertex2f(DISPLAY[0], DISPLAY[1])
    glVertex2f(0, DISPLAY[1])
    glEnd()

    glEnable(GL_DEPTH_TEST)
    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glDisable(GL_BLEND)

    # Text on top of warp. Texture-per-frame is fine for a prototype HUD.
    font = get_font(31, True)
    small = get_font(20, False)
    surf = pygame.Surface((520, 86), pygame.SRCALPHA)
    pygame.draw.rect(surf, (0, 0, 0, 70), surf.get_rect(), border_radius=16)
    hue = (t * 0.18) % 1.0
    tr, tg, tb = [int(v * 255) for v in _hsv(hue, 0.55, 1.0)]
    text = font.render("WARPING INTO THE STARS", True, (tr, tg, tb))
    sub = small.render(f"cargo manifest: {escaped_cubes}/{MAX_CELLS} cubes survived", True, (220, 245, 255))
    surf.blit(text, ((surf.get_width() - text.get_width()) // 2, 14))
    surf.blit(sub, ((surf.get_width() - sub.get_width()) // 2, 53))
    draw_surface_2d(surf, DISPLAY[0] // 2, DISPLAY[1] - 95)


# -----------------------------------------------------------------------------
# Title screen: CUBE / LIBRE built from rotating mini-cubes
# -----------------------------------------------------------------------------

TITLE_GLYPHS = {
    "C": [
        "01110",
        "10001",
        "10000",
        "10000",
        "10000",
        "10001",
        "01110",
    ],
    "U": [
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "01110",
    ],
    "B": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10001",
        "10001",
        "11110",
    ],
    "E": [
        "11111",
        "10000",
        "10000",
        "11110",
        "10000",
        "10000",
        "11111",
    ],
    "L": [
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "11111",
    ],
    "I": [
        "11111",
        "00100",
        "00100",
        "00100",
        "00100",
        "00100",
        "11111",
    ],
    "R": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10100",
        "10010",
        "10001",
    ],
}


def _build_title_cells():
    """Return stable cube positions for the title logo.

    Coordinates live in a simple 3D title-logo coordinate system. The two words are
    centered separately, so CUBE and LIBRE stack like a proper title card rather than
    one ugly left-aligned terminal dump.
    """
    cells = []
    lines = ["CUBE", "LIBRE"]
    letter_w = 5
    letter_h = 7
    letter_gap = 1
    cube_gap = 0.70
    line_gap = 1.55

    for line_idx, word in enumerate(lines):
        word_w = len(word) * letter_w + (len(word) - 1) * letter_gap
        x_offset = -word_w * cube_gap * 0.5
        # First line above, second line below. Using row inversion keeps glyphs upright.
        y_base = (letter_h * cube_gap * 0.5 + line_gap * 0.5) if line_idx == 0 else -(letter_h * cube_gap * 0.5 + line_gap * 0.5)

        for ch_idx, ch in enumerate(word):
            glyph = TITLE_GLYPHS[ch]
            letter_x = ch_idx * (letter_w + letter_gap) * cube_gap
            for row, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    x = x_offset + letter_x + col * cube_gap
                    y = y_base + (letter_h - 1 - row) * cube_gap
                    # Stable pseudo-randomized character per cube: different cube kinds,
                    # colors and rotation phases, without flickering randomness.
                    seed = (line_idx + 1) * 10000 + ch_idx * 997 + row * 67 + col * 17
                    rnd = random.Random(seed)
                    hue = (0.50 + rnd.random() * 0.42 + line_idx * 0.12) % 1.0
                    sat = 0.52 + rnd.random() * 0.42
                    val = 0.75 + rnd.random() * 0.25
                    color = _hsv(hue, sat, val)
                    kind = rnd.choice(("solid", "glass", "wire", "hot"))
                    cells.append({
                        "pos": Vec3(x, y, rnd.uniform(-0.34, 0.34)),
                        "color": color,
                        "kind": kind,
                        "phase": rnd.uniform(0.0, 360.0),
                        "axis": random_vec(1.0).normalized(),
                        "scale": rnd.uniform(0.54, 0.76),
                    })
    return cells


TITLE_CELLS = _build_title_cells()


def draw_wire_unit_cube(color, alpha=1.0):
    glColor4f(color[0], color[1], color[2], alpha)
    glLineWidth(1.9)
    glBegin(GL_LINES)
    for vx, vy, vz in CUBE_EDGES:
        glVertex3f(vx, vy, vz)
    glEnd()


def draw_title_logo(t: float):
    """Draw CUBE / LIBRE as a rotating 3D logo made from many little cubes."""
    glPushMatrix()
    # The logo itself breathes and slowly rotates, but remains readable.
    breath = 1.0 + 0.035 * math.sin(t * 2.4)
    glScalef(breath, breath, breath)
    glRotatef(math.sin(t * 0.80) * 8.0, 1, 0, 0)
    glRotatef(math.sin(t * 0.55) * 18.0, 0, 1, 0)
    glRotatef(math.sin(t * 0.40) * 5.0, 0, 0, 1)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    for i, item in enumerate(TITLE_CELLS):
        p = item["pos"]
        color = item["color"]
        kind = item["kind"]
        axis = item["axis"]
        # Tiny phase offsets make the title feel alive, not like a dead bitmap font.
        z_wobble = math.sin(t * 2.6 + item["phase"] * 0.017) * 0.12
        glPushMatrix()
        glTranslatef(p.x, p.y, p.z + z_wobble)
        glRotatef(t * (28.0 + (i % 11) * 3.0) + item["phase"], axis.x, axis.y, axis.z)
        s = item["scale"]
        glScalef(s, s, s)

        if kind == "wire":
            draw_wire_unit_cube(color, 0.92)
        elif kind == "glass":
            draw_unit_cube(color, 0.42, outline=True)
        elif kind == "hot":
            hot = _hsv(t * 0.12 + i * 0.021, 0.85, 1.0)
            draw_unit_cube(hot, 0.84, outline=True)
        else:
            draw_unit_cube(color, 0.96, outline=True)
        glPopMatrix()

    glDisable(GL_BLEND)
    glPopMatrix()


def render_title_help(t: float, score: int, best_escape: int, highest_level: int):
    """Title controls without burying the cube-logo.

    The start prompt is drawn as floating text near the top of the screen, with no
    backing slab. The lower info panel is intentionally short and parked near the
    bottom edge so the rotating CUBE / LIBRE logo remains visible instead of being
    eaten by a giant rectangle.
    """
    pulse = 0.55 + 0.45 * math.sin(t * 4.0)

    # Floating start prompt: no rectangle, just glow/shadow text over the stars.
    # Prefer the Cube Libre procedural dot-matrix font. If its module/JSON is
    # missing or broken, print a warning and keep the old pygame-font prompt.
    tr, tg, tb = [int(v * 255) for v in _hsv(t * 0.10, 0.55, 1.0)]
    if audio_start_blocked():
        label = "RENDERING AUDIO ASSETS - PLEASE WAIT"
    elif audio_setup_failed():
        label = "AUDIO DISABLED - SPACE / ENTER = NEW RUN"
    else:
        label = "SPACE / ENTER = NEW RUN"
    prompt = make_dotmatrix_title_prompt(label, t, (tr, tg, tb))
    if prompt is None:
        prompt = make_fallback_title_prompt(label, t, (tr, tg, tb))
    draw_surface_2d(prompt, DISPLAY[0] // 2, 54)

    # During first-run procedural audio rendering, keep the title screen quiet:
    # show the logo, the top rendering prompt, and the dedicated setup overlay only.
    # The controls/hotkey panel and footer become visible once start is possible.
    if audio_start_blocked():
        return

    # Compact bottom info panel. Keep it low enough to not cover the title cubes.
    panel_w = min(max(880, DISPLAY[0] - 120), 1020)
    panel = pygame.Surface((panel_w, 112), pygame.SRCALPHA)
    pygame.draw.rect(panel, (0, 0, 0, 106), panel.get_rect(), border_radius=16)
    pygame.draw.rect(panel, (0, 235, 240, 95 + int(85 * pulse)), panel.get_rect(), width=2, border_radius=16)

    small = get_font(17, False)
    tiny = get_font(15, False)
    stat = get_font(15, False)

    lines = [
        (small.render("A/D or ←/→: move X    W/S or ↑/↓: move Y", True, (210, 235, 240)), 12),
        (small.render("Q/E: move Z    Hold Shift for rush    Ctrl+R = reset options    M = mute", True, (210, 235, 240)), 38),
        (tiny.render("Alt+F / F11 / Alt+Enter = fullscreen    H = help overlay", True, (178, 222, 230)), 66),
        (stat.render(f"Score: {score}     Best escape: {best_escape}/{MAX_CELLS} cubes     Highest level: {highest_level}", True, (165, 205, 218)), 89),
    ]

    for surf, y in lines:
        panel.blit(surf, ((panel.get_width() - surf.get_width()) // 2, y))
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] - 84)

    footer_font = get_font(13, False)
    footer = footer_font.render("Esc = quit confirm from title / return to title in-game   ·   Goal: reach the portal with as many cubes intact as possible", True, (150, 190, 200))
    shadow = footer_font.render("Esc = quit confirm from title / return to title in-game   ·   Goal: reach the portal with as many cubes intact as possible", True, (0, 0, 0))
    footer_panel = pygame.Surface((min(DISPLAY[0] - 20, footer.get_width() + 20), footer.get_height() + 8), pygame.SRCALPHA)
    x = max(0, (footer_panel.get_width() - footer.get_width()) // 2)
    footer_panel.blit(shadow, (x + 1, 5))
    footer_panel.blit(footer, (x, 4))
    draw_surface_2d(footer_panel, DISPLAY[0] // 2, DISPLAY[1] - 16)

def render_audio_setup_overlay(t: float):
    """First-run procedural-audio cache notice for slow systems.

    The title window can appear before the long WAV synthesis worker is done.
    Without an explicit overlay, Windows users see a responsive-looking window
    that refuses to start and it looks like the game froze.
    """
    if not bool(_audio.get("assets_missing_on_start")):
        return
    if not (audio_setup_in_progress() or audio_setup_failed()):
        return

    ready, total = audio_asset_cache_progress()
    pulse = 0.55 + 0.45 * math.sin(t * 5.2)
    panel = pygame.Surface((760, 178), pygame.SRCALPHA)
    pygame.draw.rect(panel, (0, 0, 0, 212), panel.get_rect(), border_radius=18)

    if audio_setup_failed():
        border = (255, 90, 45, 150 + int(70 * pulse))
        title_text = "AUDIO SETUP FAILED"
        body_1 = "Procedural audio could not be loaded."
        body_2 = "The game can still run silently."
        body_3 = "SPACE / ENTER = start anyway"
    else:
        border = (0, 235, 245, 138 + int(82 * pulse))
        title_text = "FIRST RUN SETUP"
        body_1 = "Audio assets not found. Rendering procedural sound cache..."
        body_2 = f"{AUDIO_DIR_LABEL} will be reused on later runs."
        body_3 = "Please wait. Start is locked until setup is ready."

    pygame.draw.rect(panel, border, panel.get_rect(), width=2, border_radius=18)

    title = get_font(33, True)
    body = get_font(18, False)
    tiny = get_font(15, False)
    title_s = title.render(title_text, True, (230, 255, 255) if not audio_setup_failed() else (255, 210, 190))
    b1 = body.render(body_1, True, (215, 238, 242))
    b2 = body.render(body_2, True, (185, 224, 230))
    b3 = body.render(body_3, True, (255, 225, 140) if audio_setup_failed() else (180, 255, 235))

    panel.blit(title_s, ((panel.get_width() - title_s.get_width()) // 2, 18))
    panel.blit(b1, ((panel.get_width() - b1.get_width()) // 2, 62))
    panel.blit(b2, ((panel.get_width() - b2.get_width()) // 2, 89))
    panel.blit(b3, ((panel.get_width() - b3.get_width()) // 2, 116))

    bar_x, bar_y, bar_w, bar_h = 90, 150, 580, 6
    pygame.draw.rect(panel, (35, 55, 62, 210), (bar_x, bar_y, bar_w, bar_h), border_radius=4)
    fill_w = int(bar_w * (ready / max(1, total)))
    pygame.draw.rect(panel, (0, 230, 245, 225), (bar_x, bar_y, fill_w, bar_h), border_radius=4)
    progress = tiny.render(f"audio cache files: {ready}/{total}", True, (165, 205, 212))
    panel.blit(progress, ((panel.get_width() - progress.get_width()) // 2, 158))

    # During first-run rendering this replaces the normal bottom controls/footer
    # band. Keep the panel size unchanged; only move it below the CUBE LIBRE logo.
    setup_y = DISPLAY[1] - (panel.get_height() // 2) - 14
    draw_surface_2d(panel, DISPLAY[0] // 2, setup_y)


def render_quit_confirm(t: float):
    pulse = 0.55 + 0.45 * math.sin(t * 5.0)
    panel = make_text_panel(
        [
            "Y = quit to desktop",
            "N / Esc = stay on title screen",
            "SPACE / ENTER = start a fresh run",
        ],
        title="QUIT CUBE LIBRE?",
        width=650,
    )
    # Extra warning rim; make_text_panel already has one border, this gives the prompt
    # a little more 'are you sure?' presence without adding a separate UI system.
    pygame.draw.rect(panel, (255, 230, 120, 75 + int(105 * pulse)), panel.get_rect(), width=3, border_radius=16)
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2 + 150)


def render_reset_confirm(t: float, current_level: int, score: int):
    """Ctrl+R confirmation overlay.

    Reset is destructive enough that it should never instantly throw a level-6
    run back to level 1. Ask whether the player wants a full run reset, a
    current-level restart, or cancellation.
    """
    pulse = 0.55 + 0.45 * math.sin(t * 5.8)
    panel = pygame.Surface((780, 250), pygame.SRCALPHA)
    pygame.draw.rect(panel, (0, 0, 0, 224), panel.get_rect(), border_radius=18)
    pygame.draw.rect(panel, (255, 230, 95, 135 + int(90 * pulse)), panel.get_rect(), width=3, border_radius=18)

    title = get_font(38, True)
    body = get_font(21, False)
    key_font = get_font(23, True)
    tiny = get_font(15, False)

    title_s = title.render("RESET CONFIRMATION", True, (255, 235, 145))
    panel.blit(title_s, ((panel.get_width() - title_s.get_width()) // 2, 22))

    warn = body.render("Choose what gets rebuilt. Nothing happens until you choose.", True, (220, 240, 240))
    panel.blit(warn, ((panel.get_width() - warn.get_width()) // 2, 72))

    options = [
        ("1", "reset whole run to LEVEL 1", (255, 190, 145)),
        ("2", f"restart current LEVEL {current_level}", (175, 255, 205)),
        ("Esc / N", "cancel and continue", (200, 225, 235)),
    ]
    y = 112
    for key, text, color in options:
        k = key_font.render(key, True, color)
        body_s = body.render(text, True, (220, 235, 238))
        x = 110
        panel.blit(k, (x, y))
        panel.blit(body_s, (x + 112, y + 2))
        y += 38

    footer = tiny.render(f"Current score: {score}   ·   Ctrl+R opened this screen", True, (155, 190, 198))
    panel.blit(footer, ((panel.get_width() - footer.get_width()) // 2, panel.get_height() - 30))
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2 + 90)


def render_help_overlay(t: float, game_state: str, level: int):
    """Toggleable control/help overlay, drawn on top of the current scene."""
    overlay = pygame.Surface((DISPLAY[0], DISPLAY[1]), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 122))

    panel_w = min(860, max(560, DISPLAY[0] - 140))
    panel_h = 520
    panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
    pulse = 0.55 + 0.45 * math.sin(t * 4.0)
    pygame.draw.rect(panel, (0, 0, 0, 208), panel.get_rect(), border_radius=18)
    pygame.draw.rect(panel, (0, 235, 245, 128 + int(80 * pulse)), panel.get_rect(), width=2, border_radius=18)

    title = get_font(42, True)
    head = get_font(22, True)
    body = get_font(17, False)
    tiny = get_font(14, False)

    tr, tg, tb = [int(v * 255) for v in _hsv(0.50 + t * 0.07, 0.50, 1.0)]
    title_s = title.render("CUBE LIBRE HELP", True, (tr, tg, tb))
    panel.blit(title_s, ((panel_w - title_s.get_width()) // 2, 24))

    rows = [
        (head, "MOVEMENT", (220, 255, 255), 88),
        (body, "A / D or ← / →        move on world X", (210, 235, 240), 120),
        (body, "W / S or ↑ / ↓        move on world Y", (210, 235, 240), 146),
        (body, "Q / E                 move on world Z", (210, 235, 240), 172),
        (body, "Hold Shift            rush", (210, 235, 240), 198),
        (body, f"C                     request re-coupling; loose cubes expire after {RECOUPLING_FRAGMENT_RECOVERABLE_SECONDS:.0f}s", (185, 255, 205), 224),
        (body, f"                      gather rate: {RECOUPLING_GATHER_RATE * 100:.0f}%   limit: {RECOUPLING_REQUEST_LIMIT} per {RECOUPLING_REQUEST_WINDOW_SECONDS:.0f}s", (170, 230, 190), 248),
        (head, "SYSTEM", (220, 255, 255), 276),
        (body, "P                     pause / resume game and audio", (210, 235, 240), 304),
        (body, "L                     locate camera: center view on cube", (210, 235, 240), 328),
        (body, "Space / Enter         start from title or advance after transcendence", (210, 235, 240), 352),
        (body, "Esc                   title screen / quit confirm", (210, 235, 240), 376),
        (body, "Alt+F, F11, Alt+Enter fullscreen / windowed", (210, 235, 240), 400),
        (body, "M                     mute / unmute", (210, 235, 240), 424),
        (body, "Ctrl+R                reset options", (210, 235, 240), 448),
    ]
    for font, text, color, y in rows:
        s = font.render(text, True, color)
        panel.blit(s, (54, y))

    # Credits: intentionally left/right aligned on the same line. Keeping this out
    # of the normal row renderer avoids the old drunk stacked positioning.
    credit_y = panel_h - 54
    credit_left = tiny.render("by FlyingFathead", True, (170, 220, 225))
    credit_right = tiny.render("github.com/FlyingFathead", True, (150, 200, 210))
    panel.blit(credit_left, (54, credit_y))
    panel.blit(credit_right, (panel_w - 54 - credit_right.get_width(), credit_y))

    foot = tiny.render("H = close help   ·   No auto-steering: reorient the cube through the maze yourself", True, (165, 210, 218))
    panel.blit(foot, ((panel_w - foot.get_width()) // 2, panel_h - 24))

    draw_surface_2d(overlay, DISPLAY[0] // 2, DISPLAY[1] // 2)
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2)


def draw_title_screen(t: float, score: int, best_escape: int, highest_level: int):
    """Render the not-playing title state."""
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    # Background starfield / void.
    glTranslatef(0.0, 0.0, -55.0)
    glRotatef(t * 2.0, 0, 1, 0)
    glRotatef(math.sin(t * 0.37) * 6.0, 1, 0, 0)
    draw_stars()

    # Logo, closer than the stars.
    glLoadIdentity()
    glTranslatef(0.0, -1.15, -23.5)
    draw_title_logo(t)

    # A mild psychedelic veil to keep it tied to the portal/star-transcendence vibe.
    r, g, b = _hsv(t * 0.035, 0.60, 0.30)
    render_fullscreen_overlay((r, g, b), 0.10)
    render_title_help(t, score, best_escape, highest_level)

# -----------------------------------------------------------------------------
# Game state
# -----------------------------------------------------------------------------

@dataclass
class Fragment:
    pos: Vec3
    vel: Vec3
    color: tuple
    rot_axis: Vec3
    rot_angle: float
    angular_vel: float
    age: float = 0.0

    def update(self, dt: float):
        self.age += dt
        self.pos = self.pos + self.vel * dt
        self.rot_angle += self.angular_vel * dt
        # gentle damping so debris does not instantly leave the universe
        damp = max(0.0, 1.0 - 0.18 * dt)
        self.vel = self.vel * damp

    @property
    def recoverable_lifetime(self) -> float:
        # Visual debris and C-based re-coupling share the same expiry window:
        # once this time passes, the cube is gone for this level attempt.
        return min(FRAGMENT_LIFETIME, RECOUPLING_FRAGMENT_RECOVERABLE_SECONDS)

    @property
    def alive(self) -> bool:
        return self.age < self.recoverable_lifetime

    @property
    def alpha(self) -> float:
        return max(0.0, 1.0 - self.age / max(0.001, self.recoverable_lifetime))

    @property
    def expiry_ratio(self) -> float:
        return clamp(self.age / max(0.001, self.recoverable_lifetime), 0.0, 1.0)

    @property
    def expiry_remaining(self) -> float:
        return max(0.0, self.recoverable_lifetime - self.age)

    @property
    def expiry_warning(self) -> bool:
        return self.expiry_remaining <= RECOUPLING_FRAGMENT_EXPIRY_BLINK_SECONDS


@dataclass
class ReassemblyParticle:
    origin_pos: Vec3
    star_pos: Vec3
    target_pos: Vec3
    color: tuple
    rot_axis: Vec3
    rot_start: float
    rot_end: float
    delay: float
    scale: float


@dataclass
class RecouplingParticle:
    start_pos: Vec3
    target_cell: tuple
    color: tuple
    rot_axis: Vec3
    rot_start: float
    rot_end: float
    delay: float
    scale: float


def lerp_vec(a: Vec3, b: Vec3, t_value: float) -> Vec3:
    return a * (1.0 - t_value) + b * t_value


def make_reassembly_particles(player: "PlayerCube"):
    """Snapshot the current debris/cloud and prepare a deterministic rebuild of the cube.

    Death is no longer just a white screen. The remaining visible pieces first burn
    white and vanish backward into the starfield, then those same pieces get recalled
    back into a pristine 5x5x5 cube.
    """
    # Existing debris gives the death animation continuity with the actual wreckage.
    wreckage = [f.pos for f in player.fragments]
    wreckage += [player.cell_world_pos(cell) for cell in sorted(player.alive_cells)]
    if not wreckage:
        wreckage = [player.origin + random_vec(2.2)]

    target_cells = [
        (x, y, z)
        for x in centered_coords(CUBE_SIZE)
        for y in centered_coords(CUBE_SIZE)
        for z in centered_coords(CUBE_SIZE)
    ]

    particles = []
    for i, cell in enumerate(target_cells):
        rnd = random.Random(9001 + i * 37)
        origin = wreckage[i % len(wreckage)] + random_vec(0.28 + 0.55 * rnd.random())
        target = Vec3(
            START_ORIGIN[0] + cell[0] * CELL_SPACING,
            START_ORIGIN[1] + cell[1] * CELL_SPACING,
            START_ORIGIN[2] + cell[2] * CELL_SPACING,
        )
        # Pull the cubes backward and outward into the void. Because the whole course
        # rotates, these read as star-background positions rather than a flat dissolve.
        star = Vec3(
            target.x + rnd.uniform(-18.0, 18.0),
            target.y + rnd.uniform(-13.0, 13.0),
            target.z + rnd.uniform(-36.0, -18.0),
        )
        particles.append(
            ReassemblyParticle(
                origin_pos=origin,
                star_pos=star,
                target_pos=target,
                color=player.color_for_cell(cell),
                rot_axis=random_vec(1.0).normalized(),
                rot_start=rnd.uniform(0.0, 360.0),
                rot_end=rnd.uniform(360.0, 1080.0),
                delay=rnd.uniform(0.0, 0.24),
                scale=rnd.uniform(0.82, 1.08),
            )
        )
    return particles


def recoupling_request_limit_for_level(level: int) -> int:
    """Return the allowed re-coupling request count for this level.

    The default is flat: 5 requests per 10 seconds. Later we can turn
    RECOUPLING_REQUEST_LIMIT_DROP_PER_LEVEL upward if higher levels should get
    stingier without rewriting the mechanic.
    """
    level = max(1, int(level))
    return max(
        RECOUPLING_MIN_REQUEST_LIMIT,
        RECOUPLING_REQUEST_LIMIT - max(0, level - 1) * RECOUPLING_REQUEST_LIMIT_DROP_PER_LEVEL,
    )


def _recoupling_target_cells(player: "PlayerCube", count: int):
    """Pick missing local cells so the body rebuilds toward a compact cube.

    Prefer holes adjacent to the current body, then cells closer to the cube core.
    This makes C feel like structural re-coupling rather than random block soup.
    """
    if count <= 0:
        return []
    all_cells = [
        (x, y, z)
        for x in centered_coords(CUBE_SIZE)
        for y in centered_coords(CUBE_SIZE)
        for z in centered_coords(CUBE_SIZE)
    ]
    alive = set(player.alive_cells)
    missing = [cell for cell in all_cells if cell not in alive]
    if not missing:
        return []

    def neighbor_count(cell):
        x, y, z = cell
        n = 0
        for nb in ((x + 1, y, z), (x - 1, y, z), (x, y + 1, z), (x, y - 1, z), (x, y, z + 1), (x, y, z - 1)):
            if nb in alive:
                n += 1
        return n

    # Rebuild toward an actual cube: close holes glued to the surviving mass first,
    # then compact toward the centre. Edge/corner cells wait unless there is nothing
    # better, which helps damaged shapes stop looking like exploded teeth.
    missing.sort(key=lambda c: (-neighbor_count(c), c[0] * c[0] + c[1] * c[1] + c[2] * c[2], abs(c[0]) + abs(c[1]) + abs(c[2]), c))
    return missing[:count]


def begin_recoupling(player: "PlayerCube"):
    """Start reclaiming live debris fragments into missing cube cells.

    Returns a list of RecouplingParticle objects. Assigned fragments are removed
    from player.fragments because they are now controlled by the recoupling effect.
    """
    if player.intact_count() >= MAX_CELLS:
        return []
    usable = [f for f in player.fragments if f.alive and f.expiry_remaining > 0.05]
    if not usable:
        return []
    max_count = min(RECOUPLING_MAX_PER_REQUEST, len(usable), MAX_CELLS - player.intact_count())

    # Lossy capture: a re-coupling request does not perfectly vacuum every recoverable
    # fragment. At the default 0.90, ten available loose cubes usually gather about
    # nine, leaving the rest to keep drifting/blinking toward expiry. A tiny
    # stochastic rounding step avoids always flooring 9.9 to 9 etc.
    gather_rate = clamp(RECOUPLING_GATHER_RATE, 0.0, 1.0)
    if gather_rate <= 0.0:
        return []
    desired = max_count * gather_rate
    gather_count = int(math.floor(desired))
    if random.random() < (desired - gather_count):
        gather_count += 1
    if max_count > 0:
        gather_count = clamp(gather_count, 1, max_count)
    gather_count = int(gather_count)

    targets = _recoupling_target_cells(player, gather_count)
    if not targets:
        return []

    # Prefer younger/nearer fragments; old grey specks far in the void should not
    # magically become better than the chunks that just got knocked off.
    usable.sort(key=lambda f: (f.age, (f.pos - player.origin).length()))
    selected = usable[:len(targets)]
    selected_ids = {id(f) for f in selected}
    player.fragments = [f for f in player.fragments if id(f) not in selected_ids]

    particles = []
    for i, (frag, cell) in enumerate(zip(selected, targets)):
        rnd = random.Random(51000 + i * 97 + int(frag.age * 1000))
        particles.append(
            RecouplingParticle(
                start_pos=frag.pos,
                target_cell=cell,
                color=fragment_fade_color(frag),
                rot_axis=frag.rot_axis.normalized(),
                rot_start=frag.rot_angle,
                rot_end=frag.rot_angle + rnd.uniform(360.0, 1080.0),
                delay=rnd.uniform(0.0, 0.20),
                scale=rnd.uniform(0.80, 1.08),
            )
        )
    return particles


def finish_recoupling(player: "PlayerCube", particles) -> int:
    restored = 0
    for part in particles:
        if part.target_cell not in player.alive_cells and len(player.alive_cells) < MAX_CELLS:
            player.alive_cells.add(part.target_cell)
            restored += 1
    return restored


def draw_recoupling_particles(player: "PlayerCube", particles, t: float, progress: float):
    if not particles:
        return
    progress = clamp(progress, 0.0, 1.0)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    for i, part in enumerate(particles):
        local = clamp((progress - part.delay) / max(0.01, 1.0 - part.delay), 0.0, 1.0)
        u = smoothstep(local)
        start = part.start_pos
        target = player.cell_world_pos(part.target_cell)
        # A little spiralling return path: not a pure linear vacuum-cleaner pull,
        # but still readable as the loose cell being re-coupled to the body.
        axis = part.rot_axis.normalized()
        swirl_radius = (1.0 - u) * math.sin(u * math.pi) * (0.35 + 0.30 * ((i % 5) / 4.0))
        swirl = Vec3(
            math.sin(t * 8.0 + i * 0.73) * swirl_radius,
            math.cos(t * 7.2 + i * 0.41) * swirl_radius,
            math.sin(t * 6.1 + i * 0.29) * swirl_radius,
        )
        pos = lerp_vec(start, target, u) + swirl
        scale = max(0.10, part.scale * (0.40 + 0.60 * u))
        rot = part.rot_start * (1.0 - u) + part.rot_end * u
        # Green-white system glow over the fragment's current colour.
        system_green = (0.20, 1.0, 0.46)
        white_pull = smoothstep(u) * 0.45
        color = tuple(
            part.color[j] * (1.0 - 0.55 * u) + system_green[j] * (0.55 * u)
            for j in range(3)
        )
        color = tuple(color[j] * (1.0 - white_pull) + 1.0 * white_pull for j in range(3))
        alpha = 0.22 + 0.78 * smoothstep(local)

        glPushMatrix()
        glTranslatef(pos.x, pos.y, pos.z)
        glRotatef(rot + t * 80.0 * (1.0 - u), axis.x, axis.y, axis.z)
        glScalef(scale, scale, scale)
        draw_unit_cube(color, alpha, outline=True)
        glPopMatrix()

    # Draw faint green targeting filaments so the effect reads even when the
    # mini-cubes are tiny or hidden behind the rotating scene.
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glLineWidth(1.0 + 2.0 * math.sin(progress * math.pi))
    glBegin(GL_LINES)
    for i, part in enumerate(particles):
        local = clamp((progress - part.delay) / max(0.01, 1.0 - part.delay), 0.0, 1.0)
        if local <= 0.0 or local >= 0.98:
            continue
        u = smoothstep(local)
        start = part.start_pos
        target = player.cell_world_pos(part.target_cell)
        pos = lerp_vec(start, target, u)
        glColor4f(0.25, 1.0, 0.48, 0.18 * (1.0 - abs(u - 0.5) * 1.5))
        glVertex3f(pos.x, pos.y, pos.z)
        glVertex3f(target.x, target.y, target.z)
    glEnd()

    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDisable(GL_BLEND)


def draw_reassembly_particles(particles, t: float, phase: str, progress: float):
    progress = clamp(progress, 0.0, 1.0)
    if not particles:
        return

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    for i, part in enumerate(particles):
        outline_color = None
        extra_spin = t * 95.0

        if phase == "dissolve":
            u = smoothstep(progress)
            pos = lerp_vec(part.origin_pos, part.star_pos, u)
            # Snap toward white quickly, then shrink as it drifts into the starfield.
            whiten = smoothstep(min(1.0, progress * 2.4))
            color = tuple(part.color[j] * (1.0 - whiten) + whiten for j in range(3))
            alpha = 1.0 - 0.55 * u
            scale = max(0.08, part.scale * (1.0 - 0.82 * u))
            rot = part.rot_start + part.rot_end * u
            outline = progress < 0.72
        else:
            # White-void rebuild:
            #   1) pieces fly inward toward their proper cube slots;
            #   2) once in-place, the individual mini-cube rotations continue briefly;
            #   3) the rotations ease to zero so the big cube visibly locks together;
            #   4) the complete cube holds before the black/white fade-back.
            if phase == "void_reassemble":
                recall_done_at = REASSEMBLY_RECALL_DONE_AT
                settle_done_at = REASSEMBLY_SETTLE_DONE_AT
                local = clamp((progress - part.delay * 0.50) / max(0.01, recall_done_at - part.delay * 0.50), 0.0, 1.0)
            else:
                recall_done_at = 1.0
                settle_done_at = 1.0
                local = clamp((progress - part.delay) / max(0.01, 1.0 - part.delay), 0.0, 1.0)

            # Ease-out-cubic: fast inward travel, then a readable settle.
            u = 1.0 - (1.0 - local) ** 3
            pos = lerp_vec(part.star_pos, part.target_pos, u)
            alpha = 0.18 + 0.82 * smoothstep(local)
            scale = max(0.16, part.scale * (0.28 + 0.72 * smoothstep(local)))
            rot = part.rot_start + part.rot_end * (1.0 - u)
            outline = True

            if phase == "void_reassemble":
                inv = reassembly_inversion_amount(progress)
                color = (1.0 - inv, 1.0 - inv, 1.0 - inv)
                outline_color = (inv, inv, inv)
                alpha = 1.0

                if progress >= recall_done_at:
                    # The cube is now physically assembled, but the little cubes
                    # still settle their rotations for roughly half a second.
                    lock = smoothstep(clamp((progress - recall_done_at) / max(0.001, settle_done_at - recall_done_at), 0.0, 1.0))
                    pos = part.target_pos
                    scale = part.scale
                    rot *= (1.0 - lock)
                    extra_spin *= (1.0 - lock)

                    # Tiny scale compression/relaxation as the complete cube locks.
                    # Not a thick outline pulse: the cells themselves just breathe
                    # into final alignment and then become still.
                    settle_breathe = 1.0 + 0.025 * math.sin(lock * math.pi)
                    scale *= settle_breathe

                    if progress >= settle_done_at:
                        rot = 0.0
                        extra_spin = 0.0
                        scale = part.scale
                else:
                    # The closer a cube gets to its slot, the less random spin it has.
                    spin_keep = 1.0 - smoothstep(local) * 0.55
                    extra_spin *= spin_keep
            else:
                hue_shift = _hsv(t * 0.18 + i * 0.007, 0.40, 1.0)
                color = tuple(0.72 + 0.28 * hue_shift[j] for j in range(3))

        glPushMatrix()
        glTranslatef(pos.x, pos.y, pos.z)
        a = part.rot_axis.normalized()
        glRotatef(rot + extra_spin, a.x, a.y, a.z)
        glScalef(scale, scale, scale)
        draw_unit_cube(color, alpha, outline=outline, outline_color=locals().get("outline_color", None))
        glPopMatrix()

    glDisable(GL_BLEND)

def draw_tossed_reassembly_cube(t: float, progress: float):
    """White-void rebuild as a thrown/tumbling cube, not a thick-outline pulse."""
    p = smoothstep(clamp(progress, 0.0, 1.0))
    # The cube flies in from above/left/back, overshoots slightly, then settles at start.
    arc = math.sin(p * math.pi)
    offset = Vec3(
        -15.0 * (1.0 - p) + 1.2 * math.sin(p * math.pi * 2.0) * (1.0 - p),
        7.5 * arc + 3.0 * (1.0 - p),
        -10.0 * (1.0 - p),
    )
    tumble = (1.0 - p) ** 1.35
    spin_x = 720.0 * tumble + math.sin(t * 2.0) * 3.0
    spin_y = 980.0 * tumble + t * 20.0 * (1.0 - p)
    spin_z = -540.0 * tumble
    settle_pulse = 1.0 + 0.055 * math.sin(clamp((progress - 0.82) / 0.18, 0.0, 1.0) * math.pi)

    glPushMatrix()
    glTranslatef(START_ORIGIN[0] + offset.x, START_ORIGIN[1] + offset.y, START_ORIGIN[2] + offset.z)
    glRotatef(spin_x, 1, 0, 0)
    glRotatef(spin_y, 0, 1, 0)
    glRotatef(spin_z, 0, 0, 1)
    glScalef(settle_pulse, settle_pulse, settle_pulse)

    # Draw the rebuilt cube as white cells with black outlines in the white void.
    for cell in [(x, y, z) for x in centered_coords(CUBE_SIZE) for y in centered_coords(CUBE_SIZE) for z in centered_coords(CUBE_SIZE)]:
        glPushMatrix()
        glTranslatef(cell[0] * CELL_SPACING, cell[1] * CELL_SPACING, cell[2] * CELL_SPACING)
        draw_unit_cube((1.0, 1.0, 1.0), 1.0, outline=True)
        glPopMatrix()

    # Quick black landing shadow/ring at the final beat, then the white fade releases.
    lock = clamp((progress - 0.86) / 0.12, 0.0, 1.0)
    if lock > 0.0:
        half_span = (CUBE_SIZE // 2) * CELL_SPACING + CELL_HALF * 1.08
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(0.0, 0.0, 0.0, (1.0 - lock) * 0.55)
        glLineWidth(2.0 + 5.0 * math.sin(lock * math.pi))
        draw_wire_box(-half_span, half_span, -half_span, half_span, -half_span, half_span)
        glDisable(GL_BLEND)
    glPopMatrix()

def render_reassembly_overlay(t: float, progress: float):
    inv = reassembly_inversion_amount(progress)
    pct = int(clamp(progress, 0.0, 1.0) * 100)
    panel = pygame.Surface((680, 106), pygame.SRCALPHA)
    pulse = 0.5 + 0.5 * math.sin(t * 10.0)
    fg = int(255 * inv)
    bg = int(255 * (1.0 - inv))
    # Keep this sparse; the reassembly is the visual, not a HUD demo.
    pygame.draw.rect(panel, (bg, bg, bg, 8), panel.get_rect(), border_radius=16)
    pygame.draw.rect(panel, (fg, fg, fg, 115 + int(70 * pulse)), panel.get_rect(), width=2, border_radius=16)
    big = get_font(32, True)
    small = get_font(19, False)
    text = big.render("REASSEMBLY IN PROGRESS", True, (fg, fg, fg))
    sub = small.render(f"cubic debris recall: {pct:03d}%", True, (fg, fg, fg))
    panel.blit(text, ((panel.get_width() - text.get_width()) // 2, 20))
    panel.blit(sub, ((panel.get_width() - sub.get_width()) // 2, 63))
    draw_surface_2d(panel, DISPLAY[0] // 2, 92)


def render_reassembly_flash(t: float, progress: float):
    # Fade back into the normal playable scene from the black/white inversion
    # beat at the end of reassembly. In fixed-pipeline immediate-mode GL we
    # do this as an antique negative flash: dark inverse hold -> brief white
    # negative pulse -> fade back into the actual scene.
    p = clamp(progress, 0.0, 1.0)
    # Start in the inverted black void.
    black_alpha = 1.0 - smoothstep(p)
    render_fullscreen_overlay((0.0, 0.0, 0.0), black_alpha)
    # Short white negative pulse right after the release starts; this reads as
    # the old black/white inversion flash without needing shaders.
    pulse = max(0.0, 1.0 - abs(p - 0.18) / 0.18)
    if pulse > 0.0:
        render_fullscreen_overlay((1.0, 1.0, 1.0), 0.62 * smoothstep(pulse))


def reassembly_inversion_amount(progress: float) -> float:
    if not REASSEMBLY_INVERT_FLASH_ENABLED:
        return 0.0
    return smoothstep((clamp(progress, 0.0, 1.0) - REASSEMBLY_INVERT_START_AT) / max(0.001, 1.0 - REASSEMBLY_INVERT_START_AT))


def render_reassembly_invert_flash(progress: float):
    """Deprecated overlay hook kept as a no-op.

    The inversion is now done by actually fading the white void toward black
    and drawing the completed cube as black cells with white outlines. That
    reads like an old black/white inversion instead of just slapping another
    full-screen overlay on top of everything.
    """
    return


def draw_white_void_reassembly_scene(t: float, particles, progress: float):
    """Render the death rebuild in a blank white void, not over the normal course.

    The pieces rebuild at the start position, but the camera recenters that point so
    the player sees one clean outlined cube instead of a tiny mess at the far left of
    the actual level coordinates.
    """
    inv = reassembly_inversion_amount(progress)
    void_level = 1.0 - inv
    glClearColor(void_level, void_level, void_level, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glClearColor(*BACKGROUND)

    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()
    glTranslatef(0.0, 0.0, -21.5)
    glRotatef(18.0 + math.sin(t * 1.4) * 3.0, 1, 0, 0)
    glRotatef(t * 28.0, 0, 1, 0)
    glRotatef(math.sin(t * 1.1) * 4.0, 0, 0, 1)
    glTranslatef(-START_ORIGIN[0], -START_ORIGIN[1], -START_ORIGIN[2])

    # Old-style rebuild with a late snap: pieces hang in the white void, then
    # slam into the 5x5x5 cube at the final beat before the scene fades back in.
    draw_reassembly_particles(particles, t, "void_reassemble", progress)
    render_reassembly_invert_flash(progress)


class PlayerCube:
    def __init__(self):
        self.origin = Vec3(*START_ORIGIN)
        self.alive_cells = set()
        self.fragments = []
        for x in centered_coords(CUBE_SIZE):
            for y in centered_coords(CUBE_SIZE):
                for z in centered_coords(CUBE_SIZE):
                    self.alive_cells.add((x, y, z))

    def reset(self):
        self.__init__()

    def cell_world_pos(self, cell) -> Vec3:
        x, y, z = cell
        return Vec3(
            self.origin.x + x * CELL_SPACING,
            self.origin.y + y * CELL_SPACING,
            self.origin.z + z * CELL_SPACING,
        )

    def color_for_cell(self, cell):
        _, y, z = cell
        # Stable gradient based on local geometry, not the current spinning view.
        half = CUBE_SIZE // 2
        t = (y + half) / max(1, CUBE_SIZE - 1)
        zt = (z + half) / max(1, CUBE_SIZE - 1)
        return (
            1.0 * (1.0 - t) + 0.08 * t,
            0.18 + 0.32 * zt,
            0.95 * t + 0.15 * (1.0 - t),
        )

    def destroy_cell(self, cell, blast_center: Vec3):
        if cell not in self.alive_cells:
            return False
        self.alive_cells.remove(cell)
        p = self.cell_world_pos(cell)
        away = (p - blast_center).normalized()
        vel = away * random.uniform(2.0, 4.6) + random_vec(0.65)
        vel.y += random.uniform(0.3, 1.1)
        self.fragments.append(
            Fragment(
                pos=p,
                vel=vel,
                color=self.color_for_cell(cell),
                rot_axis=random_vec(1.0).normalized(),
                rot_angle=random.uniform(0, 360),
                angular_vel=random.uniform(-280, 280),
            )
        )
        return True

    def update_fragments(self, dt: float):
        for f in self.fragments:
            f.update(dt)
        self.fragments = [f for f in self.fragments if f.alive]

    def intact_count(self) -> int:
        return len(self.alive_cells)

# -----------------------------------------------------------------------------
# Rotating laser grids
# -----------------------------------------------------------------------------

@dataclass
class LaserGrid:
    name: str
    center: Vec3
    half_y: float
    half_z: float
    spacing: float
    beam_radius: float
    axis: Vec3
    spin_deg_per_sec: float
    phase_deg: float = 0.0
    # A deliberate pass-through aperture. A 5x5x5 cube has cell centers from -2..2,
    # so the opening has to be larger than that or the map is mathematically impossible.
    safe_half_y: float = 2.95
    safe_half_z: float = 2.95
    # Optional moving opening in the grid's LOCAL Y/Z plane. Keep early gates centered;
    # later gates can orbit a little to create timing without becoming a brick wall.
    gap_orbit_y: float = 0.0
    gap_orbit_z: float = 0.0
    gap_spin_deg_per_sec: float = 0.0
    gap_phase_deg: float = 0.0
    color: tuple = (1.0, 0.05, 0.03, 0.95)
    # Optional local-to-world basis. Level 1 leaves this as identity. Later
    # modules use it to rotate the exact same laser-grid logic around 90-degree
    # corridor turns. Stored as optional plain objects to avoid mutable Vec3
    # defaults in the dataclass.
    basis_x: object = None
    basis_y: object = None
    basis_z: object = None
    module_index: int = 0

    def basis(self):
        return (
            self.basis_x if self.basis_x is not None else Vec3(1.0, 0.0, 0.0),
            self.basis_y if self.basis_y is not None else Vec3(0.0, 1.0, 0.0),
            self.basis_z if self.basis_z is not None else Vec3(0.0, 0.0, 1.0),
        )

    def world_to_base_local(self, p: Vec3) -> Vec3:
        bx, by, bz = self.basis()
        d = p - self.center
        return Vec3(d.dot(bx), d.dot(by), d.dot(bz))

    def angle(self, t: float) -> float:
        return self.phase_deg + t * self.spin_deg_per_sec

    def gap_center(self, t: float):
        if self.gap_orbit_y == 0.0 and self.gap_orbit_z == 0.0:
            return (0.0, 0.0)
        a = math.radians(self.gap_phase_deg + t * self.gap_spin_deg_per_sec)
        return (math.sin(a) * self.gap_orbit_y, math.cos(a) * self.gap_orbit_z)

    def to_local(self, p: Vec3, t: float) -> Vec3:
        # Base grid lies in the local YZ plane at local X = 0. First convert
        # world coordinates into this grid's module-local basis, then undo the
        # animated spin. Level 1 identity-basis behavior is exactly the old logic.
        return rotate_axis(self.world_to_base_local(p), self.axis, -self.angle(t))

    def in_safe_gap(self, local: Vec3, t: float) -> bool:
        gy, gz = self.gap_center(t)
        # Slightly forgiving because collision tests use cube cell centers, not swept volumes.
        margin = CELL_HALF * 0.18
        return (
            abs(local.y - gy) <= self.safe_half_y + margin and
            abs(local.z - gz) <= self.safe_half_z + margin
        )

    def hits_point(self, p: Vec3, t: float) -> bool:
        local = self.to_local(p, t)
        # Plane proximity. CELL_HALF margin makes large cells get shaved by beams.
        plane_margin = self.beam_radius + CELL_HALF * 0.48
        if abs(local.x) > plane_margin:
            return False
        if abs(local.y) > self.half_y + CELL_HALF or abs(local.z) > self.half_z + CELL_HALF:
            return False

        # The designed aperture: if a cell center is inside this gap, the grid does not hit it.
        # This is what the previous version lacked, making the course effectively unwinnable.
        if self.in_safe_gap(local, t):
            return False

        # Grid openings elsewhere: only hit near horizontal/vertical laser lines.
        line_margin = self.beam_radius + CELL_HALF * 0.18
        near_y_line = dist_to_grid_line(local.y, self.spacing) < line_margin
        near_z_line = dist_to_grid_line(local.z, self.spacing) < line_margin
        return near_y_line or near_z_line

    def draw(self, t: float, alpha_scale: float = 1.0):
        alpha_scale = clamp(alpha_scale, 0.0, 1.0)
        if alpha_scale <= 0.01:
            return
        glPushMatrix()
        glTranslatef(self.center.x, self.center.y, self.center.z)
        bx, by, bz = self.basis()
        gl_apply_basis(bx, by, bz)
        a = self.axis.normalized()
        glRotatef(self.angle(t), a.x, a.y, a.z)

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # core beams
        glColor4f(self.color[0], self.color[1], self.color[2], self.color[3] * alpha_scale)
        glLineWidth(3.2)
        self._draw_grid_lines(t)

        # glow pass
        glColor4f(self.color[0], self.color[1], self.color[2], 0.18 * alpha_scale)
        glLineWidth(9.0)
        self._draw_grid_lines(t)

        # visible safe aperture frame, so the player can actually read the damn map
        gy, gz = self.gap_center(t)
        glColor4f(0.0, 1.0, 0.95, 0.42 * alpha_scale)
        glLineWidth(2.0)
        glBegin(GL_LINE_LOOP)
        glVertex3f(0.0, gy - self.safe_half_y, gz - self.safe_half_z)
        glVertex3f(0.0, gy + self.safe_half_y, gz - self.safe_half_z)
        glVertex3f(0.0, gy + self.safe_half_y, gz + self.safe_half_z)
        glVertex3f(0.0, gy - self.safe_half_y, gz + self.safe_half_z)
        glEnd()

        # frame
        glColor4f(1.0, 0.25, 0.18, 0.45 * alpha_scale)
        glLineWidth(1.5)
        glBegin(GL_LINE_LOOP)
        glVertex3f(0.0, -self.half_y, -self.half_z)
        glVertex3f(0.0,  self.half_y, -self.half_z)
        glVertex3f(0.0,  self.half_y,  self.half_z)
        glVertex3f(0.0, -self.half_y,  self.half_z)
        glEnd()

        glDisable(GL_BLEND)
        glPopMatrix()

    def _emit_segment(self, y1, z1, y2, z2):
        # Ignore microscopic garbage segments caused by aperture cuts.
        if abs(y2 - y1) + abs(z2 - z1) < 0.03:
            return
        glVertex3f(0.0, y1, z1)
        glVertex3f(0.0, y2, z2)

    def _draw_grid_lines(self, t: float):
        gy, gz = self.gap_center(t)
        y0 = gy - self.safe_half_y
        y1 = gy + self.safe_half_y
        z0 = gz - self.safe_half_z
        z1 = gz + self.safe_half_z

        glBegin(GL_LINES)

        # Lines parallel to Y at several Z coordinates, cut around the safe aperture.
        z = -self.half_z
        while z <= self.half_z + 1e-6:
            if z0 <= z <= z1:
                self._emit_segment(-self.half_y, z, max(-self.half_y, y0), z)
                self._emit_segment(min(self.half_y, y1), z, self.half_y, z)
            else:
                self._emit_segment(-self.half_y, z, self.half_y, z)
            z += self.spacing

        # Lines parallel to Z at several Y coordinates, cut around the safe aperture.
        y = -self.half_y
        while y <= self.half_y + 1e-6:
            if y0 <= y <= y1:
                self._emit_segment(y, -self.half_z, y, max(-self.half_z, z0))
                self._emit_segment(y, min(self.half_z, z1), y, self.half_z)
            else:
                self._emit_segment(y, -self.half_z, y, self.half_z)
            y += self.spacing

        glEnd()


LASERS = [
    # These are passable by design. The visible cyan aperture is large enough for the
    # full 5x5x5 body if centered, but off-center movement still shaves chunks.
    # Later gates move the aperture slightly, creating timing instead of an impossible wall.
    LaserGrid("red_washer_1", Vec3(-11.0, 0.0, 0.0), 6.4, 6.4, 1.60, 0.055, Vec3(1, 0, 0),  47.0,   0.0, 3.10, 3.10),
    LaserGrid("red_washer_2", Vec3( -5.0, 0.0, 0.0), 6.4, 6.4, 1.55, 0.052, Vec3(1, 0, 0), -62.0,  35.0, 3.00, 3.00),

    # Tilted planes, but with apertures still centered enough that a clean path exists.
    LaserGrid("tilting_grid_1", Vec3(  2.0, 0.0, 0.0), 6.6, 6.6, 1.65, 0.055, Vec3(0, 1, 0),  27.0,  20.0, 3.05, 3.05),
    LaserGrid("tilting_grid_2", Vec3(  8.0, 0.0, 0.0), 6.7, 6.7, 1.60, 0.052, Vec3(0, 0, 1), -31.0,  80.0, 3.00, 3.00, 0.35, 0.30, 34.0, 0.0),

    # Near the portal: denser and moving, but not a locked door.
    LaserGrid("last_bad_idea", Vec3( 14.0, 0.0, 0.0), 6.2, 6.2, 1.45, 0.050, Vec3(1, 0, 0),  24.0,  10.0, 2.95, 2.95, 0.45, 0.38, -28.0, 90.0),
]

BASE_LASER_TEMPLATES = list(LASERS)


@dataclass
class CourseModule:
    index: int
    start_x: float
    start_z: float
    dir_x: float
    dir_z: float

    @property
    def basis_x(self) -> Vec3:
        return Vec3(self.dir_x, 0.0, self.dir_z)

    @property
    def basis_y(self) -> Vec3:
        return Vec3(0.0, 1.0, 0.0)

    @property
    def basis_z(self) -> Vec3:
        # Local +Z is the corridor's sideways axis. For the original +X module
        # this is world +Z, preserving level 1 exactly.
        return Vec3(-self.dir_z, 0.0, self.dir_x)

    def local_to_world(self, lx: float, ly: float, lz: float) -> Vec3:
        along = lx - COURSE_X_MIN
        return Vec3(
            self.start_x + self.dir_x * along + self.basis_z.x * lz,
            ly,
            self.start_z + self.dir_z * along + self.basis_z.z * lz,
        )

    def world_to_local(self, p: Vec3) -> Vec3:
        dx = p.x - self.start_x
        dz = p.z - self.start_z
        along = dx * self.dir_x + dz * self.dir_z
        side = dx * self.basis_z.x + dz * self.basis_z.z
        return Vec3(COURSE_X_MIN + along, p.y, side)

    def end_center(self) -> Vec3:
        return self.local_to_world(COURSE_X_MAX, 0.0, 0.0)


COURSE_MODULES = []
PORTAL_MODULE = None
ACTIVE_LEVEL = 1


def make_course_modules(level: int):
    level = max(1, int(level))
    modules = []
    sx, sz = COURSE_X_MIN, 0.0
    for idx in range(level):
        dx, dz = LEVEL_DIRECTIONS[idx % len(LEVEL_DIRECTIONS)]
        mod = CourseModule(idx, sx, sz, float(dx), float(dz))
        modules.append(mod)
        end = mod.end_center()
        sx, sz = end.x, end.z
    return modules


def clone_laser_for_module(template: LaserGrid, module: CourseModule, level: int) -> LaserGrid:
    # Original template centers are already in level-1 local coordinates. Use their
    # x-position along every later module, then rotate the local grid basis.
    center = module.local_to_world(template.center.x, template.center.y, template.center.z)
    # Keep level 1 absolutely identical; later modules get tiny phase offsets so
    # stacked tunnels do not look copy-pasted in lockstep.
    phase_offset = 0.0 if module.index == 0 else module.index * 23.0 + level * 5.0
    speed_mul = 1.0 + min(0.45, max(0, level - 1) * 0.035)
    return LaserGrid(
        f"L{level}_M{module.index + 1}_{template.name}",
        center,
        template.half_y, template.half_z, template.spacing, template.beam_radius,
        template.axis, template.spin_deg_per_sec * speed_mul, template.phase_deg + phase_offset,
        template.safe_half_y, template.safe_half_z,
        template.gap_orbit_y, template.gap_orbit_z, template.gap_spin_deg_per_sec, template.gap_phase_deg + phase_offset,
        template.color,
        module.basis_x, module.basis_y, module.basis_z,
        module.index,
    )


def laser_template_allowed_for_module(template: LaserGrid, module: CourseModule) -> bool:
    """Clone the original obstacle set into every corridor module.

    Earlier patches tried to make the L bend passable by deleting grids near
    turns. That made the course look wrong and did not fix the real problem,
    which was the boundary/joint geometry. Keep the red grids; fix the pipe.
    """
    return True


def setup_level_geometry(level: int):
    global COURSE_MODULES, PORTAL_MODULE, PORTAL_POSITION, LASERS, ACTIVE_LEVEL, COLLAPSE_TRIGGERED, COLLAPSE_STARTED_AT, ZAP_BARRIERS, LASER_REVEAL_AT, LASER_REVEAL_SOUND_PLAYED
    COLLAPSE_TRIGGERED = set()
    COLLAPSE_STARTED_AT = {}
    ZAP_BARRIERS = {}
    LASER_REVEAL_AT = {}
    LASER_REVEAL_SOUND_PLAYED = set()
    ACTIVE_LEVEL = max(1, int(level))
    COURSE_MODULES = make_course_modules(ACTIVE_LEVEL)
    PORTAL_MODULE = COURSE_MODULES[-1]
    portal_world = PORTAL_MODULE.local_to_world(PORTAL_LOCAL_X, 0.0, 0.0)
    PORTAL_POSITION = portal_world.as_tuple()
    LASERS = [
        clone_laser_for_module(template, module, ACTIVE_LEVEL)
        for module in COURSE_MODULES
        for template in BASE_LASER_TEMPLATES
        if laser_template_allowed_for_module(template, module)
    ]
    # Modules that are initially visible should not replay a laser reveal effect.
    # In level 1-2 the whole course is active as before. From level 3 onward,
    # only module 0 starts fully armed; later modules wake up when revealed.
    if ACTIVE_LEVEL < PREVIEW_CULL_START_LEVEL:
        LASER_REVEAL_AT = {module.index: -9999.0 for module in COURSE_MODULES}
    else:
        LASER_REVEAL_AT = {0: -9999.0}


def module_has_prev_turn(module: CourseModule) -> bool:
    if module.index <= 0:
        return False
    prev = COURSE_MODULES[module.index - 1]
    return (prev.dir_x, prev.dir_z) != (module.dir_x, module.dir_z)


def module_has_next_turn(module: CourseModule) -> bool:
    if module.index >= len(COURSE_MODULES) - 1:
        return False
    nxt = COURSE_MODULES[module.index + 1]
    return (nxt.dir_x, nxt.dir_z) != (module.dir_x, module.dir_z)


def module_pipe_lx_span(module: CourseModule, pad: float = 0.0):
    """Visible/collidable corridor span after handing turn volume to joint cubes.

    At a 90-degree bend, the joint is a same-cross-section cube centered at the
    old module end / new module start. Corridor tubes stop at the joint face;
    they do not draw or collide through the joint. This keeps the L modular:
    tube -> joint cube with two open faces -> next tube.
    """
    start = COURSE_X_MIN
    end = COURSE_X_MAX
    if module_has_prev_turn(module):
        start += TURN_JOINT_HALF
    if module_has_next_turn(module):
        end -= TURN_JOINT_HALF
    return start - pad, end + pad


def module_box_segments(module: CourseModule):
    lx0, lx1 = module_pipe_lx_span(module, pad=0.0)
    if lx1 <= lx0:
        return []

    corners = [
        module.local_to_world(lx0, COURSE_Y_MIN, COURSE_Z_MIN),
        module.local_to_world(lx1, COURSE_Y_MIN, COURSE_Z_MIN),
        module.local_to_world(lx1, COURSE_Y_MAX, COURSE_Z_MIN),
        module.local_to_world(lx0, COURSE_Y_MAX, COURSE_Z_MIN),
        module.local_to_world(lx0, COURSE_Y_MIN, COURSE_Z_MAX),
        module.local_to_world(lx1, COURSE_Y_MIN, COURSE_Z_MAX),
        module.local_to_world(lx1, COURSE_Y_MAX, COURSE_Z_MAX),
        module.local_to_world(lx0, COURSE_Y_MAX, COURSE_Z_MAX),
    ]

    # Corridors are open tubes at joint faces. Do not draw the old module cap
    # through the L-joint, and do not draw the new module start cap inside it.
    # The joint cube itself supplies the visible corner outline.
    long_edges = [(0, 1), (2, 3), (4, 5), (6, 7)]
    start_cap = [(3, 0), (7, 4), (0, 4), (3, 7)]
    end_cap = [(1, 2), (5, 6), (1, 5), (2, 6)]

    edges = list(long_edges)
    if not module_has_prev_turn(module):
        edges.extend(start_cap)
    if not module_has_next_turn(module):
        edges.extend(end_cap)

    return [(corners[a], corners[b]) for a, b in edges]

def turn_chamber_joints():
    """Return explicit modular turn joints.

    Each joint is a same-cross-section cube at the point where two corridor
    modules meet. It has exactly two open lateral faces: the face where the
    incoming tube arrives, and the face where the outgoing tube leaves. The
    closed faces are drawn with guide/grid lines so the joint reads as a pipe
    fitting instead of a magic all-sides escape cube.
    """
    joints = []
    for idx in range(len(COURSE_MODULES) - 1):
        a = COURSE_MODULES[idx]
        b = COURSE_MODULES[idx + 1]
        if (a.dir_x, a.dir_z) == (b.dir_x, b.dir_z):
            continue
        center = a.end_center()
        # Opening normals in the horizontal X/Z plane. Incoming tube reaches
        # the joint from behind module a, outgoing tube leaves along module b.
        open_faces = {
            (int(-a.dir_x), int(-a.dir_z)),
            (int( b.dir_x), int( b.dir_z)),
        }
        joints.append((center, open_faces))
    return joints


def turn_chamber_centers():
    return [center for center, _open_faces in turn_chamber_joints()]


def turn_chamber_segments(center: Vec3):
    # Same-cross-section corner cube: 14x14x14 when the tunnel is y/z -7..7.
    # This is the L-pipe joint, not an oversized mercy box.
    h = TURN_JOINT_HALF
    corners = [
        Vec3(center.x - h, COURSE_Y_MIN, center.z - h),
        Vec3(center.x + h, COURSE_Y_MIN, center.z - h),
        Vec3(center.x + h, COURSE_Y_MAX, center.z - h),
        Vec3(center.x - h, COURSE_Y_MAX, center.z - h),
        Vec3(center.x - h, COURSE_Y_MIN, center.z + h),
        Vec3(center.x + h, COURSE_Y_MIN, center.z + h),
        Vec3(center.x + h, COURSE_Y_MAX, center.z + h),
        Vec3(center.x - h, COURSE_Y_MAX, center.z + h),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    return [(corners[a], corners[b]) for a, b in edges]


def _grid_values(lo: float, hi: float, step: float = 2.0):
    v = math.ceil(lo / step) * step
    while v <= hi + 1e-6:
        yield v
        v += step


def turn_chamber_face_grid_segments(center: Vec3, open_faces):
    """Guide-grid lines on the closed faces of a modular turn joint.

    The two open faces are deliberately left ungridded. These are visuals only;
    collision and laser damage are handled by point_inside_course() and LASERS.
    """
    h = TURN_JOINT_HALF
    x0, x1 = center.x - h, center.x + h
    z0, z1 = center.z - h, center.z + h
    y0, y1 = COURSE_Y_MIN, COURSE_Y_MAX
    segs = []

    # Lateral side faces. Normals are encoded as (dx, dz).
    lateral_faces = [
        ((-1, 0), 'x', x0),
        (( 1, 0), 'x', x1),
        ((0, -1), 'z', z0),
        ((0,  1), 'z', z1),
    ]
    for normal, axis, value in lateral_faces:
        if normal in open_faces:
            continue
        if axis == 'x':
            for z in _grid_values(z0, z1, 2.0):
                segs.append((Vec3(value, y0, z), Vec3(value, y1, z)))
            for y in _grid_values(y0, y1, 2.0):
                segs.append((Vec3(value, y, z0), Vec3(value, y, z1)))
        else:
            for x in _grid_values(x0, x1, 2.0):
                segs.append((Vec3(x, y0, value), Vec3(x, y1, value)))
            for y in _grid_values(y0, y1, 2.0):
                segs.append((Vec3(x0, y, value), Vec3(x1, y, value)))

    # Floor and ceiling are closed parts of the pipe, so grid them too.
    for y in (y0, y1):
        for x in _grid_values(x0, x1, 2.0):
            segs.append((Vec3(x, y, z0), Vec3(x, y, z1)))
        for z in _grid_values(z0, z1, 2.0):
            segs.append((Vec3(x0, y, z), Vec3(x1, y, z)))
    return segs


def all_turn_chamber_face_grid_segments():
    segs = []
    for center, open_faces in turn_chamber_joints():
        segs.extend(turn_chamber_face_grid_segments(center, open_faces))
    return segs


def point_inside_turn_chamber(p: Vec3, pad: float = 0.0) -> bool:
    # Same-size corner cube with two corridor openings. This is enough for a
    # finite cube to rotate through the L without inventing a giant invisible room.
    h = TURN_JOINT_HALF + pad
    for center in turn_chamber_centers():
        if (center.x - h <= p.x <= center.x + h and
                COURSE_Y_MIN - pad <= p.y <= COURSE_Y_MAX + pad and
                center.z - h <= p.z <= center.z + h):
            return True
    return False


def point_inside_turn_runway(p: Vec3, pad: float = 0.0) -> bool:
    # Removed. The oversized runway was the thing that made the L look and feel
    # like overlapping geometry. The same-size joint cube above is the turn.
    return False


def module_guide_segments(module: CourseModule):
    lx0, lx1 = module_pipe_lx_span(module, pad=0.0)
    if lx1 <= lx0:
        return []

    segments = []
    # Long guide lines also stop at joint faces. This is the line-grid that was
    # visually invading the L-corner and making the exit look/feel blocked.
    for z in range(int(COURSE_Z_MIN), int(COURSE_Z_MAX) + 1, 2):
        segments.append((module.local_to_world(lx0, COURSE_Y_MIN, z), module.local_to_world(lx1, COURSE_Y_MIN, z)))
        segments.append((module.local_to_world(lx0, COURSE_Y_MAX, z), module.local_to_world(lx1, COURSE_Y_MAX, z)))
    for y in range(int(COURSE_Y_MIN), int(COURSE_Y_MAX) + 1, 2):
        segments.append((module.local_to_world(lx0, y, COURSE_Z_MIN), module.local_to_world(lx1, y, COURSE_Z_MIN)))
        segments.append((module.local_to_world(lx0, y, COURSE_Z_MAX), module.local_to_world(lx1, y, COURSE_Z_MAX)))
    return segments

def turn_runway_segments(module: CourseModule):
    return []


def all_course_box_segments():
    # Modular pipe draw order: corridor tube segments first, then explicit joint
    # cubes. Corridor cage/guide lines are clipped to joint faces, so no old edge
    # grid crosses the bend volume.
    segments = [seg for module in COURSE_MODULES for seg in module_box_segments(module)]
    for center in turn_chamber_centers():
        segments.extend(turn_chamber_segments(center))
    return segments


def all_course_guide_segments():
    segs = [seg for module in COURSE_MODULES for seg in module_guide_segments(module)]
    segs.extend(all_turn_chamber_face_grid_segments())
    return segs


def point_inside_course(p: Vec3, pad: float = 0.0) -> bool:
    # True modular volume: clipped corridor tubes plus explicit same-size joint
    # cubes. The corridor volumes do not extend through the joint; the joint owns
    # the corner. This removes the phantom seam at the L exit.
    for module in COURSE_MODULES:
        local = module.world_to_local(p)
        lx0, lx1 = module_pipe_lx_span(module, pad=pad)
        if (lx0 <= local.x <= lx1 and
                COURSE_Y_MIN - pad <= local.y <= COURSE_Y_MAX + pad and
                COURSE_Z_MIN - pad <= local.z <= COURSE_Z_MAX + pad):
            return True
    return point_inside_turn_chamber(p, pad)


def course_aabb(pad: float = 0.0):
    pts = []
    for module in COURSE_MODULES:
        lx0, lx1 = module_pipe_lx_span(module, pad=0.0)
        for lx in (lx0, lx1):
            for ly in (COURSE_Y_MIN, COURSE_Y_MAX):
                for lz in (COURSE_Z_MIN, COURSE_Z_MAX):
                    pts.append(module.local_to_world(lx, ly, lz))
    for center in turn_chamber_centers():
        h = TURN_JOINT_HALF
        for x in (center.x - h, center.x + h):
            for y in (COURSE_Y_MIN, COURSE_Y_MAX):
                for z in (center.z - h, center.z + h):
                    pts.append(Vec3(x, y, z))
    if not pts:
        return (COURSE_X_MIN - pad, COURSE_X_MAX + pad, COURSE_Y_MIN - pad, COURSE_Y_MAX + pad, COURSE_Z_MIN - pad, COURSE_Z_MAX + pad)
    return (
        min(v.x for v in pts) - pad, max(v.x for v in pts) + pad,
        min(v.y for v in pts) - pad, max(v.y for v in pts) + pad,
        min(v.z for v in pts) - pad, max(v.z for v in pts) + pad,
    )


def course_center_and_zoom():
    xmin, xmax, ymin, ymax, zmin, zmax = course_aabb(0.0)
    center = Vec3((xmin + xmax) * 0.5, (ymin + ymax) * 0.5, (zmin + zmax) * 0.5)
    span = max(xmax - xmin, zmax - zmin, ymax - ymin)
    zoom = 48.0 + max(0.0, span - 46.0) * 0.38
    return center, min(120.0, zoom)


def apply_portal_orientation():
    if PORTAL_MODULE is None:
        glRotatef(90.0, 0, 1, 0)
        return
    # Existing portal drawing is in a local XY plane with normal +Z. Map that to:
    # local X -> corridor side, local Y -> vertical, local Z -> corridor forward.
    gl_apply_basis(PORTAL_MODULE.basis_z, PORTAL_MODULE.basis_y, PORTAL_MODULE.basis_x)


def save_score_stats(best_escape: int, highest_level: int):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SAVE_FILE_NAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"best_escape": int(best_escape), "highest_level": int(highest_level)}, f, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        print(f"[WARN] Could not save score stats: {exc}")


def load_score_stats():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SAVE_FILE_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "best_escape": int(data.get("best_escape", 0)),
            "highest_level": max(1, int(data.get("highest_level", 1))),
        }
    except FileNotFoundError:
        return {"best_escape": 0, "highest_level": 1}
    except Exception as exc:
        print(f"[WARN] Could not load score stats: {exc}")
        return {"best_escape": 0, "highest_level": 1}


setup_level_geometry(1)

# -----------------------------------------------------------------------------
# World drawing
# -----------------------------------------------------------------------------

stars = [
    Vec3(random.uniform(-70, 70), random.uniform(-55, 55), random.uniform(-70, 70))
    for _ in range(900)
]


def draw_stars():
    glColor4f(1.0, 1.0, 1.0, 0.9)
    glPointSize(1.7)
    glBegin(GL_POINTS)
    for s in stars:
        glVertex3f(s.x, s.y, s.z)
    glEnd()


def player_module_location(player: "PlayerCube"):
    """Return the module the player is currently closest to, plus local X.

    This is used only for visual/collision culling behind the player. Controls
    remain world-axis controls; no path steering gets smuggled back in here.
    """
    p = player.origin
    best_idx = 0
    best_lx = COURSE_X_MIN
    best_score = -1.0e18
    for module in COURSE_MODULES:
        local = module.world_to_local(p)
        side_ok = abs(local.z) <= TUNNEL_HALF + TURN_JOINT_HALF
        y_ok = COURSE_Y_MIN - TURN_JOINT_HALF <= local.y <= COURSE_Y_MAX + TURN_JOINT_HALF
        x_ok = COURSE_X_MIN - TURN_JOINT_HALF <= local.x <= COURSE_X_MAX + TURN_JOINT_HALF
        if not (side_ok and y_ok and x_ok):
            continue
        lx_clamped = clamp(local.x, COURSE_X_MIN, COURSE_X_MAX)
        # Prefer later modules once the player is actually in/near them.
        score = module.index * 1000.0 + lx_clamped
        if score > best_score:
            best_score = score
            best_idx = module.index
            best_lx = lx_clamped
    return best_idx, best_lx


def turn_joint_index_for_point(p: Vec3, pad: float = 0.0):
    """Return the modular turn-joint index containing p, or None.

    The index is the joint between module index and index + 1. Used for
    progressive reveal/culling only; controls stay world-axis based.
    """
    h = TURN_JOINT_HALF + pad
    for idx, (center, _open_faces) in enumerate(turn_chamber_joints()):
        if (center.x - h <= p.x <= center.x + h and
                COURSE_Y_MIN - pad <= p.y <= COURSE_Y_MAX + pad and
                center.z - h <= p.z <= center.z + h):
            return idx
    return None


def reveal_module_index_for_player(player: "PlayerCube") -> int:
    """Highest module allowed to render active hazards at full detail.

    From level 3 onward, future modules stay grey plumbing previews until the
    player enters the joint before them. This stops level 3+ from drawing every
    red rotating grid in the entire future maze.
    """
    if player is None or ACTIVE_LEVEL < PREVIEW_CULL_START_LEVEL:
        return len(COURSE_MODULES) - 1
    joint_idx = turn_joint_index_for_point(player.origin, pad=CELL_SPACING * 1.2)
    if joint_idx is not None:
        return min(len(COURSE_MODULES) - 1, joint_idx + 1)
    active_idx, _active_lx = player_module_location(player)
    return clamp(active_idx, 0, len(COURSE_MODULES) - 1)


def module_is_preview_only(module_index: int, player: "PlayerCube") -> bool:
    return ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and module_index > reveal_module_index_for_player(player)


def laser_is_revealed_for_player(laser: LaserGrid, player: "PlayerCube") -> bool:
    return not module_is_preview_only(getattr(laser, "module_index", 0), player)


def update_laser_reveal_state(player: "PlayerCube", t: float):
    """Register newly waking hazard modules and trigger their reveal sound.

    The grey future pipe becomes dangerous only after its module is revealed.
    This keeps the level-3+ culling readable: first plumbing wireframe, then the
    red grids simmer in from the edges with an ominous woosh.
    """
    if player is None or not COURSE_MODULES:
        return
    reveal_idx = reveal_module_index_for_player(player)
    for idx in range(0, min(reveal_idx, len(COURSE_MODULES) - 1) + 1):
        if idx not in LASER_REVEAL_AT:
            LASER_REVEAL_AT[idx] = t
            if idx > 0 and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and idx not in LASER_REVEAL_SOUND_PLAYED:
                LASER_REVEAL_SOUND_PLAYED.add(idx)
                audio_play("laser_reveal", volume=0.64, channel_name="hazard", cooldown=LASER_REVEAL_SOUND_COOLDOWN)


def laser_reveal_progress(module_index: int, t: float) -> float:
    started = LASER_REVEAL_AT.get(module_index)
    if started is None:
        return 0.0
    if started < -1000.0:
        return 1.0
    return smoothstep((t - started) / max(0.001, LASER_REVEAL_SECONDS))


def laser_reveal_collision_armed(laser: LaserGrid, t: float) -> bool:
    return laser_reveal_progress(getattr(laser, "module_index", 0), t) >= LASER_REVEAL_COLLISION_ARM_RATIO


def player_has_out_of_bounds_cells(player: "PlayerCube") -> bool:
    if player is None:
        return False
    for cell in player.alive_cells:
        if not point_inside_course(player.cell_world_pos(cell), pad=BOUNDARY_DAMAGE_PAD * 0.45):
            return True
    return False


def _trail_fade_progress(active_idx: int, active_lx: float, old_index: int) -> float:
    # Keep two joints/modules of history visible. This means level 2 does not
    # collapse behind the player yet; by level 3, the oldest pipe section can
    # flash away once the player is safely in the newer module.
    collapse_idx = old_index + TRAIL_KEEP_JOINTS
    if active_idx < collapse_idx:
        return 0.0
    if active_idx > collapse_idx:
        return 1.0
    fade_start = COURSE_X_MIN + TRAIL_FADE_START_INTO_MODULE
    return smoothstep((active_lx - fade_start) / max(0.001, TRAIL_FADE_DISTANCE))


def _trail_time_fade(old_index: int, t: float) -> float:
    key = (ACTIVE_LEVEL, old_index)
    started = COLLAPSE_STARTED_AT.get(key)
    if started is None:
        return 0.0
    return smoothstep((t - started) / max(0.001, COLLAPSE_VISUAL_SECONDS))


def _trail_combined_fade(active_idx: int, active_lx: float, old_index: int, t: float) -> float:
    # Distance decides *when* a section is eligible to collapse. Once collapse has
    # actually triggered, time also drives the alpha so the effect remains visible
    # for about a second instead of being skipped when the player rushes onward.
    distance_fade = _trail_fade_progress(active_idx, active_lx, old_index)
    time_fade = _trail_time_fade(old_index, t)
    return max(distance_fade, time_fade)


def _trail_flash(alpha: float, t: float, salt: float = 0.0) -> float:
    # In the fade band, make the old geometry flicker like a collapsing field.
    if alpha <= 0.01 or alpha >= 0.98:
        return alpha
    flash = 0.58 + 0.42 * (0.5 + 0.5 * math.sin((t + salt) * math.tau * TRAIL_FLASH_HZ))
    return clamp(alpha * flash, 0.0, 1.0)


def module_trail_alpha(module: CourseModule, player: "PlayerCube", t: float) -> float:
    active_idx, active_lx = player_module_location(player)
    fade = _trail_combined_fade(active_idx, active_lx, module.index, t)
    return _trail_flash(1.0 - fade, t, module.index * 0.137)


def joint_trail_alpha(joint_index: int, player: "PlayerCube", t: float) -> float:
    active_idx, active_lx = player_module_location(player)
    fade = _trail_combined_fade(active_idx, active_lx, joint_index, t)
    return _trail_flash(1.0 - fade, t, joint_index * 0.219 + 0.31)


def laser_trail_fade(laser: LaserGrid, player: "PlayerCube", t: float) -> float:
    active_idx, active_lx = player_module_location(player)
    return _trail_combined_fade(active_idx, active_lx, getattr(laser, "module_index", 0), t)


def laser_trail_alpha(laser: LaserGrid, player: "PlayerCube", t: float) -> float:
    fade = laser_trail_fade(laser, player, t)
    return _trail_flash(1.0 - fade, t, getattr(laser, "module_index", 0) * 0.173 + 0.62)


def laser_should_be_hard_culled(laser: LaserGrid, player: "PlayerCube", t: float) -> bool:
    """Drop passed hazard grids completely once their collapse window is over."""
    if player is None or ACTIVE_LEVEL < PREVIEW_CULL_START_LEVEL:
        return False
    module_idx = getattr(laser, "module_index", 0)
    active_idx, active_lx = player_module_location(player)
    if module_idx >= active_idx:
        return False
    # If the time-based collapse has completed, stop drawing/checking the laser.
    key = (ACTIVE_LEVEL, module_idx)
    started = COLLAPSE_STARTED_AT.get(key)
    if started is not None and (t - started) >= COLLAPSE_VISUAL_SECONDS:
        return True
    # Fallback for sections far behind even if the collapse one-shot was missed.
    return _trail_fade_progress(active_idx, active_lx, module_idx) >= 0.995


def laser_is_active_for_player(laser: LaserGrid, player: "PlayerCube", t: float = 0.0) -> bool:
    if laser_should_be_hard_culled(laser, player, t):
        return False
    if not laser_is_revealed_for_player(laser, player):
        return False
    if not laser_reveal_collision_armed(laser, t):
        return False
    active_idx, active_lx = player_module_location(player)
    fade = _trail_fade_progress(active_idx, active_lx, getattr(laser, "module_index", 0))
    return fade < 0.98


def draw_future_laser_marker(laser: LaserGrid, t: float, alpha: float = 0.13):
    """Cheap grey preview marker for unrevealed future hazard planes.

    It deliberately does not draw the full rotating red grid, only the outer
    ghost-square and aperture cross, so future modules are readable without
    turning level 3 into an immediate-mode OpenGL meat grinder.
    """
    glPushMatrix()
    glTranslatef(laser.center.x, laser.center.y, laser.center.z)
    bx, by, bz = laser.basis()
    gl_apply_basis(bx, by, bz)
    a = laser.axis.normalized()
    glRotatef(laser.angle(t), a.x, a.y, a.z)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(0.45, 0.48, 0.52, alpha)
    glLineWidth(1.0)
    glBegin(GL_LINE_LOOP)
    glVertex3f(0.0, -laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y,  laser.half_z)
    glVertex3f(0.0, -laser.half_y,  laser.half_z)
    glEnd()

    # A minimal center/aperture hint. No expensive full grid here.
    gy, gz = laser.gap_center(t)
    glColor4f(0.62, 0.64, 0.68, alpha * 0.75)
    glBegin(GL_LINES)
    glVertex3f(0.0, gy - laser.safe_half_y, gz)
    glVertex3f(0.0, gy + laser.safe_half_y, gz)
    glVertex3f(0.0, gy, gz - laser.safe_half_z)
    glVertex3f(0.0, gy, gz + laser.safe_half_z)
    glEnd()
    glDisable(GL_BLEND)
    glPopMatrix()


def draw_revealing_laser_grid(laser: LaserGrid, t: float, progress: float, alpha_scale: float = 1.0):
    """Draw a newly activated red grid as an edge-inward simmer/fortify pass."""
    p = clamp(progress, 0.0, 1.0)
    alpha_scale = clamp(alpha_scale, 0.0, 1.0)
    if p <= 0.01 or alpha_scale <= 0.01:
        return

    edge_p = smoothstep(clamp(p / 0.82, 0.0, 1.0))
    fortify = smoothstep(clamp((p - 0.42) / 0.58, 0.0, 1.0))
    simmer = 0.5 + 0.5 * math.sin(t * 17.0 + getattr(laser, "module_index", 0) * 1.7)
    red = (1.0, 0.05 + 0.10 * simmer, 0.025)

    glPushMatrix()
    glTranslatef(laser.center.x, laser.center.y, laser.center.z)
    bx, by, bz = laser.basis()
    gl_apply_basis(bx, by, bz)
    a = laser.axis.normalized()
    glRotatef(laser.angle(t), a.x, a.y, a.z)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    # Faint outer-square ignition: appears first, before the interior beams
    # grow inward from the boundary toward the aperture.
    glColor4f(red[0], red[1], red[2], (0.16 + 0.36 * p) * alpha_scale)
    glLineWidth(1.0 + 3.8 * p + 1.5 * simmer * (1.0 - fortify))
    glBegin(GL_LINE_LOOP)
    glVertex3f(0.0, -laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y,  laser.half_z)
    glVertex3f(0.0, -laser.half_y,  laser.half_z)
    glEnd()

    def emit(y1, z1, y2, z2):
        if abs(y2 - y1) + abs(z2 - z1) < 0.03:
            return
        glVertex3f(0.0, y1, z1)
        glVertex3f(0.0, y2, z2)

    def draw_edge_lines(width_limit: float):
        gy, gz = laser.gap_center(t)
        y0 = gy - laser.safe_half_y
        y1 = gy + laser.safe_half_y
        z0 = gz - laser.safe_half_z
        z1 = gz + laser.safe_half_z
        glBegin(GL_LINES)
        z = -laser.half_z
        while z <= laser.half_z + 1e-6:
            from_edge = laser.half_z - abs(z)
            if from_edge <= width_limit:
                if z0 <= z <= z1:
                    emit(-laser.half_y, z, max(-laser.half_y, y0), z)
                    emit(min(laser.half_y, y1), z, laser.half_y, z)
                else:
                    emit(-laser.half_y, z, laser.half_y, z)
            z += laser.spacing
        y = -laser.half_y
        while y <= laser.half_y + 1e-6:
            from_edge = laser.half_y - abs(y)
            if from_edge <= width_limit:
                if y0 <= y <= y1:
                    emit(y, -laser.half_z, y, max(-laser.half_z, z0))
                    emit(y, min(laser.half_z, z1), y, laser.half_z)
                else:
                    emit(y, -laser.half_z, y, laser.half_z)
            y += laser.spacing
        glEnd()

    reveal_width = max(laser.half_y, laser.half_z) * edge_p + laser.spacing * 0.25

    # Wide faint red corona: the grid is condensing out of the grey preview.
    glColor4f(red[0], red[1], red[2], (0.05 + 0.22 * p) * alpha_scale)
    glLineWidth(6.0 + 10.0 * (1.0 - fortify))
    draw_edge_lines(reveal_width)

    # Core red beams fortify after the edge shimmer.
    core_alpha = (0.12 + 0.82 * fortify) * alpha_scale
    glColor4f(red[0], red[1], red[2], core_alpha)
    glLineWidth(1.25 + 2.05 * fortify)
    draw_edge_lines(reveal_width)

    # Aperture becomes readable in the latter half, before collision arms.
    if p > 0.35:
        gy, gz = laser.gap_center(t)
        ap = smoothstep((p - 0.35) / 0.65)
        glColor4f(0.0, 1.0, 0.95, 0.42 * ap * alpha_scale)
        glLineWidth(1.0 + 1.2 * ap)
        glBegin(GL_LINE_LOOP)
        glVertex3f(0.0, gy - laser.safe_half_y, gz - laser.safe_half_z)
        glVertex3f(0.0, gy + laser.safe_half_y, gz - laser.safe_half_z)
        glVertex3f(0.0, gy + laser.safe_half_y, gz + laser.safe_half_z)
        glVertex3f(0.0, gy - laser.safe_half_y, gz + laser.safe_half_z)
        glEnd()

    glDisable(GL_BLEND)
    glPopMatrix()



def draw_dissipating_laser_grid(laser: LaserGrid, t: float, fade: float, alpha_scale: float = 1.0):
    """Draw a passed red grid as embers/ash while the old module is eaten.

    This is the opposite of draw_revealing_laser_grid(): the full red grid loses
    authority, thins to amber filaments, breaks into point embers, then greys out.
    """
    f = clamp(fade, 0.0, 1.0)
    alpha_scale = clamp(alpha_scale, 0.0, 1.0)
    if f <= 0.01 or f >= 0.999 or alpha_scale <= 0.01:
        return

    alive = 1.0 - f
    heat = 1.0 - smoothstep((f - 0.12) / 0.74)
    ash = smoothstep((f - 0.46) / 0.54)
    flicker = 0.5 + 0.5 * math.sin(t * 23.0 + getattr(laser, "module_index", 0) * 2.37)
    ember_r = 1.0 * (1.0 - ash) + 0.55 * ash
    ember_g = (0.18 + 0.30 * flicker) * (1.0 - ash) + 0.55 * ash
    ember_b = 0.035 * (1.0 - ash) + 0.58 * ash

    glPushMatrix()
    glTranslatef(laser.center.x, laser.center.y, laser.center.z)
    bx, by, bz = laser.basis()
    gl_apply_basis(bx, by, bz)
    a = laser.axis.normalized()
    glRotatef(laser.angle(t), a.x, a.y, a.z)

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    # First the red authority of the grid decays into thin ember filaments.
    line_alpha = (0.10 + 0.80 * alive) * alpha_scale * (0.55 + 0.45 * flicker)
    glColor4f(ember_r, ember_g, ember_b, line_alpha)
    glLineWidth(max(0.65, 3.0 * alive + 0.55 * flicker))
    laser._draw_grid_lines(t)

    # The outer square lingers a moment as a dying cage outline.
    frame_alpha = (0.06 + 0.46 * alive) * alpha_scale
    glColor4f(ember_r, ember_g, ember_b, frame_alpha)
    glLineWidth(max(0.5, 2.2 * alive))
    glBegin(GL_LINE_LOOP)
    glVertex3f(0.0, -laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y, -laser.half_z)
    glVertex3f(0.0,  laser.half_y,  laser.half_z)
    glVertex3f(0.0, -laser.half_y,  laser.half_z)
    glEnd()

    # Pixel-size embers/ash. Deterministic per laser so it shimmers instead of
    # becoming random snow every frame.
    seed = 9109 + getattr(laser, "module_index", 0) * 313 + sum(ord(c) for c in getattr(laser, "name", "laser"))
    rng = random.Random(seed)
    glPointSize(1.0 + 2.4 * alive)
    glBegin(GL_POINTS)
    for i in range(LASER_EMBER_POINT_COUNT):
        # Put most embers near real grid lines. Half sample horizontal beams,
        # half vertical beams; then drift out of the plane as the grid exhales.
        if rng.random() < 0.5:
            z = round(rng.uniform(-laser.half_z, laser.half_z) / laser.spacing) * laser.spacing
            y = rng.uniform(-laser.half_y, laser.half_y)
        else:
            y = round(rng.uniform(-laser.half_y, laser.half_y) / laser.spacing) * laser.spacing
            z = rng.uniform(-laser.half_z, laser.half_z)
        y = clamp(y, -laser.half_y, laser.half_y)
        z = clamp(z, -laser.half_z, laser.half_z)

        # Avoid filling the safe aperture with too many embers; leave the hole readable.
        gy, gz = laser.gap_center(t)
        if abs(y - gy) < laser.safe_half_y * 0.95 and abs(z - gz) < laser.safe_half_z * 0.95:
            if rng.random() < 0.76:
                continue

        drift_dir = -1.0 if rng.random() < 0.5 else 1.0
        local_x = drift_dir * (0.05 + f * rng.uniform(0.05, 1.45))
        y += math.sin(t * (1.7 + rng.random() * 1.8) + i) * f * 0.12
        z += math.cos(t * (1.4 + rng.random() * 1.6) + i * 0.37) * f * 0.12
        ember_alpha = alpha_scale * (alive ** 0.55) * rng.uniform(0.22, 0.88)
        rr = ember_r + rng.uniform(-0.08, 0.08) * (1.0 - ash)
        gg = ember_g + rng.uniform(-0.06, 0.12) * (1.0 - ash)
        bb = ember_b + rng.uniform(-0.03, 0.06)
        glColor4f(clamp(rr, 0.0, 1.0), clamp(gg, 0.0, 1.0), clamp(bb, 0.0, 1.0), ember_alpha)
        glVertex3f(local_x, y, z)
    glEnd()

    glDisable(GL_BLEND)
    glPopMatrix()


def draw_disintegration_curtain(player: "PlayerCube", t: float):
    """Force-field flashes where old pipe/joint sections collapse behind you.

    Earlier this only looked at active_idx - 1, which is the *kept* previous
    joint, not the oldest joint that should actually be eaten when level 3+
    starts trimming history. That made the collapse look absent. Now every
    collapsing old joint gets its own closing square flash.
    """
    active_idx, active_lx = player_module_location(player)
    if active_idx < TRAIL_KEEP_JOINTS:
        return

    old_count = max(0, active_idx - TRAIL_KEEP_JOINTS + 1)
    for old_index in range(old_count):
        distance_fade = _trail_fade_progress(active_idx, active_lx, old_index)
        key = (ACTIVE_LEVEL, old_index)
        # Use time-based fade for visible collapse once the one-shot has fired;
        # otherwise allow the distance fade to preview the flicker band.
        if key in COLLAPSE_STARTED_AT:
            fade = _trail_time_fade(old_index, t)
        else:
            fade = distance_fade
        if fade <= 0.02 or fade >= 0.98:
            continue

        prev = COURSE_MODULES[old_index]
        center = prev.end_center()
        half = TUNNEL_HALF
        pulse = 0.55 + 0.45 * math.sin(t * math.tau * (TRAIL_FLASH_HZ * 0.53 + old_index * 0.03))
        alpha = (1.0 - abs(fade - 0.5) * 2.0) * (0.28 + 0.58 * pulse)
        close = smoothstep(fade)

        glPushMatrix()
        glTranslatef(center.x, center.y, center.z)
        # Draw a portal-like square across the old corridor's outgoing face:
        # local X/Y are side/vertical, local Z is previous forward direction.
        gl_apply_basis(prev.basis_z, prev.basis_y, prev.basis_x)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

        # The joint square cage slams shut behind the player: cyan-white flash,
        # then the inner grid tightens as the old pipe is eaten.
        glColor4f(0.72, 0.95, 1.0, alpha)
        glLineWidth(2.0 + 6.5 * pulse)
        glBegin(GL_LINE_LOOP)
        glVertex3f(-half, COURSE_Y_MIN, 0.0)
        glVertex3f( half, COURSE_Y_MIN, 0.0)
        glVertex3f( half, COURSE_Y_MAX, 0.0)
        glVertex3f(-half, COURSE_Y_MAX, 0.0)
        glEnd()

        glColor4f(0.86, 0.96, 1.0, alpha * 0.68)
        glLineWidth(1.2 + 2.8 * pulse)
        glBegin(GL_LINES)
        for i in range(11):
            frac = i / 10.0
            u = -half + (2.0 * half) * frac
            y = COURSE_Y_MIN + (COURSE_Y_MAX - COURSE_Y_MIN) * frac
            jitter = math.sin(t * 11.0 + i + old_index * 0.7) * 0.26 * fade
            squeeze = half * (1.0 - 0.72 * close)
            glVertex3f(u + jitter, COURSE_Y_MIN, 0.0)
            glVertex3f(u - jitter, COURSE_Y_MAX, 0.0)
            glVertex3f(-squeeze, y + jitter, 0.0)
            glVertex3f( squeeze, y - jitter, 0.0)
        glEnd()

        if fade < 0.22:
            glColor4f(1.0, 1.0, 1.0, (1.0 - fade / 0.22) * 0.55)
            glBegin(GL_QUADS)
            glVertex3f(-half, COURSE_Y_MIN, 0.006)
            glVertex3f( half, COURSE_Y_MIN, 0.006)
            glVertex3f( half, COURSE_Y_MAX, 0.006)
            glVertex3f(-half, COURSE_Y_MAX, 0.006)
            glEnd()

        glDisable(GL_BLEND)
        glPopMatrix()


def spawn_collapse_debris(center: Vec3, direction: Vec3, severity: int = 1):
    """Grey cage shrapnel when an old joint/pipe section is eaten from existence."""
    n = direction.normalized()
    if n.length() <= 1e-6:
        n = Vec3(0.0, 0.0, -1.0)
    for _ in range(COLLAPSE_DEBRIS_COUNT + int(12 * severity)):
        grey = random.uniform(0.42, 0.88)
        cold = random.uniform(0.0, 0.10)
        color = (grey * (0.92 + cold), grey * (0.96 + cold), min(1.0, grey + 0.10 + cold))
        IMPACT_SPARKS.append(
            SparkParticle(
                pos=center + random_vec(TUNNEL_HALF * 0.85),
                vel=(n * random.uniform(2.2, 7.0)) + random_vec(3.4),
                color=color,
                lifetime=random.uniform(1.05, 2.15),
                size=random.uniform(0.65, 1.35),
            )
        )
    IMPACT_GLOWS.append(
        ImpactGlow(
            kind="collapse",
            pos=center,
            color=(0.82, 0.92, 1.0),
            lifetime=1.05,
            radius=TUNNEL_HALF * 1.22,
        )
    )


def update_collapse_triggers(player: "PlayerCube", t: float):
    """One-shot sound/debris when a kept trail segment starts collapsing."""
    active_idx, active_lx = player_module_location(player)
    if active_idx < TRAIL_KEEP_JOINTS:
        return
    for old_index in range(max(0, active_idx - TRAIL_KEEP_JOINTS + 1)):
        fade = _trail_fade_progress(active_idx, active_lx, old_index)
        key = (ACTIVE_LEVEL, old_index)
        if fade >= 0.04 and key not in COLLAPSE_TRIGGERED:
            COLLAPSE_TRIGGERED.add(key)
            COLLAPSE_STARTED_AT[key] = t
            if old_index < len(COURSE_MODULES):
                m = COURSE_MODULES[old_index]
                center = m.end_center()
                direction = m.basis_x * -1.0
            else:
                center = player.origin
                direction = Vec3(0.0, 0.0, -1.0)
            spawn_collapse_debris(center, direction, severity=2)
            ZAP_BARRIERS[key] = t
            audio_play("collapse", volume=0.82, channel_name="one_shot", cooldown=0.12)
            audio_play("laser_dissipate", volume=0.62, channel_name="hazard_fade", cooldown=LASER_DISSIPATE_SOUND_COOLDOWN)


def player_inside_collapsing_section(player: "PlayerCube", t: float) -> bool:
    """True if the player is in a pipe/joint already sealed by trail collapse."""
    if player is None or not COLLAPSE_STARTED_AT:
        return False
    active_idx, _active_lx = player_module_location(player)
    key = (ACTIVE_LEVEL, active_idx)
    if key in COLLAPSE_STARTED_AT:
        return True
    joint_idx = turn_joint_index_for_point(player.origin, pad=CELL_HALF)
    if joint_idx is not None and (ACTIVE_LEVEL, joint_idx) in COLLAPSE_STARTED_AT:
        return True
    return False


def draw_course_frame(player: "PlayerCube" = None, t: float = 0.0):
    # Draw active modules normally, but from level 3 onward future modules are
    # cheap grey plumbing previews until the player reaches the joint before them.
    # This avoids rendering every future red grid / guide line while preserving
    # the readable modular maze shape.
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    reveal_idx = len(COURSE_MODULES) - 1 if player is None else reveal_module_index_for_player(player)

    for module in COURSE_MODULES:
        # Level 3+ performance cull: far-future modules are not rendered at all.
        # The next couple of modules remain as grey plumbing preview; everything
        # beyond that will appear once you move forward.
        if (player is not None and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and
                module.index > reveal_idx + PREVIEW_MODULES_AHEAD):
            continue
        preview = player is not None and module_is_preview_only(module.index, player)
        if preview:
            # Only the exterior tube outline: no internal guide grid, no red grids.
            far = max(0, module.index - reveal_idx)
            alpha = PREVIEW_WIREFRAME_ALPHA if far <= 1 else PREVIEW_FAR_WIREFRAME_ALPHA
            glColor4f(0.42, 0.44, 0.48, alpha)
            glLineWidth(0.9)
            glBegin(GL_LINES)
            for a, b in module_box_segments(module):
                glVertex3f(a.x, a.y, a.z)
                glVertex3f(b.x, b.y, b.z)
            glEnd()
            continue

        alpha = 1.0 if player is None else module_trail_alpha(module, player, t)
        if alpha <= 0.01:
            continue
        glColor4f(0.35, 0.75, 1.0, 0.45 * alpha)
        glLineWidth(1.1)
        glBegin(GL_LINES)
        for a, b in module_box_segments(module):
            glVertex3f(a.x, a.y, a.z)
            glVertex3f(b.x, b.y, b.z)
        glEnd()

        glColor4f(0.35, 0.35, 0.50, 0.35 * alpha)
        glLineWidth(1.0)
        glBegin(GL_LINES)
        for a, b in module_guide_segments(module):
            glVertex3f(a.x, a.y, a.z)
            glVertex3f(b.x, b.y, b.z)
        glEnd()

    current_joint = None if player is None else turn_joint_index_for_point(player.origin, pad=CELL_SPACING * 1.2)
    for joint_index, (center, open_faces) in enumerate(turn_chamber_joints()):
        if (player is not None and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and
                joint_index > reveal_idx + PREVIEW_MODULES_AHEAD):
            continue
        preview = (
            player is not None and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and
            joint_index >= reveal_idx and current_joint != joint_index
        )
        if preview:
            glColor4f(0.42, 0.44, 0.48, PREVIEW_WIREFRAME_ALPHA * 0.9)
            glLineWidth(0.9)
            glBegin(GL_LINES)
            for a, b in turn_chamber_segments(center):
                glVertex3f(a.x, a.y, a.z)
                glVertex3f(b.x, b.y, b.z)
            glEnd()
            continue

        alpha = 1.0 if player is None else joint_trail_alpha(joint_index, player, t)
        if alpha <= 0.01:
            continue
        glColor4f(0.35, 0.75, 1.0, 0.45 * alpha)
        glLineWidth(1.1)
        glBegin(GL_LINES)
        for a, b in turn_chamber_segments(center):
            glVertex3f(a.x, a.y, a.z)
            glVertex3f(b.x, b.y, b.z)
        glEnd()

        glColor4f(0.35, 0.35, 0.50, 0.35 * alpha)
        glLineWidth(1.0)
        glBegin(GL_LINES)
        for a, b in turn_chamber_face_grid_segments(center, open_faces):
            glVertex3f(a.x, a.y, a.z)
            glVertex3f(b.x, b.y, b.z)
        glEnd()

    if player is not None:
        draw_disintegration_curtain(player, t)
        draw_zap_barriers(player, t)

    glDisable(GL_BLEND)

def draw_zap_barriers(player: "PlayerCube", t: float):
    if not ZAP_BARRIERS:
        return
    now_keys = list(ZAP_BARRIERS.items())
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    for key, started in now_keys:
        level, old_index = key
        if level != ACTIVE_LEVEL or old_index >= len(COURSE_MODULES):
            continue
        age = t - started
        if age < 0.0 or age > ZAP_BARRIER_SECONDS:
            continue
        prev = COURSE_MODULES[old_index]
        center = prev.end_center()
        half = TUNNEL_HALF
        p = clamp(age / max(0.001, ZAP_BARRIER_SECONDS), 0.0, 1.0)
        pulse = 0.5 + 0.5 * math.sin(t * math.tau * ZAP_BARRIER_FLASH_HZ)
        alpha = (1.0 - smoothstep(p)) * (0.35 + 0.55 * pulse)

        glPushMatrix()
        glTranslatef(center.x, center.y, center.z)
        gl_apply_basis(prev.basis_z, prev.basis_y, prev.basis_x)
        glColor4f(0.95, 1.0, 1.0, alpha)
        glLineWidth(1.8 + 4.0 * pulse)
        glBegin(GL_LINE_LOOP)
        glVertex3f(-half, COURSE_Y_MIN, 0.018)
        glVertex3f( half, COURSE_Y_MIN, 0.018)
        glVertex3f( half, COURSE_Y_MAX, 0.018)
        glVertex3f(-half, COURSE_Y_MAX, 0.018)
        glEnd()

        glColor4f(0.55, 0.95, 1.0, alpha * 0.82)
        glLineWidth(1.0 + 2.8 * pulse)
        glBegin(GL_LINES)
        for i in range(13):
            u = -half + (2.0 * half) * (i / 12.0)
            jitter = math.sin(t * 31.0 + i * 1.7) * (0.15 + 0.55 * (1.0 - p))
            glVertex3f(u + jitter, COURSE_Y_MIN, 0.022)
            glVertex3f(-u - jitter, COURSE_Y_MAX, 0.022)
            y = COURSE_Y_MIN + (COURSE_Y_MAX - COURSE_Y_MIN) * (i / 12.0)
            glVertex3f(-half, y + jitter, 0.024)
            glVertex3f( half, y - jitter, 0.024)
        glEnd()
        glPopMatrix()
    glDisable(GL_BLEND)


def draw_portal(t: float, portal_charge: float = 0.0):
    portal_charge = clamp(portal_charge, 0.0, 1.0)
    glPushMatrix()
    glTranslatef(*PORTAL_POSITION)
    apply_portal_orientation()

    pulse = 0.5 + 0.5 * math.sin(t * (4.0 + portal_charge * 6.0))
    half = PORTAL_SIZE * 0.5

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    # Glow rings. Touching the portal charges them up so the player gets a
    # readable "yes, this is the exit, keep pushing" cue before the full warp fires.
    for i in range(9, 0, -1):
        scale = 1.0 + i * (0.075 + 0.035 * portal_charge) + pulse * (0.05 + 0.08 * portal_charge)
        alpha = 0.04 + 0.028 * i + portal_charge * (0.035 + 0.018 * i)
        hue = 0.50 + portal_charge * 0.16 + t * 0.025 + i * 0.012
        rr, gg, bb = _hsv(hue, 0.55 + 0.35 * portal_charge, 1.0)
        glColor4f(rr, gg, bb, min(0.62, alpha))
        glBegin(GL_QUADS)
        glVertex3f(-half * scale, -half * scale, 0.0)
        glVertex3f( half * scale, -half * scale, 0.0)
        glVertex3f( half * scale,  half * scale, 0.0)
        glVertex3f(-half * scale,  half * scale, 0.0)
        glEnd()

    # Rotating petal halo. This is deliberately a different color family from
    # the cyan portal core: pink/violet/gold petals bloom as the player gets near
    # and get more aggressive when cells actually enter the portal plane.
    if portal_charge > 0.025:
        petal_power = smoothstep(clamp((portal_charge - 0.025) / 0.975, 0.0, 1.0))
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        petal_layers = (
            (10, 0.92, 1.72, 0.84, 0.035, 1.0),
            (14, 0.78, 1.42, 0.93, 0.024, -1.0),
        )
        for petal_count, inner_mul, outer_mul, hue_base, width_base, direction in petal_layers:
            rot = t * direction * (0.55 + 2.25 * petal_power)
            for i in range(petal_count):
                a = (i / petal_count) * math.tau + rot + math.sin(t * 1.8 + i) * 0.055
                width = width_base + petal_power * 0.045
                inner = half * (inner_mul + 0.035 * math.sin(t * 3.1 + i))
                outer = half * (outer_mul + 0.55 * petal_power + 0.10 * pulse * math.sin(t * 2.6 + i * 0.7))
                hue = hue_base + i * 0.018 + t * 0.045 + petal_power * 0.035
                rr, gg, bb = _hsv(hue, 0.72, 1.0)
                alpha = (0.045 + 0.34 * petal_power) * (0.70 + 0.30 * math.sin(t * 3.4 + i) ** 2)
                glColor4f(rr, gg, bb, alpha)
                glBegin(GL_QUADS)
                glVertex3f(math.cos(a - width) * inner, math.sin(a - width) * inner, 0.042)
                glVertex3f(math.cos(a + width) * inner, math.sin(a + width) * inner, 0.042)
                glVertex3f(math.cos(a + width * 0.42) * outer, math.sin(a + width * 0.42) * outer, 0.052)
                glVertex3f(math.cos(a - width * 0.42) * outer, math.sin(a - width * 0.42) * outer, 0.052)
                glEnd()

        # A thin rotating outline at the petal tips so the flower reads even against
        # the busy laser-grid background.
        glLineWidth(1.0 + 2.0 * petal_power)
        for ring in range(2):
            rr, gg, bb = _hsv(0.86 + ring * 0.08 + t * 0.06, 0.68, 1.0)
            glColor4f(rr, gg, bb, 0.12 + 0.26 * petal_power)
            glBegin(GL_LINE_LOOP)
            segs = 56
            for j in range(segs):
                a = (j / segs) * math.tau + t * (0.75 + ring * 0.42)
                rpetal = half * (1.28 + 0.50 * petal_power + 0.10 * math.sin(a * 10.0 - t * 3.0))
                glVertex3f(math.cos(a) * rpetal, math.sin(a) * rpetal, 0.058)
            glEnd()
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    # Commit swirl: once roughly half the surviving body has been swallowed, the
    # portal starts expanding outside the tunnel bounds. At 2/3 it grows into a
    # proper circular outside-the-maze vortex.
    if portal_charge >= 0.45:
        commit = smoothstep(clamp((portal_charge - 0.45) / 0.55, 0.0, 1.0))
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        outer_base = half * (1.30 + 2.35 * commit)
        for ring in range(5):
            rr, gg, bb = _hsv(0.76 + ring * 0.055 + t * 0.075, 0.74, 1.0)
            alpha = (0.06 + 0.15 * commit) * (1.0 - ring * 0.11)
            glColor4f(rr, gg, bb, alpha)
            glLineWidth(1.4 + 4.2 * commit)
            glBegin(GL_LINE_LOOP)
            segs = 72
            for j in range(segs):
                a = (j / segs) * math.tau + t * (0.90 + ring * 0.23)
                # Intentionally not a perfect circle: cubist/warped vortex mouth.
                wobble = 1.0 + 0.075 * math.sin(a * 7.0 - t * 4.2 + ring)
                radius = outer_base * (1.0 + ring * 0.22) * wobble
                glVertex3f(math.cos(a) * radius, math.sin(a) * radius, 0.075 + ring * 0.006)
            glEnd()

        # Spiral spokes pulling inward/outward. These extend past the portal
        # square so it reads as an expanding vortex, not just brighter petals.
        glLineWidth(1.0 + 3.2 * commit)
        for i in range(48):
            a0 = (i / 48.0) * math.tau + t * (1.25 + 2.2 * commit)
            a1 = a0 + 0.52 + 1.15 * commit
            r0 = half * (0.58 + 0.10 * math.sin(t * 3.0 + i))
            r1 = outer_base * (0.92 + 0.36 * math.sin(i * 0.73) ** 2)
            rr, gg, bb = _hsv(0.62 + i * 0.014 + t * 0.10, 0.62, 1.0)
            glColor4f(rr, gg, bb, (0.045 + 0.19 * commit) * (0.65 + 0.35 * math.sin(t * 4.0 + i) ** 2))
            glBegin(GL_LINES)
            glVertex3f(math.cos(a0) * r0, math.sin(a0) * r0, 0.09)
            glVertex3f(math.cos(a1) * r1, math.sin(a1) * r1, 0.09)
            glEnd()
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    # Core portal.
    core = 0.55 + 0.22 * pulse + 0.28 * portal_charge
    cr, cg, cb = _hsv(0.49 + t * 0.035 + portal_charge * 0.20, 0.48 + 0.42 * portal_charge, 1.0)
    glColor4f(cr, cg, cb, min(0.95, core))
    glBegin(GL_QUADS)
    glVertex3f(-half, -half, 0.0)
    glVertex3f( half, -half, 0.0)
    glVertex3f( half,  half, 0.0)
    glVertex3f(-half,  half, 0.0)
    glEnd()

    if portal_charge > 0.01:
        # Additive warp mouth / guide streaks once at least part of the body is in.
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glLineWidth(1.2 + 5.0 * portal_charge)
        for i in range(30):
            a = i / 30.0 * math.tau + t * (1.5 + portal_charge * 3.5)
            r0 = half * (0.10 + 0.14 * math.sin(t * 4.0 + i))
            r1 = half * (0.45 + 0.55 * portal_charge)
            rr, gg, bb = _hsv(t * 0.09 + i * 0.025, 0.65, 1.0)
            glColor4f(rr, gg, bb, 0.12 + 0.55 * portal_charge)
            glBegin(GL_LINES)
            glVertex3f(math.cos(a) * r0, math.sin(a) * r0, 0.035)
            glVertex3f(math.cos(a) * r1, math.sin(a) * r1, 0.035)
            glEnd()

        # Rotating cubist squares inside the portal.
        for j in range(4):
            scale = 0.35 + j * 0.17 + portal_charge * 0.18
            rot = t * (55.0 + j * 21.0) * (1.0 if j % 2 == 0 else -1.0)
            glPushMatrix()
            glRotatef(rot, 0, 0, 1)
            glColor4f(1.0, 1.0, 1.0, (0.10 + 0.16 * j) * portal_charge)
            glLineWidth(1.0 + 2.0 * portal_charge)
            glBegin(GL_LINE_LOOP)
            glVertex3f(-half * scale, -half * scale, 0.06)
            glVertex3f( half * scale, -half * scale, 0.06)
            glVertex3f( half * scale,  half * scale, 0.06)
            glVertex3f(-half * scale,  half * scale, 0.06)
            glEnd()
            glPopMatrix()

    glDisable(GL_BLEND)
    glPopMatrix()


def fragment_fade_color(fragment: Fragment):
    """Broken chunks burn toward white, then cool into grey as they expire."""
    life = fragment.expiry_ratio
    if life < 0.38:
        u = smoothstep(life / 0.38)
        return tuple(fragment.color[i] * (1.0 - u) + 1.0 * u for i in range(3))
    u = smoothstep((life - 0.38) / 0.62)
    grey = (0.43, 0.43, 0.43)
    return tuple(1.0 * (1.0 - u) + grey[i] * u for i in range(3))


def fragment_blink_alpha(fragment: Fragment, t: float) -> float:
    """Make nearly-expired lost cubes visibly blink before they vanish for good."""
    if not fragment.expiry_warning:
        return 1.0
    # Faster and harsher near the end, like the structure is losing lock.
    urgency = 1.0 - clamp(fragment.expiry_remaining / max(0.001, RECOUPLING_FRAGMENT_EXPIRY_BLINK_SECONDS), 0.0, 1.0)
    hz = 4.0 + 7.0 * urgency
    pulse = 0.5 + 0.5 * math.sin(t * math.tau * hz)
    return 0.22 + 0.78 * pulse


def portal_cell_absorbed(cell_world_pos: Vec3) -> bool:
    """Whether an intact cell has committed into the portal throat.

    Absorbed cells are still counted as surviving cargo, but are no longer
    drawn as exiting the far side. This makes the portal behave like a swallowing
    volume instead of a transparent checkpoint plane.
    """
    if PORTAL_MODULE is None:
        return False
    local = PORTAL_MODULE.world_to_local(cell_world_pos)
    dx = local.x - PORTAL_LOCAL_X
    lateral = max(abs(local.y), abs(local.z))
    return dx >= PORTAL_ABSORB_X and lateral <= PORTAL_CAPTURE_HALF


def draw_player(player: PlayerCube, absorb_portal_cells: bool = False, t: float = 0.0):
    # Intact body. Cells that have gone into the portal are swallowed by the
    # portal and stop rendering, but they remain alive for scoring/transcendence.
    for cell in sorted(player.alive_cells):
        p = player.cell_world_pos(cell)
        if absorb_portal_cells and portal_cell_absorbed(p):
            continue
        glPushMatrix()
        glTranslatef(p.x, p.y, p.z)
        cell_color, flash_outline = player_flash_tint(player.color_for_cell(cell), t)
        draw_unit_cube(cell_color, 1.0, outline=True, outline_color=flash_outline)
        glPopMatrix()

    # Debris/fragments. The pieces no longer stay candy-colored forever; they
    # bleach white and then cool toward grey as they drift off.
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    for f in player.fragments:
        glPushMatrix()
        glTranslatef(f.pos.x, f.pos.y, f.pos.z)
        a = f.rot_axis.normalized()
        glRotatef(f.rot_angle, a.x, a.y, a.z)
        blink = fragment_blink_alpha(f, t)
        alpha = (0.35 + 0.55 * f.alpha) * blink
        # Blinking expired-ish cubes get a faint outline so the player sees
        # the last chance to hit C before that chunk is gone for the level.
        draw_unit_cube(fragment_fade_color(f), alpha, outline=f.expiry_warning)
        glPopMatrix()
    glDisable(GL_BLEND)


# -----------------------------------------------------------------------------
# Collision sparks / temporary impact glows
# -----------------------------------------------------------------------------

@dataclass
class SparkParticle:
    pos: Vec3
    vel: Vec3
    color: tuple
    age: float = 0.0
    lifetime: float = SPARK_LIFETIME
    size: float = 1.0

    def update(self, dt: float):
        self.age += dt
        self.vel = Vec3(self.vel.x, self.vel.y + SPARK_GRAVITY * dt, self.vel.z)
        self.pos = self.pos + self.vel * dt
        damp = max(0.0, 1.0 - 1.7 * dt)
        self.vel = self.vel * damp

    @property
    def alpha(self):
        return max(0.0, 1.0 - self.age / max(0.001, self.lifetime))

    @property
    def alive(self):
        return self.age < self.lifetime


@dataclass
class ImpactGlow:
    kind: str
    pos: Vec3
    color: tuple
    age: float = 0.0
    lifetime: float = 0.5
    laser: object = None
    local_y: float = 0.0
    local_z: float = 0.0
    radius: float = 1.0

    def update(self, dt: float):
        self.age += dt

    @property
    def alpha(self):
        return max(0.0, 1.0 - self.age / max(0.001, self.lifetime))

    @property
    def alive(self):
        return self.age < self.lifetime


IMPACT_SPARKS = []
IMPACT_GLOWS = []
CUBE_IMPACT_FLASH_TIMER = 0.0
CUBE_IMPACT_FLASH_LIFETIME = CUBE_IMPACT_FLASH_SECONDS
CUBE_IMPACT_FLASH_COLOR = (1.0, 0.08, 0.03)
COLLAPSE_TRIGGERED = set()


def trigger_cube_impact_flash(color, seconds: float = CUBE_IMPACT_FLASH_SECONDS):
    """Flash the player cube in the same color family as the struck object.

    This is intentionally cheap: no extra cube mesh, no global screen blast.
    The existing player cells simply get color-tinted toward red/white or
    blue/white for a short antique arcade-style hit beat.
    """
    global CUBE_IMPACT_FLASH_TIMER, CUBE_IMPACT_FLASH_LIFETIME, CUBE_IMPACT_FLASH_COLOR
    CUBE_IMPACT_FLASH_TIMER = max(CUBE_IMPACT_FLASH_TIMER, seconds)
    CUBE_IMPACT_FLASH_LIFETIME = max(0.001, seconds)
    CUBE_IMPACT_FLASH_COLOR = color


def player_flash_tint(base_color, t: float):
    if CUBE_IMPACT_FLASH_TIMER <= 0.0:
        return base_color, None
    life = clamp(CUBE_IMPACT_FLASH_TIMER / max(0.001, CUBE_IMPACT_FLASH_LIFETIME), 0.0, 1.0)
    strobe = 0.5 + 0.5 * math.sin(t * math.tau * CUBE_IMPACT_FLASH_HZ)
    hot = tuple(CUBE_IMPACT_FLASH_COLOR[i] * (1.0 - strobe) + 1.0 * strobe for i in range(3))
    mix = clamp((0.28 + 0.55 * strobe) * life, 0.0, 0.88)
    tinted = tuple(base_color[i] * (1.0 - mix) + hot[i] * mix for i in range(3))
    outline = (1.0, 1.0, 1.0) if strobe > 0.62 else None
    return tinted, outline


def update_impact_effects(dt: float):
    global CUBE_IMPACT_FLASH_TIMER
    for spark in IMPACT_SPARKS:
        spark.update(dt)
    for glow in IMPACT_GLOWS:
        glow.update(dt)
    IMPACT_SPARKS[:] = [spark for spark in IMPACT_SPARKS if spark.alive]
    IMPACT_GLOWS[:] = [glow for glow in IMPACT_GLOWS if glow.alive]
    if CUBE_IMPACT_FLASH_TIMER > 0.0:
        CUBE_IMPACT_FLASH_TIMER = max(0.0, CUBE_IMPACT_FLASH_TIMER - dt)


def clear_level_runtime_effects(clear_impact_particles: bool = True):
    """Reset per-attempt transient level state without changing score/progress.

    Collapsed trail state is keyed globally by (ACTIVE_LEVEL, old_index). If the
    player dies after old tunnel sections have been eaten, those keys can keep
    the freshly reset module visually/collision-wise faded out. Death/reassembly
    should restart the *same level* from a clean maze, so clear collapse runtime
    state and optionally old sparks/glows too.
    """
    global COLLAPSE_TRIGGERED, COLLAPSE_STARTED_AT, LASER_REVEAL_AT, LASER_REVEAL_SOUND_PLAYED
    COLLAPSE_TRIGGERED = set()
    COLLAPSE_STARTED_AT = {}
    LASER_REVEAL_SOUND_PLAYED = set()
    if ACTIVE_LEVEL < PREVIEW_CULL_START_LEVEL:
        LASER_REVEAL_AT = {module.index: -9999.0 for module in COURSE_MODULES}
    else:
        LASER_REVEAL_AT = {0: -9999.0}
    if clear_impact_particles:
        IMPACT_SPARKS.clear()
        IMPACT_GLOWS.clear()
        global CUBE_IMPACT_FLASH_TIMER
        CUBE_IMPACT_FLASH_TIMER = 0.0


def _jittered_spark_velocity(normal: Vec3, speed_min: float, speed_max: float):
    n = normal.normalized()
    if n.length() <= 1e-6:
        n = random_vec(1.0).normalized()
    jitter = random_vec(1.0).normalized() * random.uniform(0.20, 0.82)
    return (n * random.uniform(speed_min, speed_max)) + jitter


def spawn_impact_sparks(pos: Vec3, base_color, normal: Vec3, count: int, speed_min: float, speed_max: float, size: float = 1.0):
    # Pixel-sized bright ejecta, color-varied around the thing that was hit.
    for _ in range(count):
        c_jitter = random.uniform(0.70, 1.28)
        white_hot = random.uniform(0.0, 0.28)
        color = (
            clamp(base_color[0] * c_jitter + white_hot + random.uniform(-0.06, 0.08), 0.0, 1.0),
            clamp(base_color[1] * c_jitter + white_hot + random.uniform(-0.06, 0.08), 0.0, 1.0),
            clamp(base_color[2] * c_jitter + white_hot + random.uniform(-0.06, 0.08), 0.0, 1.0),
        )
        IMPACT_SPARKS.append(
            SparkParticle(
                pos=pos + random_vec(0.08),
                vel=_jittered_spark_velocity(normal, speed_min, speed_max),
                color=color,
                lifetime=random.uniform(0.12, SPARK_LIFETIME * 1.05),
                size=size * random.uniform(0.55, 1.10),
            )
        )


def spawn_laser_hit_effect(pos: Vec3, laser: LaserGrid, t: float, severity: int = 1):
    local = laser.to_local(pos, t)
    y = clamp(local.y, -laser.half_y, laser.half_y)
    z = clamp(local.z, -laser.half_z, laser.half_z)
    # Pin the glow to the actual red grid line/square that was contacted.
    y_line = round(y / laser.spacing) * laser.spacing
    z_line = round(z / laser.spacing) * laser.spacing
    if abs(y - y_line) < abs(z - z_line):
        y = y_line
    else:
        z = z_line

    sev = clamp(float(severity), 1.0, 3.0)
    red = (laser.color[0], laser.color[1], laser.color[2])
    IMPACT_GLOWS.append(
        ImpactGlow(
            kind="laser",
            pos=pos,
            color=red,
            lifetime=LASER_GLOW_LIFETIME + 0.035 * sev,
            laser=laser,
            local_y=y,
            local_z=z,
            radius=laser.spacing * (0.82 + 0.14 * sev),
        )
    )
    trigger_cube_impact_flash((1.0, 0.05, 0.02))
    normal = (pos - laser.center).normalized()
    spawn_impact_sparks(pos, red, normal, 7 + int(3 * sev), 4.5, 11.0, 0.65)


def spawn_bound_hit_effect(pos: Vec3, normal: Vec3, severity: int = 1):
    sev = clamp(float(severity), 1.0, 3.0)
    blue = (0.15, 0.80, 1.0)
    IMPACT_GLOWS.append(
        ImpactGlow(
            kind="bounds",
            pos=pos,
            color=blue,
            lifetime=BOUND_GLOW_LIFETIME + 0.03 * sev,
            radius=0.9 + 0.35 * sev,
        )
    )
    trigger_cube_impact_flash((0.35, 0.90, 1.0))
    spawn_impact_sparks(pos, (0.28, 0.92, 1.0), normal, 7 + int(3 * sev), 3.5, 9.0, 0.65)


def _draw_glow_ring_at(pos: Vec3, radius: float, color, alpha: float, t: float):
    glPushMatrix()
    glTranslatef(pos.x, pos.y, pos.z)
    glLineWidth(1.3 + 3.0 * alpha)
    axes = ((0, 1), (1, 2), (0, 2))
    for ring, (a0, a1) in enumerate(axes):
        glColor4f(color[0], color[1], color[2], alpha * (0.34 - ring * 0.055))
        glBegin(GL_LINE_LOOP)
        segs = 24
        wobble = 1.0 + 0.08 * math.sin(t * 9.0 + ring)
        for i in range(segs):
            a = i / segs * math.tau
            coords = [0.0, 0.0, 0.0]
            coords[a0] = math.cos(a) * radius * wobble
            coords[a1] = math.sin(a) * radius * wobble
            glVertex3f(coords[0], coords[1], coords[2])
        glEnd()
    glPopMatrix()


def draw_impact_effects(t: float):
    """Primitive local impact feedback: struck object flashes, a few pixels fly.

    Deliberately not a modern particle/glow festival: hit the red grid,
    the red grid patch flashes red/white; hit the blue cage, that local
    cage point flashes blue/white. The player cube also flashes the same
    color family.
    """
    if not IMPACT_SPARKS and not IMPACT_GLOWS:
        return

    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glDisable(GL_DEPTH_TEST)
    glDepthMask(GL_FALSE)

    for glow in IMPACT_GLOWS:
        a = smoothstep(glow.alpha)
        strobe = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * math.tau * 22.0))
        aa = clamp(a * strobe, 0.0, 1.0)

        if glow.kind == "laser" and glow.laser is not None:
            laser = glow.laser
            glPushMatrix()
            glTranslatef(laser.center.x, laser.center.y, laser.center.z)
            bx, by, bz = laser.basis()
            gl_apply_basis(bx, by, bz)
            laxis = laser.axis.normalized()
            glRotatef(laser.angle(t), laxis.x, laxis.y, laxis.z)

            y = glow.local_y
            z = glow.local_z
            tile = max(0.42, laser.spacing * 0.55)
            y0 = clamp(y - tile, -laser.half_y, laser.half_y)
            y1 = clamp(y + tile, -laser.half_y, laser.half_y)
            z0 = clamp(z - tile, -laser.half_z, laser.half_z)
            z1 = clamp(z + tile, -laser.half_z, laser.half_z)

            # Antique-game flash: a hot red/white local square/cross, no filled
            # billboard, no global red flash, no expensive aura blob.
            glLineWidth(4.5)
            flash_white = 0.5 + 0.5 * math.sin(t * math.tau * 18.0)
            glColor4f(1.0, 0.04 + 0.96 * flash_white, 0.02 + 0.98 * flash_white, 0.95 * aa)
            glBegin(GL_LINE_LOOP)
            glVertex3f(0.030, y0, z0)
            glVertex3f(0.030, y1, z0)
            glVertex3f(0.030, y1, z1)
            glVertex3f(0.030, y0, z1)
            glEnd()
            glBegin(GL_LINES)
            glVertex3f(0.035, y0, z)
            glVertex3f(0.035, y1, z)
            glVertex3f(0.035, y, z0)
            glVertex3f(0.035, y, z1)
            glEnd()
            glLineWidth(2.0)
            glColor4f(1.0, 1.0, 1.0, 0.85 * aa)
            glBegin(GL_LINES)
            glVertex3f(0.045, y - tile * 0.35, z)
            glVertex3f(0.045, y + tile * 0.35, z)
            glVertex3f(0.045, y, z - tile * 0.35)
            glVertex3f(0.045, y, z + tile * 0.35)
            glEnd()
            glPopMatrix()

        elif glow.kind == "bounds":
            # Blue/white local cage flash: just a point plus a simple cross.
            glPointSize(8.0 + 8.0 * aa)
            glColor4f(0.55, 0.92, 1.0, 0.92 * aa)
            glBegin(GL_POINTS)
            glVertex3f(glow.pos.x, glow.pos.y, glow.pos.z)
            glEnd()
            glPointSize(3.0 + 3.0 * aa)
            glColor4f(1.0, 1.0, 1.0, 0.75 * aa)
            glBegin(GL_POINTS)
            glVertex3f(glow.pos.x, glow.pos.y, glow.pos.z)
            glEnd()
            glLineWidth(1.8 + 3.0 * aa)
            glColor4f(0.55, 0.92, 1.0, 0.82 * aa)
            r = glow.radius * 0.45
            glBegin(GL_LINES)
            glVertex3f(glow.pos.x - r, glow.pos.y, glow.pos.z)
            glVertex3f(glow.pos.x + r, glow.pos.y, glow.pos.z)
            glVertex3f(glow.pos.x, glow.pos.y - r, glow.pos.z)
            glVertex3f(glow.pos.x, glow.pos.y + r, glow.pos.z)
            glVertex3f(glow.pos.x, glow.pos.y, glow.pos.z - r)
            glVertex3f(glow.pos.x, glow.pos.y, glow.pos.z + r)
            glEnd()

        elif glow.kind == "collapse":
            # Keep the existing collapse effect readable, but simpler here too.
            _draw_glow_ring_at(glow.pos, glow.radius * (1.0 + 0.45 * (1.0 - a)), glow.color, min(1.0, a), t)

    # Pixel sparks only. No tails by default; tails are the expensive/modern-looking
    # bit the design does not need here.
    glPointSize(2.2)
    glBegin(GL_POINTS)
    for spark in IMPACT_SPARKS:
        a = smoothstep(spark.alpha)
        glColor4f(min(1.0, spark.color[0] + 0.20), min(1.0, spark.color[1] + 0.20), min(1.0, spark.color[2] + 0.20), 0.95 * a)
        glVertex3f(spark.pos.x, spark.pos.y, spark.pos.z)
    glEnd()

    glDepthMask(GL_TRUE)
    glEnable(GL_DEPTH_TEST)
    glDisable(GL_BLEND)
    glLineWidth(1.0)
    glPointSize(1.0)


# -----------------------------------------------------------------------------
# Course materialization / start transition
# -----------------------------------------------------------------------------

def _lerp_vec(a: Vec3, b: Vec3, p: float) -> Vec3:
    return a + (b - a) * p


def _gl_vertex_vec(v: Vec3):
    glVertex3f(v.x, v.y, v.z)


def _rgba(color, alpha_scale=1.0):
    if len(color) == 4:
        return color[0], color[1], color[2], clamp(color[3] * alpha_scale, 0.0, 1.0)
    return color[0], color[1], color[2], clamp(alpha_scale, 0.0, 1.0)


def _draw_singularity_point(source: Vec3, color, progress: float, t: float, strength: float = 1.0):
    p = clamp(progress, 0.0, 1.0)
    if p >= 0.98:
        return
    r, g, b, a = _rgba(color, (1.0 - p) * strength)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glPointSize(5.0 + (1.0 - p) * 15.0 + 2.0 * math.sin(t * 18.0))
    glColor4f(r, g, b, a)
    glBegin(GL_POINTS)
    _gl_vertex_vec(source)
    glEnd()
    glDisable(GL_BLEND)


def draw_materializing_line_segments(segments, source: Vec3, progress: float, color, line_width: float = 1.0,
                                     glow_width: float = 0.0, stagger: float = 0.22, alpha_scale: float = 1.0):
    """Draw target segments as if they are extruding from a single point.

    Each line gets a tiny deterministic delay so a whole structure is not just one
    boring scale-up. It looks like the playfield is being plotted into existence.
    """
    progress = clamp(progress, 0.0, 1.0)
    if progress <= 0.001:
        _draw_singularity_point(source, color, progress, 0.0, alpha_scale)
        return

    def emit_segments(width, blend_mode, glow_alpha):
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, blend_mode)
        glLineWidth(width)
        glBegin(GL_LINES)
        for i, (a, b) in enumerate(segments):
            if stagger > 0.0:
                delay = (((i * 37) % 100) / 100.0) * stagger
                local_p = smoothstep((progress - delay) / max(0.001, 1.0 - delay))
            else:
                local_p = smoothstep(progress)
            if local_p <= 0.001:
                continue
            aa = _lerp_vec(source, a, local_p)
            bb = _lerp_vec(source, b, local_p)
            r, g, bcol, a_alpha = _rgba(color, alpha_scale * glow_alpha * (0.35 + 0.65 * local_p))
            glColor4f(r, g, bcol, a_alpha)
            _gl_vertex_vec(aa)
            _gl_vertex_vec(bb)
        glEnd()
        glDisable(GL_BLEND)

    if glow_width > 0.0:
        emit_segments(glow_width, GL_ONE, 0.22)
    emit_segments(line_width, GL_ONE_MINUS_SRC_ALPHA, 1.0)
    _draw_singularity_point(source, color, progress, 0.0, alpha_scale * 0.75)


def _course_box_segments():
    return all_course_box_segments()

def _course_guide_segments():
    return all_course_guide_segments()


COURSE_BOX_SEGMENTS = _course_box_segments()
COURSE_GUIDE_SEGMENTS = _course_guide_segments()


def draw_materializing_course_frame(progress: float, t: float):
    source = Vec3(0.0, 0.0, 0.0)
    p = smoothstep(progress)
    draw_materializing_line_segments(
        _course_box_segments(), source, p,
        (0.35, 0.85, 1.0, 0.72), line_width=1.25, glow_width=8.5, stagger=0.28,
    )
    draw_materializing_line_segments(
        _course_guide_segments(), source, clamp((p - 0.10) / 0.90, 0.0, 1.0),
        (0.34, 0.48, 0.62, 0.42), line_width=1.0, glow_width=4.5, stagger=0.38,
    )


def _add_segment(segments, y1, z1, y2, z2):
    if abs(y2 - y1) + abs(z2 - z1) < 0.03:
        return
    segments.append((Vec3(0.0, y1, z1), Vec3(0.0, y2, z2)))


def _laser_beam_segments(laser: LaserGrid, t: float):
    segments = []
    gy, gz = laser.gap_center(t)
    y0 = gy - laser.safe_half_y
    y1 = gy + laser.safe_half_y
    z0 = gz - laser.safe_half_z
    z1 = gz + laser.safe_half_z

    z = -laser.half_z
    while z <= laser.half_z + 1e-6:
        if z0 <= z <= z1:
            _add_segment(segments, -laser.half_y, z, max(-laser.half_y, y0), z)
            _add_segment(segments, min(laser.half_y, y1), z, laser.half_y, z)
        else:
            _add_segment(segments, -laser.half_y, z, laser.half_y, z)
        z += laser.spacing

    y = -laser.half_y
    while y <= laser.half_y + 1e-6:
        if y0 <= y <= y1:
            _add_segment(segments, y, -laser.half_z, y, max(-laser.half_z, z0))
            _add_segment(segments, y, min(laser.half_z, z1), y, laser.half_z)
        else:
            _add_segment(segments, y, -laser.half_z, y, laser.half_z)
        y += laser.spacing
    return segments


def _rect_segments(half_y, half_z, gy=0.0, gz=0.0):
    return [
        (Vec3(0.0, gy - half_y, gz - half_z), Vec3(0.0, gy + half_y, gz - half_z)),
        (Vec3(0.0, gy + half_y, gz - half_z), Vec3(0.0, gy + half_y, gz + half_z)),
        (Vec3(0.0, gy + half_y, gz + half_z), Vec3(0.0, gy - half_y, gz + half_z)),
        (Vec3(0.0, gy - half_y, gz + half_z), Vec3(0.0, gy - half_y, gz - half_z)),
    ]


def draw_materializing_laser_grid(laser: LaserGrid, t: float, progress: float):
    p = clamp(progress, 0.0, 1.0)
    if p <= 0.001:
        return

    glPushMatrix()
    glTranslatef(laser.center.x, laser.center.y, laser.center.z)
    bx, by, bz = laser.basis()
    gl_apply_basis(bx, by, bz)
    a = laser.axis.normalized()
    glRotatef(laser.angle(t), a.x, a.y, a.z)

    gy, gz = laser.gap_center(t)
    source = Vec3(0.0, gy, gz)
    beams = _laser_beam_segments(laser, t)
    draw_materializing_line_segments(beams, source, p, laser.color, line_width=3.0, glow_width=10.0, stagger=0.34)
    draw_materializing_line_segments(_rect_segments(laser.safe_half_y, laser.safe_half_z, gy, gz), source, p,
                                     (0.0, 1.0, 0.95, 0.58), line_width=2.0, glow_width=6.5, stagger=0.05)
    draw_materializing_line_segments(_rect_segments(laser.half_y, laser.half_z, 0.0, 0.0), source, clamp((p - 0.12) / 0.88, 0.0, 1.0),
                                     (1.0, 0.25, 0.18, 0.46), line_width=1.4, glow_width=4.5, stagger=0.10)

    glPopMatrix()


def _square_segments_2d(half: float, z: float = 0.0):
    return [
        (Vec3(-half, -half, z), Vec3( half, -half, z)),
        (Vec3( half, -half, z), Vec3( half,  half, z)),
        (Vec3( half,  half, z), Vec3(-half,  half, z)),
        (Vec3(-half,  half, z), Vec3(-half, -half, z)),
    ]


def draw_materializing_portal(t: float, progress: float):
    p = clamp(progress, 0.0, 1.0)
    if p <= 0.001:
        return
    glPushMatrix()
    glTranslatef(*PORTAL_POSITION)
    apply_portal_orientation()

    source = Vec3(0.0, 0.0, 0.0)
    half = PORTAL_SIZE * 0.5
    glEnable(GL_BLEND)
    for i in range(7, 0, -1):
        ring_p = clamp((p - i * 0.035) / max(0.001, 1.0 - i * 0.035), 0.0, 1.0)
        hue = 0.49 + t * 0.025 + i * 0.018
        color = (*_hsv(hue, 0.58 + 0.22 * ring_p, 1.0), 0.30 + 0.42 * ring_p)
        draw_materializing_line_segments(
            _square_segments_2d(half * (1.0 + i * 0.08), 0.015 * i), source, ring_p,
            color, line_width=1.3 + 0.15 * i, glow_width=7.0 + i * 0.8, stagger=0.06,
        )

    core_p = clamp((p - 0.62) / 0.38, 0.0, 1.0)
    if core_p > 0.0:
        pulse = 0.5 + 0.5 * math.sin(t * 5.5)
        cr, cg, cb = _hsv(0.50 + t * 0.04, 0.72, 1.0)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(cr, cg, cb, (0.16 + 0.35 * pulse) * smoothstep(core_p))
        glBegin(GL_QUADS)
        glVertex3f(-half, -half, 0.0)
        glVertex3f( half, -half, 0.0)
        glVertex3f( half,  half, 0.0)
        glVertex3f(-half,  half, 0.0)
        glEnd()

    glDisable(GL_BLEND)
    glPopMatrix()


def draw_materializing_player(player: PlayerCube, t: float, progress: float):
    p = clamp(progress, 0.0, 1.0)
    if p <= 0.46:
        return
    source = player.origin
    cells = sorted(player.alive_cells)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    for idx, cell in enumerate(cells):
        delay = 0.46 + (((idx * 23) % 100) / 100.0) * 0.28
        local_p = smoothstep((p - delay) / max(0.001, 1.0 - delay))
        if local_p <= 0.001:
            continue
        target = player.cell_world_pos(cell)
        pos = _lerp_vec(source, target, local_p)
        spin = (1.0 - local_p) * (220.0 + ((idx * 19) % 90))
        scale = 0.10 + 0.90 * local_p
        glPushMatrix()
        glTranslatef(pos.x, pos.y, pos.z)
        glRotatef(spin + t * 35.0, 1.0, 0.7, 0.25)
        glScalef(scale, scale, scale)
        draw_unit_cube(player.color_for_cell(cell), 0.18 + 0.82 * local_p, outline=True)
        glPopMatrix()
    glDisable(GL_BLEND)


def render_materialization_overlay(t: float, progress: float):
    p = clamp(progress, 0.0, 1.0)
    surf = pygame.Surface((520, 76), pygame.SRCALPHA)
    pulse = 0.5 + 0.5 * math.sin(t * 7.0)
    pygame.draw.rect(surf, (0, 0, 0, 95), surf.get_rect(), border_radius=14)
    pygame.draw.rect(surf, (0, 230, 245, 95 + int(75 * pulse)), surf.get_rect(), width=2, border_radius=14)
    font = get_font(24, True)
    small = get_font(15, False)
    tr, tg, tb = [int(v * 255) for v in _hsv(0.50 + t * 0.08, 0.58, 1.0)]
    label = font.render(f"LEVEL {ACTIVE_LEVEL} FIELD MATERIALIZING", True, (tr, tg, tb))
    sub = small.render("structures plotted from singular points", True, (190, 225, 232))
    surf.blit(label, ((surf.get_width() - label.get_width()) // 2, 10))
    surf.blit(sub, ((surf.get_width() - sub.get_width()) // 2, 42))
    # Progress rail
    bar_x, bar_y, bar_w, bar_h = 64, 62, 392, 4
    pygame.draw.rect(surf, (45, 65, 72, 180), (bar_x, bar_y, bar_w, bar_h), border_radius=3)
    pygame.draw.rect(surf, (0, 230, 245, 210), (bar_x, bar_y, int(bar_w * p), bar_h), border_radius=3)
    draw_surface_2d(surf, DISPLAY[0] // 2, DISPLAY[1] - 74)


def draw_course_materialization_scene(player: PlayerCube, t: float, scene_angles, progress: float):
    """Start-run transition: stars first, then the field draws itself in from points."""
    progress = clamp(progress, 0.0, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    center, zoom = course_center_and_zoom()
    glTranslatef(0.0, 0.0, -zoom)
    glRotatef(scene_angles[0], 1, 0, 0)
    glRotatef(scene_angles[1], 0, 1, 0)
    glRotatef(scene_angles[2], 0, 0, 1)
    glTranslatef(-center.x, -center.y, -center.z)

    draw_stars()

    p = smoothstep(progress)
    draw_materializing_course_frame(clamp(p / 0.72, 0.0, 1.0), t)

    for idx, laser in enumerate(LASERS):
        laser_p = clamp((p - 0.12 - idx * 0.065) / 0.58, 0.0, 1.0)
        draw_materializing_laser_grid(laser, t, laser_p)

    portal_p = clamp((p - 0.34) / 0.58, 0.0, 1.0)
    draw_materializing_portal(t, portal_p)

    player_p = clamp((p - 0.40) / 0.60, 0.0, 1.0)
    draw_materializing_player(player, t, player_p)

    # Tiny finishing flash, not a death-style whiteout.
    if p > 0.88:
        flash = (p - 0.88) / 0.12
        render_fullscreen_overlay((0.65, 0.95, 1.0), 0.10 * math.sin(flash * math.pi))
    render_materialization_overlay(t, p)


# -----------------------------------------------------------------------------
# Procedural audio
# -----------------------------------------------------------------------------

_audio = {
    "ok": False,
    "muted": False,
    "sounds": {},
    "channels": {},
    "last_play": {},
    # Audio asset generation can be surprisingly slow because the long
    # alien-gamelan/drone WAVs are synthesized sample-by-sample in pure Python.
    # Keep the window/title responsive by generating assets in the background
    # and loading them into pygame.mixer from the main loop once ready.
    "init_started": False,
    "load_attempted": False,
    "assets_ready": False,
    "asset_paths": {},
    "asset_error": None,
    "worker": None,
    "worker_process": None,
    "worker_mode": None,
    "assets_missing_on_start": False,
    "asset_missing_names": [],
}


def _audio_dir():
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, AUDIO_DIR_NAME)


AUDIO_ASSET_FILENAMES = {
    "crash": "crash_collision.wav",
    "structure_alert": "structure_loss_dee_doo.wav",
    "ambient": "spaceship_engine_room_hum_loop.wav",
    # Source-gained version of the safe v3 alien-gamelan. If the old v3 cache
    # exists, generation just copies it with gain, instead of re-synthesizing.
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

LEGACY_GAMELAN_ASSET_FILENAMES = (
    "alien_gamelan_more_frequent_v3.wav",
)


def expected_audio_asset_paths(out_dir: str = None):
    if out_dir is None:
        out_dir = _audio_dir()
    return {name: os.path.join(out_dir, filename) for name, filename in AUDIO_ASSET_FILENAMES.items()}


def audio_missing_asset_names():
    return [name for name, path in expected_audio_asset_paths().items() if not os.path.exists(path)]


def audio_asset_cache_progress():
    paths = expected_audio_asset_paths()
    total = len(paths)
    ready = sum(1 for path in paths.values() if os.path.exists(path))
    return ready, total


def _pcm16(v: float) -> int:
    return int(max(-1.0, min(1.0, v)) * 32767)


def _write_wav_mono(path: str, samples, sample_rate: int = AUDIO_SAMPLE_RATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = array("h", (_pcm16(v) for v in samples))
    # Write through a sidecar temp file and atomically rename it into place.
    # The parent Pygame process polls file existence for progress; this prevents
    # it from counting or later trying to load a half-written WAV if the builder
    # is interrupted.
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(data.tobytes())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _source_gain_samples(samples, gain: float = 1.0, peak_ceiling: float = 0.86):
    """Apply source gain without digital clipping.

    This is intentionally boring: multiply the old/safe generated waveform, then
    pull it down only if it would exceed the configured peak ceiling. No extra
    partials, no density change, no soft-clipped distortion baked into the asset.
    """
    samples = list(samples)
    if not samples:
        return samples
    gain = max(0.0, float(gain))
    peak_ceiling = clamp(float(peak_ceiling), 0.05, 0.98)
    peak_after_gain = max(abs(v * gain) for v in samples)
    if peak_after_gain <= 1e-9:
        return samples
    if peak_after_gain > peak_ceiling:
        gain *= peak_ceiling / peak_after_gain
    return [clamp(v * gain, -peak_ceiling, peak_ceiling) for v in samples]


def _read_wav_mono_float(path: str):
    """Read a 16-bit mono/stereo WAV as mono float samples for cache post-gain."""
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width for {path!r}: {width}")
    pcm = array("h")
    pcm.frombytes(frames)
    if sys.byteorder != "little":
        pcm.byteswap()
    if channels <= 1:
        return [v / 32768.0 for v in pcm]
    out = []
    for i in range(0, len(pcm) - channels + 1, channels):
        out.append(sum(pcm[i:i + channels]) / (32768.0 * channels))
    return out


def _write_source_gained_wav(src_path: str, dst_path: str) -> bool:
    try:
        samples = _read_wav_mono_float(src_path)
        _write_wav_mono(dst_path, _source_gain_samples(samples, GAMELAN_SOURCE_GAIN, GAMELAN_SOURCE_TARGET_PEAK))
        print(f"[INFO] Generated source-gained alien gamelan from existing cache: {dst_path}")
        return True
    except Exception as exc:
        print(f"[WARN] Could not source-gain existing gamelan cache {src_path}: {exc}")
        return False


def _env_adsr(t: float, dur: float, attack: float = 0.01, release: float = 0.08) -> float:
    if dur <= 0.0:
        return 0.0
    if t < attack:
        return t / max(attack, 1e-6)
    if t > dur - release:
        return max(0.0, (dur - t) / max(release, 1e-6))
    return 1.0


def _synth_seconds(duration: float, func, sample_rate: int = AUDIO_SAMPLE_RATE):
    n = int(duration * sample_rate)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / sample_rate
        out.append(func(t, i, sample_rate))
    return out


def _soft_clip(x: float) -> float:
    # Cheap tanh-ish clamp without importing numpy. Keeps stacked oscillators from
    # making horrible digital square-crap unless that is specifically desired.
    return x / (1.0 + abs(x))


def generate_audio_assets(force: bool = False):
    """Generate small procedural WAVs. No external assets, no numpy dependency."""
    if not AUDIO_ENABLED:
        return {}
    out_dir = _audio_dir()
    os.makedirs(out_dir, exist_ok=True)
    audio_rng = random.Random(24601)

    paths = expected_audio_asset_paths(out_dir)

    def need(name):
        return force or not os.path.exists(paths[name])

    if need("crash"):
        dur = 0.72
        def f(t, i, sr):
            e = math.exp(-t * 7.2) * _env_adsr(t, dur, 0.002, 0.12)
            thump = math.sin(math.tau * (78.0 - 34.0 * min(1.0, t / dur)) * t) * e * 0.95
            metal = (
                math.sin(math.tau * 277.0 * t + 1.7) * 0.30 +
                math.sin(math.tau * 431.0 * t + 0.2) * 0.18 +
                math.sin(math.tau * 733.0 * t + 2.8) * 0.10
            ) * math.exp(-t * 10.5)
            # Decaying noise burst for broken cube shrapnel.
            noise = (audio_rng.random() * 2.0 - 1.0) * math.exp(-t * 16.0) * 0.50
            return _soft_clip(thump + metal + noise) * 0.78
        _write_wav_mono(paths["crash"], _synth_seconds(dur, f))

    if need("structure_alert"):
        # "DEE-DOO-DEE-DOO-DEE-DOO" structural-loss alarm. Short enough to
        # layer over impacts, but lower and warmer than the critical distress beep.
        note_len = 0.135
        gap = 0.020
        notes = [660.0, 440.0, 660.0, 440.0, 660.0, 440.0]
        dur = len(notes) * (note_len + gap) + 0.05
        def f(t, i, sr):
            idx = int(t / (note_len + gap))
            if idx < 0 or idx >= len(notes):
                return 0.0
            local = t - idx * (note_len + gap)
            if local > note_len:
                return 0.0
            # Soft siren envelope; no square-wave ugliness unless the universe earns it.
            gate = math.sin(math.pi * local / note_len) ** 0.42
            freq = notes[idx]
            vibr = 1.0 + 0.012 * math.sin(math.tau * 7.0 * local)
            sig = math.sin(math.tau * freq * vibr * t) * 0.70
            sig += math.sin(math.tau * (freq * 2.0) * t) * 0.12
            sig += math.sin(math.tau * (freq * 0.5) * t + 0.4) * 0.10
            return _soft_clip(sig * gate) * 0.62
        _write_wav_mono(paths["structure_alert"], _synth_seconds(dur, f))

    if need("ambient"):
        # Slow breathing engine-room hum: title + level ambience, not "music".
        # 24 seconds and integer-ish oscillator cycles keep the loop from clicking.
        dur = 24.0
        def f(t, i, sr):
            p = t / dur
            breath = 0.5 + 0.5 * math.sin(math.tau * p - math.pi / 2.0)
            slow = breath ** 1.85
            micro = 0.5 + 0.5 * math.sin(math.tau * 0.125 * t + 1.1)
            f0 = 36.0 + 3.0 * math.sin(math.tau * p)
            body = math.sin(math.tau * f0 * t) * 0.46
            body += math.sin(math.tau * 54.0 * t + 0.8 + 0.25 * math.sin(math.tau * p)) * 0.28
            body += math.sin(math.tau * 72.0 * t + 1.7) * 0.18
            body += math.sin(math.tau * 108.0 * t + 2.2) * 0.08
            # Barely-there upper air, like machinery behind a wall.
            air = math.sin(math.tau * 216.0 * t + math.sin(math.tau * 0.25 * t) * 0.6) * 0.025
            amp = 0.10 + 0.26 * slow + 0.035 * micro
            return _soft_clip((body + air) * amp) * 0.78
        _write_wav_mono(paths["ambient"], _synth_seconds(dur, f))

    if need("gamelan"):
        # Alien-gamelan / ritual-engine-room tones. First try to reuse the old
        # safe v3 cache and simply write a source-gained copy. If the legacy
        # cache is not there, synthesize the same old-safe material and apply
        # the same plain source gain. No extra density/brightness/clipped rewrite.
        legacy_done = False
        for legacy_name in LEGACY_GAMELAN_ASSET_FILENAMES:
            legacy_path = os.path.join(out_dir, legacy_name)
            if os.path.exists(legacy_path):
                legacy_done = _write_source_gained_wav(legacy_path, paths["gamelan"])
                if legacy_done:
                    break

        if not legacy_done:
            dur = 48.0
            rng = random.Random(73092)
            # Pelog-ish / phrygian-ish uneven scale ratios. The slightly crooked values are
            # intentional; equal temperament sounded too clean and civilized for this thing.
            ratios = [1.0, 1.055, 1.128, 1.172, 1.255, 1.394, 1.515, 1.675, 1.782, 1.883, 2.0]
            roots = [55.0, 61.735, 65.406, 73.416]  # dark A/B/C/D-ish fundamentals
            events = []
            cursor = 1.00
            while cursor < dur - 4.0:
                root = rng.choice(roots)
                ratio = rng.choice(ratios)
                octave = rng.choice([0, 0, 0, 1])
                f0 = root * ratio * (2.0 ** octave)
                tail = rng.uniform(3.5, 7.0)
                amp = rng.uniform(0.17, 0.32)
                brightness = rng.uniform(0.60, 1.30)
                events.append((cursor, tail, f0, amp, brightness, rng.uniform(0.0, math.tau)))
                cursor += rng.uniform(1.15, 3.10)

            def f(t, i, sr):
                sig = 0.0
                for start, tail, f0, amp, brightness, phase0 in events:
                    local = t - start
                    if local < 0.0 or local > tail:
                        continue
                    attack = min(1.0, local / 0.055)
                    decay = math.exp(-local * (0.20 + 0.035 * brightness))
                    release = 1.0 if local < tail - 1.2 else max(0.0, (tail - local) / 1.2)
                    env = (attack ** 0.45) * decay * release

                    drift = 1.0 + 0.0028 * math.sin(math.tau * 0.041 * t + phase0)
                    base = f0 * drift

                    tone = math.sin(math.tau * base * t + phase0) * 0.62
                    tone += math.sin(math.tau * (base * 2.012) * t + phase0 * 0.31) * 0.22 * brightness
                    tone += math.sin(math.tau * (base * 2.713) * t + 1.3) * 0.13 * brightness
                    tone += math.sin(math.tau * (base * 3.917) * t + 2.1) * 0.070 * brightness
                    tone += math.sin(math.tau * (base * 5.431) * t + 0.7) * 0.035 * brightness

                    strike = 0.0
                    if local < 0.22:
                        strike_env = math.exp(-local * 22.0)
                        strike = math.sin(math.tau * (base * 8.0 + 270.0) * t) * strike_env * 0.11 * brightness
                        strike += (audio_rng.random() * 2.0 - 1.0) * strike_env * 0.015

                    sig += (tone + strike) * env * amp

                room = math.sin(math.tau * 27.5 * t + 0.4 * math.sin(math.tau * 0.03125 * t)) * 0.025
                room += math.sin(math.tau * 41.25 * t + 1.2) * 0.014
                return _soft_clip(sig + room) * 0.76

            _write_wav_mono(
                paths["gamelan"],
                _source_gain_samples(_synth_seconds(dur, f), GAMELAN_SOURCE_GAIN, GAMELAN_SOURCE_TARGET_PEAK),
            )

    if need("field"):
        # 8 seconds, four even wooms, loop-safe-ish because all LFOs use whole cycles.
        dur = 8.0
        def f(t, i, sr):
            woom = 0.5 + 0.5 * math.sin(math.tau * 0.5 * t - math.pi / 2.0)
            wobble = 0.5 + 0.5 * math.sin(math.tau * 0.125 * t)
            freq = 42.0 + 22.0 * (woom ** 1.7) + 3.0 * math.sin(math.tau * 1.0 * t)
            base = math.sin(math.tau * freq * t)
            sub = math.sin(math.tau * (freq * 0.5) * t + 0.4) * 0.55
            rasp = math.sin(math.tau * (freq * 2.01) * t + math.sin(math.tau * 0.25 * t) * 1.2) * 0.12
            amp = 0.13 + 0.20 * (woom ** 2.2) + 0.04 * wobble
            return _soft_clip((base + sub + rasp) * amp) * 0.85
        _write_wav_mono(paths["field"], _synth_seconds(dur, f))

    if need("critical"):
        dur = 1.60
        def f(t, i, sr):
            # Four short alarm pips per loop. Slight two-tone warble so it cuts through.
            beat = t % 0.40
            if beat > 0.105:
                return 0.0
            gate = math.sin(math.pi * beat / 0.105) ** 0.35
            warble = 920.0 + 110.0 * math.sin(math.tau * 18.0 * t)
            sig = math.sin(math.tau * warble * t) * 0.72 + math.sin(math.tau * 1840.0 * t) * 0.18
            return sig * gate * 0.52
        _write_wav_mono(paths["critical"], _synth_seconds(dur, f))

    if need("portal"):
        dur = 4.2
        def f(t, i, sr):
            p = t / dur
            rise = p * p
            env = _env_adsr(t, dur, 0.10, 0.70)
            # Floating fifth-ish shimmer plus a rising low sweep and airy noise.
            freqs = [146.8, 220.0, 329.6, 440.0, 659.2]
            pad = 0.0
            for k, base in enumerate(freqs):
                vibr = 1.0 + 0.010 * math.sin(math.tau * (0.17 + k * 0.041) * t + k)
                pad += math.sin(math.tau * (base * (1.0 + 0.42 * rise) * vibr) * t + k * 0.9) * (0.18 / (1 + k * 0.20))
            sweep_freq = 55.0 + 330.0 * (rise ** 1.35)
            sweep = math.sin(math.tau * sweep_freq * t + 5.0 * rise) * (0.20 + 0.28 * rise)
            shimmer = math.sin(math.tau * (880.0 + 1200.0 * rise) * t) * 0.08 * (p ** 0.5)
            noise = (audio_rng.random() * 2.0 - 1.0) * (0.025 + 0.080 * rise) * (1.0 - p * 0.20)
            return _soft_clip((pad + sweep + shimmer + noise) * env) * 0.86
        _write_wav_mono(paths["portal"], _synth_seconds(dur, f))

    if need("portal_wou"):
        # Slow portal-contact oscillation: "wou... wou... wou..." starts as soon
        # as any intact cube cell is actually inside the portal throat. Kept
        # lower/slower than the final ethereal portal-warp sound.
        dur = 8.0
        def f(t, i, sr):
            p = t / dur
            # 1.25 Hz gives ten pulses over eight seconds: insistent, not frantic.
            lfo = 0.5 + 0.5 * math.sin(math.tau * 1.25 * t - math.pi / 2.0)
            gate = 0.20 + 0.80 * (lfo ** 1.65)
            bend = 1.0 + 0.055 * math.sin(math.tau * 1.25 * t + 0.35)
            f0 = 74.0 * bend
            body = math.sin(math.tau * f0 * t) * 0.54
            body += math.sin(math.tau * (f0 * 1.52) * t + 0.7) * 0.24
            body += math.sin(math.tau * (f0 * 2.03) * t + 1.8) * 0.13
            # Vowel-ish moving upper resonance, enough to say "wou" without
            # becoming a goofy siren sample.
            formant = 270.0 + 95.0 * lfo + 25.0 * math.sin(math.tau * 0.25 * t)
            air = math.sin(math.tau * formant * t + 0.4 * math.sin(math.tau * 1.25 * t)) * 0.07
            air += math.sin(math.tau * (formant * 1.91) * t + 1.2) * 0.025
            shimmer = math.sin(math.tau * (520.0 + 40.0 * math.sin(math.tau * 0.5 * t)) * t) * 0.018
            loop_env = 0.5 - 0.5 * math.cos(math.tau * p)
            return _soft_clip((body + air + shimmer) * gate * (0.82 + 0.18 * loop_env)) * 0.62
        _write_wav_mono(paths["portal_wou"], _synth_seconds(dur, f))


    if need("laser_reveal"):
        # Ominous hazard wake-up: a low reverse-ish woosh with red-electric sizzle.
        # Short enough to play whenever a future module's laser grids fortify.
        dur = 1.55
        rng = random.Random(41417)
        def f(t, i, sr):
            p = t / dur
            env = _env_adsr(t, dur, 0.04, 0.38)
            swell = smoothstep(p) * (1.0 - 0.22 * smoothstep(max(0.0, p - 0.76) / 0.24))
            low_freq = 46.0 + 74.0 * smoothstep(p)
            low = math.sin(math.tau * low_freq * t + 2.4 * p * p) * 0.42
            mid = math.sin(math.tau * (138.0 + 170.0 * p) * t + 7.0 * p) * 0.20
            bite = math.sin(math.tau * (510.0 + 210.0 * math.sin(t * 7.0)) * t) * 0.055 * smoothstep(p)
            hiss = (rng.random() * 2.0 - 1.0) * (0.18 * smoothstep(p) * math.exp(-max(0.0, t - 0.18) * 0.7))
            pulse = (0.5 + 0.5 * math.sin(math.tau * (2.0 + 3.2 * p) * t)) ** 2.0
            return _soft_clip((low + mid + bite + hiss * pulse) * env * (0.25 + 0.90 * swell)) * 0.72
        _write_wav_mono(paths["laser_reveal"], _synth_seconds(dur, f))


    if need("laser_dissipate"):
        # Dying red-grid exhale: "aaaaahhhhh..." with ember hiss and a low falling body.
        # This plays when passed hazard grids are zapped/greyed out behind the player.
        dur = 2.85
        rng = random.Random(55217)
        def f(t, i, sr):
            p = t / dur
            env = _env_adsr(t, dur, 0.035, 0.95)
            sigh = math.exp(-t * 0.62)
            # Vowel-ish descending formants: not a literal voice sample, more like
            # the laser cage breathing out and cooling into ash.
            f0 = 132.0 - 55.0 * smoothstep(p)
            body = math.sin(math.tau * f0 * t + 0.55 * math.sin(math.tau * 0.37 * t)) * 0.34
            body += math.sin(math.tau * (f0 * 1.49) * t + 1.1) * 0.17
            form1 = 420.0 - 160.0 * smoothstep(p)
            form2 = 730.0 - 230.0 * smoothstep(p)
            vowel = math.sin(math.tau * form1 * t + 0.6) * 0.11
            vowel += math.sin(math.tau * form2 * t + 1.9) * 0.065
            ember = math.sin(math.tau * (980.0 - 360.0 * p) * t) * 0.030 * (1.0 - p)
            hiss = (rng.random() * 2.0 - 1.0) * (0.16 * math.exp(-t * 0.72) + 0.035 * math.exp(-t * 3.4))
            wobble = 0.72 + 0.28 * math.sin(math.tau * (1.05 - 0.35 * p) * t + 0.4)
            return _soft_clip((body + vowel + ember + hiss) * env * sigh * wobble) * 0.74
        _write_wav_mono(paths["laser_dissipate"], _synth_seconds(dur, f))


    if need("materialize"):
        dur = COURSE_MATERIALIZE_SECONDS + 0.15
        def f(t, i, sr):
            p = min(1.0, t / max(0.001, dur))
            env = _env_adsr(t, dur, 0.02, 0.30)
            # Rising computer-room shimmer with little construction ticks.
            rise_freq = 90.0 + 420.0 * (p ** 1.6)
            body = math.sin(math.tau * rise_freq * t + p * 7.0) * (0.18 + 0.18 * p)
            body += math.sin(math.tau * (rise_freq * 1.503) * t) * 0.12
            tick_phase = (t * 12.0) % 1.0
            tick = 0.0
            if tick_phase < 0.045:
                tick = math.sin(math.tau * (800.0 + 900.0 * p) * t) * (1.0 - tick_phase / 0.045) * 0.28
            return _soft_clip((body + tick) * env) * 0.72
        _write_wav_mono(paths["materialize"], _synth_seconds(dur, f))

    if need("death"):
        dur = DEATH_DISSOLVE_SECONDS + 0.30
        def f(t, i, sr):
            p = t / dur
            env = _env_adsr(t, dur, 0.004, 0.18)
            drop = math.sin(math.tau * (180.0 - 135.0 * p) * t) * (1.0 - p) * 0.55
            crackle = (audio_rng.random() * 2.0 - 1.0) * math.exp(-t * 8.0) * 0.35
            white = math.sin(math.tau * (1200.0 + 900.0 * p) * t) * (p ** 1.7) * 0.18
            return _soft_clip((drop + crackle + white) * env) * 0.78
        _write_wav_mono(paths["death"], _synth_seconds(dur, f))

    if need("reassembly"):
        dur = REASSEMBLY_SECONDS
        def f(t, i, sr):
            p = t / dur
            env = _env_adsr(t, dur, 0.03, 0.18)
            zipf = 140.0 + 620.0 * smoothstep(p)
            sig = math.sin(math.tau * zipf * t + p * 10.0) * 0.28
            sig += math.sin(math.tau * (zipf * 1.5) * t + 1.2) * 0.14
            # Particle-lock clicks accelerate toward the end.
            rate = 5.0 + 24.0 * p
            click_phase = (t * rate) % 1.0
            click = 0.0
            if click_phase < 0.035:
                click = math.sin(math.tau * (650.0 + 900.0 * p) * t) * (1.0 - click_phase / 0.035) * 0.20
            return _soft_clip((sig + click) * env) * 0.72
        _write_wav_mono(paths["reassembly"], _synth_seconds(dur, f))

    if need("recouple"):
        dur = RECOUPLING_SECONDS + 0.20
        def f(t, i, sr):
            p = min(1.0, t / max(0.001, dur))
            env = _env_adsr(t, dur, 0.012, 0.20)
            sweep = 120.0 + 510.0 * smoothstep(p)
            sig = math.sin(math.tau * sweep * t + 6.0 * p) * 0.30
            sig += math.sin(math.tau * (sweep * 1.505) * t + 0.7) * 0.15
            sig += math.sin(math.tau * (sweep * 2.02) * t + 1.4) * 0.07
            # Soft grid ticks accelerating toward lock-in.
            rate = 7.0 + 21.0 * p
            ph = (t * rate) % 1.0
            tick = 0.0
            if ph < 0.035:
                tick = math.sin(math.tau * (720.0 + 520.0 * p) * t) * (1.0 - ph / 0.035) * 0.20
            return _soft_clip((sig + tick) * env) * 0.62
        _write_wav_mono(paths["recouple"], _synth_seconds(dur, f))

    if need("collapse"):
        # CASHHHHHHhhhh, octave-dropped: violent low grid-collapse scrape, then decaying grey debris hiss.
        # New filename forces regeneration instead of reusing the brighter older WAV.
        dur = 2.35
        rng = random.Random(91357)
        def f(t, i, sr):
            p = t / dur
            env = _env_adsr(t, dur, 0.002, 0.50)
            scrape_env = math.exp(-t * 1.38)

            # Main collapse body dropped roughly one octave from the previous version.
            # Keep a little high transient/noise so it still reads as CASHHHH instead of pure subwoofer mud.
            low = math.sin(math.tau * (44.0 - 16.0 * p) * t) * 0.50 * scrape_env
            sub_grind = math.sin(math.tau * (31.0 + 5.0 * math.sin(t * 4.5)) * t) * 0.28 * math.exp(-t * 1.05)
            metal = (
                math.sin(math.tau * (205.0 + 65.0 * math.sin(t * 8.0)) * t) * 0.24 +
                math.sin(math.tau * (365.0 - 140.0 * p) * t + 1.1) * 0.18 +
                math.sin(math.tau * (610.0 + 265.0 * p) * t + 2.4) * 0.10
            ) * scrape_env
            hiss = (rng.random() * 2.0 - 1.0) * (0.22 * math.exp(-t * 0.95) + 0.15 * math.exp(-t * 4.6))
            flash = 0.0
            if t < 0.18:
                flash = math.sin(math.tau * 925.0 * t) * (1.0 - t / 0.18) * 0.20
            return _soft_clip((low + sub_grind + metal + hiss + flash) * env) * 0.86
        _write_wav_mono(paths["collapse"], _synth_seconds(dur, f))

    if need("time_tick"):
        # Gentle countdown clock: slow tick-tock, not a frantic arcade metronome.
        # One 2-second loop: tick at 0.0, lower tock at 1.0.
        dur = 2.0
        def f(t, i, sr):
            sig = 0.0
            for start, freq, amp in ((0.02, 1180.0, 0.34), (1.02, 760.0, 0.30)):
                local = t - start
                if local < 0.0 or local > 0.20:
                    continue
                env = math.exp(-local * 22.0) * _env_adsr(local, 0.20, 0.001, 0.06)
                click = (audio_rng.random() * 2.0 - 1.0) * math.exp(-local * 60.0) * 0.18
                tone = math.sin(math.tau * freq * local) * 0.55
                tone += math.sin(math.tau * (freq * 2.01) * local + 0.4) * 0.13
                sig += (tone + click) * env * amp
            sig += math.sin(math.tau * 46.0 * t) * 0.018 * (0.6 + 0.4 * math.sin(math.tau * 0.5 * t))
            return _soft_clip(sig) * 0.82
        _write_wav_mono(paths["time_tick"], _synth_seconds(dur, f))

    if need("time_buzzer"):
        # Last-10-seconds dynamite warning: long ugly BERRRRRT pulse.
        # It is intentionally buzzer-ish, not musical. Played once per second by audio_update.
        dur = 0.68
        def f(t, i, sr):
            env = _env_adsr(t, dur, 0.006, 0.11)
            wob = 1.0 + 0.018 * math.sin(math.tau * 31.0 * t) + 0.009 * math.sin(math.tau * 53.0 * t + 0.6)
            base = 104.0 * wob
            sig = math.sin(math.tau * base * t) * 0.58
            sig += math.sin(math.tau * (base * 2.02) * t + 0.4) * 0.36
            sig += math.sin(math.tau * (base * 3.01) * t + 1.2) * 0.21
            sig += math.sin(math.tau * (base * 4.04) * t + 2.0) * 0.12
            sig += (audio_rng.random() * 2.0 - 1.0) * 0.055
            # Harsh gate flutter makes it read as BERRRRT rather than a bass note.
            gate = 0.76 + 0.24 * (1.0 if math.sin(math.tau * 18.0 * t) > -0.15 else 0.30)
            return _soft_clip(sig * env * gate) * 0.88
        _write_wav_mono(paths["time_buzzer"], _synth_seconds(dur, f))

    if need("time_siren"):
        # Last-5-seconds WIUWIUWIU siren loop. Higher pitched and obnoxious enough
        # to signal that the pipe is about to eat the player.
        dur = 1.0
        def f(t, i, sr):
            lfo = 0.5 + 0.5 * math.sin(math.tau * 4.0 * t)
            freq = 780.0 + 620.0 * (lfo ** 0.80)
            sig = math.sin(math.tau * freq * t) * 0.52
            sig += math.sin(math.tau * (freq * 1.995) * t + 0.8) * 0.18
            sig += math.sin(math.tau * (freq * 0.502) * t + 1.3) * 0.12
            # Light AM wobble so it goes WIU-WIU instead of flat ambulance wallpaper.
            amp = 0.72 + 0.28 * math.sin(math.tau * 4.0 * t + 0.3)
            return _soft_clip(sig * amp) * 0.64
        _write_wav_mono(paths["time_siren"], _synth_seconds(dur, f))

    return paths


def _set_baseline_sound_volumes(sounds):
    """Baseline volumes; one-shots can override per play."""
    sounds["crash"].set_volume(0.70)
    sounds["structure_alert"].set_volume(0.48)
    sounds["ambient"].set_volume(0.18)
    sounds["gamelan"].set_volume(audio_gain_db(0.24, GAMELAN_GAIN_DB))
    sounds["field"].set_volume(0.26)
    sounds["critical"].set_volume(0.46)
    sounds["portal"].set_volume(0.82)
    sounds["portal_wou"].set_volume(0.34)
    sounds["laser_reveal"].set_volume(0.58)
    sounds["laser_dissipate"].set_volume(0.48)
    sounds["materialize"].set_volume(0.62)
    sounds["death"].set_volume(0.72)
    sounds["reassembly"].set_volume(0.58)
    sounds["recouple"].set_volume(0.52)
    sounds["collapse"].set_volume(0.72)
    sounds["time_tick"].set_volume(0.34)
    sounds["time_buzzer"].set_volume(0.78)
    sounds["time_siren"].set_volume(0.54)


def _reserve_audio_channels():
    _audio["channels"] = {
        "ambient": pygame.mixer.Channel(0),
        "gamelan": pygame.mixer.Channel(8),
        "field": pygame.mixer.Channel(1),
        "critical": pygame.mixer.Channel(2),
        "portal": pygame.mixer.Channel(3),
        "one_shot": pygame.mixer.Channel(4),
        "hazard": pygame.mixer.Channel(9),
        "hazard_fade": pygame.mixer.Channel(10),
        "ui": pygame.mixer.Channel(5),
        "reassembly": pygame.mixer.Channel(6),
        "alert": pygame.mixer.Channel(7),
        "time": pygame.mixer.Channel(11),
        "time_buzzer": pygame.mixer.Channel(12),
        "time_siren": pygame.mixer.Channel(13),
    }


def _audio_asset_worker(force: bool = False):
    """Thread fallback only. Prefer the subprocess builder for first-run cache."""
    try:
        _audio["asset_paths"] = generate_audio_assets(force=force)
        _audio["asset_error"] = None
    except Exception as exc:
        _audio["asset_paths"] = {}
        _audio["asset_error"] = str(exc)
    finally:
        _audio["assets_ready"] = True


def _audio_builder_command(force: bool = False):
    """Command line for the isolated first-run audio cache builder."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, AUDIO_BUILDER_ARG]
    else:
        cmd = [sys.executable or "python", os.path.abspath(__file__), AUDIO_BUILDER_ARG]
    if force:
        cmd.append("--force")
    return cmd


def _start_audio_builder_process(force: bool = False):
    """Start procedural WAV generation in a separate Python process.

    A thread still contends with the GIL, which can starve the Pygame event loop
    while long sample-by-sample synth loops run. A process gives the UI its own
    interpreter and keeps GNOME/Wayland from seeing a dead window.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    kwargs = {
        "cwd": os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd(),
        "env": env,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "posix" and AUDIO_BUILDER_NICE:
        nice_value = int(AUDIO_BUILDER_NICE)
        def _nice_child():
            try:
                os.nice(nice_value)
            except Exception:
                pass
        kwargs["preexec_fn"] = _nice_child
    elif os.name == "nt":
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        if flags:
            kwargs["creationflags"] = flags
    return subprocess.Popen(_audio_builder_command(force=force), **kwargs)


def _poll_audio_builder_process():
    """Update _audio when the external cache-builder process exits."""
    proc = _audio.get("worker_process")
    if proc is None or _audio.get("assets_ready"):
        return
    rc = proc.poll()
    if rc is None:
        return

    _audio["worker_process"] = None
    _audio["worker"] = None

    if rc != 0:
        _audio["asset_paths"] = {}
        _audio["asset_error"] = f"audio cache builder exited with status {rc}"
        _audio["assets_ready"] = True
        return

    missing = audio_missing_asset_names()
    if missing:
        _audio["asset_paths"] = {}
        _audio["asset_error"] = "audio cache builder finished but assets are still missing: " + ", ".join(missing)
    else:
        _audio["asset_paths"] = expected_audio_asset_paths()
        _audio["asset_error"] = None
    _audio["assets_ready"] = True


def init_audio():
    """Start audio without blocking the first visible title frame.

    Missing first-run WAV cache files are generated by a separate Python process,
    not by a thread. This keeps Pygame responsive while the procedural synth code
    burns CPU in another interpreter.
    """
    if not AUDIO_ENABLED:
        return
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=AUDIO_SAMPLE_RATE, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(16)
        _reserve_audio_channels()
        missing_names = audio_missing_asset_names()
        _audio["init_started"] = True
        _audio["load_attempted"] = False
        _audio["assets_ready"] = False
        _audio["asset_paths"] = {}
        _audio["asset_error"] = None
        _audio["worker"] = None
        _audio["worker_process"] = None
        _audio["worker_mode"] = None
        _audio["assets_missing_on_start"] = bool(missing_names)
        _audio["asset_missing_names"] = missing_names

        if not missing_names:
            _audio["asset_paths"] = expected_audio_asset_paths()
            _audio["assets_ready"] = True
            print(f"[INFO] Using cached procedural audio assets: {_audio_dir()}")
            return

        print(f"[INFO] Audio assets not found/generated yet ({len(missing_names)} missing). Rendering procedural audio into: {_audio_dir()}")
        try:
            proc = _start_audio_builder_process(force=False)
            _audio["worker_process"] = proc
            _audio["worker"] = proc
            _audio["worker_mode"] = "process"
            print(f"[INFO] Procedural audio cache builder started as subprocess PID {proc.pid}: {_audio_dir()}")
        except Exception as exc:
            # Last-resort fallback: still better to have audio than to hard-fail,
            # but warn loudly because this can make first-run UI janky again.
            print(f"[WARN] Could not start subprocess audio builder ({exc}); falling back to thread.")
            worker = threading.Thread(target=_audio_asset_worker, kwargs={"force": False}, daemon=True)
            _audio["worker"] = worker
            _audio["worker_mode"] = "thread_fallback"
            worker.start()
            print(f"[INFO] Procedural audio generation/loading started in background thread: {_audio_dir()}")
    except Exception as exc:
        _audio["ok"] = False
        _audio["init_started"] = False
        _audio["assets_missing_on_start"] = False
        _audio["asset_missing_names"] = []
        _audio["worker_process"] = None
        print(f"[WARN] Audio disabled: {exc}")


def audio_try_finish_init():
    """Load generated assets into pygame.mixer once the builder finishes."""
    _poll_audio_builder_process()
    if _audio.get("ok") or not _audio.get("init_started") or _audio.get("load_attempted"):
        return
    if not _audio.get("assets_ready"):
        return

    _audio["load_attempted"] = True
    if _audio.get("asset_error"):
        _audio["ok"] = False
        print(f"[WARN] Audio disabled: {_audio.get('asset_error')}")
        return

    try:
        paths = _audio.get("asset_paths") or expected_audio_asset_paths()
        sounds = {name: pygame.mixer.Sound(path) for name, path in paths.items()}
        _set_baseline_sound_volumes(sounds)
        _audio["sounds"] = sounds
        if not _audio.get("channels"):
            _reserve_audio_channels()
        _audio["ok"] = True
        print(f"[INFO] Procedural audio ready: {paths.get('field', _audio_dir())}")
    except Exception as exc:
        _audio["ok"] = False
        print(f"[WARN] Audio disabled: {exc}")

def audio_setup_in_progress() -> bool:
    return (
        bool(_audio.get("init_started"))
        and not bool(_audio.get("ok"))
        and not bool(_audio.get("load_attempted"))
        and not bool(_audio.get("asset_error"))
    )


def audio_start_blocked() -> bool:
    return (
        bool(AUDIO_BLOCK_START_WHILE_GENERATING)
        and bool(_audio.get("assets_missing_on_start"))
        and audio_setup_in_progress()
    )


def audio_setup_failed() -> bool:
    return bool(_audio.get("assets_missing_on_start")) and bool(_audio.get("load_attempted")) and not bool(_audio.get("ok"))


def audio_available() -> bool:
    return bool(_audio.get("ok")) and not bool(_audio.get("muted"))


def audio_play(name: str, volume: float = None, channel_name: str = "one_shot", cooldown: float = 0.0):
    if not audio_available():
        return
    sound = _audio["sounds"].get(name)
    channel = _audio["channels"].get(channel_name)
    if sound is None or channel is None:
        return
    now = pygame.time.get_ticks() / 1000.0
    last = _audio["last_play"].get(name, -9999.0)
    if cooldown > 0.0 and now - last < cooldown:
        return
    _audio["last_play"][name] = now
    if volume is not None:
        sound.set_volume(clamp(volume, 0.0, 1.0))
    try:
        channel.play(sound)
    except Exception:
        pass


def audio_start_loop(name: str, channel_name: str, volume: float = None, fade_ms: int = 300):
    if not audio_available():
        return
    sound = _audio["sounds"].get(name)
    channel = _audio["channels"].get(channel_name)
    if sound is None or channel is None:
        return
    if volume is not None:
        sound.set_volume(clamp(volume, 0.0, 1.0))
    if not channel.get_busy():
        try:
            channel.play(sound, loops=-1, fade_ms=fade_ms)
        except Exception:
            pass


def audio_stop_loop(channel_name: str, fade_ms: int = 250):
    if not _audio.get("ok"):
        return
    channel = _audio["channels"].get(channel_name)
    if channel and channel.get_busy():
        try:
            channel.fadeout(fade_ms)
        except Exception:
            try:
                channel.stop()
            except Exception:
                pass


def audio_stop_all(fade_ms: int = 250):
    if not _audio.get("ok"):
        return
    for name in ("ambient", "gamelan", "field", "critical", "portal", "one_shot", "hazard", "hazard_fade", "ui", "reassembly", "alert", "time", "time_buzzer", "time_siren"):
        audio_stop_loop(name, fade_ms)


def audio_gain_db(volume: float, db: float) -> float:
    """Return volume scaled by dB, clamped to pygame's 0..1 sound volume range."""
    return clamp(float(volume) * (10.0 ** (float(db) / 20.0)), 0.0, 1.0)


def audio_toggle_mute() -> bool:
    _audio["muted"] = not bool(_audio.get("muted"))
    if _audio["muted"]:
        audio_stop_all(120)
    return _audio["muted"]


def audio_pause_all():
    """Pause pygame mixer channels without destroying loop state."""
    if not _audio.get("ok"):
        return
    try:
        pygame.mixer.pause()
    except Exception:
        pass


def audio_resume_all():
    """Resume pygame mixer channels after P-pause."""
    if not _audio.get("ok") or _audio.get("muted"):
        return
    try:
        pygame.mixer.unpause()
    except Exception:
        pass


def audio_update(game_state: str, player: "PlayerCube", level: int = 1, timed_leg_timer: float = None):
    audio_try_finish_init()
    if not _audio.get("ok"):
        return
    if _audio.get("muted"):
        audio_stop_all(80)
        return

    # Slow ship-engine ambience: present on the title screen and normal level,
    # softer during overlays, absent in the hard-white death void and portal wash.
    if game_state in ("title", "quit_confirm", "level_ready", "course_materialize", "playing", "result_overlay", "time_intro"):
        charge = portal_overlap_charge(player) if game_state == "playing" else 0.0
        if game_state in ("title", "quit_confirm"):
            ambient_vol = 0.34
        elif game_state == "level_ready":
            ambient_vol = 0.28
        elif game_state == "course_materialize":
            ambient_vol = 0.20
        elif game_state == "result_overlay":
            ambient_vol = 0.17
        elif game_state == "time_intro":
            ambient_vol = 0.20
        else:
            ambient_vol = 0.15 + 0.05 * charge
        audio_start_loop("ambient", "ambient", volume=ambient_vol, fade_ms=1200)
    else:
        audio_stop_loop("ambient", fade_ms=900)

    # Sparse alien-gamelan tones: long struck-metal notes with long pauses. This is
    # menu/level atmosphere, so it stays away from the hard-white death void and the
    # portal climax where the dedicated portal wash should own the foreground.
    if game_state in ("title", "quit_confirm", "level_ready", "course_materialize", "playing", "result_overlay", "time_intro"):
        if game_state in ("title", "quit_confirm"):
            gamelan_vol = 0.260
        elif game_state == "level_ready":
            gamelan_vol = 0.220
        elif game_state == "course_materialize":
            gamelan_vol = 0.205
        elif game_state == "result_overlay":
            gamelan_vol = 0.180
        elif game_state == "time_intro":
            gamelan_vol = 0.160
        else:
            gamelan_vol = 0.190
        gamelan_vol = audio_gain_db(gamelan_vol, GAMELAN_GAIN_DB)
        audio_start_loop("gamelan", "gamelan", volume=gamelan_vol, fade_ms=1800)
    else:
        audio_stop_loop("gamelan", fade_ms=1300)

    # The stronger rhythmic field bed lives while the level is being drawn in and
    # while the player is moving through the rotating grids. It gets out of the
    # way for the white death void and the big portal wash.
    if game_state in ("course_materialize", "playing"):
        intact = player.intact_count()
        danger = 1.0 - clamp(intact / MAX_CELLS, 0.0, 1.0)
        charge = portal_overlap_charge(player) if game_state == "playing" else 0.0
        vol = 0.18 + 0.12 * danger + 0.08 * charge
        audio_start_loop("field", "field", volume=vol, fade_ms=500)
    else:
        audio_stop_loop("field", fade_ms=500)

    if game_state == "playing" and player.intact_count() <= CRITICAL_CUBE_WARNING and player.intact_count() > 0:
        audio_start_loop("critical", "critical", volume=0.48, fade_ms=80)
    else:
        audio_stop_loop("critical", fade_ms=160)

    # Portal contact oscillator: as soon as any surviving cube cell is inside
    # the square portal throat, start a slow "wou-wou" loop. It intensifies
    # with commitment, but full transcendence still requires going all in.
    if game_state == "playing":
        intact = max(1, player.intact_count())
        overlap_ratio = portal_cell_overlap_count(player, generous=False) / intact
        absorbed_ratio = portal_absorption_ratio(player)
        if overlap_ratio > 0.0:
            u = max(
                smoothstep(clamp(overlap_ratio / max(PORTAL_CHARGE_RATIO, 1e-6), 0.0, 1.0)) * 0.45,
                smoothstep(clamp((absorbed_ratio - PORTAL_CHARGE_RATIO) / (1.0 - PORTAL_CHARGE_RATIO), 0.0, 1.0)),
            )
            audio_start_loop("portal_wou", "portal", volume=0.16 + 0.34 * u, fade_ms=180)
        else:
            audio_stop_loop("portal", fade_ms=260)
    elif game_state != "portal_warp":
        audio_stop_loop("portal", fade_ms=350)

    # Level 5+ time pressure: normal tick-tock for the whole 30s leg.
    # Last 10s adds a long once-per-second BERRRRT buzzer. Last 5s switches
    # on a higher WIUWIU siren loop over the ticking clock.
    timed_audio_active = (
        (game_state == "time_intro" and level >= TIME_MODE_START_LEVEL) or
        (game_state == "playing" and level >= TIME_MODE_START_LEVEL and timed_leg_timer is not None)
    )
    if timed_audio_active:
        urgency = 0.0
        if timed_leg_timer is not None:
            urgency = 1.0 - clamp(timed_leg_timer / max(0.001, TIME_TIMER_WARNING_SECONDS), 0.0, 1.0)
        audio_start_loop("time_tick", "time", volume=0.20 + 0.18 * urgency, fade_ms=450)

        if game_state == "playing" and timed_leg_timer is not None:
            # Layered countdown: the tick-tock loop stays on for the whole leg.
            # At 10 seconds, add the low BERRRRT buzzer on top. At 5 seconds,
            # keep the buzzer going and add the higher WIUWIU siren on top of both.
            if 0.0 < timed_leg_timer <= TIME_BUZZER_START_SECONDS:
                buzz_urgency = 1.0 - clamp(timed_leg_timer / max(0.001, TIME_BUZZER_START_SECONDS), 0.0, 1.0)
                audio_play("time_buzzer", volume=0.62 + 0.24 * buzz_urgency, channel_name="time_buzzer", cooldown=TIME_BUZZER_COOLDOWN_SECONDS)

            if 0.0 < timed_leg_timer <= TIME_SIREN_START_SECONDS:
                siren_urgency = 1.0 - clamp(timed_leg_timer / max(0.001, TIME_SIREN_START_SECONDS), 0.0, 1.0)
                audio_start_loop("time_siren", "time_siren", volume=0.42 + 0.26 * siren_urgency, fade_ms=90)
            else:
                audio_stop_loop("time_siren", fade_ms=180)
        else:
            audio_stop_loop("time_siren", fade_ms=180)
    else:
        audio_stop_loop("time", fade_ms=500)
        audio_stop_loop("time_siren", fade_ms=180)

# -----------------------------------------------------------------------------
# Collision / effects
# -----------------------------------------------------------------------------

screen_shake_timer = 0.0
flash_timer = 0.0
message_timer = 0.0
message_text = ""


def trigger_hit_effects(severity: int = 1):
    global screen_shake_timer, flash_timer
    screen_shake_timer = SCREEN_SHAKE_DURATION
    # No full-screen red blast on ordinary hits. Collision feedback now belongs
    # on the impacted laser/cage segment via sparks/glows, not across the user's eyes.
    flash_timer = FLASH_DURATION if IMPACT_SCREEN_FLASH_ENABLED else 0.0
    sev = clamp(float(severity), 0.0, 3.0)
    audio_play("crash", volume=0.58 + 0.10 * sev, cooldown=0.10)
    audio_play("structure_alert", volume=0.34 + 0.08 * sev, channel_name="alert", cooldown=0.42)


def set_message(text: str, seconds: float = 1.6):
    global message_text, message_timer
    message_text = text
    message_timer = seconds


def apply_screen_shake():
    if screen_shake_timer <= 0.0:
        return
    strength = 0.12 + 0.28 * (screen_shake_timer / SCREEN_SHAKE_DURATION)
    glTranslatef(random.uniform(-strength, strength), random.uniform(-strength, strength), 0.0)


def render_flash_overlay():
    if flash_timer <= 0.0 or FLASH_DURATION <= 0.0:
        return
    alpha = min(0.45, flash_timer / FLASH_DURATION * 0.45)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    gluOrtho2D(-1, 1, -1, 1)

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glColor4f(1.0, 0.05, 0.0, alpha)
    glBegin(GL_QUADS)
    glVertex2f(-1, -1)
    glVertex2f( 1, -1)
    glVertex2f( 1,  1)
    glVertex2f(-1,  1)
    glEnd()

    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glDisable(GL_BLEND)


def damage_from_lasers(player: PlayerCube, t: float):
    if player_in_turn_laser_grace(player):
        return 0

    # Performance: build the active hazard list once per damage tick.
    # The old code asked every cell to re-check every laser's cull/reveal/trail
    # state, and those helpers in turn call module-location logic. By level 4+
    # that became a Python-side tax even when most hazards were not actually
    # being drawn.
    active_lasers = [laser for laser in LASERS if laser_is_active_for_player(laser, player, t)]
    if not active_lasers:
        return 0

    candidates = []
    for cell in list(player.alive_cells):
        p = player.cell_world_pos(cell)
        if point_in_turn_laser_grace(p, pad=CELL_HALF * 1.5):
            continue
        for laser in active_lasers:
            if laser.hits_point(p, t):
                # Sort from most exposed / closest to laser center, so damage looks coherent.
                local = laser.to_local(p, t)
                exposure = abs(local.x) + 0.04 * (abs(local.y) + abs(local.z))
                candidates.append((exposure, cell, laser))
                break

    if not candidates:
        return 0

    candidates.sort(key=lambda x: x[0])
    destroyed = 0
    seen = set()
    for _, cell, laser in candidates:
        if cell in seen:
            continue
        hit_pos = player.cell_world_pos(cell)
        if player.destroy_cell(cell, laser.center):
            destroyed += 1
            seen.add(cell)
            spawn_laser_hit_effect(hit_pos, laser, t, destroyed)
        if destroyed >= MAX_DAMAGE_PER_EVENT:
            break

    if destroyed:
        trigger_hit_effects(destroyed)
        set_message(f"HIT: -{destroyed} cubes", 0.7)
    return destroyed


def damage_from_bounds(player: PlayerCube):
    # Bounds are less aggressive than lasers: shave cells that protrude outside the cage.
    victims = []
    for cell in list(player.alive_cells):
        p = player.cell_world_pos(cell)
        outside = not point_inside_course(p, pad=BOUNDARY_DAMAGE_PAD)
        if outside:
            victims.append(cell)

    if not victims:
        return 0

    destroyed = 0
    blast_center = player.origin
    for cell in victims[:MAX_DAMAGE_PER_EVENT]:
        hit_pos = player.cell_world_pos(cell)
        normal = (hit_pos - player.origin).normalized()
        if player.destroy_cell(cell, blast_center):
            destroyed += 1
            spawn_bound_hit_effect(hit_pos, normal, destroyed)
    if destroyed:
        trigger_hit_effects(destroyed)
        set_message(f"FIELD EDGE: -{destroyed} cubes", 0.7)
    return destroyed


def portal_cell_overlap_count(player: PlayerCube, generous: bool = False) -> int:
    """Count intact cells in the square portal slab.

    This is the visual/feedback overlap, not the final win condition. A few cells
    touching the portal can make it glow, but the player has to commit the cube
    into the throat before transcendence fires.
    """
    if PORTAL_MODULE is None or player.intact_count() <= 0:
        return 0

    half = PORTAL_CAPTURE_HALF + (0.45 if generous else 0.0)
    before = PORTAL_CAPTURE_X_BEFORE + (0.55 if generous else 0.0)
    after = PORTAL_CAPTURE_X_AFTER + (0.75 if generous else 0.0)

    inside = 0
    for cell in player.alive_cells:
        local = PORTAL_MODULE.world_to_local(player.cell_world_pos(cell))
        dx = local.x - PORTAL_LOCAL_X
        if -before <= dx <= after and abs(local.y) <= half and abs(local.z) <= half:
            inside += 1
    return inside


def portal_absorbed_cell_count(player: PlayerCube) -> int:
    """Count cells that have gone far enough into the portal to be swallowed."""
    if PORTAL_MODULE is None or player.intact_count() <= 0:
        return 0
    absorbed = 0
    for cell in player.alive_cells:
        if portal_cell_absorbed(player.cell_world_pos(cell)):
            absorbed += 1
    return absorbed


def portal_absorption_ratio(player: PlayerCube) -> float:
    intact = player.intact_count()
    if intact <= 0:
        return 0.0
    return clamp(portal_absorbed_cell_count(player) / intact, 0.0, 1.0)


def portal_overlap_charge(player: PlayerCube) -> float:
    """How much the portal should visibly react to the player entering it.

    Below 50% commitment it only gives a modest hint. At ~50% the portal starts
    opening outward. Around 2/3 it gets visibly hungry. Full transcendence still
    waits until essentially the whole surviving body has gone in.
    """
    intact = player.intact_count()
    if intact <= 0 or PORTAL_MODULE is None:
        return 0.0

    slab_ratio = portal_cell_overlap_count(player, generous=True) / max(1, intact)
    absorbed_ratio = portal_absorption_ratio(player)

    local_origin = PORTAL_MODULE.world_to_local(player.origin)
    # Square-distance proximity: charge petals when the cube approaches any part
    # of the square portal, not just the circular centre.
    square_edge_excess = max(abs(local_origin.y), abs(local_origin.z)) - (PORTAL_SIZE * 0.5 + 1.1)
    lateral_factor = 1.0 - clamp(square_edge_excess / 3.0, 0.0, 1.0)
    proximity = clamp((local_origin.x - (PORTAL_LOCAL_X - 6.2)) / 6.2, 0.0, 1.0) * lateral_factor
    soft_hint = proximity * 0.16 + clamp(slab_ratio / PORTAL_CHARGE_RATIO, 0.0, 1.0) * 0.18

    if absorbed_ratio < PORTAL_CHARGE_RATIO:
        return clamp(soft_hint, 0.0, 0.36)

    if absorbed_ratio < PORTAL_STRONG_RATIO:
        u = (absorbed_ratio - PORTAL_CHARGE_RATIO) / (PORTAL_STRONG_RATIO - PORTAL_CHARGE_RATIO)
        return 0.48 + 0.24 * smoothstep(u)

    u = (absorbed_ratio - PORTAL_STRONG_RATIO) / max(0.001, (1.0 - PORTAL_STRONG_RATIO))
    return 0.72 + 0.28 * smoothstep(u)


def portal_reached(player: PlayerCube) -> bool:
    # One cube cell nicking the portal is no longer enough. The portal starts
    # reacting around half commitment, but transcendence requires going all in.
    if PORTAL_MODULE is None or player.intact_count() <= 0:
        return False

    return portal_absorption_ratio(player) >= PORTAL_TRANSCEND_RATIO


def render_level_ready(t: float, level: int):
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()
    glTranslatef(0.0, 0.0, -55.0)
    glRotatef(t * 2.0, 0, 1, 0)
    draw_stars()

    # Solid level-card: no translucent star dandruff bleeding through the sign.
    # The palette is deterministic per level but still feels random across a run.
    bg = level_ready_palette(level)
    pulse = 0.50 + 0.50 * math.sin(t * 5.5)
    panel = pygame.Surface((660, 188), pygame.SRCALPHA)
    pygame.draw.rect(panel, (*bg, 255), panel.get_rect(), border_radius=22)

    # Heavy black techno-card outline plus a tiny animated inner rim.
    pygame.draw.rect(panel, (0, 0, 0, 255), panel.get_rect(), width=5, border_radius=22)
    inner = panel.get_rect().inflate(-16, -16)
    pygame.draw.rect(panel, (0, 0, 0, 145 + int(70 * pulse)), inner, width=2, border_radius=15)

    big = get_font(58, True)
    mid = get_font(35, True)

    # White fill with black outlines reads against every random level-card color.
    line1 = render_outlined_text(big, f"LEVEL {level}", (245, 248, 238), (0, 0, 0), width=4)
    line2 = render_outlined_text(mid, "GET READY!", (255, 246, 185), (0, 0, 0), width=3)

    panel.blit(line1, ((panel.get_width() - line1.get_width()) // 2, 28))
    panel.blit(line2, ((panel.get_width() - line2.get_width()) // 2, 110))
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2)


def render_time_intro(t: float, timer: float):
    """White void announcement before level 5 introduces the timed maze legs."""
    progress = clamp(timer / max(0.001, TIME_INTRO_SECONDS), 0.0, 1.0)
    fade = smoothstep(clamp(timer / max(0.001, TIME_INTRO_FADE_IN_SECONDS), 0.0, 1.0))
    inv = reassembly_inversion_amount(progress)
    void_level = 1.0 - inv
    glClearColor(void_level, void_level, void_level, 1.0)
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glClearColor(*BACKGROUND)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    font = get_font(72, True)
    small = get_font(22, False)
    shade = int(255 * (1.0 - fade))
    alpha = int(255 * fade)
    label = font.render("TIME ...", True, (shade, shade, shade))
    sub = small.render(f"{int(TIME_PER_LEG_SECONDS)} SECONDS PER LEG FROM HERE", True, (shade, shade, shade))
    label.set_alpha(alpha)
    sub.set_alpha(alpha)
    panel = pygame.Surface((840, 220), pygame.SRCALPHA)
    breath = 1.0 + 0.012 * math.sin(t * 2.8)
    if progress > 0.72:
        out = smoothstep((progress - 0.72) / 0.28)
        label.set_alpha(int(alpha * (1.0 - out)))
        sub.set_alpha(int(alpha * (1.0 - out)))
    if breath != 1.0:
        w = max(1, int(label.get_width() * breath))
        h = max(1, int(label.get_height() * breath))
        label = pygame.transform.smoothscale(label, (w, h))
    panel.blit(label, ((panel.get_width() - label.get_width()) // 2, 46))
    panel.blit(sub, ((panel.get_width() - sub.get_width()) // 2, 145))
    draw_surface_2d(panel, DISPLAY[0] // 2, DISPLAY[1] // 2)


def render_time_counter(t: float, seconds_left: float):
    """Top-right timed-leg countdown, Amiga-ish and angry near zero."""
    seconds_left = max(0.0, seconds_left)
    warn = seconds_left <= TIME_TIMER_WARNING_SECONDS
    blink = 0.5 + 0.5 * math.sin(t * math.tau * (5.0 if warn else 1.4))
    if warn and blink < 0.10:
        return
    font = get_font(34, True)
    small = get_font(13, False)
    whole = int(math.ceil(seconds_left))
    main_col = (255, 60, 30) if warn else (245, 245, 210)
    if warn and blink > 0.65:
        main_col = (255, 245, 80)
    label = render_outlined_text(font, f"TIME {whole:02d}", main_col, (0, 0, 0), width=3)
    sub = render_outlined_text(small, "PER LEG", (210, 220, 190), (0, 0, 0), width=2)
    w = max(label.get_width(), sub.get_width()) + 22
    h = label.get_height() + sub.get_height() + 13
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, (0, 0, 0, 88), surf.get_rect(), border_radius=8)
    pygame.draw.rect(surf, (255, 235, 120, 110 if not warn else 180), surf.get_rect(), width=1, border_radius=8)
    surf.blit(label, ((w - label.get_width()) // 2, 5))
    surf.blit(sub, ((w - sub.get_width()) // 2, label.get_height() + 2))
    for yy in range(0, h, 4):
        pygame.draw.line(surf, (0, 0, 0, 38), (0, yy), (w, yy))
    draw_surface_2d(surf, DISPLAY[0] - w / 2 - 18, 28 + h / 2)


def render_danger_out_of_bounds(t: float):
    # Amiga-ish raster warning: large outlined red text at top center.
    blink = 0.5 + 0.5 * math.sin(t * math.tau * 5.0)
    if blink < 0.18:
        return
    font = get_font(42, True)
    label = "DANGER!  OUT OF BOUNDS!"
    # Fake raster: stack a couple of offset red/orange passes with black outline.
    surf = pygame.Surface((720, 74), pygame.SRCALPHA)
    for yoff, col in ((4, (80, 0, 0)), (2, (210, 25, 10)), (0, (255, 220, 90))):
        txt = render_outlined_text(font, label, col, (0, 0, 0), width=3)
        surf.blit(txt, ((surf.get_width() - txt.get_width()) // 2, 10 + yoff))
    # scanline cuts
    for yy in range(0, surf.get_height(), 4):
        pygame.draw.line(surf, (0, 0, 0, 55), (0, yy), (surf.get_width(), yy))
    draw_surface_2d(surf, DISPLAY[0] // 2, 58)


def render_pause_overlay(t: float, locate_enabled: bool):
    """Amiga-ish pause card. Drawn over frozen scene while audio is paused."""
    overlay = pygame.Surface((DISPLAY[0], DISPLAY[1]), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 92))
    draw_surface_2d(overlay, DISPLAY[0] // 2, DISPLAY[1] // 2)

    font = get_font(62, True)
    small = get_font(17, True)
    pulse = 0.55 + 0.45 * math.sin(t * math.tau * 1.6)
    text = "GAME PAUSED"
    # black outline passes
    for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, -2), (-2, 2), (2, 2)):
        s0 = font.render(text, True, (0, 0, 0))
        draw_surface_2d(s0, DISPLAY[0] // 2 + ox, DISPLAY[1] // 2 - 22 + oy)
    green = (80 + int(70 * pulse), 255, 105 + int(55 * pulse))
    s1 = font.render(text, True, green)
    draw_surface_2d(s1, DISPLAY[0] // 2, DISPLAY[1] // 2 - 22)

    sub_text = f"P = resume     L = locate {'ON' if locate_enabled else 'OFF'}     H = help     ESC = title"
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        outline = small.render(sub_text, True, (0, 0, 0))
        draw_surface_2d(outline, DISPLAY[0] // 2 + ox, DISPLAY[1] // 2 + 42 + oy)
    sub = small.render(sub_text, True, (160, 255, 185))
    draw_surface_2d(sub, DISPLAY[0] // 2, DISPLAY[1] // 2 + 42)


def render_recoupling_notice(t: float, progress: float, active_count: int, notice_timer: float):
    if progress <= 0.0 and notice_timer <= 0.0:
        return
    font = get_font(36, True)
    small = get_font(17, True)
    blink = 0.68 + 0.32 * math.sin(t * math.tau * 4.0)
    alpha = 1.0
    if notice_timer > 0.0:
        alpha = clamp(notice_timer / RECOUPLING_NOTICE_SECONDS, 0.0, 1.0)
    label = "RE-COUPLING REQUESTED"
    sublabel = f"LOOSE CELLS RETURNING: {active_count:02d}" if active_count else "STRUCTURAL FIELD STANDING BY"
    surf = pygame.Surface((760, 94), pygame.SRCALPHA)
    # Green Amiga/system raster: no big opaque panel, just outlined phosphor text.
    green = (50, int(190 + 55 * blink), 92)
    hot = (170, 255, 190)
    txt_shadow = render_outlined_text(font, label, (0, 80, 24), (0, 0, 0), width=4)
    txt = render_outlined_text(font, label, hot if blink > 0.80 else green, (0, 0, 0), width=3)
    sub = render_outlined_text(small, sublabel, (90, 255, 130), (0, 0, 0), width=2)
    surf.blit(txt_shadow, ((surf.get_width() - txt_shadow.get_width()) // 2 + 2, 8 + 3))
    surf.blit(txt, ((surf.get_width() - txt.get_width()) // 2, 8))
    surf.blit(sub, ((surf.get_width() - sub.get_width()) // 2, 56))
    for yy in range(0, surf.get_height(), 4):
        pygame.draw.line(surf, (0, 0, 0, int(50 * alpha)), (0, yy), (surf.get_width(), yy))
    surf.set_alpha(int(255 * clamp(alpha, 0.0, 1.0)))
    draw_surface_2d(surf, DISPLAY[0] // 2, 104)

def render_recoupling_cooldown_notice(t: float, notice_timer: float, remaining: float, used: int, limit: int):
    if notice_timer <= 0.0:
        return
    alpha = clamp(notice_timer / max(0.001, RECOUPLING_COOLDOWN_NOTICE_SECONDS), 0.0, 1.0)
    blink = 0.50 + 0.50 * math.sin(t * math.tau * 6.0)
    font = get_font(27 if DISPLAY[0] < 1100 else 31, True)
    small = get_font(15 if DISPLAY[0] < 1100 else 17, True)
    w = min(DISPLAY[0] - 80, 890)
    h = 82
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, (0, 0, 0, int(82 * alpha)), surf.get_rect(), border_radius=10)
    pygame.draw.rect(surf, (255, 35, 25, int((125 + 105 * blink) * alpha)), surf.get_rect(), width=2, border_radius=10)

    label = "RE-COUPLING ON COOLDOWN"
    if RECOUPLING_REQUEST_WINDOW_SECONDS > 0.0:
        sublabel = f"CANNOT RE-COUPLE WHILE ON COOLDOWN   {used}/{limit} USED   WAIT {max(0.0, remaining):0.1f}s"
    else:
        sublabel = f"CANNOT RE-COUPLE WHILE ON COOLDOWN   {used}/{limit} USED THIS LEVEL"

    main_col = (255, int(52 + 90 * blink), 45)
    text = render_outlined_text(font, label, main_col, (0, 0, 0), width=4)
    sub = render_outlined_text(small, sublabel, (255, 175, 150), (0, 0, 0), width=2)
    surf.blit(text, ((w - text.get_width()) // 2, 9))
    surf.blit(sub, ((w - sub.get_width()) // 2, 52))
    for yy in range(1, h, 4):
        pygame.draw.line(surf, (0, 0, 0, int(58 * alpha)), (0, yy), (w, yy))
    surf.set_alpha(int(255 * alpha))
    draw_surface_2d(surf, DISPLAY[0] // 2, DISPLAY[1] - 78)


def render_recoupling_recovery_prompt(player: "PlayerCube", t: float, recoupling_active: bool = False):
    """Bottom-screen warning for recoverable loose cells.

    Design intent: the HUD owns the top-left, out-of-bounds owns top-center,
    and active re-coupling owns upper-center green text. This warning therefore
    lives at the bottom as a yellow Amiga/raster last-chance prompt.
    """
    if recoupling_active:
        return

    recoverable = [f for f in player.fragments if f.alive and f.expiry_remaining > 0.05]
    if not recoverable:
        return

    newest_age = min(f.age for f in recoverable)
    urgent = any(f.expiry_warning for f in recoverable)

    # Show briefly after structural loss. Then stay quiet until the actual
    # expiry blink window, so it is useful instead of becoming permanent UI nagging.
    if newest_age <= RECOUPLING_PROMPT_SECONDS:
        # Fade out near the end of the initial 3-second prompt.
        alpha = 1.0
        fade_start = max(0.0, RECOUPLING_PROMPT_SECONDS - RECOUPLING_PROMPT_FADE_SECONDS)
        if newest_age > fade_start:
            alpha = 1.0 - (newest_age - fade_start) / max(0.001, RECOUPLING_PROMPT_FADE_SECONDS)
        label = "CUBES LOST !!!"
        sublabel = f"PRESS C TO RE-COUPLE   RECOVERABLE: {len(recoverable):02d}"
        blink_hz = 2.4
    elif urgent:
        alpha = 1.0
        label = "LAST CHANCE: PRESS C TO RE-COUPLE !!!"
        soonest = min(f.expiry_remaining for f in recoverable)
        sublabel = f"LOOSE CELLS EXPIRING IN {soonest:0.1f}s"
        blink_hz = 7.5
    else:
        return

    blink = 0.48 + 0.52 * math.sin(t * math.tau * blink_hz)
    if urgent and blink < 0.16:
        # Hard blink during expiry panic; initial 3s warning remains easier to read.
        return

    font = get_font(26 if DISPLAY[0] < 1100 else 30, True)
    small = get_font(15 if DISPLAY[0] < 1100 else 17, True)
    w = min(DISPLAY[0] - 70, 900)
    h = 82
    surf = pygame.Surface((w, h), pygame.SRCALPHA)

    # Thin dark backing strip only, not a huge panel. Keeps yellow readable over stars/lasers.
    pygame.draw.rect(surf, (0, 0, 0, int(72 * alpha)), surf.get_rect(), border_radius=10)
    edge_alpha = int((105 + 95 * blink) * alpha)
    pygame.draw.rect(surf, (255, 210, 35, edge_alpha), surf.get_rect(), width=2, border_radius=10)

    main_col = (255, int(202 + 45 * blink), 35) if not urgent else (255, int(80 + 120 * blink), 20)
    shadow = render_outlined_text(font, label, (92, 72, 0), (0, 0, 0), width=4)
    text = render_outlined_text(font, label, main_col, (0, 0, 0), width=3)
    sub = render_outlined_text(small, sublabel, (255, 245, 120), (0, 0, 0), width=2)
    surf.blit(shadow, ((w - shadow.get_width()) // 2 + 2, 10 + 2))
    surf.blit(text, ((w - text.get_width()) // 2, 10))
    surf.blit(sub, ((w - sub.get_width()) // 2, 50))

    # Raster/scanline feel.
    for yy in range(0, h, 4):
        pygame.draw.line(surf, (0, 0, 0, int(42 * alpha)), (0, yy), (w, yy))
    surf.set_alpha(int(255 * clamp(alpha, 0.0, 1.0)))
    draw_surface_2d(surf, DISPLAY[0] // 2, DISPLAY[1] - 52)



# -----------------------------------------------------------------------------
# Init / main loop
# -----------------------------------------------------------------------------

SELECTED_SDL_VIDEO_DRIVER = None
SELECTED_SDL_VIDEO_DRIVER_ACTUAL = None
DISPLAY_ENV_REPORTED = False
DISPLAY_ATTEMPT_FAILURES = []


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def _display_env_snapshot():
    keys = (
        "XDG_SESSION_TYPE",
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "SDL_VIDEODRIVER",
        "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION",
        "GDMSESSION",
        "XDG_SESSION_ID",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_SHLVL",
    )
    return {key: os.environ.get(key, "") for key in keys}


def _fmt_env_value(value: str) -> str:
    value = "" if value is None else str(value)
    return value if value else "<unset>"


def _conda_context():
    """Best-effort Conda/Miniconda detection for startup diagnostics."""
    env = _display_env_snapshot()
    exe = os.path.realpath(sys.executable or "")
    prefix = os.path.realpath(sys.prefix or "")
    conda_prefix = env.get("CONDA_PREFIX", "").strip()
    default_env = env.get("CONDA_DEFAULT_ENV", "").strip()
    conda_shlvl = env.get("CONDA_SHLVL", "").strip()
    haystack = " ".join([exe, prefix, conda_prefix, default_env]).lower()
    detected = bool(conda_prefix or default_env or conda_shlvl) or any(
        token in haystack for token in ("conda", "miniconda", "anaconda", "mambaforge", "miniforge")
    )
    return {
        "detected": detected,
        "prefix": conda_prefix or prefix,
        "default_env": default_env,
        "shlvl": conda_shlvl,
        "executable": exe or sys.executable or "python",
    }


def _is_conda_python() -> bool:
    return bool(_conda_context().get("detected"))


def _display_process_hints():
    """Best-effort process hints; useful when GNOME says Wayland but Xwayland exists."""
    if not _is_linux():
        return []
    try:
        result = subprocess.run(
            ["ps", "-e", "-o", "comm=,args="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            check=False,
        )
    except Exception:
        return []
    hints = []
    wanted = ("Xorg", "Xwayland", "gnome-shell", "kwin_wayland", "kwin_x11")
    for line in result.stdout.splitlines():
        if any(name in line for name in wanted):
            clean = " ".join(line.split())
            if clean not in hints:
                hints.append(clean[:180])
        if len(hints) >= 8:
            break
    return hints


def _report_display_environment_once():
    global DISPLAY_ENV_REPORTED
    if DISPLAY_ENV_REPORTED:
        return
    DISPLAY_ENV_REPORTED = True

    env = _display_env_snapshot()
    print(f"[INFO] Platform: {stdlib_platform.system()} {stdlib_platform.release()} ({sys.platform})")
    print(f"[INFO] Python: {sys.version.split()[0]} at {sys.executable}")
    conda = _conda_context()
    if conda["detected"]:
        print(
            "[INFO] Conda environment detected: "
            f"env={_fmt_env_value(conda['default_env'])} "
            f"prefix={_fmt_env_value(conda['prefix'])} "
            f"CONDA_SHLVL={_fmt_env_value(conda['shlvl'])}"
        )
    try:
        print(f"[INFO] Pygame: {pygame.version.ver} / SDL {pygame.get_sdl_version()}")
    except Exception:
        pass

    if _is_windows():
        print("[INFO] Display environment: Windows native SDL video path.")
        return

    if _is_linux():
        print(
            "[INFO] Linux display environment: "
            f"XDG_SESSION_TYPE={_fmt_env_value(env['XDG_SESSION_TYPE'])} "
            f"WAYLAND_DISPLAY={_fmt_env_value(env['WAYLAND_DISPLAY'])} "
            f"DISPLAY={_fmt_env_value(env['DISPLAY'])} "
            f"SDL_VIDEODRIVER={_fmt_env_value(env['SDL_VIDEODRIVER'])}"
        )
        print(
            "[INFO] Desktop hints: "
            f"XDG_CURRENT_DESKTOP={_fmt_env_value(env['XDG_CURRENT_DESKTOP'])} "
            f"DESKTOP_SESSION={_fmt_env_value(env['DESKTOP_SESSION'])} "
            f"GDMSESSION={_fmt_env_value(env['GDMSESSION'])}"
        )
        ld_library_path = env.get("LD_LIBRARY_PATH", "").strip()
        pythonpath = env.get("PYTHONPATH", "").strip()
        if ld_library_path:
            print("[WARN] LD_LIBRARY_PATH is set. This can override SDL/OpenGL/Mesa libraries and break Pygame OpenGL context creation.")
            print(f"[WARN] Current LD_LIBRARY_PATH={ld_library_path}")
            print("[WARN] Try: unset LD_LIBRARY_PATH")
        if pythonpath:
            print("[WARN] PYTHONPATH is set. This can make Python import packages from unexpected locations.")
            print(f"[WARN] Current PYTHONPATH={pythonpath}")
            print("[WARN] Try: unset PYTHONPATH")
        hints = _display_process_hints()
        if hints:
            print("[INFO] Display process hints:")
            for line in hints:
                print(f"[INFO]   {line}")
        return

    print("[INFO] Display environment: non-Windows/non-Linux SDL default path.")


def _linux_display_preflight_or_exit():
    """Quit cleanly when there is obviously no graphical display to open."""
    if not _is_linux():
        return
    env = _display_env_snapshot()
    if not env["DISPLAY"] and not env["WAYLAND_DISPLAY"]:
        print("[ERROR] No Linux graphical display was detected.", file=sys.stderr)
        print("[ERROR] DISPLAY and WAYLAND_DISPLAY are both unset, so Pygame cannot open a window.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run Cube Libre from a graphical desktop session, or use SSH with X forwarding only if OpenGL forwarding works.", file=sys.stderr)
        raise SystemExit(2)


def _sdl_driver_candidates():
    """Return SDL video-driver candidates in the order we should try them.

    None means: remove SDL_VIDEODRIVER and let SDL/Pygame choose. On Linux we
    then explicitly try wayland/x11 where relevant, because SDL's default choice
    can be wrong or incomplete on mixed Wayland/Xwayland systems.
    """
    if not _is_linux():
        return [None]

    env = _display_env_snapshot()
    session = env["XDG_SESSION_TYPE"].strip().lower()
    has_wayland = bool(env["WAYLAND_DISPLAY"].strip())
    has_x11 = bool(env["DISPLAY"].strip())
    manual = env["SDL_VIDEODRIVER"].strip()

    candidates = []

    def add(candidate):
        if candidate not in candidates:
            candidates.append(candidate)

    if manual:
        add(manual)

    add(None)  # SDL/Pygame default, with SDL_VIDEODRIVER unset.

    if session == "wayland":
        if has_wayland:
            add("wayland")
        if has_x11:
            add("x11")  # Xwayland fallback inside a Wayland session.
    elif session in ("x11", "xorg"):
        if has_x11:
            add("x11")
        if has_wayland:
            add("wayland")
    else:
        if has_wayland:
            add("wayland")
        if has_x11:
            add("x11")

    return candidates


def _candidate_label(candidate) -> str:
    return "SDL default" if candidate is None else f"SDL_VIDEODRIVER={candidate}"


def _apply_sdl_driver_candidate(candidate):
    if candidate is None:
        os.environ.pop("SDL_VIDEODRIVER", None)
    else:
        os.environ["SDL_VIDEODRIVER"] = str(candidate)


def _set_gl_compat_attributes(profile: str):
    """Set GL attributes before set_mode(). profile='default' leaves SDL alone."""
    if profile == "default":
        return
    try:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 2)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 1)
    except Exception:
        pass
    # Ask for a compatibility profile when SDL/Pygame exposes the constants.
    # If unavailable, this silently degrades to the version request above.
    try:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_COMPATIBILITY)
    except Exception:
        pass


def _try_set_mode_once(mode_size, flags, candidate, profile: str):
    _apply_sdl_driver_candidate(candidate)
    try:
        pygame.display.quit()
    except Exception:
        pass
    try:
        pygame.display.init()
    except Exception as exc:
        return False, None, f"display init failed: {exc}"

    pre_driver = None
    try:
        pre_driver = pygame.display.get_driver()
    except Exception:
        pass

    _set_gl_compat_attributes(profile)

    try:
        pygame.display.set_mode(mode_size, flags)
    except Exception as exc:
        try:
            pygame.display.quit()
        except Exception:
            pass
        driver_text = f"preselected SDL driver {pre_driver!r}; " if pre_driver else ""
        return False, pre_driver, driver_text + str(exc)

    try:
        actual = pygame.display.get_driver()
    except Exception:
        actual = pre_driver
    return True, actual, None


def _print_display_failure_help(failures):
    env = _display_env_snapshot()
    print("[ERROR] Cube Libre could not create a Pygame/OpenGL display.", file=sys.stderr)
    print("[ERROR] This is a windowing/OpenGL context creation failure, not a gameplay-code crash.", file=sys.stderr)
    print("", file=sys.stderr)
    if _is_linux():
        print("Linux display environment:", file=sys.stderr)
        for key, value in env.items():
            print(f"  {key}={_fmt_env_value(value)}", file=sys.stderr)
        print("", file=sys.stderr)

    print("Display attempts:", file=sys.stderr)
    for label, profile, error in failures:
        print(f"  - {label}, GL profile {profile}: {error}", file=sys.stderr)

    if _is_linux():
        print("", file=sys.stderr)
        if env.get("LD_LIBRARY_PATH", "").strip():
            print("Warning: LD_LIBRARY_PATH is set and may override SDL/OpenGL/Mesa libraries.", file=sys.stderr)
            print("Try before launching Cube Libre:", file=sys.stderr)
            print("  unset LD_LIBRARY_PATH", file=sys.stderr)
            print("", file=sys.stderr)
        if env.get("PYTHONPATH", "").strip():
            print("Warning: PYTHONPATH is set and may affect which Python packages are imported.", file=sys.stderr)
            print("Try before launching Cube Libre:", file=sys.stderr)
            print("  unset PYTHONPATH", file=sys.stderr)
            print("", file=sys.stderr)

        conda = _conda_context()
        if conda["detected"]:
            print("Conda environment detected during this failed launch:", file=sys.stderr)
            print(f"  env={_fmt_env_value(conda['default_env'])}", file=sys.stderr)
            print(f"  prefix={_fmt_env_value(conda['prefix'])}", file=sys.stderr)
            print(f"  executable={_fmt_env_value(conda['executable'])}", file=sys.stderr)
            print("", file=sys.stderr)
            print("Conda-specific fixes to try:", file=sys.stderr)
            print("  conda update --all", file=sys.stderr)
            print("", file=sys.stderr)
            print("Or create a clean Cube Libre Conda env instead of using base:", file=sys.stderr)
            print("  conda create -n cube-libre python=3.12 pygame pyopengl numpy", file=sys.stderr)
            print("  conda activate cube-libre", file=sys.stderr)
            print("  unset SDL_VIDEODRIVER PYTHONPATH LD_LIBRARY_PATH", file=sys.stderr)
            print(f"  python {_quote_cmd_part(sys.argv[0])}", file=sys.stderr)
            print("", file=sys.stderr)
            print("Known-good Ubuntu fallback:", file=sys.stderr)
            print("  sudo apt install python3-pygame python3-opengl python3-numpy", file=sys.stderr)
            print(f"  env -u SDL_VIDEODRIVER -u PYTHONPATH -u LD_LIBRARY_PATH /usr/bin/python3 {_quote_cmd_part(sys.argv[0])}", file=sys.stderr)
            print("", file=sys.stderr)

        print("Useful checks on Ubuntu:", file=sys.stderr)
        print("  echo $XDG_SESSION_TYPE", file=sys.stderr)
        print("  echo $WAYLAND_DISPLAY", file=sys.stderr)
        print("  echo $DISPLAY", file=sys.stderr)
        print("  echo $LD_LIBRARY_PATH", file=sys.stderr)
        print("  echo $PYTHONPATH", file=sys.stderr)
        print("  glxinfo -B", file=sys.stderr)
        print("  eglinfo | head -80", file=sys.stderr)
        print("", file=sys.stderr)
        print("If glxinfo/eglinfo are missing:", file=sys.stderr)
        print("  sudo apt install mesa-utils mesa-utils-extra", file=sys.stderr)
        print("", file=sys.stderr)
        print("Manual backend tests:", file=sys.stderr)
        print(f"  SDL_VIDEODRIVER=wayland {_quote_cmd_part(sys.executable)} {_quote_cmd_part(sys.argv[0])}", file=sys.stderr)
        print(f"  SDL_VIDEODRIVER=x11 {_quote_cmd_part(sys.executable)} {_quote_cmd_part(sys.argv[0])}", file=sys.stderr)
        print("", file=sys.stderr)
        print("GNOME note: in a Wayland session, DISPLAY=:0 usually means Xwayland is available; it does not mean the session is X11.", file=sys.stderr)
        print("To test a real X11 session, log out and choose 'Ubuntu on Xorg' from the gear icon on the login screen.", file=sys.stderr)
        if not _is_conda_python():
            print("", file=sys.stderr)
            print("Conda note: if this works outside Conda but fails inside Conda, update Conda or use a clean non-base Conda environment.", file=sys.stderr)


def _set_game_display_robust(mode_size, flags):
    global SELECTED_SDL_VIDEO_DRIVER, SELECTED_SDL_VIDEO_DRIVER_ACTUAL, DISPLAY_ATTEMPT_FAILURES

    # After a successful first window, toggles/resizes should normally reuse the
    # same selected SDL backend. If that fails, fall through to the full candidate
    # ladder so fullscreen changes do not permanently brick the run.
    candidates = []
    if SELECTED_SDL_VIDEO_DRIVER is not None:
        candidates.append(SELECTED_SDL_VIDEO_DRIVER)
    for candidate in _sdl_driver_candidates():
        if candidate not in candidates:
            candidates.append(candidate)

    profiles = ("default", "compat-2.1")
    failures = []
    for candidate in candidates:
        for profile in profiles:
            label = _candidate_label(candidate)
            print(f"[INFO] Trying OpenGL display via {label}, GL profile {profile}...")
            ok, actual_driver, error = _try_set_mode_once(mode_size, flags, candidate, profile)
            if ok:
                SELECTED_SDL_VIDEO_DRIVER = actual_driver if _is_linux() else candidate
                SELECTED_SDL_VIDEO_DRIVER_ACTUAL = actual_driver
                DISPLAY_ATTEMPT_FAILURES = failures
                print(f"[INFO] OpenGL display created with SDL driver: {actual_driver or '<unknown>'}")
                return
            print(f"[WARN] Display attempt failed via {label}, GL profile {profile}: {error}")
            failures.append((label, profile, error))

    DISPLAY_ATTEMPT_FAILURES = failures
    _print_display_failure_help(failures)
    raise SystemExit(2)


def _current_window_size(preferred_size=None):
    """Best-effort current drawable/window size for pygame+OpenGL.

    On Windows, an OpenGL surface can keep reporting the old set_mode() size after
    the native window has been maximized. pygame.display.get_window_size() tracks
    the actual client area more reliably, and resize events are still accepted as
    an explicit override when SDL provides them.
    """
    if preferred_size is not None:
        try:
            w, h = preferred_size
            return (max(1, int(w)), max(1, int(h)))
        except Exception:
            pass

    try:
        w, h = pygame.display.get_window_size()
        if w and h:
            return (max(1, int(w)), max(1, int(h)))
    except Exception:
        pass

    surface = pygame.display.get_surface()
    if surface is not None:
        try:
            w, h = surface.get_size()
            if w and h:
                return (max(1, int(w)), max(1, int(h)))
        except Exception:
            pass

    return DISPLAY


def apply_gl_viewport_for_display(preferred_size=None, force=False):
    """Refresh viewport/projection after resize/fullscreen/maximize.

    The old version only trusted pygame.display.get_surface().get_size(), which
    can stay at the original 1000x760 on Windows even after the window shell is
    maximized. That leaves the OpenGL viewport in the lower-left/corner. This
    function uses the actual window client size and is also called once per frame
    so native maximize/restore events cannot get missed.
    """
    global DISPLAY, LAST_GL_VIEWPORT_SIZE

    DISPLAY = _current_window_size(preferred_size)

    if not force and LAST_GL_VIEWPORT_SIZE == DISPLAY:
        return
    LAST_GL_VIEWPORT_SIZE = DISPLAY

    glViewport(0, 0, DISPLAY[0], DISPLAY[1])
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(45.0, DISPLAY[0] / max(1, DISPLAY[1]), 0.1, 220.0)

    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    glEnable(GL_DEPTH_TEST)
    glDepthFunc(GL_LEQUAL)
    glClearColor(*BACKGROUND)


def set_game_display(size=None, fullscreen=None):
    """Create/recreate the OpenGL display surface.

    Windowed mode is resizable, so the OS maximize button works. Fullscreen uses
    the current desktop size, and can be toggled without changing gameplay state.
    """
    global DISPLAY, WINDOWED_DISPLAY, IS_FULLSCREEN, LAST_GL_VIEWPORT_SIZE

    if fullscreen is not None:
        IS_FULLSCREEN = bool(fullscreen)

    if size is not None and not IS_FULLSCREEN:
        w, h = size
        WINDOWED_DISPLAY = (max(640, int(w)), max(480, int(h)))

    flags = DOUBLEBUF | OPENGL
    if IS_FULLSCREEN:
        flags |= FULLSCREEN
        try:
            mode_size = pygame.display.get_desktop_sizes()[0]
        except Exception:
            info = pygame.display.Info()
            mode_size = (info.current_w or WINDOWED_DISPLAY[0], info.current_h or WINDOWED_DISPLAY[1])
    else:
        flags |= RESIZABLE
        mode_size = WINDOWED_DISPLAY

    _set_game_display_robust(mode_size, flags)
    LAST_GL_VIEWPORT_SIZE = None
    pygame.display.set_caption(f"Cube Libre (demo, v.{version_number})")
    apply_gl_viewport_for_display(mode_size, force=True)


def toggle_fullscreen():
    set_game_display(fullscreen=not IS_FULLSCREEN)
    return IS_FULLSCREEN


def init_pygame_and_gl():
    _report_display_environment_once()
    _linux_display_preflight_or_exit()

    try:
        pygame.mixer.pre_init(AUDIO_SAMPLE_RATE, -16, 2, 512)
    except Exception:
        pass

    pygame.init()
    pygame.font.init()

    set_game_display(DISPLAY, fullscreen=False)

    version = glGetString(GL_VERSION)
    if version:
        print("OpenGL version:", version.decode(errors="replace"))
    else:
        print("[WARN] Could not query OpenGL version.")

    # Audio is kicked off last and asynchronously, so slow first-run WAV synthesis
    # cannot hold the title window hostage.
    init_audio()


def control_module_for_point(p: Vec3, forward_sign: float = 0.0) -> CourseModule:
    # Kept only for old debug references. Gameplay controls are world-space in
    # handle_input(); no automatic turn steering.
    if not COURSE_MODULES:
        return CourseModule(0, COURSE_X_MIN, 0.0, 1.0, 0.0)
    return COURSE_MODULES[0]


def point_in_turn_laser_grace(p: Vec3, pad: float = 0.0) -> bool:
    # Only cells physically inside the joint cube are ignored by laser checks.
    # Do not suppress the whole player just because the origin is in/near the
    # joint; otherwise the first laser grids after the corner become harmless.
    return point_inside_turn_chamber(p, pad=0.0)


def player_in_turn_laser_grace(player: PlayerCube) -> bool:
    # Kept for call-site compatibility. Whole-player turn grace caused the
    # nearest post-corner laser grids to stop dealing damage.
    return False


def handle_input(player: PlayerCube, dt: float):
    # Fixed world-space controls. Do not rotate/hijack controls inside L bends:
    # the point of the maze is that the player reorients spatially.
    #
    # Axis map:
    #   A/D or Left/Right      -> world X
    #   W/S or Up/Down         -> world Y
    #   Q/E                    -> world Z
    #   Ctrl + A/D or arrows   -> old alternate world-Z control
    #
    # Important: Q/E and Ctrl+A/D are aliases, not stackable thrust. Earlier
    # versions added both together, so Ctrl+A+Q or Ctrl+D+E doubled Z speed.
    keys = pygame.key.get_pressed()
    speed = MOVE_SPEED * (FAST_MULT if (keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]) else 1.0)
    step = speed * dt

    dx = dy = dz = 0.0
    ctrl = keys[pygame.K_LCTRL] or keys[pygame.K_RCTRL]

    # Y axis.
    y_intent = 0
    if keys[pygame.K_w] or keys[pygame.K_UP]:
        y_intent += 1
    if keys[pygame.K_s] or keys[pygame.K_DOWN]:
        y_intent -= 1
    dy = clamp(y_intent, -1, 1) * step

    # Z axis: Q/E is the primary explicit Z control. Ctrl+A/D is kept only as
    # an old alternate input when Q/E is not already giving a Z intent.
    z_intent = 0
    if keys[pygame.K_q]:
        z_intent += 1
    if keys[pygame.K_e]:
        z_intent -= 1
    if ctrl and z_intent == 0:
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            z_intent += 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            z_intent -= 1
    dz = clamp(z_intent, -1, 1) * step

    # X axis. Ctrl turns A/D into the old alternate Z control, so no simultaneous
    # X movement is emitted from A/D while Ctrl is held.
    x_intent = 0
    if not ctrl:
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            x_intent -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            x_intent += 1
    dx = clamp(x_intent, -1, 1) * step

    player.origin = Vec3(player.origin.x + dx, player.origin.y + dy, player.origin.z + dz)

    # Soft AABB clamp only keeps the origin from vanishing into space. Actual
    # damage/collision is handled by point_inside_course(), i.e. the L-pipe union.
    # Let the player drift outside the cage far enough to be punished by
    # disintegration instead of turning the cage into a hard invisible wall.
    xmin, xmax, ymin, ymax, zmin, zmax = course_aabb(5.0)
    player.origin.x = clamp(player.origin.x, xmin, xmax)
    player.origin.y = clamp(player.origin.y, ymin, ymax)
    player.origin.z = clamp(player.origin.z, zmin, zmax)


def draw_scene(player: PlayerCube, t: float, scene_angles, draw_player_body: bool = True, recoupling_particles=None, recoupling_progress: float = 0.0, center_on_player: bool = False):
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    if center_on_player and player is not None:
        center = player.origin
        # Keep the old rotating-field madness, but frame the cube instead of the
        # entire ever-growing pipe. The zoom stays stable so level 5+ does not
        # turn into microscopic space plumbing.
        zoom = PLAYER_CENTER_ZOOM
    else:
        center, zoom = course_center_and_zoom()
    glTranslatef(0.0, 0.0, -zoom)
    apply_screen_shake()

    # Constant axis rotation of the entire playing field: this preserves the original
    # unstable, cubistic vibe instead of turning the game into a flat obstacle course.
    glRotatef(scene_angles[0], 1, 0, 0)
    glRotatef(scene_angles[1], 0, 1, 0)
    glRotatef(scene_angles[2], 0, 0, 1)
    glTranslatef(-center.x, -center.y, -center.z)

    draw_stars()
    draw_course_frame(player if draw_player_body else None, t)
    draw_portal(t, portal_overlap_charge(player) if draw_player_body else 0.0)
    if draw_player_body:
        update_laser_reveal_state(player, t)
    reveal_idx = reveal_module_index_for_player(player) if draw_player_body else len(COURSE_MODULES) - 1
    active_idx = 0
    if draw_player_body:
        active_idx, _active_lx = player_module_location(player)
    for laser in LASERS:
        module_idx = getattr(laser, "module_index", 0)
        if draw_player_body and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL:
            # Absolute hard cull: only keep the current module, one recent module
            # behind, and the next revealed/preview module. The older condition
            # kept TRAIL_KEEP_JOINTS+1 modules behind, which at level 5 could
            # still leave almost the entire maze iterating/drawing ember states.
            if module_idx < active_idx - 1:
                continue
            if module_idx > reveal_idx + 1:
                continue
        if draw_player_body and laser_should_be_hard_culled(laser, player, t):
            continue
        if draw_player_body and ACTIVE_LEVEL >= PREVIEW_CULL_START_LEVEL and module_idx > reveal_idx:
            # Future hazards are only grey ghost-markers, not active rotating red grids.
            # This is the level-3+ culling win.
            if module_idx <= reveal_idx + 1:
                draw_future_laser_marker(laser, t, PREVIEW_WIREFRAME_ALPHA * 0.55)
            continue
        la = laser_trail_alpha(laser, player, t) if draw_player_body else 1.0
        if la > 0.01:
            if draw_player_body:
                trail_fade = laser_trail_fade(laser, player, t)
                rp = laser_reveal_progress(module_idx, t)
                if trail_fade > 0.025:
                    # Passed grids cool into embers/ash instead of staying as full red
                    # laser squares until the whole section disappears.
                    draw_dissipating_laser_grid(laser, t, trail_fade, la)
                elif rp < 0.995:
                    draw_revealing_laser_grid(laser, t, rp, la)
                else:
                    laser.draw(t, la)
            else:
                laser.draw(t, la)
    if draw_player_body:
        draw_player(player, absorb_portal_cells=True, t=t)
        draw_recoupling_particles(player, recoupling_particles or [], t, recoupling_progress)

    # Draw impact glows/sparks last so the struck laser square/cage point visibly
    # flashes instead of being hidden behind the cube body. This is still local
    # structure feedback, not a full-screen flash.
    draw_impact_effects(t)

    render_flash_overlay()


def audio_builder_main(argv=None) -> int:
    """CLI entry point for the isolated procedural-audio cache builder."""
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    try:
        paths = generate_audio_assets(force=force)
        missing = [name for name, path in paths.items() if not os.path.exists(path)]
        if missing:
            print("[ERROR] Audio cache builder finished with missing assets: " + ", ".join(missing), file=sys.stderr)
            return 2
        print(f"[INFO] Audio cache builder finished: {_audio_dir()}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Audio cache builder failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


def main():
    global screen_shake_timer, flash_timer, message_timer

    init_pygame_and_gl()
    clock = pygame.time.Clock()
    player = PlayerCube()

    t = 0.0
    damage_timer = 0.0
    scene_angles = [0.0, 0.0, 0.0]
    caption_timer = 0.0

    stats = load_score_stats()
    score = 0
    best_escape = stats["best_escape"]
    highest_level = stats["highest_level"]
    current_level = 1
    completed_level = 0
    next_level_to_start = 1
    last_escape_count = 0
    win_overlay_timer = 0.0
    WIN_OVERLAY_SECONDS = TRANSCENDENCE_WHITE_SECONDS
    show_help_overlay = False
    paused = False
    pause_ui_t = 0.0
    reset_confirm_active = False
    reset_confirm_previous_paused = False
    locate_camera_enabled = AUTO_CENTER_ON_PLAYER
    timed_mode_intro_seen = False
    time_intro_timer = 0.0
    timed_leg_timer = TIME_PER_LEG_SECONDS
    timed_current_module = 0

    # Small explicit state machine. This prevents title, level ready, course materialization,
    # death/white-void reassembly, portal warp, input and scoring overlays from stomping on
    # each other.
    game_state = "title"  # title | quit_confirm | level_ready | time_intro | course_materialize | playing | death_dissolve | reassembly | reassembly_flash | portal_warp | result_overlay
    death_timer = 0.0
    level_ready_timer = 0.0
    course_materialize_timer = 0.0
    portal_warp_timer = 0.0
    reassembly_particles = []
    recoupling_particles = []
    recoupling_timer = 0.0
    recoupling_notice_timer = 0.0
    recoupling_request_times = []
    recoupling_requests_used_this_level = 0
    recoupling_cooldown_notice_timer = 0.0
    recoupling_cooldown_remaining = 0.0
    recoupling_cooldown_used = 0
    recoupling_cooldown_limit = recoupling_request_limit_for_level(current_level)

    setup_level_geometry(current_level)

    def reset_recoupling_quota():
        nonlocal recoupling_request_times, recoupling_requests_used_this_level
        nonlocal recoupling_cooldown_notice_timer, recoupling_cooldown_remaining
        nonlocal recoupling_cooldown_used, recoupling_cooldown_limit
        recoupling_request_times = []
        recoupling_requests_used_this_level = 0
        recoupling_cooldown_notice_timer = 0.0
        recoupling_cooldown_remaining = 0.0
        recoupling_cooldown_used = 0
        recoupling_cooldown_limit = recoupling_request_limit_for_level(current_level)

    def recoupling_quota_status(now: float):
        nonlocal recoupling_request_times
        limit = recoupling_request_limit_for_level(current_level)
        if RECOUPLING_REQUEST_WINDOW_SECONDS > 0.0:
            cutoff = now - RECOUPLING_REQUEST_WINDOW_SECONDS
            recoupling_request_times = [stamp for stamp in recoupling_request_times if stamp >= cutoff]
            used = len(recoupling_request_times)
            if used >= limit:
                remaining = max(0.0, recoupling_request_times[0] + RECOUPLING_REQUEST_WINDOW_SECONDS - now)
                return False, used, limit, remaining
            return True, used, limit, 0.0
        used = recoupling_requests_used_this_level
        if used >= limit:
            return False, used, limit, 0.0
        return True, used, limit, 0.0

    def consume_recoupling_quota(now: float):
        nonlocal recoupling_request_times, recoupling_requests_used_this_level
        if RECOUPLING_REQUEST_WINDOW_SECONDS > 0.0:
            recoupling_request_times.append(now)
        else:
            recoupling_requests_used_this_level += 1

    def show_recoupling_cooldown_notice(remaining: float, used: int, limit: int):
        nonlocal recoupling_cooldown_notice_timer, recoupling_cooldown_remaining
        nonlocal recoupling_cooldown_used, recoupling_cooldown_limit
        recoupling_cooldown_notice_timer = RECOUPLING_COOLDOWN_NOTICE_SECONDS
        recoupling_cooldown_remaining = remaining
        recoupling_cooldown_used = used
        recoupling_cooldown_limit = limit

    def recoverable_loose_fragment_count() -> int:
        return sum(1 for f in player.fragments if f.alive and f.expiry_remaining > 0.05)

    def request_recoupling_or_cooldown() -> bool:
        """Consume re-coupling request quota when the C press actually matters.

        Returns True when the request may proceed. Returns False after showing
        the cooldown warning. The important bit: active re-coupling spam can
        burn quota, so C-spamming finally has teeth.
        """
        allowed, used, limit, remaining = recoupling_quota_status(t)
        if not allowed:
            show_recoupling_cooldown_notice(remaining, used, limit)
            set_message("RE-COUPLING ON COOLDOWN", 0.75)
            return False
        consume_recoupling_quota(t)
        return True

    def begin_level_ready(level: int, label: str = None):
        nonlocal game_state, level_ready_timer, current_level, highest_level, damage_timer, reassembly_particles, recoupling_particles, recoupling_timer, recoupling_notice_timer, paused
        nonlocal timed_leg_timer, timed_current_module
        paused = False
        audio_resume_all()
        current_level = max(1, int(level))
        setup_level_geometry(current_level)
        timed_leg_timer = TIME_PER_LEG_SECONDS
        timed_current_module = 0
        reset_recoupling_quota()
        highest_level = max(highest_level, current_level)
        save_score_stats(best_escape, highest_level)
        player.reset()
        player.fragments.clear()
        clear_level_runtime_effects(clear_impact_particles=True)
        reassembly_particles = []
        recoupling_particles = []
        recoupling_timer = 0.0
        recoupling_notice_timer = 0.0
        game_state = "level_ready"
        level_ready_timer = 0.0
        damage_timer = 999.0
        audio_stop_loop("critical", fade_ms=80)
        set_message(label or f"LEVEL {current_level} GET READY", LEVEL_READY_SECONDS)

    def begin_course_materialize(label: str = None):
        nonlocal game_state, death_timer, course_materialize_timer, portal_warp_timer, win_overlay_timer, damage_timer, reassembly_particles, recoupling_particles, recoupling_timer, recoupling_notice_timer
        player.reset()
        clear_level_runtime_effects(clear_impact_particles=True)
        reassembly_particles = []
        recoupling_particles = []
        recoupling_timer = 0.0
        recoupling_notice_timer = 0.0
        game_state = "course_materialize"
        course_materialize_timer = 0.0
        death_timer = 0.0
        portal_warp_timer = 0.0
        win_overlay_timer = 0.0
        # Disable gameplay damage during the construction intro. It gets reset
        # when course_materialize hands off to playing.
        damage_timer = 999.0
        audio_stop_loop("critical", fade_ms=80)
        audio_play("materialize", volume=0.62, channel_name="ui", cooldown=0.20)
        set_message(label or f"LEVEL {current_level}", COURSE_MATERIALIZE_SECONDS)

    def start_new_run(label="NEW RUN"):
        nonlocal score, completed_level, next_level_to_start, last_escape_count, win_overlay_timer, portal_warp_timer, death_timer, course_materialize_timer
        nonlocal timed_mode_intro_seen, time_intro_timer, timed_leg_timer, timed_current_module
        score = 0
        completed_level = 0
        timed_mode_intro_seen = False
        time_intro_timer = 0.0
        timed_leg_timer = TIME_PER_LEG_SECONDS
        timed_current_module = 0
        next_level_to_start = 1
        last_escape_count = 0
        win_overlay_timer = 0.0
        portal_warp_timer = 0.0
        death_timer = 0.0
        course_materialize_timer = 0.0
        begin_level_ready(1, f"{label}: LEVEL 1")

    def restart_current_level(label="RESET CURRENT LEVEL"):
        """Restart the current level attempt without wiping run score/progress.

        Full run reset is handled by start_new_run(). This path is for the
        confirmation screen option that keeps the player on the same level,
        which matters once a run has reached deeper levels.
        """
        nonlocal next_level_to_start, last_escape_count, win_overlay_timer, portal_warp_timer, death_timer, course_materialize_timer
        nonlocal time_intro_timer, timed_leg_timer, timed_current_module
        target = max(1, int(current_level))
        next_level_to_start = max(next_level_to_start, target)
        last_escape_count = 0
        win_overlay_timer = 0.0
        portal_warp_timer = 0.0
        death_timer = 0.0
        course_materialize_timer = 0.0
        time_intro_timer = 0.0
        timed_leg_timer = TIME_PER_LEG_SECONDS
        timed_current_module = 0
        begin_level_ready(target, f"{label}: LEVEL {target}")

    def open_reset_confirm():
        nonlocal reset_confirm_active, reset_confirm_previous_paused, paused, show_help_overlay
        if reset_confirm_active:
            return
        reset_confirm_active = True
        reset_confirm_previous_paused = paused
        paused = True
        show_help_overlay = False
        audio_pause_all()
        set_message("RESET? 1 LEVEL ONE / 2 CURRENT / ESC CANCEL", 1.2)

    def cancel_reset_confirm():
        nonlocal reset_confirm_active, paused
        reset_confirm_active = False
        paused = reset_confirm_previous_paused
        if not paused:
            audio_resume_all()
        set_message("RESET CANCELLED", 0.8)

    def confirm_reset_to_level_one():
        nonlocal reset_confirm_active, paused
        reset_confirm_active = False
        paused = False
        audio_resume_all()
        start_new_run("RESET RUN")

    def confirm_reset_current_level():
        nonlocal reset_confirm_active, paused
        reset_confirm_active = False
        paused = False
        audio_resume_all()
        restart_current_level("RESET CURRENT LEVEL")

    def begin_time_intro(target_level: int):
        nonlocal game_state, time_intro_timer, current_level, next_level_to_start, damage_timer, paused
        paused = False
        audio_resume_all()
        current_level = max(TIME_MODE_START_LEVEL, int(target_level))
        next_level_to_start = current_level
        time_intro_timer = 0.0
        damage_timer = 999.0
        game_state = "time_intro"
        set_message("TIME ...", TIME_INTRO_SECONDS)

    def advance_after_transcendence():
        nonlocal next_level_to_start, timed_mode_intro_seen
        # Belt-and-suspenders against the recurring "level 1 twice" bug: if a
        # result overlay exists, never allow the next level to be less than the
        # just-completed level + 1, regardless of stale UI/event state.
        target = max(2 if completed_level <= 1 else completed_level + 1, next_level_to_start, current_level + 1)
        next_level_to_start = target
        if target >= TIME_MODE_START_LEVEL and completed_level == TIME_MODE_START_LEVEL - 1 and not timed_mode_intro_seen:
            timed_mode_intro_seen = True
            begin_time_intro(target)
        else:
            begin_level_ready(target, f"LEVEL {target} GET READY")

    def return_to_title():
        nonlocal game_state, death_timer, portal_warp_timer, win_overlay_timer, damage_timer, reassembly_particles, level_ready_timer, course_materialize_timer, recoupling_particles, recoupling_timer, recoupling_notice_timer, paused
        nonlocal reset_confirm_active, reset_confirm_previous_paused
        reset_confirm_active = False
        reset_confirm_previous_paused = False
        paused = False
        audio_resume_all()
        reassembly_particles = []
        recoupling_particles = []
        recoupling_timer = 0.0
        recoupling_notice_timer = 0.0
        reset_recoupling_quota()
        game_state = "title"
        death_timer = 0.0
        portal_warp_timer = 0.0
        level_ready_timer = 0.0
        course_materialize_timer = 0.0
        win_overlay_timer = 0.0
        damage_timer = 0.5
        audio_stop_all(fade_ms=220)
        set_message("TITLE SCREEN", 0.8)

    running = True
    while running:
        raw_dt = clock.tick(FPS_LIMIT) / 1000.0
        raw_dt = min(raw_dt, 0.05)  # avoid giant physics step after dragging the window
        dt = raw_dt
        if not paused:
            t += dt
        else:
            dt = 0.0
            pause_ui_t += raw_dt

        # Native Windows maximize/restore can resize the OpenGL client area without
        # a useful VIDEORESIZE event. Keep viewport/projection synchronized anyway.
        apply_gl_viewport_for_display()
        # Poll the background procedural-audio worker before accepting title input,
        # so first-run setup can unlock the start prompt as soon as assets load.
        audio_try_finish_init()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in (
                pygame.VIDEORESIZE,
                getattr(pygame, "WINDOWRESIZED", -999),
                getattr(pygame, "WINDOWSIZECHANGED", -998),
            ):
                # Keep the GL viewport in lock-step with resize/maximize events.
                size = (getattr(event, "w", DISPLAY[0]), getattr(event, "h", DISPLAY[1]))
                if not IS_FULLSCREEN and event.type == pygame.VIDEORESIZE:
                    set_game_display(size, fullscreen=False)
                else:
                    apply_gl_viewport_for_display(size, force=True)
            elif event.type == pygame.KEYDOWN:
                mods = pygame.key.get_mods()
                alt_down = bool(mods & pygame.KMOD_ALT)
                if (event.key == pygame.K_f and alt_down) or event.key == pygame.K_F11 or (event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and alt_down):
                    fs = toggle_fullscreen()
                    set_message("FULLSCREEN" if fs else "WINDOWED", 0.9)
                    continue

                if reset_confirm_active:
                    if event.key in (pygame.K_1, pygame.K_KP1):
                        confirm_reset_to_level_one()
                    elif event.key in (pygame.K_2, pygame.K_KP2):
                        confirm_reset_current_level()
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        cancel_reset_confirm()
                    else:
                        set_message("RESET? 1 LEVEL ONE / 2 CURRENT / ESC CANCEL", 0.95)
                    continue

                if event.key == pygame.K_l:
                    locate_camera_enabled = not locate_camera_enabled
                    set_message("LOCATE CAMERA ON" if locate_camera_enabled else "LOCATE CAMERA OFF", 0.85)
                    continue

                if event.key == pygame.K_p:
                    if game_state not in ("title", "quit_confirm", "result_overlay"):
                        paused = not paused
                        if paused:
                            audio_pause_all()
                            set_message("GAME PAUSED", 0.85)
                        else:
                            audio_resume_all()
                            set_message("GAME RESUMED", 0.65)
                    continue

                if event.key == pygame.K_h:
                    show_help_overlay = not show_help_overlay
                    set_message("HELP ON" if show_help_overlay else "HELP OFF", 0.7)
                    continue

                if show_help_overlay and event.key == pygame.K_ESCAPE:
                    show_help_overlay = False
                    set_message("HELP OFF", 0.7)
                    continue

                if event.key == pygame.K_m:
                    muted = audio_toggle_mute()
                    set_message("AUDIO MUTED" if muted else "AUDIO ON", 0.8)
                    continue


                if game_state == "quit_confirm":
                    if event.key == pygame.K_y:
                        running = False
                    elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                        game_state = "title"
                        set_message("QUIT CANCELLED", 0.8)
                    elif event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
                        if audio_start_blocked():
                            set_message("AUDIO ASSETS GENERATING", 0.95)
                        else:
                            start_new_run("NEW RUN")

                elif event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
                    # No mid-level SPACE restart. On the result overlay, advance
                    # the cleared run to the next level instead of accidentally
                    # starting level 1 again.
                    if game_state == "title":
                        if audio_start_blocked():
                            set_message("AUDIO ASSETS GENERATING", 0.95)
                        else:
                            start_new_run("NEW RUN")
                    elif game_state == "result_overlay":
                        advance_after_transcendence()
                    else:
                        set_message("RUN IN PROGRESS", 0.55)

                elif event.key == pygame.K_ESCAPE:
                    if game_state == "title":
                        game_state = "quit_confirm"
                        set_message("QUIT? Y/N", 1.2)
                    else:
                        paused = False
                        audio_resume_all()
                        return_to_title()

                elif event.key == pygame.K_c:
                    if game_state == "playing" and not show_help_overlay:
                        # A C press only avoids quota cost when it is genuinely
                        # meaningless: no loose recoverable cells and no active
                        # re-coupling animation. Spam during an active reclaim does
                        # count, because otherwise hammering C is consequence-free.
                        if recoupling_particles:
                            if RECOUPLING_ACTIVE_SPAM_CONSUMES_QUOTA and not request_recoupling_or_cooldown():
                                continue
                            elif RECOUPLING_ACTIVE_SPAM_CONSUMES_QUOTA:
                                recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS
                                set_message("RE-COUPLING ALREADY ACTIVE", 0.55)
                            else:
                                recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS
                                set_message("RE-COUPLING ALREADY ACTIVE", 0.55)
                        else:
                            loose_count = recoverable_loose_fragment_count()
                            if loose_count <= 0:
                                recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS * 0.65
                                set_message("NO RECOVERABLE LOOSE CELLS", 0.75)
                            elif request_recoupling_or_cooldown():
                                recoupling_particles = begin_recoupling(player)
                                if recoupling_particles:
                                    recoupling_timer = 0.0
                                    recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS
                                    audio_play("recouple", volume=0.62, channel_name="ui", cooldown=0.15)
                                    set_message(f"RE-COUPLING REQUESTED: {len(recoupling_particles)} cells", RECOUPLING_NOTICE_SECONDS)
                                else:
                                    # Rare race/fallback: fragments expired between
                                    # counting and assigning. The request already
                                    # hit the quota because the player pressed C
                                    # during a meaningful recovery window.
                                    recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS * 0.65
                                    set_message("NO RECOVERABLE LOOSE CELLS", 0.75)
                    else:
                        set_message("RE-COUPLING UNAVAILABLE", 0.55)

                elif event.key == pygame.K_r:
                    # Keep R as an explicit reset/debug key, but never let an
                    # accidental R tap during active play throw a deep run back
                    # to level 1. During a run, plain R only reminds the player;
                    # Ctrl+R opens a confirmation with full-run/current-level/cancel.
                    active_run_states = (
                        "level_ready", "time_intro", "course_materialize", "playing",
                        "death_dissolve", "reassembly", "reassembly_flash", "portal_warp",
                    )
                    ctrl_down = bool(mods & pygame.KMOD_CTRL)
                    if game_state in active_run_states:
                        if RUN_RESET_REQUIRES_CTRL_DURING_PLAY and not ctrl_down:
                            set_message("CTRL+R = RESET OPTIONS", 0.85)
                        else:
                            open_reset_confirm()
                    else:
                        start_new_run("RESET RUN")

        # Timers/effects always update.
        player.update_fragments(dt)
        update_impact_effects(dt)
        scene_angles[0] = (scene_angles[0] + SCENE_ROT_SPEED_X * dt) % 360.0
        scene_angles[1] = (scene_angles[1] + SCENE_ROT_SPEED_Y * dt) % 360.0
        scene_angles[2] = (scene_angles[2] + SCENE_ROT_SPEED_Z * dt) % 360.0

        if screen_shake_timer > 0.0:
            screen_shake_timer = max(0.0, screen_shake_timer - dt)
        if flash_timer > 0.0:
            flash_timer = max(0.0, flash_timer - dt)
        if message_timer > 0.0:
            message_timer = max(0.0, message_timer - dt)
        if recoupling_notice_timer > 0.0:
            recoupling_notice_timer = max(0.0, recoupling_notice_timer - dt)
        if recoupling_cooldown_notice_timer > 0.0:
            recoupling_cooldown_notice_timer = max(0.0, recoupling_cooldown_notice_timer - dt)
            if recoupling_cooldown_remaining > 0.0 and RECOUPLING_REQUEST_WINDOW_SECONDS > 0.0:
                recoupling_cooldown_remaining = max(0.0, recoupling_cooldown_remaining - dt)

        # Re-coupling animation is a gameplay effect, but it should pause with
        # the help overlay just like movement/damage.
        if game_state == "playing" and recoupling_particles and not show_help_overlay:
            recoupling_timer += dt
            if recoupling_timer >= RECOUPLING_SECONDS:
                restored = finish_recoupling(player, recoupling_particles)
                recoupling_particles = []
                recoupling_timer = 0.0
                recoupling_notice_timer = RECOUPLING_NOTICE_SECONDS * 0.72
                if restored:
                    screen_shake_timer = max(screen_shake_timer, 0.08)
                    set_message(f"RE-COUPLED +{restored} CUBES", 0.85)
                else:
                    set_message("RE-COUPLING FAILED", 0.65)

        # State-specific update.
        if game_state in ("title", "quit_confirm"):
            pass

        elif game_state == "level_ready":
            level_ready_timer += dt
            if level_ready_timer >= LEVEL_READY_SECONDS:
                level_ready_timer = 0.0
                begin_course_materialize(f"LEVEL {current_level}")

        elif game_state == "time_intro":
            time_intro_timer += dt
            if time_intro_timer >= TIME_INTRO_SECONDS:
                begin_level_ready(next_level_to_start, f"LEVEL {next_level_to_start} GET READY")

        elif game_state == "course_materialize":
            course_materialize_timer += dt
            if course_materialize_timer >= COURSE_MATERIALIZE_SECONDS:
                course_materialize_timer = 0.0
                game_state = "playing"
                damage_timer = 0.45
                set_message(f"LEVEL {current_level}", 0.9)

        elif game_state == "playing":
            if show_help_overlay:
                # Let the scene breathe visually, but do not punish the player while
                # the help overlay is up. No movement, no laser shaving.
                pass
            else:
                handle_input(player, dt)
                update_collapse_triggers(player, t)

                if current_level >= TIME_MODE_START_LEVEL:
                    active_idx, _active_lx = player_module_location(player)
                    if active_idx > timed_current_module:
                        timed_current_module = active_idx
                        timed_leg_timer = TIME_PER_LEG_SECONDS
                        set_message(f"TIME RESET: LEG {active_idx + 1}", 0.7)
                    else:
                        timed_leg_timer = max(0.0, timed_leg_timer - dt)
                    if timed_leg_timer <= 0.0:
                        set_message("TIME COLLAPSE", 1.0)
                        spawn_collapse_debris(player.origin, Vec3(0.0, 0.0, -1.0), severity=3)
                        audio_play("collapse", volume=0.92, channel_name="one_shot", cooldown=0.05)
                        audio_play("laser_dissipate", volume=0.70, channel_name="hazard_fade", cooldown=0.05)
                        player.alive_cells.clear()
                    elif player_inside_collapsing_section(player, t):
                        set_message("SEALED IN COLLAPSE", 1.0)
                        spawn_collapse_debris(player.origin, Vec3(0.0, 0.0, -1.0), severity=2)
                        audio_play("collapse", volume=0.88, channel_name="one_shot", cooldown=0.05)
                        player.alive_cells.clear()

                damage_timer -= dt
                if damage_timer <= 0.0 and player.intact_count() > 0:
                    hit_count = damage_from_lasers(player, t)
                    if hit_count == 0:
                        hit_count = damage_from_bounds(player)
                    if hit_count:
                        damage_timer = DAMAGE_COOLDOWN

            if player.intact_count() <= 0:
                recoupling_particles = []
                recoupling_timer = 0.0
                recoupling_notice_timer = 0.0
                game_state = "death_dissolve"
                death_timer = 0.0
                damage_timer = 999.0
                reassembly_particles = make_reassembly_particles(player)
                screen_shake_timer = max(screen_shake_timer, SCREEN_SHAKE_DURATION * 1.7)
                audio_stop_loop("field", fade_ms=180)
                audio_stop_loop("critical", fade_ms=100)
                audio_play("death", volume=0.74, channel_name="one_shot", cooldown=0.15)
                set_message("CUBICALLY DECOMMISSIONED", 1.1)

            elif portal_reached(player):
                recoupling_particles = []
                recoupling_timer = 0.0
                recoupling_notice_timer = 0.0
                completed_level = current_level
                next_level_to_start = max(next_level_to_start, completed_level + 1)
                last_escape_count = player.intact_count()
                best_escape = max(best_escape, last_escape_count)
                highest_level = max(highest_level, current_level)
                save_score_stats(best_escape, highest_level)
                score += last_escape_count * 100
                game_state = "portal_warp"
                portal_warp_timer = 0.0
                damage_timer = 999.0
                audio_stop_loop("field", fade_ms=700)
                audio_stop_loop("critical", fade_ms=120)
                audio_play("portal", volume=0.88, channel_name="portal", cooldown=0.20)
                set_message(f"TRANSCENDENCE: LEVEL {completed_level}", PORTAL_WARP_SECONDS)

        elif game_state == "death_dissolve":
            death_timer += dt
            if death_timer >= DEATH_DISSOLVE_SECONDS:
                player.alive_cells.clear()
                player.fragments.clear()
                death_timer = 0.0
                game_state = "reassembly"
                audio_play("reassembly", volume=0.60, channel_name="reassembly", cooldown=0.20)
                set_message("REASSEMBLY IN PROGRESS", REASSEMBLY_SECONDS)

        elif game_state == "reassembly":
            death_timer += dt
            if death_timer >= REASSEMBLY_SECONDS:
                # Death is an attempt reset, not a permanent maze-collapse state.
                # Rebuild the current level geometry and clear collapse keys so
                # any trail sections eaten before death are restored.
                setup_level_geometry(current_level)
                clear_level_runtime_effects(clear_impact_particles=True)
                reset_recoupling_quota()
                # Timed mode is per attempt/leg. If death happens from timer collapse
                # or inside a timed section, restart the level attempt with a fresh
                # first-leg clock instead of re-entering play with the old zero/near-zero
                # timer and immediately dying again.
                if current_level >= TIME_MODE_START_LEVEL:
                    timed_leg_timer = TIME_PER_LEG_SECONDS
                    timed_current_module = 0
                player.reset()
                death_timer = 0.0
                game_state = "reassembly_flash"
                damage_timer = 0.7
                set_message("REASSEMBLED", 0.8)

        elif game_state == "reassembly_flash":
            death_timer += dt
            if death_timer >= REASSEMBLY_FLASH_SECONDS:
                death_timer = 0.0
                reassembly_particles = []
                game_state = "playing"
                damage_timer = 0.4

        elif game_state == "portal_warp":
            portal_warp_timer += dt
            if portal_warp_timer >= PORTAL_WARP_SECONDS:
                win_overlay_timer = WIN_OVERLAY_SECONDS
                game_state = "result_overlay"
                portal_warp_timer = 0.0
                damage_timer = 999.0
                set_message(f"TRANSCENDENCE: LEVEL {completed_level}", WIN_OVERLAY_SECONDS)

        elif game_state == "result_overlay":
            win_overlay_timer = max(0.0, win_overlay_timer - dt)
            if win_overlay_timer <= 0.0:
                advance_after_transcendence()

        if not paused:
            audio_update(game_state, player, current_level, timed_leg_timer if current_level >= TIME_MODE_START_LEVEL else None)

        # Draw world first, then overlays/HUD. Title/quit confirm are not-playing scenes.
        if game_state in ("title", "quit_confirm"):
            draw_title_screen(t, score, best_escape, highest_level)
            render_audio_setup_overlay(t)
            if game_state == "quit_confirm":
                render_quit_confirm(t)
        elif game_state == "level_ready":
            render_level_ready(t, current_level)
            ready_fade = 1.0 - smoothstep(level_ready_timer / max(0.001, LEVEL_READY_FADE_IN_SECONDS))
            render_fullscreen_overlay((1.0, 1.0, 1.0), ready_fade)
        elif game_state == "time_intro":
            render_time_intro(t, time_intro_timer)
        else:
            if game_state == "course_materialize":
                progress = clamp(course_materialize_timer / COURSE_MATERIALIZE_SECONDS, 0.0, 1.0)
                draw_course_materialization_scene(player, t, scene_angles, progress)
            elif game_state == "reassembly":
                progress = clamp(death_timer / REASSEMBLY_SECONDS, 0.0, 1.0)
                draw_white_void_reassembly_scene(t, reassembly_particles, progress)
                render_reassembly_overlay(t, progress)
            else:
                if game_state == "result_overlay" and win_overlay_timer > 0.0:
                    render_win_overlay(last_escape_count, score, best_escape, win_overlay_timer, completed_level, highest_level)
                else:
                    hide_player = game_state == "death_dissolve"
                    draw_scene(
                        player,
                        t,
                        scene_angles,
                        draw_player_body=not hide_player,
                        recoupling_particles=recoupling_particles,
                        recoupling_progress=clamp(recoupling_timer / max(0.001, RECOUPLING_SECONDS), 0.0, 1.0),
                        center_on_player=locate_camera_enabled,
                    )

                    if game_state == "death_dissolve":
                        progress = clamp(death_timer / DEATH_DISSOLVE_SECONDS, 0.0, 1.0)
                        draw_reassembly_particles(reassembly_particles, t, "dissolve", progress)
                        render_fullscreen_overlay((1.0, 1.0, 1.0), smoothstep(progress))
                    elif game_state == "reassembly_flash":
                        progress = clamp(death_timer / REASSEMBLY_FLASH_SECONDS, 0.0, 1.0)
                        render_reassembly_flash(t, progress)
                    elif game_state == "portal_warp":
                        progress = clamp(portal_warp_timer / PORTAL_WARP_SECONDS, 0.0, 1.0)
                        render_portal_warp(t, progress, last_escape_count)

                    if game_state not in ("death_dissolve", "reassembly_flash", "portal_warp"):
                        render_hud(player, t, score, best_escape, game_state, current_level, highest_level)
                        if game_state == "playing" and current_level >= TIME_MODE_START_LEVEL:
                            render_time_counter(t, timed_leg_timer)
                        if player_has_out_of_bounds_cells(player):
                            render_danger_out_of_bounds(t)
                        render_recoupling_notice(
                            t,
                            clamp(recoupling_timer / max(0.001, RECOUPLING_SECONDS), 0.0, 1.0) if recoupling_particles else 0.0,
                            len(recoupling_particles),
                            recoupling_notice_timer,
                        )
                        if recoupling_cooldown_notice_timer > 0.0:
                            render_recoupling_cooldown_notice(
                                t,
                                recoupling_cooldown_notice_timer,
                                recoupling_cooldown_remaining,
                                recoupling_cooldown_used,
                                recoupling_cooldown_limit,
                            )
                        else:
                            render_recoupling_recovery_prompt(player, t, recoupling_active=bool(recoupling_particles))

        if paused:
            render_pause_overlay(pause_ui_t, locate_camera_enabled)

        if reset_confirm_active:
            render_reset_confirm(pause_ui_t, current_level, score)

        if show_help_overlay:
            render_help_overlay(t if not paused else pause_ui_t, game_state, current_level)

        pygame.display.flip()

        caption_timer -= dt
        if caption_timer <= 0.0:
            msg = f" | {message_text}" if message_timer > 0.0 else ""
            if audio_start_blocked():
                ready, total = audio_asset_cache_progress()
                msg = f" | audio assets {ready}/{total} generating"
            pygame.display.set_caption(
                f"Cube Libre v.{version_number} | state: {game_state} | level: {current_level} | intact: {player.intact_count():3d}/{MAX_CELLS} | "
                f"score: {score} | best: {best_escape}/{MAX_CELLS} | highest level: {highest_level} | "
                f"Space/Enter title/next level | P pause | L locate {'ON' if locate_camera_enabled else 'OFF'} | Esc title/quit confirm | H help | Alt+F/F11 fullscreen | M mute | A/D X, W/S Y, Q/E Z, Shift rush, C re-couple limited, Ctrl+R reset options{msg}"
            )
            caption_timer = 0.12

    save_score_stats(best_escape, highest_level)
    pygame.quit()


if __name__ == "__main__":
    if AUDIO_BUILDER_ARG in sys.argv:
        raise SystemExit(audio_builder_main(sys.argv[1:]))
    main()
