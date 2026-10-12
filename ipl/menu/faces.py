"""The small cubes that animate inside the glass menu cube, one group per face.

Each face has three behaviours, picked every frame by FUN_813123b0:
  active   - the cube is turned towards this face (states 1/3/5/7)
  submenu  - the face's menu is open (states 2/4/6/8): the cubes gather and fade
  idle     - any other state: the cubes fade out and the animation rewinds
All four use the same model (res 0xB2 'cube_in_manu'); the colour comes from TEV
register C0, which the IPL swaps in per group (signed GX_TevColorS10 values).
"""
import random

import numpy as np

from .. import rom
from ..mathx import hermite, rot_trans, s16, scale_cols, sin16, cos16, trans


def cdiv(a, b):
    """C integer division (truncates towards zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def clamp8(v):
    return max(0, min(255, v))


def towards_zero(v, step):
    if v > 0:
        return max(0.0, v - step)
    if v < 0:
        return min(0.0, v + step)
    return v


def tev(c):
    return tuple(x / 255.0 for x in c)


class Cube:
    """One drawn instance: the 0x78-byte object of the IPL reduced to what matters."""
    __slots__ = ("alpha", "mtx", "scale", "color")

    def __init__(self):
        self.alpha = 0
        self.mtx = np.eye(4)
        self.scale = 1.0
        self.color = (0, 0, 0, 255)


# ----------------------------------------------------------------------------
# Memory Card face (bottom, state 3/4): FUN_813218c8 .. FUN_81322ee8
# ----------------------------------------------------------------------------
def _inner(i):
    """FUN_81321b98: the 3x4 block in the middle of the 6x6 grid."""
    return 7 <= i <= 10 or 13 <= i <= 16 or 19 <= i <= 22


_RING = {0: 0, 1: 1, 6: 1, 2: 2, 12: 2, 3: 3, 18: 3, 4: 4, 24: 4, 5: 5, 25: 5, 30: 5,
         11: 6, 26: 6, 31: 6, 17: 7, 27: 7, 32: 7, 23: 8, 28: 8, 33: 8, 29: 9, 34: 9, 35: 10}

# FUN_81321f60: the inner cubes light up as a diagonal wave
_WAVE = [[7], [8, 13], [9, 14, 19], [10, 15, 20], [16, 21], [22]]


class MemcardFace:
    COLOR = (-45, -40, 50, 255)      # DAT_814b6380 [0..3]
    SCALE = 0.7                      # +0x10
    POS_STEP = 5                     # +0x5e
    ALPHA_STEP = 13                  # +0x60

    def __init__(self):
        self.cubes = [Cube() for _ in range(36)]
        self.t = 0
        self.group = 0
        self.done = False
        self.pos = [[0.0, 0.0, 0.0] for _ in range(36)]
        self.start = [(0, 0, 0)] * 36
        self.target = [((i % 6) * 20 - 50, 50 - (i // 6) * 20, 0) for i in range(36)]
        self.t0 = [0] * 36
        self.t1 = [0] * 36
        self.spin = [0] * 36
        self._randomise()

    def _randomise(self):
        for i in range(36):
            r = random.getrandbits(16) & 0x1F
            self.t0[i] = r
            self.t1[i] = r + 20

    def _fly(self):
        """FUN_81321c68."""
        t = self.t
        for i in range(36):
            t0, t1 = self.t0[i], self.t1[i]
            if t <= t0:
                self.pos[i] = list(self.start[i])
            if t1 <= t:
                self.pos[i] = list(self.target[i])
            if t0 < t < t1:
                sx, sy, _ = self.start[i]
                tx, ty, _ = self.target[i]
                self.pos[i] = [int(hermite(t, t0, sx, (tx - sx) / (t1 - t0), t1, tx, 0.0)),
                               int(hermite(t, t0, sy, (ty - sy) / (t1 - t0), t1, ty, 0.0)), 0]
        for i, c in enumerate(self.cubes):
            if _inner(i):
                continue
            t0, t1 = self.t0[i], self.t1[i]
            if t0 < t < t1:
                c.alpha = cdiv((t - t0) * 255, t1 - t0)
            elif t0 < t:
                c.alpha = 255
            else:
                c.alpha = 0

    def _wave(self, on):
        """FUN_81321f60."""
        if not on:
            for group in _WAVE:
                for i in group:
                    self.cubes[i].alpha = clamp8(self.cubes[i].alpha - 15)
            return
        for gi, group in enumerate(_WAVE):
            if gi == 0 or self.cubes[_WAVE[gi - 1][0]].alpha > 100:
                for i in group:
                    self.cubes[i].alpha = clamp8(self.cubes[i].alpha + 30)

    def _group_rot(self, u):
        """FUN_81322434: the whole grid turns about its vertical axis."""
        if u < 50:
            v = hermite(u, 0, 0, 0, 50, -8000, 0)
        elif u < 90:
            v = hermite(u, 50, -8000, 0, 90, 32768, 0)
        elif u < 140:
            v = 0x8000
        elif u < 190:
            v = hermite(u, 140, 32768, 0, 190, 40768, 0)
        elif u < 230:
            v = hermite(u, 190, 40768, 0, 230, 0, 0)
        elif u < 280:
            v = 0
        else:
            return
        self.group = s16(int(v))

    def active(self):
        """FUN_81322650."""
        if self.t == 0:
            self._randomise()
        if self.t < 0x72:
            self._fly()
        t = self.t
        for lo in (0x96, 300, 0x1B8):
            if lo <= t <= lo + 0x2D:
                u = t - lo
                for i in range(36):
                    if _inner(i):
                        continue
                    o = _RING.get(i, 0)
                    if o * 3 <= u <= o * 3 + 15:
                        self.spin[i] = cdiv((u - o * 3) * 0x4000, 15)
                break
        else:
            self.spin = [0] * 36
        self._wave(self.t >= 0x154)
        if 0xD1 < self.t < 0x1EB:
            self._group_rot(self.t - 0xD2)
        self.t += 1
        if self.t > 0x1EA:
            self.t = 0xD2

    def idle(self):
        """FUN_81322d58."""
        for c in self.cubes:
            c.alpha = max(0, c.alpha - 10)
        self.t = 0
        self.group = 0

    def submenu(self):
        """FUN_81322ee8."""
        settled = 0
        for i, c in enumerate(self.cubes):
            c.alpha = max(0, c.alpha - self.ALPHA_STEP)
            for k in range(3):
                self.pos[i][k] = int(towards_zero(self.pos[i][k], self.POS_STEP))
                if self.pos[i][k] == 0 and c.alpha == 0:
                    settled += 1
        self.done = settled == 108
        self.t = 0
        self.group = 0

    def place(self, cube_world):
        """FUN_81322a18."""
        base = cube_world @ rot_trans(0, 0, 0, 0x4000, 0, 0) @ rot_trans(0, 0, 0, 0, self.group, 0)
        for i, c in enumerate(self.cubes):
            x, y, z = self.pos[i]
            a = self.spin[i]
            c.mtx = base @ rot_trans(x, y, z, a, a, 0)
            c.scale = self.SCALE * (0.5 if _inner(i) else 1.0)
            c.color = self.COLOR

    def draw_list(self):
        return [c for c in self.cubes if c.alpha > 0]


# ----------------------------------------------------------------------------
# Game Play face (top, state 1/2): FUN_813230f0 .. FUN_81323bc0
# ----------------------------------------------------------------------------
class GameFace:
    COLOR = (0, -90, 20, 255)        # DAT_814b6360 [0..3]
    RADIUS = 75                      # +0x10
    SCALE = 0.65                     # +0x14
    POS_STEP = 5                     # +0x1a
    ALPHA_STEP = 10                  # +0x1c

    def __init__(self):
        self.cubes = [Cube() for _ in range(16)]
        self.start = [k * 10 for k in range(16)]
        self.end = [k * 10 + 20 for k in range(16)]
        self.angle = [k * 0x1000 for k in range(16)]
        self.r = [0] * 16
        self.xy = [[0.0, 0.0] for _ in range(16)]
        self.t = 0
        self.tumble_x = 0
        self.tumble_z = 0
        self.spin = 0
        self.dx = 100      # G_814ad0c8
        self.dz = 50       # G_814ad0ca
        self.done = False

    def active(self):
        """FUN_813233c8."""
        t = self.t
        for k, c in enumerate(self.cubes):
            s, e = self.start[k], self.end[k]
            if s < t < e:
                self.r[k] = cdiv(self.RADIUS * (t - s), e - s)
                c.alpha = cdiv((t - s) * 255, e - s)
            elif s < t:
                self.r[k] = self.RADIUS
                c.alpha = 255
            else:
                self.r[k] = 0
                c.alpha = 0
            a = s16(self.angle[k] - self.spin)
            self.xy[k] = [self.r[k] * sin16(a), self.r[k] * cos16(a)]
        self.t += 1
        self.spin = s16(self.spin + 0x199)
        if self.t > 300:
            self.tumble_x = s16(self.tumble_x + self.dx)
            self.t = 300
            self.tumble_z = s16(self.tumble_z + self.dz)
            if self.tumble_x > 32000:
                self.dx = -100
            if self.tumble_x < 1000:
                self.dx = 100
            if self.tumble_z > 0x2000:
                self.dz = -50
            if self.tumble_z < -0x2000:
                self.dz = 50
            if self.spin > 0x1000:
                self.spin %= 0x1000

    def idle(self):
        """FUN_81323868."""
        n = 0
        for c in self.cubes:
            c.alpha -= 10
            if c.alpha < 0:
                c.alpha = 0
                n += 1
        self.t = 0
        if n == 16:
            self.tumble_x = self.tumble_z = self.spin = 0

    def submenu(self):
        """FUN_813239e4."""
        n = 0
        for k, c in enumerate(self.cubes):
            c.alpha = max(0, c.alpha - self.ALPHA_STEP)
            self.xy[k][0] = towards_zero(self.xy[k][0], self.POS_STEP)
            self.xy[k][1] = towards_zero(self.xy[k][1], self.POS_STEP)
            n += (self.xy[k][0] == 0.0) + (self.xy[k][1] == 0.0)
        if n == 32:
            self.tumble_x = self.tumble_z = self.spin = 0
            self.done = True
        else:
            self.done = False
        self.t = 0

    def place(self, cube_world):
        """FUN_813235d4."""
        base = cube_world @ rot_trans(0, 0, 0, -0x4000, 0, 0) @ rot_trans(0, 0, 0, self.tumble_x, 0, self.tumble_z)
        for k, c in enumerate(self.cubes):
            x, y = self.xy[k]
            c.mtx = base @ trans(x, y, 0.0)
            c.scale = self.SCALE
            c.color = self.COLOR

    def draw_list(self):
        """FUN_8132373c: once the ring is out, draw it back-to-front in pairs."""
        if self.t < 300:
            order = range(16)
        else:
            order = [i for s in range(8) for i in (7 - s, s + 8)]
        return [self.cubes[i] for i in order if self.cubes[i].alpha > 0]


# ----------------------------------------------------------------------------
# Options face (left, state 5/6): FUN_81323c30 .. FUN_81325db0
# ----------------------------------------------------------------------------
class _Rec:
    """The 5-word record per cube at DAT_814dd978 / DAT_814ddbd0."""
    __slots__ = ("x", "y", "dx", "dy", "rx", "ry")

    def __init__(self, x=0.0, y=0.0):
        self.x, self.y, self.dx, self.dy, self.rx, self.ry = x, y, x, y, 0, 0


def _corner(k):
    return (k % 2) * 160 - 80, (k // 2) * -140 + 70


class OptionsFace:
    COLOR = (-30, 20, -50, 255)      # DAT_814d9f00 [0..3]
    POS_STEP = 5                     # +0x1e
    ALPHA_STEP = 12                  # +0x20

    def __init__(self):
        self.scale_a = rom.f32(0x8137EE40)
        self.scale_b = rom.f32(0x8137EE4C)
        self.start = rom.f32(0x814ADFE0)
        self.corners = list(rom.rom()[rom.off(0x814ADFF0):rom.off(0x814ADFF0) + 4])     # 0 7 22 15
        self.lap_cube = list(rom.rom()[rom.off(0x814ADFF4):rom.off(0x814ADFF4) + 4])    # 22 0 15 7
        self.lap_from = list(rom.rom()[rom.off(0x814ADFF8):rom.off(0x814ADFF8) + 4])    # 2 0 3 1
        self.bitmap = rom.rom()[rom.off(0x81381450):rom.off(0x81381450) + 72]
        self.a = [Cube() for _ in range(30)]
        self.b = [Cube() for _ in range(31)]
        self.ra = [_Rec() for _ in range(30)]
        self.rb = [_Rec() for _ in range(31)]
        self.t = 0
        self.done = False
        self.layout()

    def layout(self):
        """FUN_81323e20: 30 cubes on the border of a 9x8 grid, 31 cubes in the icon bitmap."""
        for i in range(30):
            if 1 <= i <= 6:
                x, y = 80, 70 - 20 * i
            elif 8 <= i <= 14:
                x, y = (15 - i) * 20 - 80, -70
            elif 16 <= i <= 21:
                x, y = -80, 70 - 20 * (22 - i)
            elif 23 <= i <= 29:
                x, y = (i - 22) * 20 - 80, 70
            else:
                x, y = 0, 0
            r = self.ra[i]
            r.x, r.y, r.rx, r.ry = float(x), float(y), 0, 0
            self.a[i].alpha = 0
        cells = [i for i in range(72) if self.bitmap[i]]
        for k in range(31):
            idx = cells[k] if k < len(cells) else 0
            self.rb[k].x = float((idx % 9) * 20 - 80)
            self.rb[k].y = float(70 - (idx // 9) * 20)
            self.b[k].alpha = 0

    def _copy(self):
        """FUN_81324098."""
        for r in self.ra + self.rb:
            r.dx, r.dy = r.x, r.y

    def _lap(self, u, n):
        """FUN_81324268: the four corner cubes run along the border, lighting it up."""
        for k in range(4):
            c = self.lap_from[k]
            cube = self.lap_cube[k]
            sx, sy = _corner(c)
            ex, ey = _corner(k)
            r = self.ra[cube]
            r.x = hermite(u, 0, sx, cdiv(ex - sx, n), n, ex, 0)
            r.y = hermite(u, 0, sy, cdiv(ey - sy, n), n, ey, 0)
            self.a[cube].alpha = clamp8(self.a[cube].alpha + 6)
            if k == 2:
                lit = int((r.x + 80.0) / 20.0)
                for s in range(8, lit, -1):
                    self.a[15 - s].alpha = 255
            elif k == 0:
                lit = int((70.0 - r.y) / 20.0)
                for s in range(7, lit, -1):
                    self.a[22 - s].alpha = 255
            elif k == 1:
                lit = int((r.x + 80.0) / 20.0)
                for s in range(max(0, lit)):
                    self.a[23 + s].alpha = 255
            else:
                lit = int((70.0 - r.y) / 20.0)
                for s in range(max(0, lit)):
                    self.a[1 + s].alpha = 255
        self._copy()

    def _flip(self, u):
        """FUN_81324938: a wave of quarter turns around the border."""
        for i, r in enumerate(self.ra):
            lo = 5 * i
            if lo <= u <= lo + 10:
                v = cdiv((u - lo) * 0x4000, 10)
                if i < 7:
                    r.rx, r.ry = v, 0
                elif i < 15:
                    r.rx, r.ry = 0, -v
                elif i < 22:
                    r.rx, r.ry = -v, 0
                else:
                    r.rx, r.ry = 0, v
        self._copy()

    @staticmethod
    def _clamp(v, h):
        if v > 0 and v > h:
            return float(h)
        if v < 0 and v < -h:
            return float(-h)
        return v

    def _toggle(self):
        for c in self.a + self.b:
            c.alpha ^= 0xFF

    def _bounce(self, u):
        """FUN_81324eec: the icon cubes hop, row by row, twice."""
        for r in self.rb:
            row = int(7.0 - (70.0 - r.y) / 20.0)
            r.dy = r.y
            for base in (row, row + 8):
                if 4 * base <= u < 4 * base + 16:
                    r.dy = r.y + hermite(u - 4 * base, 0, 0, 0, 16, 10, 0)
                    break
                if 4 * base + 16 <= u <= 4 * base + 32 - (1 if base != row else 0):
                    r.dy = r.y + hermite(u - 4 * base - 16, 0, 10, 0, 16, 0, 0)
                    break

    def active(self):
        """FUN_8132554c."""
        if self.t == 0:
            self.layout()
        t = self.t
        if t < 20:
            for i in (0, 7, 15, 22):
                self.ra[i].x = self.ra[i].y = self.start
                self.a[i].alpha = clamp8(self.a[i].alpha + 6)
            self._copy()
        elif t < 40:
            for k in range(4):
                tx, ty = _corner(k)
                r = self.ra[self.corners[k]]
                r.x = hermite(t - 20, 0, 0, cdiv(tx, 20), 20, tx, 0)
                r.y = hermite(t - 20, 0, 0, cdiv(ty, 20), 20, ty, 0)
                c = self.a[self.corners[k]]
                c.alpha = clamp8(c.alpha + 6)
            self._copy()
        elif t < 70:
            self._lap(t - 40, 30)
        elif t < 225:
            self._flip(t - 70)
        elif t < 265:
            h = int(hermite(t - 225, 0, 80, 0, 40, 0, 0))
            for r in self.ra:
                r.dx = self._clamp(r.x, h)
        elif t < 305:
            u = t - 265
            h = int(hermite(u, 0, 0, 0, 40, 80, 0))
            if u == 0:
                self._toggle()
            for r in self.rb:
                r.dx = self._clamp(r.x, h)
        elif t < 397:
            self._bounce(t - 305)
        elif t < 519:
            self._bounce(t - 397)
        elif t < 559:
            h = int(hermite(t - 519, 0, 70, 0, 40, 0, 0))
            for r in self.rb:
                r.dy = self._clamp(r.y, h)
                r.dx = r.x
        elif t < 599:
            u = t - 559
            h = int(hermite(u, 0, 0, 0, 40, 80, 0))
            if u == 0:
                self._toggle()
            for r in self.ra:
                r.dy = self._clamp(r.y, h)
                r.dx = r.x
        self.t += 1
        if self.t > 599:
            self.t = 70

    def idle(self):
        """FUN_81325c30."""
        for c in self.a + self.b:
            c.alpha = clamp8(c.alpha - 10)
        self.t = 0

    def submenu(self):
        """FUN_81325db0."""
        n = 0
        for c, r in list(zip(self.a, self.ra)) + list(zip(self.b, self.rb)):
            c.alpha = clamp8(c.alpha - self.ALPHA_STEP)
            r.dx = towards_zero(r.dx, self.POS_STEP)
            r.dy = towards_zero(r.dy, self.POS_STEP)
            n += (r.dx == 0.0) + (r.dy == 0.0)
        self.done = n == 0x7A
        self.t = 0

    def place(self, cube_world):
        """FUN_81325960."""
        base = cube_world @ rot_trans(0, 0, 0, 0, -0x4000, 0)
        for c, r in zip(self.a, self.ra):
            c.mtx = base @ rot_trans(r.dx, r.dy, 0.0, r.rx, r.ry, 0)
            c.scale = self.scale_a
            c.color = self.COLOR
        for c, r in zip(self.b, self.rb):
            c.mtx = base @ trans(r.dx, r.dy, 0.0)
            c.scale = self.scale_b
            c.color = self.COLOR

    def draw_list(self):
        return [c for c in self.a + self.b if c.alpha > 0]


# ----------------------------------------------------------------------------
# Calendar face (right, state 7/8): a real-time analogue clock, FUN_81326114 .. FUN_81326fdc
# ----------------------------------------------------------------------------
class CalendarFace:
    HAND_COLOR = (40, 20, -50, 255)   # DAT_814d9f38 [0..3]
    MARK_COLOR = (80, 0, -60, 255)    # [4..7]
    POS_STEP = 6                      # +0x22
    ALPHA_STEP = 14                   # +0x24

    def __init__(self, clock):
        self.clock = clock            # callable -> (sec, min, hour)
        self.marks = [Cube() for _ in range(5)]
        self.minute = [Cube() for _ in range(4)]
        self.hour = [Cube() for _ in range(3)]
        self.second = Cube()
        self.p_marks = [[0.0, 0.0] for _ in range(5)]
        self.p_minute = [[0.0, 0.0] for _ in range(4)]
        self.p_hour = [[0.0, 0.0] for _ in range(3)]
        self.p_second = [0.0, 0.0]
        self.t = 0
        self.done = False
        self.hand_scale_m = rom.f32(0x8137EE64)
        self.hand_scale_h = rom.f32(0x8137EE70)

    def active(self):
        """FUN_813262e4."""
        sec, mnt, hr = self.clock()
        self.t = min(self.t + 1, 0x32)
        t = self.t
        for k, c in enumerate(self.marks):
            r = 0 if k == 0 else 100
            c.alpha = cdiv(min(t, 30) * 255, 30) & 0xFF
            a = s16((k - 1) * 0x4000)
            self.p_marks[k] = [r * sin16(a), r * cos16(a)]
        u = max(0, t - 0x28)
        a_min = cdiv(-(mnt << 16), 60) - cdiv(sec << 16, 3600)
        for k, c in enumerate(self.minute):
            c.alpha = cdiv(u * 255, 10) & 0xFF
            r = (k + 1) * 20 * u
            self.p_minute[k] = [r * sin16(a_min) / 10.0, r * cos16(a_min) / 10.0]
        a_hr = cdiv(-((hr % 12) << 16), 12) - cdiv(mnt << 16, 720)
        for k, c in enumerate(self.hour):
            c.alpha = cdiv(u * 255, 10) & 0xFF
            r = (k + 1) * 20 * u
            self.p_hour[k] = [r * sin16(a_hr) / 10.0, r * cos16(a_hr) / 10.0]
        self.second.alpha = cdiv(u * 255, 10) & 0xFF
        a_sec = cdiv(-(sec << 16), 60) - 3
        self.p_second = [100.0 * sin16(a_sec), 100.0 * cos16(a_sec)]

    def _all(self):
        return self.marks + self.minute + self.hour + [self.second]

    def idle(self):
        """FUN_81326de0."""
        n = 0
        for c in self._all():
            c.alpha -= 10
            if c.alpha < 0:
                c.alpha = 0
                n += 1
        if n == 13:
            self.t = 0

    def submenu(self):
        """FUN_81326fdc."""
        n = 0
        for c, p in zip(self.marks + self.minute + self.hour, self.p_marks + self.p_minute + self.p_hour):
            c.alpha = max(0, c.alpha - self.ALPHA_STEP)
            p[0] = towards_zero(p[0], self.POS_STEP)
            p[1] = towards_zero(p[1], self.POS_STEP)
            n += (p[0] == 0.0) + (p[1] == 0.0)
        self.second.alpha = max(0, self.second.alpha - self.ALPHA_STEP)
        self.p_second[0] = towards_zero(self.p_second[0], self.POS_STEP)
        self.p_second[1] = towards_zero(self.p_second[1], self.POS_STEP)
        n += (self.p_second[0] == 0.0) + (self.p_second[1] == 0.0)
        if n == 0x1A:
            self.t = 0
            self.done = True
        else:
            self.done = False

    def place(self, cube_world):
        """FUN_81326808."""
        base = cube_world @ rot_trans(0, 0, 0, 0, -0x4000, 0)
        grow = 0.7 * min(self.t, 30) / 30.0
        for c, p in zip(self.marks, self.p_marks):
            c.mtx, c.scale, c.color = base @ trans(p[0], p[1], 0.0), grow, self.MARK_COLOR
        for c, p in zip(self.minute, self.p_minute):
            c.mtx, c.scale, c.color = base @ trans(p[0], p[1], 0.0), self.hand_scale_m, self.HAND_COLOR
        for c, p in zip(self.hour, self.p_hour):
            c.mtx, c.scale, c.color = base @ trans(p[0], p[1], 0.0), self.hand_scale_h, self.HAND_COLOR
        u = max(0, self.t - 0x28)
        self.second.mtx = base @ trans(self.p_second[0], self.p_second[1], 0.0)
        self.second.scale = 0.55 * u / 10.0
        self.second.color = self.HAND_COLOR

    def draw_list(self):
        return [c for c in self._all() if c.alpha > 0]


def cube_matrix(c):
    return scale_cols(c.mtx, c.scale, c.scale, c.scale)
