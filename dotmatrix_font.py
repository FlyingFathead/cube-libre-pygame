"""
Cube Libre dot-matrix font renderer.

This module is intentionally usable from the game without launching the editor.
It stores glyphs as simple 0/1 grids and draws them procedurally in pygame.

Basic game usage:

    from dotmatrix_font import DotMatrixFont

    dm_font = DotMatrixFont.from_builtin()
    dm_font.draw(screen, "CUBE LIBRE", (80, 80), dot_size=8, gap=3)

JSON font usage:

    dm_font = DotMatrixFont.load("cube_libre_5x7.json")
    dm_font.draw(screen, "REASSEMBLY IN PROGRESS", (40, 420), dot_size=5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
import json
import math
import random

Glyph = List[str]
Color = Tuple[int, int, int] | Tuple[int, int, int, int]
DotCallback = Callable[[str, int, int, int, int, Color], Optional[Color]]

PRINTABLE_ASCII = "".join(chr(i) for i in range(32, 127))
# Common non-ASCII glyphs needed for Finnish/Swedish/German UI text.
# Keep this explicit rather than pretending the tiny 5x7 font is universal Unicode.
NORDIC_LATIN = "ÅÄÖÜåäöü"
DEFAULT_CHARSET = PRINTABLE_ASCII + NORDIC_LATIN


def blank_glyph(cols: int = 5, rows: int = 7) -> Glyph:
    return ["0" * cols for _ in range(rows)]


def _normalize_row(row: str, cols: int) -> str:
    clean = "".join("1" if c in ("1", "#", "X", "x", "@", "*") else "0" for c in str(row))
    if len(clean) < cols:
        clean += "0" * (cols - len(clean))
    return clean[:cols]


def normalize_glyph(glyph: Sequence[str] | None, cols: int = 5, rows: int = 7) -> Glyph:
    if glyph is None:
        return blank_glyph(cols, rows)
    out = [_normalize_row(row, cols) for row in list(glyph)[:rows]]
    while len(out) < rows:
        out.append("0" * cols)
    return out


def glyph_from_pixels(pixels: Iterable[Iterable[int | bool]], cols: int = 5, rows: int = 7) -> Glyph:
    out: Glyph = []
    for y, row in enumerate(pixels):
        if y >= rows:
            break
        bits = "".join("1" if bool(v) else "0" for v in list(row)[:cols])
        if len(bits) < cols:
            bits += "0" * (cols - len(bits))
        out.append(bits)
    while len(out) < rows:
        out.append("0" * cols)
    return out


# Hand-made 5x7 starter glyphs. The editor can save over these to JSON.
_RAW_5X7: Dict[str, Glyph] = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
    '"': ["01010", "01010", "01010", "00000", "00000", "00000", "00000"],
    "#": ["01010", "01010", "11111", "01010", "11111", "01010", "01010"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "%": ["11001", "11010", "00100", "01000", "10011", "00111", "00000"],
    "&": ["01100", "10010", "10100", "01000", "10101", "10010", "01101"],
    "'": ["00100", "00100", "01000", "00000", "00000", "00000", "00000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "*": ["00000", "10101", "01110", "11111", "01110", "10101", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "/": ["00001", "00010", "00100", "01000", "10000", "00000", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    ";": ["00000", "01100", "01100", "00000", "01100", "00100", "01000"],
    "<": ["00010", "00100", "01000", "10000", "01000", "00100", "00010"],
    "=": ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
    ">": ["01000", "00100", "00010", "00001", "00010", "00100", "01000"],
    "?": ["01110", "10001", "00001", "00010", "00100", "00000", "00100"],
    "@": ["01110", "10001", "10111", "10101", "10111", "10000", "01110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00001", "00001", "00001", "00001", "10001", "10001", "01110"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "[": ["01110", "01000", "01000", "01000", "01000", "01000", "01110"],
    "\\": ["10000", "01000", "00100", "00010", "00001", "00000", "00000"],
    "]": ["01110", "00010", "00010", "00010", "00010", "00010", "01110"],
    "^": ["00100", "01010", "10001", "00000", "00000", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    "`": ["01000", "00100", "00010", "00000", "00000", "00000", "00000"],
    "a": ["00000", "00000", "01110", "00001", "01111", "10001", "01111"],
    "b": ["10000", "10000", "10110", "11001", "10001", "10001", "11110"],
    "c": ["00000", "00000", "01111", "10000", "10000", "10000", "01111"],
    "d": ["00001", "00001", "01101", "10011", "10001", "10001", "01111"],
    "e": ["00000", "00000", "01110", "10001", "11111", "10000", "01110"],
    "f": ["00110", "01001", "01000", "11100", "01000", "01000", "01000"],
    "g": ["00000", "01111", "10001", "10001", "01111", "00001", "01110"],
    "h": ["10000", "10000", "10110", "11001", "10001", "10001", "10001"],
    "i": ["00100", "00000", "01100", "00100", "00100", "00100", "01110"],
    "j": ["00010", "00000", "00110", "00010", "00010", "10010", "01100"],
    "k": ["10000", "10000", "10010", "10100", "11000", "10100", "10010"],
    "l": ["01100", "00100", "00100", "00100", "00100", "00100", "01110"],
    "m": ["00000", "00000", "11010", "10101", "10101", "10101", "10101"],
    "n": ["00000", "00000", "10110", "11001", "10001", "10001", "10001"],
    "o": ["00000", "00000", "01110", "10001", "10001", "10001", "01110"],
    "p": ["00000", "00000", "11110", "10001", "11110", "10000", "10000"],
    "q": ["00000", "00000", "01111", "10001", "01111", "00001", "00001"],
    "r": ["00000", "00000", "10110", "11001", "10000", "10000", "10000"],
    "s": ["00000", "00000", "01111", "10000", "01110", "00001", "11110"],
    "t": ["01000", "01000", "11100", "01000", "01000", "01001", "00110"],
    "u": ["00000", "00000", "10001", "10001", "10001", "10011", "01101"],
    "v": ["00000", "00000", "10001", "10001", "10001", "01010", "00100"],
    "w": ["00000", "00000", "10001", "10001", "10101", "10101", "01010"],
    "x": ["00000", "00000", "10001", "01010", "00100", "01010", "10001"],
    "y": ["00000", "00000", "10001", "10001", "01111", "00001", "01110"],
    "z": ["00000", "00000", "11111", "00010", "00100", "01000", "11111"],
    "{": ["00010", "00100", "00100", "01000", "00100", "00100", "00010"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    "}": ["01000", "00100", "00100", "00010", "00100", "00100", "01000"],
    "~": ["00000", "00000", "01000", "10101", "00010", "00000", "00000"],

    # Nordic / German extras for Finnish text. These are compressed to fit 5x7.
    "Å": ["00100", "01010", "01110", "10001", "11111", "10001", "10001"],
    "Ä": ["01010", "01110", "10001", "11111", "10001", "10001", "10001"],
    "Ö": ["01010", "01110", "10001", "10001", "10001", "10001", "01110"],
    "Ü": ["01010", "10001", "10001", "10001", "10001", "10001", "01110"],
    "å": ["00100", "01010", "01110", "00001", "01111", "10001", "01111"],
    "ä": ["01010", "00000", "01110", "00001", "01111", "10001", "01111"],
    "ö": ["01010", "00000", "01110", "10001", "10001", "10001", "01110"],
    "ü": ["01010", "00000", "10001", "10001", "10001", "10011", "01101"],
}


def default_5x7_glyphs() -> Dict[str, Glyph]:
    glyphs: Dict[str, Glyph] = {}
    question = _RAW_5X7["?"]
    for ch in DEFAULT_CHARSET:
        glyphs[ch] = normalize_glyph(_RAW_5X7.get(ch, question), 5, 7)
    return glyphs


@dataclass
class DotMatrixFont:
    glyphs: Dict[str, Glyph] = field(default_factory=default_5x7_glyphs)
    cols: int = 5
    rows: int = 7
    name: str = "Cube Libre Dot Matrix 5x7"
    missing_char: str = "?"

    @classmethod
    def from_builtin(cls) -> "DotMatrixFont":
        return cls(default_5x7_glyphs(), cols=5, rows=7)

    @classmethod
    def empty(cls, cols: int = 5, rows: int = 7, chars: str = DEFAULT_CHARSET, name: str = "Untitled Dot Matrix") -> "DotMatrixFont":
        return cls({ch: blank_glyph(cols, rows) for ch in chars}, cols=cols, rows=rows, name=name)

    @classmethod
    def load(cls, path: str | Path) -> "DotMatrixFont":
        path = Path(path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cols = int(data.get("cols", 5))
        rows = int(data.get("rows", 7))
        glyphs_in = data.get("glyphs", {})
        glyphs: Dict[str, Glyph] = {}
        for ch in DEFAULT_CHARSET:
            # If an older JSON was ASCII-only, fall back to the built-in extra glyphs
            # instead of creating blank Å/Ä/Ö/Ü ghosts.
            source = glyphs_in[ch] if ch in glyphs_in else _RAW_5X7.get(ch)
            glyphs[ch] = normalize_glyph(source, cols, rows)
        for key, value in glyphs_in.items():
            if key not in glyphs:
                glyphs[str(key)[:1]] = normalize_glyph(value, cols, rows)
        return cls(
            glyphs=glyphs,
            cols=cols,
            rows=rows,
            name=str(data.get("name", path.stem)),
            missing_char=str(data.get("missing_char", "?"))[:1] or "?",
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "format": "cube-libre-dotmatrix-font-v1",
            "name": self.name,
            "cols": self.cols,
            "rows": self.rows,
            "missing_char": self.missing_char,
            "glyphs": {ch: normalize_glyph(g, self.cols, self.rows) for ch, g in sorted(self.glyphs.items())},
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def get_glyph(self, ch: str) -> Glyph:
        if not ch:
            ch = self.missing_char
        if ch in self.glyphs:
            return normalize_glyph(self.glyphs[ch], self.cols, self.rows)
        if ch.upper() in self.glyphs:
            return normalize_glyph(self.glyphs[ch.upper()], self.cols, self.rows)
        return normalize_glyph(self.glyphs.get(self.missing_char), self.cols, self.rows)

    def set_glyph(self, ch: str, glyph: Sequence[str]) -> None:
        self.glyphs[ch[:1]] = normalize_glyph(glyph, self.cols, self.rows)

    def measure(
        self,
        text: str,
        dot_size: int = 6,
        gap: int = 2,
        char_spacing: Optional[int] = None,
        line_spacing: Optional[int] = None,
    ) -> Tuple[int, int]:
        char_spacing = dot_size + gap if char_spacing is None else char_spacing
        line_spacing = dot_size * 2 if line_spacing is None else line_spacing
        cell = dot_size + gap
        lines = text.split("\n") or [""]
        max_chars = max((len(line) for line in lines), default=0)
        char_w = self.cols * cell - gap
        line_h = self.rows * cell - gap
        width = 0 if max_chars == 0 else max_chars * char_w + max(0, max_chars - 1) * char_spacing
        height = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing
        return width, height

    def draw(
        self,
        surface: Any,
        text: str,
        pos: Tuple[int, int],
        dot_size: int = 6,
        gap: int = 2,
        color: Color = (255, 255, 255),
        char_spacing: Optional[int] = None,
        line_spacing: Optional[int] = None,
        align: str = "left",
        valign: str = "top",
        dot_shape: str = "square",
        border_color: Optional[Color] = None,
        shadow: Optional[Tuple[int, int, Color]] = None,
        jitter: float = 0.0,
        blink_phase: Optional[float] = None,
        blink_rate: float = 0.0,
        blink_depth: float = 0.0,
        on_dot: Optional[DotCallback] = None,
    ) -> None:
        """Draw text onto a pygame surface.

        dot_shape: "square", "round", or "diamond".
        on_dot: optional callback(ch, row, col, px, py, color) -> replacement color or None.
        blink_phase/rate/depth: quick built-in flicker; useful for warnings.
        """
        import pygame  # lazy import, so JSON/tools can be used without pygame installed

        x, y = pos
        width, height = self.measure(text, dot_size, gap, char_spacing, line_spacing)
        if align == "center":
            x -= width // 2
        elif align == "right":
            x -= width
        if valign == "middle":
            y -= height // 2
        elif valign == "bottom":
            y -= height

        char_spacing = dot_size + gap if char_spacing is None else char_spacing
        line_spacing = dot_size * 2 if line_spacing is None else line_spacing
        cell = dot_size + gap
        char_w = self.cols * cell - gap
        line_h = self.rows * cell - gap

        def apply_blink(base_color: Color, ch_i: int, row_i: int, col_i: int) -> Color:
            if blink_phase is None or blink_rate <= 0.0 or blink_depth <= 0.0:
                return base_color
            wave = 0.5 + 0.5 * math.sin(blink_phase * blink_rate + ch_i * 0.37 + row_i * 0.81 + col_i * 0.53)
            mult = 1.0 - blink_depth + blink_depth * wave
            if len(base_color) == 4:
                return (int(base_color[0] * mult), int(base_color[1] * mult), int(base_color[2] * mult), base_color[3])
            return (int(base_color[0] * mult), int(base_color[1] * mult), int(base_color[2] * mult))

        for line_i, line in enumerate(text.split("\n")):
            cursor_x = x
            cursor_y = y + line_i * (line_h + line_spacing)
            for ch_i, ch in enumerate(line):
                glyph = self.get_glyph(ch)
                for row_i, row in enumerate(glyph):
                    for col_i, bit in enumerate(row):
                        if bit != "1":
                            continue
                        px = cursor_x + col_i * cell
                        py = cursor_y + row_i * cell
                        if jitter > 0.0:
                            px += int(random.uniform(-jitter, jitter))
                            py += int(random.uniform(-jitter, jitter))

                        dot_color: Color = apply_blink(color, ch_i, row_i, col_i)
                        if on_dot is not None:
                            replacement = on_dot(ch, row_i, col_i, px, py, dot_color)
                            if replacement is not None:
                                dot_color = replacement

                        if shadow is not None:
                            sx, sy, scolor = shadow
                            self._draw_dot(pygame, surface, px + sx, py + sy, dot_size, dot_shape, scolor, None)
                        self._draw_dot(pygame, surface, px, py, dot_size, dot_shape, dot_color, border_color)
                cursor_x += char_w + char_spacing

    @staticmethod
    def _draw_dot(pygame: Any, surface: Any, x: int, y: int, dot_size: int, dot_shape: str, color: Color, border_color: Optional[Color]) -> None:
        rect = pygame.Rect(int(x), int(y), int(dot_size), int(dot_size))
        if dot_shape == "round":
            pygame.draw.circle(surface, color, rect.center, max(1, dot_size // 2))
            if border_color is not None:
                pygame.draw.circle(surface, border_color, rect.center, max(1, dot_size // 2), 1)
        elif dot_shape == "diamond":
            cx, cy = rect.center
            points = [(cx, y), (x + dot_size, cy), (cx, y + dot_size), (x, cy)]
            pygame.draw.polygon(surface, color, points)
            if border_color is not None:
                pygame.draw.polygon(surface, border_color, points, 1)
        else:
            pygame.draw.rect(surface, color, rect)
            if border_color is not None:
                pygame.draw.rect(surface, border_color, rect, 1)

    def render_to_surface(
        self,
        text: str,
        dot_size: int = 6,
        gap: int = 2,
        color: Color = (255, 255, 255),
        bg_color: Optional[Color] = None,
        padding: int = 0,
        dot_shape: str = "square",
        **draw_kwargs: Any,
    ) -> Any:
        import pygame

        w, h = self.measure(text, dot_size, gap, draw_kwargs.get("char_spacing"), draw_kwargs.get("line_spacing"))
        flags = pygame.SRCALPHA if bg_color is None else 0
        surf = pygame.Surface((max(1, w + padding * 2), max(1, h + padding * 2)), flags)
        if bg_color is not None:
            surf.fill(bg_color)
        self.draw(surf, text, (padding, padding), dot_size=dot_size, gap=gap, color=color, dot_shape=dot_shape, **draw_kwargs)
        return surf


# Convenience global for game code that wants a quick import.
DEFAULT_FONT = DotMatrixFont.from_builtin()
