"""The Memory Card menu (state 4): FUN_81318f80 (logic) and FUN_8131b94c (drawing).

Each slot shows its 127 directory entries as glass cubes (res 0x57 empty /
0x58 with the save's icon) in a 4x4 window that scrolls by rows. Saves can be
moved, copied and erased between slot A and B, and a damaged card can be
formatted. The cards are Dolphin .raw images in <project>/cards (see
ipl/gcmemcard.py); with no image a slot reads "Nothing is inserted".

Modes (G_814ad470): 1 browse, 2 choose Move/Copy/Erase, 3 Yes/No,
4 format prompt, 5 waiting for / showing a result.
"""
import os
import random

import moderngl
import numpy as np

from .. import gcmemcard as gc
from ..mathx import hermite, rot_trans, scale_cols, sin16, cos16
from ..model import GpuModel
from ..ui import Fader, Layout, Strings, bti_texture, fourcc
from .block import block
from .faces import cdiv

RES_LAYOUT = [0x1C] * 6                                   # G_814ad38c
RES_STRINGS = [0x1E, 0x20, 0x1F, 0x22, 0x21, 0x1D]        # DAT_814af710
RES_DIGITS = 0x5A                                         # G_814ad3ac: the 11-glyph block counter font
RES_CUBE_EMPTY, RES_CUBE_FILE = 0x57, 0x58

SE_CURSOR, SE_EDGE, SE_ENTER, SE_BACK, SE_LEAVE, SE_YESNO = 0xB, 0xD, 8, 7, 6, 9

# DAT_814b6380 (FUN_81315800), the Memory Card face parameters the menu also reads
P_BOB_STEP = 5            # +0x14
SCALE_HI, SCALE_LO = 2.0, 1.3      # +0x48 / +0x4c
MENU_TXT = (0xFF, 0xFF, 0xFF)      # +0x50..0x54
MENU_HI = (0xF0, 0xE6, 0x00)       # +0x56..0x5a
MENU_DIM = 0x46                    # +0x5c
LIFT_X, LIFT_Y = 10.0, 5.0         # +0x64 / +0x68
SPOT_Z = 320.0                     # +0x70

# r13-0x7fa0.. : TEV C0 of the slot cubes for 8 KiB-sector cards (FUN_81320dd0)
CUBE_COLORS = {0: (100, 150, 255, 255), 1: (20, 50, 200, 255), 2: (30, 80, 100, 255), 3: (0, 20, 60, 255)}

# error codes (G_814ad46c) -> message string, per operation (FUN_81318858 / FUN_81318a88)
OK, E_CANT, E_NOCARD, E_BADOBJ, E_TYPES, E_EXISTS, E_FULL, E_FAIL, E_WRONG = 0, -0x1A, -0x17, -0x16, -0x1B, -0x18, -0x19, -0x1C, -0x1C
COPY_MSG = {OK: 19, E_CANT: 20, E_TYPES: 27, E_EXISTS: 28, E_FULL: 29, E_FAIL: 30}
MOVE_MSG = {OK: 7, E_CANT: 8, E_TYPES: 15, E_EXISTS: 16, E_FULL: 17, E_FAIL: 18}

COPY, MOVE, ERASE = 1, 0, 2        # G_814ad474: strings 2 Move, 3 Copy, 4 Erase


class SlotCube:
    """The 0x5c-byte per-entry animation record (DAT_814c4e20)."""
    __slots__ = ("t0", "t", "t1", "alpha", "on", "bob", "scale", "pos", "vel", "p0", "m0", "p1")

    def __init__(self):
        self.t0 = self.t = 0
        self.t1 = 6
        self.alpha = 0
        self.on = 0
        self.bob = 0
        self.scale = SCALE_LO
        self.pos = np.zeros(3)
        self.vel = np.zeros(3)
        self.p0 = np.zeros(3)
        self.m0 = np.zeros(3)
        self.p1 = np.zeros(3)


class MemcardMenu:
    def __init__(self, hub, root):
        self.hub = hub
        self.root = root
        ctx = hub.ctx
        self.layout = Layout(hub.r_bytes(RES_LAYOUT[0]))
        self.digits_tex, self.digits_w, self.digits_h = bti_texture(ctx, hub.r_bytes(RES_DIGITS))
        self.cube_empty = GpuModel(ctx, hub.r_bytes(RES_CUBE_EMPTY))
        self.cube_file = GpuModel(ctx, hub.r_bytes(RES_CUBE_FILE))
        self.icon_slot_tex = self.cube_file.textures[1] if len(self.cube_file.textures) > 1 else None
        self.grid = [self._grid(0), self._grid(1)]       # DAT_814b63f8 / 6438 (1/16 px)
        self.cards = [None, None]
        self.paths = [None, None]
        self.order = [list(range(127)), list(range(127))]
        self.cubes = [[SlotCube() for _ in range(127)] for _ in range(2)]
        self.icon_tex = [{}, {}]
        self.icon_timer = [[0] * 127, [0] * 127]
        self.icon_dir = [[1] * 127, [1] * 127]
        # cursor (G_814ad450 / 454 / 458 / 45c / 460)
        self.slot = 0
        self.col = [0, 0]
        self.row = [0, 0]
        self.page = [0, 0]
        self.max_page = [0, 0]
        self.mode = self.mode_next = 1
        self.action = self.action_next = 1
        self.yes = 1                # G_814ad480: 0 Yes, 1 No
        self.op = 0                 # G_814ad468
        self.op_slot = 0            # G_814ad464
        self.result = 0             # G_814ad46c
        self.fmt = [1, 1]           # r13-0x7b48 per slot: bit0 = No, bit1 = second question
        self.intro = True           # G_814ad4fc
        self.time = 0               # G_814ad508
        self.arrow_t, self.arrow_d = 0, 1     # G_814ad4a4 / 4a6 (0..15)
        self.glow_t, self.glow_d = 0, 1       # G_814ad4aa / 4ac (0..30)
        self.msg = [Fader(index=i) for i in range(0x2F)]    # DAT_814b6e64: fader i shows string i
        self.slot_on = [Fader(), Fader()]                   # DAT_814b7154/7164
        self.can_copy = [Fader(), Fader()]                  # DAT_814b7174/7184
        self.can_move = [Fader(), Fader()]                  # DAT_814b7194/71a4
        self.arrow_up = [Fader(), Fader()]                  # DAT_814b71f4/7204
        self.arrow_dn = [Fader(), Fader()]                  # DAT_814b7214/7224
        self.popup = [[0, 20, 0, 30, 10, 20, 0], [0, 20, 0, 30, 10, 20, 0]]   # DAT_814b71b4 (FUN_8131a644)
        self.spots = [Fader(length=10), Fader(length=10), Fader(length=10)]   # DAT_814b7234/7244/7254
        self.poll()

    # -- cards ----------------------------------------------------------------------------
    def _grid(self, slot):
        """FUN_8131593c: the 16 window positions from the layout (ia00..ia15 / ib00..ib15)."""
        out = []
        for i in range(16):
            name = fourcc(("ia" if slot == 0 else "ib") + f"{i:02d}")
            e = [e for e in self.layout.elements if e["name"] == name]
            out.append((e[0]["x"], e[0]["y"]) if e else (0, 0))
        return out

    def poll(self):
        """Insert / remove: rescan the cards folder (the console's CARD probing, FUN_8131c268)."""
        paths = gc.find_cards(self.root)
        for s in range(2):
            if paths[s] != self.paths[s] or (self.cards[s] is not None and not os.path.exists(paths[s] or "")):
                self.paths[s] = paths[s]
                self.cards[s] = gc.Card(paths[s]) if paths[s] else None
                self.icon_tex[s] = {}
                self.sort(s)

    def state(self, s):
        c = self.cards[s]
        return gc.NO_CARD if c is None else c.state

    def entry(self, s, i):
        c = self.cards[s]
        if c is None or c.state != gc.READY:
            return None
        return c.entries[i]

    def sort(self, s):
        """FUN_81317564: saves by modification time, then the empty entries."""
        c = self.cards[s]
        keys = []
        for i in range(127):
            e = c.entries[i] if c is not None and c.state == gc.READY else None
            keys.append(((e.modtime if e else (0xFFFFFFFF - i) & 0xFFFFFFFF), i))
        self.order[s] = [i for _, i in sorted(keys)]
        if c is not None and c.state == gc.READY:
            # FUN_813164fc: rows past the 4-row window, over the used entries
            u = 0x7F - c.free_entries
            self.max_page[s] = max(0, u // 4 - (4 if u % 4 == 0 else 3))
        else:
            self.max_page[s] = 0
        self.page[s] = min(self.page[s], self.max_page[s])

    def selected_index(self, s=None):
        """FUN_813190c8: directory index under the cursor."""
        s = self.slot if s is None else s
        k = self.col[s] + (self.row[s] + self.page[s]) * 4
        return self.order[s][min(126, k)]

    # -- the cubes (FUN_813207c8 / FUN_8132056c / FUN_81320920 / FUN_8131f8f8) ---------------
    def enter(self):
        """FUN_81319124 + FUN_8131783c + FUN_813207c8 (state 3 -> 4)."""
        for f in self.msg:
            f.value = 0
            f.ticks = 0
        self.poll()
        for s in range(2):
            self.sort(s)
            for c in self.cubes[s]:
                c.pos = np.array([4672.0, 3584.0, 0.0])
                r = random.getrandbits(16)
                c.vel = np.array([350.0 * sin16(r), 350.0 * cos16(r), 0.0])
                c.p0 = c.pos.copy()
                c.m0 = c.vel.copy()
                c.t = 0
                c.alpha = 0
                c.t0 = random.getrandbits(4)
                c.t1 = c.t0 + 0x21
        self.intro = True
        self.layout_cubes()

    def layout_cubes(self):
        """FUN_8132056c: send each slot's cubes to the window rows, the rest off the top or bottom."""
        for s in range(2):
            g = self.grid[s]
            pg = self.page[s] * 4
            for u, f in enumerate(self.order[s]):
                c = self.cubes[s][f]
                c.t0 = 0 if not self.intro else c.t0
                if not self.intro:
                    c.t = 0
                    c.t1 = 0x28
                c.p0 = c.pos.copy()
                c.m0 = c.vel.copy()
                if self.flyer is not None and s == self.flyer["slot"] and f == self.flyer["file"]:
                    c.on = 0
                elif pg <= u <= pg + 15:
                    c.on = 0xFF
                    x, y = g[u - pg]
                    c.p1 = np.array([float(x), float(y), 0.0])
                else:
                    c.on = 0
                    if u < pg:
                        x, y = g[u & 3]
                        c.p1 = np.array([float(x), float(y) - 512.0, 0.0])
                    else:
                        x, y = g[12 + (u & 3)]
                        c.p1 = np.array([float(x), float(y) + 512.0, 0.0])

    def gather(self, s):
        """FUN_81320920: an empty slot's cubes fade, then wait in the middle of the window."""
        cs = self.cubes[s]
        for c in cs:
            c.on = 0
        if all(c.alpha == 0 for c in cs):
            g = self.grid[s]
            cx = (g[5][0] + g[6][0]) // 2
            cy = (g[5][1] + g[9][1]) // 2
            for c in cs:
                c.pos = np.array([float(cx), float(cy), 0.0])
                c.vel = np.zeros(3)
                c.p0, c.m0 = c.pos.copy(), c.vel.copy()
                c.t = 0

    def animate_cubes(self):
        """FUN_8131f8f8 (the flight part)."""
        step = 0x19 if self.intro else 8
        for s in range(2):
            sel = self.selected_index(s) if s == self.slot else -1
            for f, c in enumerate(self.cubes[s]):
                if c.t0 <= c.t:
                    c.alpha = max(0, min(255, c.alpha + (step if c.on == 0xFF else -step)))
                if c.t < c.t0:
                    new = c.p0.copy()
                else:
                    # FUN_813079fc: tangents are per frame, the end tangent is zero
                    new = hermite(float(c.t), float(c.t0), c.p0, c.m0, float(c.t1), c.p1, 0.0)
                c.vel = new - c.pos
                c.pos = new
                c.t = min(c.t1, c.t + 1)
                c.scale = hermite(c.bob, 0, SCALE_LO, 0, 6, SCALE_HI, 0)
                c.bob += 1 if (f == sel and not self.intro) else -1
                c.bob = max(0, min(6, c.bob))
        if self.intro and self.arrived():
            self.intro = False
        self.time = (self.time + 7) & 0xFFFF

    def arrived(self):
        """FUN_81320abc: every cube of the ready slots has landed."""
        if not self.slots_shown():
            return False
        for s in range(2):
            if self.state(s) == gc.READY and any(c.t != c.t1 for c in self.cubes[s]):
                return False
        return True

    def slots_shown(self):
        """FUN_81314cc8: the three submenu faders are up."""
        return all(f.alpha() >= 0xAD for f in self.hub.sub_faders["memcard"])

    # -- faders (FUN_81317f40 / FUN_81318cb8 / FUN_81318e80) --------------------------------------
    def faders(self):
        for s in range(2):
            st = self.state(s)
            self.slot_on[s].step(0 if st == gc.READY else 1)
            if st == gc.READY:
                ok_c, _ = self.check(s, self.selected_index(s), COPY)
                ok_m, _ = self.check(s, self.selected_index(s), MOVE)
                self.can_copy[s].step(0 if ok_c else 1)
                self.can_move[s].step(0 if ok_m else 1)
            self.msg[0x21 + s].step(0 if st == gc.NO_CARD else 1)
            self.msg[0x23 + s].step(0 if st in (gc.BROKEN, gc.WRONG_DEVICE, 7, 9) else 1)
            if st in (gc.NEEDS_FORMAT, 8) and (self.mode == 4 or self.mode == 5 and self.op in (1, 8)):
                second = self.fmt[s] & 2
                cur = s == self.slot
                if not second:
                    self.msg[0x27 + s].step(0 if cur else 2)
                    self.msg[0x29 + s].step(1)
                else:
                    self.msg[0x27 + s].step(1)
                    self.msg[0x29 + s].step(0 if cur else 2)
            else:
                self.msg[0x27 + s].step(1)
                self.msg[0x29 + s].step(1)
            if self.mode == 1:
                self.arrow_up[s].step(1 if self.page[s] < 1 else 0)
                self.arrow_dn[s].step(0 if self.page[s] < self.max_page[s] else 1)
            else:
                self.arrow_up[s].step(1)
                self.arrow_dn[s].step(1)
        # results
        on = set()
        if self.mode == 5 and self.result != -0x15:
            if self.op in (2, 5):
                on.add(COPY_MSG.get(self.result, 30) if self.result not in (E_NOCARD, E_BADOBJ) else
                       (0x16 - self.op_slot if self.result == E_NOCARD else 0x18 - self.op_slot))
            elif self.op in (3, 6):
                on.add(MOVE_MSG.get(self.result, 18) if self.result not in (E_NOCARD, E_BADOBJ) else
                       (10 - self.op_slot if self.result == E_NOCARD else 12 - self.op_slot))
            elif self.op in (4, 7):
                on.add(31 if self.result == 0 else 32)
            elif self.op in (1, 8):
                on.add(0x2B + self.op_slot if self.result == 0 else 0x2D + self.op_slot)
        for i in list(range(7, 0x21)) + [0x2B, 0x2C, 0x2D, 0x2E]:
            self.msg[i].step(0 if i in on else 1)
        # the action popups (FUN_8131a688)
        for s in range(2):
            p = self.popup[s]
            if self.state(self.slot) == gc.READY and self.mode == 2 and s == self.slot:
                kind = 0
            elif self.state(self.slot) == gc.READY and self.mode == 3 and s == self.slot:
                kind = 2
            else:
                kind = 1
            if kind == 1:
                p[0] = max(0, p[0] - 1)
                p[2] = max(0, p[2] - 1)
            elif kind == 0:
                p[0] = min(p[1], p[0] + 1)
                p[2] = max(0, p[2] - 1)
            else:
                p[0] = min(p[1], p[0] + 1)
                p[2] = min(p[3], p[2] + 1)
            p[6] = (p[6] + P_BOB_STEP) & 0xFFFF
        # the spotlights over the chosen slot
        lit = 0
        for s in range(2):
            on = s == self.slot and self.state(s) == gc.READY
            self.spots[s].step(0 if on else 1)
            lit += on
        self.spots[2].step(0 if lit == 0 else 1)
        self.arrow_t += self.arrow_d
        if self.arrow_t < 0:
            self.arrow_t, self.arrow_d = 0, 1
        if self.arrow_t > 15:
            self.arrow_t, self.arrow_d = 15, -1
        self.glow_t += self.glow_d
        if self.glow_t < 0:
            self.glow_t, self.glow_d = 0, 1
        if self.glow_t > 30:
            self.glow_t, self.glow_d = 30, -1

    # -- checks (FUN_81315fdc / FUN_813161f8) -------------------------------------------------------
    def check(self, s, i, what):
        e = self.entry(s, i)
        other = 1 - s
        st = self.state(other)
        if st != gc.READY:
            if st == gc.NO_CARD:
                return False, E_NOCARD
            if st in (gc.BROKEN, 7, 9):
                return False, E_BADOBJ
            return False, E_WRONG
        if e is None:
            return False, E_CANT
        if not (e.copyable if what == COPY else e.movable):
            return False, E_CANT
        a, b = self.cards[s], self.cards[other]
        if a.encoding != b.encoding:
            return False, E_TYPES
        if e.blocks > b.free_blocks or b.free_entries == 0:
            return False, E_FULL
        if b.has_file(e):
            return False, E_EXISTS
        return True, OK

    # -- input (FUN_81317b80 and the per-mode handlers) ---------------------------------------
    def tick(self, pad):
        """FUN_81318f80."""
        self.poll()
        self.faders()
        self.icons_tick()
        for s in range(2):
            if self.state(s) != gc.READY:
                self.gather(s)
        self.animate_cubes()
        self.fly()
        self.mode = self.mode_next
        self.action = self.action_next
        if self.state(self.slot) != gc.READY:
            other = 1 - self.slot
            if self.state(other) == gc.READY and self.state(self.slot) == gc.NO_CARD:
                self.slot = other
        for s in range(2):
            if self.state(s) in (gc.NEEDS_FORMAT, 8) and self.mode == 1 and self.fmt[s] & 1 and not (self.fmt[s] & 4):
                self.slot = s
                self.mode = self.mode_next = 4
                self.fmt[s] = 1 | 4
        nxt = 4
        if self.mode == 1:
            nxt = self.browse(pad)
        elif self.mode == 2:
            nxt = self.choose(pad)
        elif self.mode == 3:
            nxt = self.confirm(pad)
        elif self.mode == 4:
            nxt = self.format_prompt(pad)
        elif self.mode == 5:
            nxt = self.message(pad)
        return nxt

    def ready_for_input(self):
        return self.arrived()

    def browse(self, pad):
        """FUN_813164fc."""
        audio = self.hub.audio
        s = self.slot
        st = self.state(s)
        if self.ready_for_input() and st == gc.READY:
            if "UP" in pad.dirs:
                self.row[s] -= 1
                if self.row[s] < 0:
                    self.row[s] = 0
                    if self.page[s] > 0:
                        self.page[s] -= 1
                        self.layout_cubes()
                        audio.play_se(SE_CURSOR)
                    elif not pad.repeating:
                        audio.play_se(SE_EDGE)
                else:
                    audio.play_se(SE_CURSOR)
            if "DOWN" in pad.dirs:
                self.row[s] += 1
                if self.col[s] + (self.row[s] + self.page[s]) * 4 < 0x7F:
                    if self.row[s] < 4:
                        audio.play_se(SE_CURSOR)
                    else:
                        self.row[s] = 3
                        if self.page[s] < self.max_page[s]:
                            self.page[s] += 1
                            self.layout_cubes()
                            audio.play_se(SE_CURSOR)
                        elif not pad.repeating:
                            audio.play_se(SE_EDGE)
                else:
                    if not pad.repeating:
                        audio.play_se(SE_EDGE)
                    self.row[s] -= 1
            if "LEFT" in pad.dirs:
                if s == 0:
                    if self.col[0] < 1:
                        if not pad.repeating:
                            audio.play_se(SE_EDGE)
                    else:
                        audio.play_se(SE_CURSOR)
                        self.col[0] -= 1
                else:
                    self.col[1] -= 1
                    if self.col[1] < 0:
                        self.col[1] = 0
                        if self.state(0) == gc.READY and self.col[0] + (self.row[1] + self.page[0]) * 4 < 0x7F:
                            self.slot = 0
                            self.row[0] = self.row[1]
                            self.col[0] = 3
                            audio.play_se(SE_CURSOR)
                        elif not pad.repeating:
                            audio.play_se(SE_EDGE)
                    else:
                        audio.play_se(SE_CURSOR)
            if "RIGHT" in pad.dirs:
                if s == 1:
                    if self.col[1] < 3 and self.col[1] + 1 + (self.row[1] + self.page[1]) * 4 < 0x7F:
                        self.col[1] += 1
                        audio.play_se(SE_CURSOR)
                    elif not pad.repeating:
                        audio.play_se(SE_EDGE)
                else:
                    self.col[0] += 1
                    if self.col[0] + (self.row[0] + self.page[0]) * 4 < 0x7F:
                        if self.col[0] < 4:
                            audio.play_se(SE_CURSOR)
                        else:
                            self.col[0] = 3
                            if self.state(1) == gc.READY:
                                self.slot = 1
                                self.row[1] = self.row[0]
                                self.col[1] = 0
                                audio.play_se(SE_CURSOR)
                            elif not pad.repeating:
                                audio.play_se(SE_EDGE)
                    else:
                        self.col[0] -= 1
                        if not pad.repeating:
                            audio.play_se(SE_EDGE)
            if "A" in pad.pressed and self.entry(self.slot, self.selected_index()) is not None:
                self.mode_next = 2
                audio.play_se(SE_ENTER)
        if "B" in pad.pressed and (self.hub.p.zoom == 0x7FFF or self.hub.p.busy) and self.ready_for_input():
            audio.play_se(SE_LEAVE)
            for t in range(2):
                self.fmt[t] = 1
            return 3
        return 4

    def choose(self, pad):
        """FUN_81316bac: Move / Copy / Erase."""
        audio = self.hub.audio
        if "UP" in pad.dirs:
            audio.play_se(10)
            self.action_next = (self.action_next - 1) % 3
        if "DOWN" in pad.dirs:
            audio.play_se(10)
            self.action_next = (self.action_next + 1) % 3
        if "A" in pad.pressed and self.action_next == self.action:
            i = self.selected_index()
            self.op_slot = self.slot
            self.yes = 1
            if self.action == ERASE:
                audio.play_se(SE_ENTER)
                self.mode_next = 3
            else:
                ok, err = self.check(self.slot, i, self.action)
                if ok:
                    audio.play_se(SE_ENTER)
                    self.mode_next = 3
                else:
                    audio.play_se(SE_EDGE)
                    self.op = 5 if self.action == COPY else 6
                    self.result = err
                    self.mode_next = 5
        if "B" in pad.pressed:
            audio.play_se(SE_BACK)
            self.back_to_browse()
        return 4

    def confirm(self, pad):
        """FUN_81316ddc: Yes / No, then do it."""
        audio = self.hub.audio
        if "UP" in pad.dirs:
            if self.yes != 0:
                audio.play_se(SE_YESNO)
            self.yes = 0
        if "DOWN" in pad.dirs:
            if self.yes != 1:
                audio.play_se(SE_YESNO)
            self.yes = 1
        if "A" in pad.pressed:
            if self.yes == 0:
                audio.play_se(SE_ENTER)
                self.run(self.action)
                self.mode_next = 5
            else:
                audio.play_se(SE_BACK)
                self.mode_next = 2
        if "B" in pad.pressed:
            audio.play_se(SE_BACK)
            self.mode_next = 2
        if self.state(1 - self.slot) != gc.READY and self.action in (COPY, MOVE):
            self.mode_next = 2
        return 4

    def run(self, what):
        """FUN_81320094: the save's cube leaves the grid; the card work happens when the flight ends."""
        s = self.slot
        f = self.selected_index()
        c = self.cubes[s][f]
        self.op_slot = s
        self.op = {ERASE: 4, COPY: 2, MOVE: 3}[what]
        self.result = -0x15                         # busy
        fl = {"what": what, "slot": s, "file": f, "pos": c.pos.copy(), "vel": c.vel.copy(), "t": 0,
              "scale": c.scale, "grid": self.col[s] + self.row[s] * 4}
        if what == ERASE:
            fl["shrink"] = 20                       # DAT_814d9e1a, counts down
            fl["burst"] = None
        else:
            r = random.getrandbits(16)
            fl["p0"], fl["m0"] = c.pos.copy(), c.vel.copy()
            fl["p1"] = np.array([16.0 * (292.0 + 40.0 * sin16(r)), 16.0 * (200.0 + 40.0 * cos16(r)), 0.0])
            fl["t1"] = 0x28
            fl["laps"] = 0
        c.alpha = 0
        c.on = 0
        self.flyer = fl

    flyer = None

    def fly(self):
        """FUN_8131f8f8 (G_814ad4f8 / G_814ad500 parts): circle the middle, or shrink and burst."""
        fl = self.flyer
        if fl is None:
            return
        if fl["what"] == ERASE:
            if fl["shrink"] > 0:
                fl["scale"] = hermite(fl["shrink"], 0, 0.0, 0, 20, SCALE_HI, 0)
                fl["shrink"] -= 1
            else:
                if fl["burst"] is None:
                    fl["burst"] = [(0, 10, random.getrandbits(2), random.getrandbits(16)) for _ in range(12)]
                    fl["bt"] = 0
                fl["bt"] += 1
                if all(fl["bt"] > delay + n for _, n, delay, _ in fl["burst"]):
                    self.finish_op()
            return
        fl["t"] += 1
        new = hermite(float(fl["t"]), 0.0, fl["p0"], fl["m0"], float(fl["t1"]), fl["p1"], 0.0)
        fl["vel"] = new - fl["pos"]
        fl["pos"] = new
        if fl["t"] >= fl["t1"] - 5:
            fl["laps"] += 1
            if fl["laps"] >= 2:
                self.finish_op()
                return
            # bounce to the mirror point around the middle (4672, 3200) for another lap
            fl["p0"], fl["m0"] = fl["pos"].copy(), fl["vel"].copy()
            px, py = fl["p1"][0], fl["p1"][1]
            fl["p1"] = np.array([4672.0 + (4672.0 - px), 3200.0 + (3200.0 - py), 0.0])
            fl["t"] = 0
            fl["t1"] = 0xB4 // 3

    def finish_op(self):
        """The card thread's job (FUN_8131e130 copy/move, FUN_8131eb04 erase) and FUN_8132030c."""
        fl = self.flyer
        self.flyer = None
        what, s, i = fl["what"], fl["slot"], fl["file"]
        a = self.cards[s]
        new_index = None
        i = self.selected_index()
        a = self.cards[s]
        self.op_slot = s
        try:
            if what == ERASE:
                self.op = 7
                self.result = OK if a.erase(i) >= 0 else E_FAIL
                self.hub.audio.play_se(0x13 if self.result == OK else SE_EDGE)
            else:
                b = self.cards[1 - s]
                e = a.entries[i]
                ok, err = self.check(s, i, what)
                if not ok:
                    self.result = err
                else:
                    data = a.file_data(e)
                    r = b.write_file(e, data)
                    if r < 0:
                        self.result = E_FULL
                    else:
                        self.result = OK
                        new_index = r
                        if what == MOVE:
                            a.erase(i)
                self.op = 5 if what == COPY else 6
                if self.result == OK:
                    self.hub.audio.play_se(0x14 if s == 0 else 0x15)
                else:
                    self.hub.audio.play_se(SE_EDGE)
        except OSError:
            self.result = E_FAIL
            self.op = {ERASE: 7, COPY: 5, MOVE: 6}[what]
        for t in range(2):
            self.icon_tex[t] = {}
            self.sort(t)
        self.layout_cubes()
        dst = 1 - s
        if new_index is not None:
            # the new save's cube starts where the flyer was (FUN_8132030c)
            c = self.cubes[dst][new_index]
            c.pos = fl["pos"].copy()
            c.p0 = c.pos.copy()
            c.m0 = np.array([0.0, 150.0 if fl["grid"] < 8 else -150.0, 0.0])
            c.t, c.t1 = 0, 0x28
            c.alpha = 0xFF
        if self.result != OK and what != ERASE:
            c = self.cubes[s][i]          # FUN_813204b0: back home
            c.pos = fl["pos"].copy()
            c.p0, c.m0 = c.pos.copy(), np.zeros(3)
            c.t, c.t1 = 0, 0x28
            c.alpha = 0xFF

    def message(self, pad):
        """FUN_813173ec: dismiss the result."""
        if self.flyer is not None:
            return 4
        if ("A" in pad.pressed or "B" in pad.pressed) and self.msg_shown():
            self.op = 0
            self.result = 0
            self.yes = 1
            self.hub.audio.play_se(SE_BACK)
            self.back_to_browse(force=True)
        return 4

    def msg_shown(self):
        return any(self.msg[i].alpha() > 0xAD for i in range(7, 0x2F))

    def back_to_browse(self, force=False):
        """FUN_81316414."""
        other = 1 - self.slot
        if self.state(other) in (8, gc.NEEDS_FORMAT) and self.fmt[other] & 1 and not self.fmt[other] & 4:
            self.mode_next = 4
            self.slot = other
        else:
            self.mode_next = 1
            if self.state(self.slot) != gc.READY and self.state(other) == gc.READY:
                self.slot = other

    def format_prompt(self, pad):
        """FUN_81317150: "must be formatted, format now?" then "all data will be erased, OK?"."""
        audio = self.hub.audio
        s = self.slot
        f = self.fmt[s]
        if not self.ready_for_input() and not self.msg[0x27 + s].alpha() > 0xAD and not self.msg[0x29 + s].alpha() > 0xAD:
            return 4
        if "UP" in pad.dirs:
            if f & 1:
                audio.play_se(SE_CURSOR)
            self.fmt[s] = f & ~1
        if "DOWN" in pad.dirs:
            if not f & 1:
                audio.play_se(SE_CURSOR)
            self.fmt[s] = f | 1
        if "A" in pad.pressed:
            f = self.fmt[s]
            if f & 3 == 2:          # second question, Yes
                audio.play_se(SE_ENTER)
                self.op_slot = s
                self.op = 8
                self.result = OK if self.cards[s].format() >= 0 else E_FAIL
                self.hub.audio.play_se(0x10 if self.result == OK else SE_EDGE)
                self.sort(s)
                self.layout_cubes()
                self.mode_next = 5
            elif f & 3 == 0:        # first question, Yes
                self.fmt[s] = 3
                audio.play_se(SE_ENTER)
            else:                   # No
                audio.play_se(SE_BACK)
                self.fmt[s] = 1 | 4
                self.back_to_browse()
        if "B" in pad.pressed:
            audio.play_se(SE_BACK)
            self.fmt[s] = 1 | 4
            self.back_to_browse()
        return 4

    def flag(self):
        """FUN_8131bf50: 1 while browsing."""
        return 1 if self.mode == 1 else 0

    # -- icons (FUN_813186f4) ------------------------------------------------------------------
    def art(self, s, i):
        c = self.cards[s]
        if c is None or c.state != gc.READY or c.entries[i] is None:
            return None
        return c.art(i)

    def icons_tick(self):
        for s in range(2):
            for k in range(16):
                u = k + self.page[s] * 4
                if u >= 127:
                    break
                f = self.order[s][u]
                a = self.art(s, f)
                if not a or not a["delays"]:
                    continue
                total = sum(a["delays"])
                t = self.icon_timer[s][f] + self.icon_dir[s][f]
                if t >= total:
                    if a["pingpong"]:
                        t, self.icon_dir[s][f] = total - a["delays"][-1] - 1, -1
                    else:
                        t, self.icon_dir[s][f] = 0, 1
                if t < 0:
                    t, self.icon_dir[s][f] = (a["delays"][0] if a["pingpong"] else 0), 1
                self.icon_timer[s][f] = t

    def icon_frame(self, s, f):
        a = self.art(s, f)
        if not a or not a["icons"]:
            return None
        t = self.icon_timer[s][f]
        acc = 0
        for k, d in enumerate(a["delays"]):
            acc += d
            if t < acc:
                return k
        return len(a["icons"]) - 1

    def texture_for(self, s, f, k):
        key = (f, k)
        if key not in self.icon_tex[s]:
            img = self.art(s, f)["icons"][k]
            t = self.hub.ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.icon_tex[s][key] = t
        return self.icon_tex[s][key]

    def banner_texture(self, s, f):
        key = ("banner", f)
        if key not in self.icon_tex[s]:
            img = self.art(s, f)["banner"]
            if img is None:
                self.icon_tex[s][key] = None
            else:
                t = self.hub.ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
                t.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self.icon_tex[s][key] = t
        return self.icon_tex[s][key]

    # -- drawing (FUN_8131b94c) --------------------------------------------------------------------
    def strings(self):
        return Strings(self.hub.r_bytes(RES_STRINGS[self.hub.settings.language]))

    def draw(self, a0, a1, a2):
        hub, ui, L, bank = self.hub, self.hub.ui, self.layout, self.hub.bank
        st = self.strings()
        spots = []
        for s in range(2):
            g = self.grid[s]
            x = (g[5][0] + g[6][0]) / 32.0
            y = (g[5][1] + g[9][1]) / 32.0
            spots.append((x, y, SPOT_Z, self.spots[s].alpha() / 255.0))
        hub.r.draw_backdrop(a1 / 255.0, spots=spots, center=self.spots[2].alpha() / 255.0)
        ui.screen()
        ui.draw_frames_named("mes6", L, bank, 0xFFFFFF00 | a2)
        order = (1, 0) if self.slot == 0 else (0, 1)
        for s in order:
            self.draw_cubes(s, a0)
        self.draw_flyer(a0)
        for s in order:
            ui.screen()
            self.draw_slot(s, a2, st)
            for i in ((0x21, 0x23) if s == 0 else (0x22, 0x24)):
                f = self.msg[i]
                if f.alpha() > 0:
                    ui.draw_string(st, i, L, bank, 0xFFFFFF00 | (a2 * f.alpha() // 255), offy=f.offset())
            self.draw_format_prompt(s, a2, st)
        for s in range(2):
            self.draw_popup(s, a2, st)
        for i in list(range(7, 0x21)) + [0x2B, 0x2C, 0x2D, 0x2E]:
            f = self.msg[i]
            if f.alpha() > 0:
                ui.draw_string(st, i, L, bank, 0xFFFFFF00 | (a2 * f.alpha() // 255), offy=f.offset())

    def cube_matrix(self, c):
        """FUN_8131f048: a cube in screen space, the selected one lifted and wobbling."""
        p = block()
        f = (c.scale - SCALE_LO) / (SCALE_HI - SCALE_LO)
        t1 = (self.time * 0x23) & 0xFFFF
        t2 = (self.time * 0x46) & 0xFFFF
        rz = int(f * p.h(0x24) * cos16(t1))
        ry = int(f * p.h(0x22) * cos16(p.h(0x2E) + t1))
        rx = int(f * p.h(0x20) * cos16(t2))
        x = c.pos[0] / 16.0 + LIFT_X * f * sin16(p.h(0x30) + t1) - 292.0
        y = -c.pos[1] / 16.0 - LIFT_Y * f * sin16(t2) + 224.0
        z = 2.0 * f
        return scale_cols(rot_trans(x, y, z, rx, ry, rz), c.scale, c.scale, c.scale)

    def draw_cubes(self, s, a0):
        """FUN_81321378."""
        r = self.hub.r
        view = r.view2d()
        cur_sel = self.selected_index(s) if s == self.slot and not self.intro else -1
        card = self.cards[s]
        top = None
        for f in range(127):
            c = self.cubes[s][f]
            alpha = a0 * c.alpha // 255
            if alpha <= 0:
                continue
            m = self.cube_matrix(c)
            e = card.entries[f] if card is not None and card.state == gc.READY else None
            if e is None:
                kind = 2 if f == cur_sel else 3
                self._tint(self.cube_empty, kind)
                r.draw(self.cube_empty, view, m, alpha / 255.0)
            elif f == cur_sel:
                top = (f, m, alpha)
            else:
                self._draw_file(s, f, m, alpha, 1)
        if top is not None:
            self._draw_file(s, top[0], top[1], top[2], 0)

    def draw_flyer(self, a0):
        fl = self.flyer
        if fl is None:
            return
        s, f = fl["slot"], fl["file"]
        if fl["what"] != ERASE or fl["shrink"] > 0:
            c = SlotCube()
            c.pos = fl["pos"]
            c.scale = fl["scale"] if fl["what"] == ERASE else SCALE_HI
            self._draw_file(s, f, self.cube_matrix(c), a0, 0)
            return
        # FUN_8131f770: twelve small cubes fly apart and fade
        for d, n, delay, ang in fl["burst"] or ():
            t = fl["bt"] - delay
            if t <= 0 or t > n:
                continue
            u = t / n
            r = 48.0 * (0.25 + 0.75 * u)
            m = rot_trans(fl["pos"][0] / 16.0 - 292.0 + r * sin16(ang), -fl["pos"][1] / 16.0 + 224.0 + r * cos16(ang),
                          0.0, 0, 0, 0)
            m = scale_cols(m, 0.3, 0.3, 0.3)
            self._draw_file(s, f, m, int(a0 * (1.0 - u)), 0)

    def _tint(self, model, kind):
        col = tuple(x / 255.0 for x in CUBE_COLORS[kind])
        for mi, mat in enumerate(model.j.materials):
            if mat["textures"] and model.j.texture_names[mat["textures"][0]].startswith("icon"):
                continue
            model.tev_color[mi] = [col] + list(mat["tev_colors"][1:])

    def _draw_file(self, s, f, m, alpha, kind):
        model = self.cube_file
        self._tint(model, kind)
        k = self.icon_frame(s, f)
        icon_mats = {mi for mi, mat in enumerate(model.j.materials)
                     if mat["textures"] and model.j.texture_names[mat["textures"][0]].startswith("icon")}
        body = {mi for mi in range(len(model.j.materials)) if mi not in icon_mats}
        r = self.hub.r
        view = r.view2d()
        r.draw(model, view, m, alpha / 255.0, only=body)
        if k is not None and icon_mats:
            ti = model.j.materials[min(icon_mats)]["textures"][0]
            saved = model.textures[ti]
            model.textures[ti] = self.texture_for(s, f, k)
            r.draw(model, view, m, alpha / 255.0, only=icon_mats)
            model.textures[ti] = saved

    def draw_slot(self, s, a2, st):
        """FUN_81319d70 / FUN_81319bc4: slot letter, free blocks, arrows, and the chosen save's banner and text."""
        ui, L, bank = self.hub.ui, self.layout, self.hub.bank
        sa = a2 * self.slot_on[s].alpha() // 255
        if sa > 0:
            col = 0xFFFFFF00 | sa
            if s == self.slot:
                glow = 0x80 * self.glow_t // 30
                col = 0xFFFFFF00 | (sa * ((0xFF - 0x80) + glow) // 255)
            ui.draw_named(fourcc("texa") + s, L, bank, col)
            ui.draw_frames_named(fourcc("ffra") + s, L, bank, col)
            ui.draw_string(st, s, L, bank, col)
            card = self.cards[s]
            self.draw_number(fourcc("fnma") + s, card.free_blocks if card else 0, col)
        # scroll arrows (bobbing)
        bob = int(hermite(self.arrow_t, 0, 0, 0, 15, 128, 0))
        au = a2 * self.arrow_up[s].alpha() // 255
        ad = a2 * self.arrow_dn[s].alpha() // 255
        up, dn = (("arau", "arad") if s == 0 else ("arbu", "arbd"))
        if au > 0:
            ui.draw_named(up, L, bank, 0xFFFFFF00 | au, offy=-bob)
        if ad > 0:
            ui.draw_named(dn, L, bank, 0xFFFFFF00 | ad, offy=bob)
        if s == self.slot and self.state(s) == gc.READY and sa > 0:
            f = self.selected_index(s)
            e = self.entry(s, f)
            if e is not None:
                bt = self.banner_texture(s, f)
                if bt is not None:
                    ui.draw_named("bana", L, bank, 0xFFFFFF00 | sa, tex=(bt, 96, 32))
                a = self.art(s, f)
                box = {t["name"]: t for t in L.texts}
                for name, text in ((fourcc("titl"), a["title"]), (fourcc("info"), a["info"])):
                    if name in box:
                        ui.draw_text(box[name], text, 0xFFFFFF00 | sa, wrap=False)
                ui.draw_frames_named("frmc", L, bank, 0xFFFFFF00 | sa)
                self.draw_number("nmc1", e.blocks, 0xFFFFFF00 | sa)

    def draw_number(self, name, value, color):
        """FUN_8131959c: a right-to-left run of glyphs from the 110x14 counter font, centred on an element."""
        ui, L = self.hub.ui, self.layout
        els = [e for e in L.elements if e["name"] == fourcc(name)] if isinstance(name, str) else \
            [e for e in L.elements if e["name"] == name]
        if not els:
            return
        e = els[0]
        digits = str(max(0, int(value)))
        gw = (self.digits_w << 4) // 11
        step = gw + 0x20
        x = e["x"] - (step * (len(digits) - 1)) // 2
        y = e["y"]
        h = self.digits_h * 16
        r, g, b, a = ((color >> 24) & 255) / 255, ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255
        verts = []
        for ch in digits:
            d = int(ch)
            s0, s1 = d / 11.0, (d + 1) / 11.0
            verts += ui.quad(x - gw // 2, y - h // 2, x + gw // 2, y + h // 2, s0, 0.0, s1, 1.0, (r, g, b, a))
            x += step
        ui.quads(self.digits_tex, verts)

    def draw_format_prompt(self, s, a2, st):
        """FUN_8131a13c: the two format questions with Yes / No."""
        ui, L, bank = self.hub.ui, self.layout, self.hub.bank
        for base in (0x27, 0x29):
            f = self.msg[base + s]
            fa = f.alpha()
            if fa <= 0:
                continue
            col = 0xFFFFFF00 | (a2 * fa // 255)
            ui.draw_string(st, base + s, L, bank, col)
            sel = self.fmt[s] & 1
            for k, idx in enumerate((5, 6)):
                hi = (k == sel) and s == self.slot
                c = ((MENU_HI[0] << 24) | (MENU_HI[1] << 16) | (MENU_HI[2] << 8)) if hi else 0xFFFFFF00
                ui.draw_string(st, idx, L, bank, c | (a2 * fa // 255), offy=k * 22 * 16 + 56 * 16)

    def draw_popup(self, s, a2, st):
        """FUN_8131b018: Move / Copy / Erase beside the chosen cube, then Yes / No."""
        p = self.popup[s]
        if p[0] <= 0 or s != self.slot:
            return
        ui, L, bank = self.hub.ui, self.layout, self.hub.bank
        a = a2 * 0xFF * p[0] // p[1] // 255
        g = self.grid[s][self.col[s] + self.row[s] * 4]
        fr = [k for k, f in enumerate(L.frames) if f["name"] == fourcc("mes0")]
        x = g[0] + 0x280 if self.col[s] < 2 else g[0] - 0x280 - 91 * 16
        y = g[1] - 0x300 if self.row[s] < 2 else g[1] - 0x900
        w, h = 91 * 16, 70 * 16
        if fr:
            ui.draw_frame(L, bank, fr[0], 0xFFFFFF00 | a, x, y, w, h)
        box = [t for t in L.texts if t["name"] == fourcc("txt0")]
        base = dict(box[0]) if box else None
        for k, (idx, act) in enumerate(((2, MOVE), (3, COPY), (4, ERASE))):
            enabled = True
            if act == COPY:
                enabled = self.can_copy[s].alpha() > 0x80
            elif act == MOVE:
                enabled = self.can_move[s].alpha() > 0x80
            hi = self.action_next == act
            c = (MENU_HI if hi else MENU_TXT)
            al = a if enabled else a * MENU_DIM // 255
            if base is not None:
                bx = dict(base, x=x + w // 2, y=y + 0x180 + k * 22 * 16, ax=128, ay=0)
                ui.draw_text(bx, st.entries[idx][1], (c[0] << 24) | (c[1] << 16) | (c[2] << 8) | al)
        if p[2] > 0:
            ya = a2 * 0xFF * p[2] // p[3] // 255
            y2 = y + h + 0x200
            if fr:
                ui.draw_frame(L, bank, fr[0], 0xFFFFFF00 | ya, x, y2, w, 48 * 16)
            for k, idx in enumerate((5, 6)):
                hi = self.yes == k
                c = (MENU_HI if hi else MENU_TXT)
                if base is not None:
                    bx = dict(base, x=x + w // 2, y=y2 + 0x180 + k * 22 * 16, ax=128, ay=0)
                    ui.draw_text(bx, st.entries[idx][1], (c[0] << 24) | (c[1] << 16) | (c[2] << 8) | ya)
