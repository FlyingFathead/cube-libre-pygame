#!/usr/bin/env python3
"""
Standalone dot-matrix font editor/viewer for Cube Libre.

Run:
    python tools/dotmatrix_font_editor.py --font assets/fonts/cube_libre_5x7.json

Controls:
    Mouse left      toggle glyph cell / select character from right palette
    [ / ]           previous / next character
    Arrow keys      shift glyph pixels
    C               clear current glyph
    I               invert current glyph
    H               horizontal mirror current glyph
    M               vertical mirror current glyph
    V               toggle full sheet viewer
    F2              edit preview text
    Ctrl+S          save JSON font
    Esc             quit / leave preview edit mode / leave sheet view
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import List, Optional, Tuple

from cube_libre.dotmatrix_font import DEFAULT_CHARSET, DotMatrixFont, blank_glyph, normalize_glyph

BG = (13, 14, 22)
PANEL = (24, 26, 40)
PANEL_2 = (31, 34, 52)
GRID_OFF = (44, 49, 72)
GRID_ON = (220, 236, 255)
GRID_LINE = (82, 91, 130)
TEXT = (220, 226, 245)
MUTED = (135, 145, 180)
WARN = (255, 210, 95)
ACCENT = (122, 190, 255)
RED = (255, 105, 125)


class DotMatrixEditor:
    def __init__(self, font_path: Path, window_size: Tuple[int, int] = (1280, 820), cols: int = 5, rows: int = 7, new_blank: bool = False):
        import pygame

        self.pygame = pygame
        pygame.init()
        pygame.display.set_caption("Cube Libre Dot-Matrix Font Editor")
        self.screen = pygame.display.set_mode(window_size, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.ui_font = pygame.font.SysFont("consolas", 18)
        self.ui_font_small = pygame.font.SysFont("consolas", 14)
        self.ui_font_big = pygame.font.SysFont("consolas", 28, bold=True)

        self.font_path = font_path
        if font_path.exists() and not new_blank:
            self.font = DotMatrixFont.load(font_path)
        elif new_blank:
            self.font = DotMatrixFont.empty(cols=cols, rows=rows, name="Cube Libre Custom Dot Matrix")
        else:
            self.font = DotMatrixFont.from_builtin()
            self.font.name = "Cube Libre Custom Dot Matrix"

        self.chars = self.make_palette_chars()
        self.current_index = self.chars.index("A") if "A" in self.chars else 0
        self.current_char = self.chars[self.current_index]
        self.preview_text = "CUBE LIBRE\nREASSEMBLY IN PROGRESS\nCUBES LEFT: 17\nÅÄÖ Ü / åäö ü"
        self.preview_editing = False
        self.sheet_mode = False
        self.dirty = False
        self.status = ""
        self.status_timer = 0.0
        self.scroll_y = 0

    def make_palette_chars(self) -> str:
        """Characters shown in the editor/sheet palette.

        DEFAULT_CHARSET gives the built-in ASCII + Nordic set. If a loaded JSON
        contains additional one-codepoint glyphs, include them too so custom
        extended characters do not become ghost glyphs.
        """
        seen = set()
        chars = []
        for ch in DEFAULT_CHARSET + "".join(self.font.glyphs.keys()):
            if len(ch) == 1 and ch not in seen:
                seen.add(ch)
                chars.append(ch)
        return "".join(chars)

    def run(self) -> None:
        pygame = self.pygame
        running = True
        while running:
            dt = self.clock.tick(60) / 1000.0
            if self.status_timer > 0:
                self.status_timer -= dt
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.VIDEORESIZE:
                    self.screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
                elif event.type == pygame.MOUSEWHEEL:
                    self.scroll_y += event.y * 36
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self.handle_mouse(event.pos, event.button)
                elif event.type == pygame.KEYDOWN:
                    if not self.handle_key(event):
                        running = False
            self.draw()
            pygame.display.flip()
        pygame.quit()

    def handle_key(self, event) -> bool:
        pygame = self.pygame
        mods = pygame.key.get_mods()

        if self.preview_editing:
            if event.key == pygame.K_ESCAPE or event.key == pygame.K_F2:
                self.preview_editing = False
                return True
            if event.key == pygame.K_BACKSPACE:
                self.preview_text = self.preview_text[:-1]
                return True
            if event.key == pygame.K_RETURN:
                self.preview_text += "\n"
                return True
            if event.unicode:
                self.preview_text += event.unicode
                return True

        if event.key == pygame.K_ESCAPE:
            if self.sheet_mode:
                self.sheet_mode = False
                return True
            return False

        if event.key == pygame.K_s and (mods & pygame.KMOD_CTRL):
            self.save()
            return True
        if event.key == pygame.K_F2:
            self.preview_editing = True
            self.flash("Preview edit mode. Esc/F2 exits.")
            return True
        if event.key == pygame.K_v:
            self.sheet_mode = not self.sheet_mode
            return True
        if event.key == pygame.K_RIGHTBRACKET:
            self.next_char(1)
            return True
        if event.key == pygame.K_LEFTBRACKET:
            self.next_char(-1)
            return True
        if event.key == pygame.K_c:
            self.set_current(blank_glyph(self.font.cols, self.font.rows))
            return True
        if event.key == pygame.K_i:
            self.set_current(["".join("0" if b == "1" else "1" for b in row) for row in self.font.get_glyph(self.current_char)])
            return True
        if event.key == pygame.K_h:
            self.set_current([row[::-1] for row in self.font.get_glyph(self.current_char)])
            return True
        if event.key == pygame.K_m:
            self.set_current(list(reversed(self.font.get_glyph(self.current_char))))
            return True
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            dx = -1 if event.key == pygame.K_LEFT else 1 if event.key == pygame.K_RIGHT else 0
            dy = -1 if event.key == pygame.K_UP else 1 if event.key == pygame.K_DOWN else 0
            self.shift_current(dx, dy)
            return True
        return True

    def handle_mouse(self, pos: Tuple[int, int], button: int) -> None:
        if button not in (1, 3):
            return
        if self.sheet_mode:
            return
        mx, my = pos
        grid_rect, cell_size = self.editor_grid_rect()
        if grid_rect.collidepoint(mx, my):
            col = int((mx - grid_rect.x) // cell_size)
            row = int((my - grid_rect.y) // cell_size)
            if 0 <= col < self.font.cols and 0 <= row < self.font.rows:
                glyph = self.font.get_glyph(self.current_char)
                row_bits = list(glyph[row])
                row_bits[col] = "0" if row_bits[col] == "1" else "1"
                glyph[row] = "".join(row_bits)
                self.set_current(glyph, flash=False)
            return

        hit = self.palette_hit(pos)
        if hit is not None:
            self.current_index = hit
            self.current_char = self.chars[self.current_index]
            return

    def next_char(self, delta: int) -> None:
        self.current_index = (self.current_index + delta) % len(self.chars)
        self.current_char = self.chars[self.current_index]

    def set_current(self, glyph: List[str], flash: bool = True) -> None:
        self.font.set_glyph(self.current_char, glyph)
        self.dirty = True
        if flash:
            self.flash(f"Edited {repr(self.current_char)}")

    def shift_current(self, dx: int, dy: int) -> None:
        old = self.font.get_glyph(self.current_char)
        rows, cols = self.font.rows, self.font.cols
        new = [["0" for _ in range(cols)] for _ in range(rows)]
        for y in range(rows):
            for x in range(cols):
                ny, nx = y + dy, x + dx
                if 0 <= ny < rows and 0 <= nx < cols and old[y][x] == "1":
                    new[ny][nx] = "1"
        self.set_current(["".join(row) for row in new], flash=False)

    def save(self) -> None:
        self.font.save(self.font_path)
        self.dirty = False
        self.flash(f"Saved {self.font_path}")

    def flash(self, msg: str) -> None:
        self.status = msg
        self.status_timer = 3.0

    def editor_grid_rect(self):
        pygame = self.pygame
        w, h = self.screen.get_size()
        margin = 36
        top = 142
        max_grid_w = min(520, w - 460)
        cell = max(22, min(72, max_grid_w // max(1, self.font.cols)))
        rect = pygame.Rect(margin, top, self.font.cols * cell, self.font.rows * cell)
        return rect, cell

    def palette_rects(self):
        pygame = self.pygame
        w, h = self.screen.get_size()
        panel_w = 380
        x0 = w - panel_w + 18
        y0 = 86 + self.scroll_y
        cell_w = 42
        cell_h = 54
        cols = max(1, (panel_w - 36) // cell_w)
        rects = []
        for i, ch in enumerate(self.chars):
            x = x0 + (i % cols) * cell_w
            y = y0 + (i // cols) * cell_h
            rects.append((i, ch, pygame.Rect(x, y, cell_w - 6, cell_h - 6)))
        return rects

    def palette_hit(self, pos: Tuple[int, int]) -> Optional[int]:
        for i, ch, rect in self.palette_rects():
            if rect.collidepoint(*pos):
                return i
        return None

    def draw(self) -> None:
        if self.sheet_mode:
            self.draw_sheet()
        else:
            self.draw_editor()

    def textline(self, text: str, pos: Tuple[int, int], color=TEXT, small: bool = False, big: bool = False) -> None:
        font = self.ui_font_big if big else self.ui_font_small if small else self.ui_font
        surf = font.render(text, True, color)
        self.screen.blit(surf, pos)

    def draw_editor(self) -> None:
        pygame = self.pygame
        screen = self.screen
        w, h = screen.get_size()
        screen.fill(BG)

        # Right palette panel
        panel_w = 380
        pygame.draw.rect(screen, PANEL, pygame.Rect(w - panel_w, 0, panel_w, h))
        self.textline("Glyph palette", (w - panel_w + 18, 20), TEXT, big=True)
        self.textline("click char | mouse wheel scroll", (w - panel_w + 18, 55), MUTED, small=True)

        # Header
        dirty = " *" if self.dirty else ""
        code = ord(self.current_char)
        label = f"Editing {repr(self.current_char)}  U+{code:04X}  {self.font.cols}x{self.font.rows}{dirty}"
        self.textline(label, (36, 26), TEXT, big=True)
        self.textline(f"File: {self.font_path}", (38, 64), MUTED, small=True)
        self.textline("Ctrl+S save | [ ] prev/next | arrows shift | C clear | I invert | H mirror | M flip | V sheet | F2 preview", (38, 92), MUTED, small=True)

        # Main glyph editor grid
        grid_rect, cell = self.editor_grid_rect()
        glyph = self.font.get_glyph(self.current_char)
        pygame.draw.rect(screen, PANEL_2, grid_rect.inflate(18, 18), border_radius=8)
        for row in range(self.font.rows):
            for col in range(self.font.cols):
                r = pygame.Rect(grid_rect.x + col * cell, grid_rect.y + row * cell, cell - 2, cell - 2)
                on = glyph[row][col] == "1"
                pygame.draw.rect(screen, GRID_ON if on else GRID_OFF, r, border_radius=max(0, cell // 12))
                pygame.draw.rect(screen, GRID_LINE, r, 1, border_radius=max(0, cell // 12))

        # Current glyph preview
        preview_x = grid_rect.right + 40
        preview_y = grid_rect.y
        self.textline("Live glyph", (preview_x, preview_y - 32), MUTED, small=True)
        self.font.draw(screen, self.current_char, (preview_x, preview_y), dot_size=18, gap=7, color=ACCENT, border_color=(38, 51, 80), dot_shape="square")

        # Text preview panel
        ptop = grid_rect.bottom + 46
        pygame.draw.rect(screen, PANEL_2, pygame.Rect(36, ptop, max(100, w - panel_w - 72), max(120, h - ptop - 34)), border_radius=8)
        self.textline("Preview text" + ("  [editing]" if self.preview_editing else ""), (54, ptop + 16), WARN if self.preview_editing else MUTED, small=True)
        self.font.draw(
            screen,
            self.preview_text,
            (58, ptop + 48),
            dot_size=7,
            gap=3,
            color=TEXT,
            shadow=(2, 2, (0, 0, 0)),
            dot_shape="square",
        )

        # Palette chars
        view_clip = pygame.Rect(w - panel_w, 80, panel_w, h - 80)
        old_clip = screen.get_clip()
        screen.set_clip(view_clip)
        for i, ch, rect in self.palette_rects():
            if rect.bottom < 80 or rect.top > h:
                continue
            active = i == self.current_index
            pygame.draw.rect(screen, ACCENT if active else PANEL_2, rect, border_radius=5)
            pygame.draw.rect(screen, (54, 62, 91), rect, 1, border_radius=5)
            # Label uses system font so even unedited glyphs are identifiable.
            label = "SP" if ch == " " else ch
            lab_surf = self.ui_font_small.render(label, True, BG if active else TEXT)
            screen.blit(lab_surf, (rect.x + 5, rect.y + 4))
            self.font.draw(screen, ch, (rect.x + 8, rect.y + 23), dot_size=3, gap=1, color=BG if active else TEXT)
        screen.set_clip(old_clip)

        if self.status_timer > 0:
            self.textline(self.status, (36, h - 26), WARN, small=True)

    def draw_sheet(self) -> None:
        pygame = self.pygame
        screen = self.screen
        w, h = screen.get_size()
        screen.fill(BG)
        self.textline("Full dot-matrix sheet viewer", (36, 24), TEXT, big=True)
        self.textline("V/Esc returns to editor | Ctrl+S saves | mouse wheel scroll", (38, 62), MUTED, small=True)

        x0 = 40
        y0 = 104 + self.scroll_y
        cell_w = max(86, self.font.cols * 6 + 38)
        cell_h = max(72, self.font.rows * 6 + 34)
        cols = max(1, (w - 80) // cell_w)

        clip = pygame.Rect(0, 88, w, h - 88)
        old_clip = screen.get_clip()
        screen.set_clip(clip)
        for i, ch in enumerate(self.chars):
            x = x0 + (i % cols) * cell_w
            y = y0 + (i // cols) * cell_h
            rect = pygame.Rect(x, y, cell_w - 10, cell_h - 10)
            if rect.bottom < 88 or rect.top > h:
                continue
            pygame.draw.rect(screen, PANEL_2, rect, border_radius=6)
            pygame.draw.rect(screen, (52, 58, 84), rect, 1, border_radius=6)
            label = "SP" if ch == " " else ch
            self.textline(f"{label} {ord(ch)}", (x + 8, y + 6), MUTED, small=True)
            self.font.draw(screen, ch, (x + 12, y + 28), dot_size=5, gap=2, color=TEXT)
        screen.set_clip(old_clip)


def parse_args():
    parser = argparse.ArgumentParser(description="Cube Libre dot-matrix font editor/viewer")
    parser.add_argument("--font", default="cube_libre_5x7.json", help="JSON font file to load/save")
    parser.add_argument("--cols", type=int, default=5, help="columns for --new-blank")
    parser.add_argument("--rows", type=int, default=7, help="rows for --new-blank")
    parser.add_argument("--new-blank", action="store_true", help="start with a blank font instead of built-in starter glyphs")
    parser.add_argument("--sheet", action="store_true", help="start in full sheet viewer mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = DotMatrixEditor(Path(args.font), cols=args.cols, rows=args.rows, new_blank=args.new_blank)
    app.sheet_mode = bool(args.sheet)
    app.run()


if __name__ == "__main__":
    main()
