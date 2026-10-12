"""The cube dot-matrix displays of the Calendar, Options and Game Play menus
(BS2 0x81329890..0x81337530).

Every "pixel" of the big displays is a small cube (model res 0x68). A CAFD file
(res 0x25) stores, for each of five displays, a Hermite path per pixel; the last
key of a path is the pixel's place on screen and the path is how it flies in.
Per-language index tables (res 0xB3..0xB8) group the pixels into digit cells,
labels, arrows... and ten glyph tables say which pixels of a 4x7 cell form 0..9.

Each pixel has five attributes that ease towards a target by 1 per frame
(FUN_8132bf20):  A highlight (scale / colour), B visible, C (unused here),
D how much of the ripple displacement applies, E colour mix. The menu logic sets
targets for whole groups; FUN_8132bd00 then turns them into alpha, offset and the
two TEV colours of every cube.
"""
import random
import struct

import moderngl
import numpy as np

from .. import rom
from ..mathx import hermite, sin16, cos16
from .block import block
from .faces import cdiv

N = 0x861
RES_CAFD = 0x25
RES_GROUPS = [0xB4, 0xB6, 0xB5, 0xB8, 0xB7, 0xB3]      # FUN_8130bad0(lang)
RES_CUBE = 0x68


def _res(i):
    from ..render import res_bytes
    return res_bytes(i)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
class Paths:
    """CAFD: five displays, one Hermite path (pos + in/out tangents per key) per pixel."""

    def __init__(self, data):
        nset, koff, toff = struct.unpack_from(">III", data, 4)
        nkeys_total = (toff - koff) // 0x24
        keys = np.frombuffer(data, ">f4", nkeys_total * 9, koff).reshape(-1, 9).astype(np.float64)
        self.pos, self.tin, self.tout = keys[:, 0:3], keys[:, 3:6], keys[:, 6:9]
        self.sets = []
        for m in range(nset):
            o = toff + 8 * m
            cnt, nk, rel = struct.unpack_from(">HHI", data, o)
            rec = o + rel
            ab = np.zeros((cnt, 2))
            idx = np.zeros((cnt, nk), np.int64)
            for i in range(cnt):
                a, b, _, _, r2 = struct.unpack_from(">BBBBi", data, rec + 8 * i)
                ab[i] = (a, b)
                idx[i] = struct.unpack_from(f">{nk}H", data, rec + 8 * i + r2)
            self.sets.append((cnt, nk, ab, idx))

    def evaluate(self, m, t):
        """FUN_813298b0: positions of display m at progress t (0..1)."""
        cnt, nk, ab, idx = self.sets[m]
        t = min(1.0, max(0.0, t))
        dt = 1.0 / (nk - 1)
        lo = 0.4 * (1.0 - ab[:, 0] / 255.0)
        hi = 0.6 + 0.4 * (1.0 - ab[:, 1] / 255.0)
        u = (t - lo) / (hi - lo)
        seg = np.clip(np.trunc(u / dt).astype(np.int64), 0, nk - 2)
        u = np.clip(u, 0.0, 1.0)
        rows = np.arange(cnt)
        k0 = idx[rows, seg]
        k1 = idx[rows, seg + 1]
        t0 = seg * dt
        t1 = (seg + 1) * dt
        uu = (u - t0)[:, None]
        inv = (1.0 / (t1 - t0))[:, None]
        # FUN_81307d08 per component, tangents in units of t
        a = inv * uu * uu
        b = uu * a * inv
        c = 3.0 * a * inv
        p0, m0 = self.pos[k0], self.tout[k0]
        p1, m1 = self.pos[k1], self.tin[k1]
        out = (m1 * (b - a) + m0 * (uu + (b - 2.0 * a))
               + p0 * (1.0 + (2.0 * b * inv - c)) + p1 * (-2.0 * b * inv + c))
        before = (u < t0)[:, None]
        after = (u > t1)[:, None]
        out = np.where(before, p0, np.where(after, p1, out))
        return out


class Groups:
    """One language's index tables: group k = (u16 count, u16 offset) at 4k."""

    def __init__(self, data):
        self.data = data
        self.cache = {}

    def __getitem__(self, k):
        if k not in self.cache:
            cnt, off = struct.unpack_from(">HH", self.data, 4 * k)
            self.cache[k] = np.array(struct.unpack_from(f">{cnt}H", self.data, 4 * k + off), np.int64)
        return self.cache[k]

    def glyph(self, digit):
        """FUN_81332d7c: the cell pixels that form a digit (groups 30..39)."""
        return self[30 + digit]


def split4(v):
    """FUN_8132c660: (ones, tens, hundreds, thousands)."""
    if v < 0 or v > 9999:
        return [0, 0, 0, 0]
    th = v // 1000 if v >= 1000 else 0
    v -= th * 1000
    hu = v // 100 if v >= 100 else 0
    v -= hu * 100
    te = v // 10 if v >= 10 else 0
    v -= te * 10
    return [v, te, hu, th]


def wave(a, x, lo, hi):
    """FUN_81334b40: up from lo to hi at a, back down to lo at 1."""
    if a <= x:
        return hermite(x, a, hi, 0.0, 1.0, lo, 0.0)
    return hermite(x, 0.0, lo, 0.0, a, hi, 0.0)


def deg16(d):
    """FUN_8132cb60."""
    return int(65535.0 * d / 360.0)


def rot_xyz(rx, ry, rz):
    """FUN_81307510 / the rotation part of FUN_813073b4."""
    sx, cx = sin16(rx), cos16(rx)
    sy, cy = sin16(ry), cos16(ry)
    sz, cz = sin16(rz), cos16(rz)
    return np.array([
        [cz * cy, -sz * cx + sx * (cz * sy), sz * sx + cx * (cz * sy)],
        [sz * cy, cz * cx + sx * (sz * sy), -cz * sx + cx * (sz * sy)],
        [-sy, cy * sx, cy * cx]])


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------
class DotMatrix:
    def __init__(self, language=lambda: 0):
        self.p = block()
        self.language = language
        self.paths = Paths(_res(RES_CAFD))
        self.lang_groups = [Groups(_res(r)) for r in RES_GROUPS]
        # attributes: current / target (DAT_814de638 + k * 0x2184)
        self.cur = np.zeros((5, N), np.int32)
        self.tgt = np.zeros((5, N), np.int32)
        self.progress = 0          # DAT_814e8dcc
        self.progress_to = 0       # DAT_814e8dce
        self.s = [0] + [0x7F] * 13     # DAT_814e8dd0 (this frame's request)
        self.ps = [0] + [0x7F] * 13    # DAT_814e8dec (last frame's)
        # this frame's cubes
        self.count = 0             # G_814ad5f0
        self.scale = np.ones((N, 3))
        self.rot = np.zeros((N, 3), np.int32)
        self.pos = np.zeros((N, 3))
        self.alpha = np.zeros(N, np.int32)       # DAT_814f9a28
        self.col0 = np.zeros((N, 4))             # DAT_814fa28c (TEV C0: the face)
        self.col1 = np.zeros((N, 4))             # DAT_814fc410 (TEV C1: the edges)
        self.extra = []            # appended cubes: (scale, rot, pos, alpha, col0, col1)
        # ripple (DAT_815425a0: per cube rotation + offset)
        self.disp_vec = np.zeros((N, 3))
        self.disp_rot = np.zeros((N, 3), np.int32)
        self.ripple_t = 0          # G_814ad610
        self.ripple_u = 0          # G_814ad614
        self.sway_t = 0            # G_814ad5d0
        self.colors = [(0, 0, 0, 255)] * 6       # G_814ad5d8.. a0 a1 b0 b1 c0 c1
        # digit roll while editing (FUN_813355e0)
        self.roll = {"618": 0, "61a": 0, "61c": 0, "61e": 0, "620": 0, "622": 0}
        self.roll_dir = np.zeros(3)
        self.roll_speed = np.zeros(0x70)

    # -- helpers ---------------------------------------------------------------
    @property
    def g(self):
        return self.lang_groups[self.language()]

    def set_attr(self, k, idx, on):
        """FUN_8132cdd4 (A) / ce3c (C) / cea4 (D) / cf14 (E) / cf7c (B, also snaps)."""
        idx = np.asarray(idx, np.int64)
        idx = idx[idx < N]
        top = self.p.h(0xAA) if k in (0, 3) else self.p.h(0xA8)
        self.tgt[k, idx] = top if on else 0
        if k == 1:
            self.cur[k, idx] = self.tgt[k, idx]

    A, B, C, D, E = 0, 1, 2, 3, 4

    def A_(self, idx, on):
        self.set_attr(self.A, idx, on)

    def B_(self, idx, on):
        self.set_attr(self.B, idx, on)

    def C_(self, idx, on):
        self.set_attr(self.C, idx, on)

    def D_(self, idx, on):
        self.set_attr(self.D, idx, on)

    def E_(self, idx, on):
        self.set_attr(self.E, idx, on)

    def reset(self):
        """memset(DAT_814de638, 0, 0xa798): attributes and progress."""
        self.cur[:] = 0
        self.tgt[:] = 0
        self.progress = 0
        self.progress_to = 0

    def prog(self):
        """FUN_8132ccf4."""
        return self.progress / self.p.h(0xD6)

    def request(self, **fields):
        """FUN_8132d10c / d194 / d084: remember last frame's request, store this one."""
        self.ps = list(self.s)
        self.s = [0x7F] * 14
        for k, v in fields.items():
            self.s[int(k[1:])] = v

    def centroid(self, idx=None):
        """FUN_8132c830."""
        if idx is None:
            return self.pos[:self.count].mean(0)
        return self.pos[idx].mean(0)

    def zoom_group(self, idx, off=(0.0, 0.0), s=(1.0, 1.0, 1.0), c=None):
        """The block repeated through FUN_8132d234 / defc / f010: shift, then scale about the centroid."""
        idx = np.asarray(idx, np.int64)
        if len(idx) == 0:
            return
        if c is None:
            c = self.centroid(idx)
        self.pos[idx, 0] += off[0]
        self.pos[idx, 1] += off[1]
        s = np.asarray(s, float)
        if s.ndim == 1:
            self.pos[idx] = c + s * (self.pos[idx] - c)
            self.scale[idx] *= s
        else:
            self.pos[idx] = c + s * (self.pos[idx] - c)
            self.scale[idx] *= s

    def grow(self, idx, factor, c=None):
        """s = 1 + A/aa * factor per cube (the highlight zoom)."""
        idx = np.asarray(idx, np.int64)
        if len(idx) == 0:
            return
        if c is None:
            c = self.centroid(idx)
        s = 1.0 + self.cur[self.A, idx] / self.p.h(0xAA) * factor
        s = np.repeat(s[:, None], 3, 1)
        self.pos[idx] = c + s * (self.pos[idx] - c)
        self.scale[idx] *= s

    def load_set(self, m):
        """FUN_813298b0 at the current progress."""
        cnt = self.paths.sets[m][0]
        self.count = cnt
        self.pos[:cnt] = self.paths.evaluate(m, self.prog())
        self.rot[:cnt] = 0
        self.scale[:cnt] = self.p.f(0xAC)

    def reset_colors(self):
        """FUN_8132fff0."""
        self.col0[:] = self.colors[0]
        self.col1[:] = self.colors[1]
        self.tgt[self.D, :] = 0
        self.tgt[self.E, :] = 0

    def set_colors(self, offs):
        """G_814ad5d8..5ef from six (r, g, b) triples in the menu block."""
        out = []
        for o in offs:
            out.append((self.p.h(o) & 0xFF, self.p.h(o + 4) & 0xFF, self.p.h(o + 8) & 0xFF, 255))
        # order in the IPL: a0 (o0, o0+4, o0+8), a1 (o0+2, ...): see callers
        self.colors = out

    def ripple_wave(self):
        """FUN_8133498c."""
        p = self.p
        t = self.ripple_t / p.h(0xC8)
        u5 = (t + p.f(0xC4) * 0.5) % 1.0
        d8 = t % 1.0
        u6 = (0.25 + 2.0 * d8) % 1.0
        d8b = (0.25 + t) % 1.0
        dx = wave(p.f(0xC4), u5, -p.f(0xB4), p.f(0xB4))
        dy = wave(0.5, u6, -p.f(0xB8), p.f(0xB8))
        rx = deg16(wave(0.5, d8b, -p.f(0xBC), p.f(0xBC)))
        ry = deg16(wave(0.5, d8b, -p.f(0xC0), p.f(0xC0)))
        return dx, dy, rx, ry

    def ripple_groups(self, groups):
        """FUN_81333898 / FUN_81333c7c: each group sways about its own centre."""
        dx, dy, rx, ry = self.ripple_wave()
        R = rot_xyz(0, rx, ry)
        for idx in groups:
            idx = np.asarray(idx, np.int64)
            if len(idx) == 0:
                continue
            c = self.centroid(idx)
            v = self.pos[idx] - c
            w = v @ R.T
            self.disp_vec[idx] = w + c - self.pos[idx]
            self.disp_vec[idx, 0] += dx
            self.disp_vec[idx, 1] += dy
            self.disp_rot[idx] = (0, rx, ry)
        self.ripple_step()

    def ripple_step(self):
        """FUN_81334928."""
        self.ripple_t += 1
        self.ripple_u += 1
        if self.ripple_t >= self.p.h(0xC8):
            self.ripple_t = 0
        if self.ripple_u > 99:
            self.ripple_u = 0

    def finish(self):
        """FUN_8132bd00: alpha, ripple displacement and colours of every cube."""
        p = self.p
        n = self.count
        pr = self.prog()
        Bn = self.cur[self.B, :n] / p.h(0xA8)
        Dn = self.cur[self.D, :n] / p.h(0xAA)
        An = self.tgt[self.A, :n] / p.h(0xAA)
        En = self.cur[self.E, :n] / p.h(0xA8)
        self.alpha[:n] = (255.0 * (pr * Bn)).astype(np.int32)
        self.pos[:n] += self.disp_vec[:n] * Dn[:, None]
        self.rot[:n] = (self.disp_rot[:n] * Dn[:, None]).astype(np.int32)
        a0, a1, b0, b1, c0, c1 = (np.array(c, float) for c in self.colors)
        A_ = An[:, None]
        E_ = En[:, None]
        self.col0[:n] = np.floor(A_ * a0 + (1 - A_) * (E_ * b0 + (1 - E_) * c0))
        self.col1[:n] = np.floor(A_ * a1 + (1 - A_) * (E_ * b1 + (1 - E_) * c1))

    def step(self):
        """FUN_8132bf20: every attribute moves one step towards its target."""
        n = self.count
        d = np.sign(self.tgt[:, :n] - self.cur[:, :n])
        self.cur[:, :n] += d
        if self.progress < self.progress_to:
            self.progress += 1
        elif self.progress > self.progress_to:
            self.progress -= 1

    # -- the Calendar display (FUN_8132a14c) --------------------------------------
    def calendar(self, substate, cursor, date_field, time_field, values, now):
        """values = DAT_814de600 (the three numbers being edited), now = datetime."""
        p = self.p
        self.request(s0=2, s7=substate, s8=cursor, s9=date_field, s10=time_field,
                     s11=values[0], s12=values[1], s13=values[2])
        if self.ps[0] != 2:
            self.reset()
            self.progress_to = p.h(0xD6)
            self.ripple_t = self.ripple_u = 0
            self.disp_vec[:] = 0
            self.disp_rot[:] = 0
        # G_814ad5d8.. : (15a,15e,162) (15c,160,164) (166,16a,16e) (168,16c,170) (172,176,17a) (174,178,17c)
        self.colors = [self._rgb(0x15A), self._rgb(0x15C), self._rgb(0x166), self._rgb(0x168),
                       self._rgb(0x172), self._rgb(0x174)]
        self.reset_colors()
        self._calendar_digits(now)
        self._calendar_highlight()
        self.load_set(0)
        self._calendar_layout()
        if p.f(0xD8) < self.prog():
            g = self.g
            self.ripple_groups([g[k] for k in (2, 3, 4, 5, 23, 24, 25, 26, 17, 18, 8, 9, 11, 12, 14, 15, 20, 21)])
        self.finish()
        self.extra = []
        self._calendar_roll()
        if self.s[7] != 0 and self.ps[7] == 0:
            self.roll["620"] = self.roll["622"] = p.h(0xD4)     # FUN_81334c28
        self.step()

    # -- digits rolling while a field is edited (FUN_813355e0 / FUN_81336650 / FUN_8133682c)
    ROLL_DIRS = {   # (wrap, normal) direction vectors per field
        "year": (0x8137EF9C, 0x8137EFA8), "month": (0x8137EFB4, 0x8137EFC0), "day": (0x8137EFCC, 0x8137EFD8),
        "hour": (0x8137EFE4, 0x8137EFF0), "minute": (0x8137EFFC, 0x8137F008), "second": (0x8137F014, 0x8137F020),
    }

    def _roll_start(self, d, old, new):
        """FUN_81336650."""
        if old == new:
            return
        d = np.asarray(d, float)
        self.roll_dir = -d if (new - old) < 1 else d
        r = np.array([random.getrandbits(16) % 600 + 200 for _ in range(0x70)], float)
        self.roll_speed = np.trunc(self.p.f(0xCC) * r / 10.0)
        o = split4(abs(old))
        n = split4(abs(new))
        if o[1] != n[1]:
            self.roll["622"] = 0
            self.roll["61e"] = o[1]
            self.roll["61a"] = n[1]
        if o[0] != n[0]:
            self.roll["61c"] = o[0]
            self.roll["620"] = 0
            self.roll["618"] = n[0]

    def _calendar_roll(self):
        p, g, s, ps, R = self.p, self.g, self.s, self.ps, self.roll
        if s[7] == 0:
            return
        cur = self._values_for(s)
        if ps[7] == 0:
            v = cur[s[9]] if s[7] == 1 else cur[s[10] + 3]
            d = split4(v)
            R["61c"] = R["618"] = d[0]
            R["61e"] = R["61a"] = d[1]
            return
        prev = self._values_for(ps)
        if s[7] == 1:
            f = s[9]
            name, i, tens, ones, wrap = {
                0: ("year", 0, 25, 26, (2099, 2000)),
                1: ("month", 1, 17, 18, (12, 1)),
                2: ("day", 2, 8, 9, None)}.get(f, (None, None, None, None, None))
        else:
            f = s[10]
            name, i, tens, ones, wrap = {
                0: ("hour", 3, 11, 12, (23, 0)),
                1: ("minute", 4, 14, 15, (59, 0)),
                2: ("second", 5, 20, 21, (59, 0))}.get(f, (None, None, None, None, None))
        if name is None:
            return
        a, b = cur[i], prev[i]
        if name == "day":
            big = lambda x: x == 31 or 28 <= x <= 30
            wrapped = (big(a) and b == 1) or (a == 1 and big(b))
        else:
            wrapped = (a, b) in (wrap, wrap[::-1])
        dw, dn = self.ROLL_DIRS[name]
        self._roll_start(rom.f32(dw if wrapped else dn, 3), b, a)
        field_now = s[9] if s[7] == 1 else s[10]
        field_was = ps[9] if s[7] == 1 else ps[10]
        if field_now != field_was:
            R["620"] = R["622"] = p.h(0xD4)
            d = split4(a)
            R["618"] = R["61c"] = d[0]
            R["61a"] = R["61e"] = d[1]
        self._roll_cubes(g[tens], g[ones])

    def _values_for(self, st):
        y, mo, d, h, mi, se = self._now_parts
        if st[7] == 1:
            y, mo, d = st[11], st[12], st[13]
        elif st[7] == 2:
            h, mi, se = st[11], st[12], st[13]
        return [y, mo + 1, d, h, mi, se]

    def _roll_cubes(self, tens, ones):
        """The four 28-cube sets: old tens out, new tens in, old ones out, new ones in."""
        p, g, R = self.p, self.g, self.roll
        n = len(tens)
        t_tens = R["622"] / p.h(0xD4)
        t_ones = R["620"] / p.h(0xD4)
        sp = self.roll_speed
        out_pos, out_rot, out_sc = [], [], []
        alpha = np.zeros(4 * n, np.int32)
        k = p.f(0xD0)
        specs = ((tens, R["61e"], -k * t_tens, 255 * (1 - t_tens)),
                 (tens, R["61a"], k * (1 - t_tens), 255 * t_tens),
                 (ones, R["61c"], -t_ones, 255 * (1 - t_ones)),
                 (ones, R["618"], 1 - t_ones, 255 * t_ones))
        for j, (cell, digit, amount, a) in enumerate(specs):
            speed = sp[j * n:(j + 1) * n]
            out_pos.append(self.pos[cell] + self.roll_dir * (speed * amount)[:, None])
            out_rot.append(self.rot[cell])
            out_sc.append(self.scale[cell])
            alpha[g.glyph(digit)[:n] + j * n] = int(a)
        m = 4 * n
        self.extra.append((np.concatenate(out_sc), np.concatenate(out_rot), np.concatenate(out_pos), alpha,
                           np.tile(np.array(self.colors[0], float), (m, 1)),
                           np.tile(np.array(self.colors[1], float), (m, 1))))
        top = p.h(0xD4)
        R["620"] = min(top, R["620"] + 1)
        R["622"] = min(top, R["622"] + 1)

    def _rgb(self, off):
        p = self.p
        return (p.h(off) & 0xFF, p.h(off + 4) & 0xFF, p.h(off + 8) & 0xFF, 255)

    def _datetime_values(self, now):
        """FUN_8132a690: [year, month, day, hour, minute, second], the edited ones substituted."""
        self._now_parts = (now.year, now.month - 1, now.day, now.hour, now.minute, now.second)
        return self._values_for(self.s)

    def _light(self, cell, digit):
        g = self.g
        cell = g[cell]
        glyph = g.glyph(digit)
        n = len(cell)
        self.B_(cell[glyph[:n]], True)

    def _calendar_digits(self, now):
        """FUN_8132b114."""
        g = self.g
        v = self._datetime_values(now)
        self.B_(g[6], False)
        self.B_(g[0], False)
        d = split4(v[0])
        self._light(23, d[3])
        self._light(24, d[2])
        self._light(25, d[1])
        self._light(26, d[0])
        d = split4(v[1])
        self._light(17, d[1])
        self._light(18, d[0])
        d = split4(v[2])
        self._light(8, d[1])
        self._light(9, d[0])
        d = split4(v[3])
        self._light(11, d[1])
        self._light(12, d[0])
        d = split4(v[4])
        self._light(14, d[1])
        self._light(15, d[0])
        d = split4(v[5])
        self._light(20, d[1])
        self._light(21, d[0])
        self.B_(g[1], True)

    def _calendar_highlight(self):
        """FUN_8132b678."""
        g, s, ps = self.g, self.s, self.ps
        if s[8] == 0:
            self.A_(g[6], False), self.D_(g[6], True), self.E_(g[6], True)
            self.A_(g[0], False), self.D_(g[0], False)
        else:
            self.A_(g[6], False), self.D_(g[6], False)
            self.A_(g[0], False), self.D_(g[0], True), self.E_(g[0], True)

        def pick(idx, hide):
            if hide:
                self.B_(idx, False)
            self.C_(idx, True), self.A_(idx, True), self.D_(idx, False)

        if s[7] == 1:
            if self.p.h(0x17E) == 0:
                self.D_(g[6], False)
            f = s[9]
            if f == 1:
                pick(g[16], ps[7] == 1)
            elif f == 0:
                pick(g[23], False), pick(g[24], False)
                pick(g[25], ps[7] == 1), pick(g[26], ps[7] == 1)
            elif f == 2:
                pick(g[7], ps[7] == 1)
        elif s[7] == 2:
            if self.p.h(0x17E) == 0:
                self.D_(g[0], False)
            f = s[10]
            if f == 1:
                pick(g[13], ps[7] == 2)
            elif f == 0:
                pick(g[10], ps[7] == 2)
            elif f == 2:
                pick(g[19], ps[7] == 2)
        else:
            self.C_(g[6], True)
            self.C_(g[0], True)

    def _calendar_layout(self):
        """FUN_8132defc."""
        p, g, s = self.p, self.g, self.s
        self.zoom_group(g[6], (p.f(0x7C), p.f(0x80)), (p.f(0x84),) * 3)
        self.zoom_group(g[0], (p.f(0x8C), p.f(0x90)), (p.f(0x94),) * 3)
        if s[7] == 1:
            k = {1: 16, 0: 22, 2: 7}.get(s[9])
            if k is not None:
                self.grow(g[k], p.f(0x88))
        elif s[7] == 2:
            k = {1: 13, 0: 10, 2: 19}.get(s[10])
            if k is not None:
                self.grow(g[k], p.f(0x98))

    # -- the Options display (FUN_81329ce4) ---------------------------------------
    def options(self, sub, cursor, offset, sound, lang_sel, lang_now):
        """sub 0 choose / 1 sound / 2 screen position / 3 language; lang_sel = G_814ad59a."""
        p = self.p
        if self.lang_sel_seen != lang_sel:
            self._underline_to(self.lang_sel_seen, lang_sel, lang_sel)
        self.lang_sel_seen = lang_sel
        self.request(s0=1, s3=sub, s4=cursor, s5=offset, s6=sound)
        if self.ps[0] != 1:
            self.bars_state = self.bars_t = 0
            self.ripple_t = self.ripple_u = 0
            self.disp_vec[:] = 0
            self.disp_rot[:] = 0
            self.reset()
            self.sway_h = [0, 0.0, 0.0]
            self.progress_to = p.h(0xD6)
            self.sway_t = 0
            self.lang_sel_seen = lang_sel
            self._underline_to(lang_sel, lang_sel, lang_sel)
            self.morph = [lang_sel, lang_sel, 10, 0.0]
        self.colors = [self._rgb(0x136), self._rgb(0x138), self._rgb(0x142), self._rgb(0x144),
                       self._rgb(0x14E), self._rgb(0x150)]
        self.reset_colors()
        self._opt_visible()
        self._opt_cursor(lang_sel)
        self._opt_A()
        self._opt_D()
        self.load_set(2)
        self._opt_arrows(lang_sel)
        self._opt_rows()
        if p.f(0xD8) < self.prog():
            g = self.g
            self.ripple_groups([g[40], g[46], g[47], g[54], g[43]])
        self.finish()
        self.extra = []
        self._bars()
        self._offset_roll()
        if self.s[3] == 2 and self.ps[3] != 2:
            self.roll["620"] = self.roll["622"] = p.h(0xD4)
        self._sound_morph()
        if self.s[3] == 1 and self.ps[3] != 1:
            self.morph_t = p.h(0xD4)
        if self.s[3] == 3:
            self._lang_morph()
        self.step()
        # FUN_8132bf20 also walks DAT_814fe598 and DAT_814fe828 towards 10
        self.morph[2] += (self.morph[2] < 10) - (self.morph[2] > 10)
        self.sway_h[0] += (self.sway_h[0] < 10) - (self.sway_h[0] > 10)
        self.sway_t += 1
        if p.h(0xEC) <= self.sway_t:
            self.sway_t = 0

    lang_sel_seen = 0
    sway_h = [10, 0.0, 0.0]        # DAT_814fe828 (counter to 10), fe82c (from), fe830 (to)
    morph = [0, 0, 10, 0.0]        # DAT_814fe594 old, fe596 new, fe598 counter, fe59c direction
    morph_rand = np.zeros(0x280)
    bars_state = 0                 # G_814ad608
    bars_t = 0                     # G_814ad60a
    morph_t = 0                    # G_814ad628
    morph_old = 0                  # G_814ad62a
    morph_new = 0                  # G_814ad62c
    morph_speed = np.zeros(0x596)
    morph_dir = np.zeros(3)

    def _underline_to(self, old, new, sel):
        """FUN_8132dd84: the language underline glides to the chosen name; FUN_813306fc starts the word morph."""
        nudge = struct.unpack_from(">i", self.p.b, 0x64 + 4 * new)[0]     # menu block +0x64.. (s32 per language)
        self.sway_h = [0, self._sway_value(), float(nudge + self.lang_x[sel])]
        self.morph = [old, new, 0, 1.0 if not old < new else -1.0]
        self.morph_rand = np.array([random.getrandbits(16) % 60 + 20 for _ in range(0x280)], float)

    lang_x = [0] * 6               # DAT_814de5f0: x of lan1..lan6 in the language layout

    def _sway_value(self):
        """FUN_8132de98."""
        h = getattr(self, "sway_h", [10, 0.0, 0.0])
        return hermite(h[0], 0.0, h[1], 0.0, 10.0, h[2], 0.0)

    def _arrow(self, k, on):
        """FUN_8132eeb0: arrows fade (B target only), always bright."""
        idx = self.g[48 + k] if k < 4 else self.g[52 + (k - 4)]
        idx = idx[idx < N]
        self.tgt[self.C, idx] = self.p.h(0xA8) if on else 0
        self.tgt[self.B, idx] = self.p.h(0xA8) if on else 0
        self.tgt[self.E, idx] = self.p.h(0xA8)
        self.tgt[self.A, idx] = self.p.h(0xAA)

    def _opt_visible(self):
        """FUN_8132adcc."""
        g, s = self.g, self.s
        self.B_(g[45], False)
        self.B_(g[54], False)
        self.B_(g[43], True)
        if s[6] == 1:
            self.B_(g[55], True)
            self._arrow(0, True), self._arrow(1, False), self._arrow(2, False), self._arrow(3, False)
        elif s[6] == 0:
            self.B_(g[44], True)
            self._arrow(0, False), self._arrow(1, True), self._arrow(2, False), self._arrow(3, False)
        if s[5] < 0:
            self.B_(g[41], True)
        elif s[5] > 0:
            self.B_(g[42], True)
        d = split4(abs(s[5]))
        tens, ones = g[46], g[47]
        self.B_(tens[g.glyph(d[1])[:len(tens)]], True)
        self.B_(ones[g.glyph(d[0])[:len(ones)]], True)

    def _opt_cursor(self, lang_sel):
        """FUN_8132a75c."""
        g, s, ps = self.g, self.s, self.ps
        row = {1: 45, 0: 54, 2: 43}.get(s[4])
        if row is not None:
            self.E_(g[row], True)
        if s[3] == 2:
            self.C_(g[54], False), self.C_(g[45], True), self.C_(g[43], False)
            if ps[3] == 2:
                self.B_(g[46], False), self.B_(g[47], False)
            self._arrow(0, False), self._arrow(1, False)
            self._arrow(2, s[5] >= -0x1F)
            self._arrow(3, s[5] < 0x20)
            self._arrow(4, False), self._arrow(5, False)
        elif s[3] == 0:
            self.C_(g[54], True), self.C_(g[45], True), self.C_(g[43], True)
            for k in range(6):
                self._arrow(k, False)
        elif s[3] == 1:
            self.C_(g[54], True), self.C_(g[45], False), self.C_(g[43], False)
            if s[6] == 1:
                self._arrow(0, True), self._arrow(1, False)
            elif s[6] == 0:
                self._arrow(0, False), self._arrow(1, True)
            for k in range(2, 6):
                self._arrow(k, False)
            if ps[3] == 1:
                self.B_(g[54], False)
        elif s[3] == 3:
            self.C_(g[54], False), self.C_(g[45], False), self.C_(g[43], True)
            for k in range(4):
                self._arrow(k, False)
            self._arrow(4, lang_sel != 0)
            self._arrow(5, lang_sel != 5)

    def _opt_A(self):
        """FUN_8132c024."""
        g, s = self.g, self.s
        on = {2: 45, 1: 54, 3: 43}.get(s[3])
        for k in (54, 45, 43):
            self.A_(g[k], k == on)

    def _opt_D(self):
        """FUN_8132c2fc."""
        g, s = self.g, self.s
        on = {1: 45, 0: 54, 2: 43}.get(s[4]) if s[3] == 0 else None
        for k in (54, 45, 43):
            self.D_(g[k], k == on)

    def _tri(self):
        """FUN_8132ff6c."""
        u = self.sway_t / self.p.h(0xEC)
        return 2.0 * (1.0 - u) if u >= 0.5 else 2.0 * u

    def _opt_arrows(self, lang_sel):
        """FUN_8132f010: arrows beside each row, swaying in and out."""
        p, g = self.p, self.g
        c54 = self.centroid(g[54]) + (p.f(0x34), p.f(0x38), 0.0)
        c45 = self.centroid(g[45]) + (p.f(0x44), p.f(0x48), 0.0)
        c43 = self.centroid(g[43]) + (p.f(0x54), p.f(0x58), 0.0)
        h = hermite(self._tri(), 0.0, 0.0, 0.0, 1.0, 1.0, 0.0)
        specs = ((48, c54, 0x34, 0x3C, 0x40, -1, 0xF4, 0xF8),
                 (49, c54, 0x34, 0x3C, 0x40, +1, None, 0x100),
                 (50, c45, 0x44, 0x4C, 0x50, -1, 0x104, 0x108),
                 (51, c45, 0x44, 0x4C, 0x50, +1, 0x10C, 0x110),
                 (52, c43, 0x54, 0x5C, 0x60, -1, 0x114, 0x118),
                 (53, c43, 0x54, 0x5C, 0x60, +1, 0x11C, 0x120))
        for k, c, off, sc, grow, side, xo, yo in specs:
            idx = g[k]
            self.zoom_group(idx, (p.f(off), p.f(off + 4)), (p.f(sc),) * 3, c=c)
            self.zoom_group(idx, (0.0, 0.0), (1.0 + p.f(grow),) * 3, c=c)
            self.pos[idx, 0] += side * p.f(0xF0) * h
            if xo is None:      # the right arrow of the sound row sits further out for Italian
                self.pos[idx, 0] += -40.0 if lang_sel == 4 else p.f(0xFC)
            else:
                self.pos[idx, 0] += p.f(xo)
            self.pos[idx, 1] += p.f(yo)

    def _opt_rows(self):
        """FUN_8132d234."""
        p, g = self.p, self.g
        pr = self.prog()
        self.zoom_group(g[54], (p.f(0x34) * pr, p.f(0x38) * pr), (p.f(0x3C),) * 3)
        self.zoom_group(g[45], (p.f(0x44) * pr, p.f(0x48) * pr), (p.f(0x4C),) * 3)
        c = self.centroid(g[43])
        c = np.array([5.0, -135.0, c[2]])
        self.zoom_group(g[43], (p.f(0x54) * pr, p.f(0x58) * pr), (p.f(0x5C),) * 3, c=c)
        self.grow(g[54], p.f(0x40))
        self.grow(g[45], p.f(0x50))
        idx = g[43]
        c = self.centroid(idx)
        self.grow(idx, p.f(0x60), c=c)
        u = min(1.0, max(0.0, (pr - 0.9) / 0.1))
        self.pos[idx, 0] += u * (-271.0 + 0.9155405405405406 * self._sway_value())

    def _bars(self):
        """FUN_81333384 + FUN_813334ac: two vertical rulers while the screen position is edited."""
        p, s, ps = self.p, self.s, self.ps
        self.bars_t += 1
        if self.bars_state == 2:
            if p.h(0x126) <= self.bars_t:
                self.bars_t = 0
                self.bars_state = 0
        elif self.bars_state == 1:
            if p.h(0x124) <= self.bars_t:
                self.bars_t = p.h(0x124)
        if s[3] != ps[3]:
            if s[3] == 0:
                if ps[3] == 2:
                    self.bars_state, self.bars_t = 2, 0
                else:
                    self.bars_state = 0
            elif s[3] == 2:
                self.bars_state, self.bars_t = 1, 0
            else:
                self.bars_state = 0
        if self.bars_state == 0:
            return
        if self.bars_state == 2:
            t = 1.0 - self.bars_t / p.h(0x126)
            a = int(255.0 - 255.0 * self.bars_t / p.h(0x126))
            m = 4
        else:
            t = self.bars_t / p.h(0x124)
            a = int(255.0 * self.bars_t / p.h(0x124))
            m = 3
        pos = self.paths.evaluate(m, t)
        n = len(pos)
        self.extra.append((np.full((n, 3), p.f(0xAC)), np.zeros((n, 3), np.int32), pos,
                           np.full(n, a, np.int32), np.tile(np.array(self.colors[0], float), (n, 1)),
                           np.tile(np.array(self.colors[1], float), (n, 1))))

    def _offset_roll(self):
        """FUN_81334c60 / FUN_813368f0."""
        s, ps, R, g = self.s, self.ps, self.roll, self.g
        if s[3] != 2:
            return
        if ps[3] != 2:
            d = split4(abs(s[5]))
            R["618"] = R["61c"] = d[0]
            R["61a"] = R["61e"] = d[1]
            return
        self._roll_start(rom.f32(0x8137EF90, 3), ps[5], s[5])
        self._roll_cubes(g[46], g[47])

    def _sound_morph(self):
        """FUN_813369b0: "Mono" and "Stereo" slide past each other."""
        p, s, ps, g = self.p, self.s, self.ps, self.g
        if not (s[3] == 1 and ps[3] == 1):
            self.morph_old = self.morph_new = s[6]
            return
        if ps[6] != s[6]:
            d = rom.f32(0x8137F03C if s[6] == 0 else 0x8137F030, 3)
            self.morph_dir = np.asarray(d, float)
            r = np.array([random.getrandbits(16) % 600 + 200 for _ in range(0x596)], float)
            self.morph_speed = np.trunc(p.f(0xCC) * r / 10.0)
            self.morph_old = ps[6]
            self.morph_t = 0
            self.morph_new = s[6]
        t = self.morph_t / p.h(0xD4)
        word = lambda v: g[55] if v == 1 else g[44]
        a_idx, b_idx = word(self.morph_old), word(self.morph_new)
        na, nb = len(a_idx), len(b_idx)
        sp = self.morph_speed
        pos_a = self.pos[a_idx] - self.morph_dir * (sp[:na] * t)[:, None]
        pos_b = self.pos[b_idx] + self.morph_dir * (sp[na:na + nb] * (1.0 - t))[:, None]
        al = np.concatenate([np.full(na, int(255 * (1.0 - t))), np.full(nb, int(255 * t))]).astype(np.int32)
        idx = np.concatenate([a_idx, b_idx])
        n = len(idx)
        self.extra.append((self.scale[idx].copy(), self.rot[idx].copy(), np.concatenate([pos_a, pos_b]), al,
                           np.tile(np.array(self.colors[0], float), (n, 1)),
                           np.tile(np.array(self.colors[1], float), (n, 1))))
        self.morph_t = min(p.h(0xD4), self.morph_t + 1)

    def _lang_morph(self):
        """FUN_813307b4 + FUN_81330b7c: the sound word slides into the chosen language's spelling."""
        g, s = self.g, self.s
        old, new, cnt, speed = self.morph
        k = 55 if s[6] == 1 else 44
        old_idx = self.lang_groups[old][k]
        new_idx = self.lang_groups[new][k]
        self.alpha[g[54]] = 0
        t = cnt / 10.0
        rnd = self.morph_rand
        c0 = np.array(self.colors[4], float)
        c1 = np.array(self.colors[5], float)
        na, nb = len(old_idx), len(new_idx)
        pa = self.pos[old_idx].copy()
        pa[:, 0] -= 0.2 * speed * rnd[:na] * t
        pb = self.pos[new_idx].copy()
        pb[:, 0] += 0.2 * speed * rnd[na:na + nb] * (1.0 - t)
        self.extra.append((self.scale[old_idx].copy(), self.rot[old_idx].copy(), pa,
                           np.full(na, int(255 * (1.0 - t)), np.int32), np.tile(c0, (na, 1)), np.tile(c1, (na, 1))))
        self.extra.append((self.scale[new_idx].copy(), self.rot[new_idx].copy(), pb,
                           np.full(nb, int(255 * t), np.int32), np.tile(c0, (nb, 1)), np.tile(c1, (nb, 1))))
        # FUN_81330b7c: the screen-position row is copied sliding out while the original slides in
        row = g[45]
        nr = len(row)
        pr = self.pos[row].copy()
        pr[:, 0] -= 0.2 * speed * rnd[:nr] * t
        a_copy = (self.alpha[row] * (1.0 - t)).astype(np.int32)
        self.extra.append((self.scale[row].copy(), self.rot[row].copy(), pr, a_copy,
                           self.col0[row].copy(), self.col1[row].copy()))
        self.pos[row, 0] += 0.2 * speed * rnd[:nr] * (1.0 - t)
        self.alpha[row] = (self.alpha[row] * t).astype(np.int32)

    # -- the Game Play display (FUN_8132a3d4) ---------------------------------------
    def game(self, starting, which, lang_sel):
        """which: 0 PRESS START, 1 NO DISC, 2 ? (the text is drawn by the morph, FUN_81336f9c)."""
        p = self.p
        self.request(s0=3, s1=starting, s2=which)
        if self.ps[0] != 3:
            self.reset()
            self.alpha[:] = 0xFF
            self.tgt[self.D, :] = p.h(0xAA)
            self.tgt[self.C, :] = p.h(0xA8)
            self.tgt[self.B, :] = self.cur[self.B, :] = p.h(0xA8)
            self.ripple_t = self.ripple_u = 0
            self.disp_vec[:] = 0
            self.disp_rot[:] = 0
            self.progress_to = p.h(0xD6)
            self.morph_t = 0
        pair = (self._rgb(0x12A), self._rgb(0x12C))
        self.colors = [pair[0], pair[1]] * 3
        if p.h(0x128) >= 0:
            self.progress = p.h(0x128)
        self.load_set(1)
        self._game_fit(lang_sel)
        self.reset_colors()
        # FUN_8132c5e0: every pixel hidden, the text is all in the morph cubes
        self.tgt[self.D, :] = p.h(0xAA)
        self.tgt[self.C, :] = p.h(0xA8)
        self.tgt[self.A, :] = 0
        self.tgt[self.B, :] = self.cur[self.B, :] = 0
        if p.f(0xD8) < self.prog():
            self._game_ripple()
        self.finish()
        self.extra = []
        self._game_morph()
        self.step()

    def _game_fit(self, lang_sel):
        """FUN_8132ec54: fit the text to the language's word lengths."""
        p = self.p
        if lang_sel == 2:
            off, s = -36.0, 0.6
        elif lang_sel == 1:
            off, s = -36.0, 0.65
        else:
            off, s = p.f(0x9C), p.f(0xA4)
        n = self.count
        c = self.pos[:n].mean(0)
        self.pos[:n, 0] += off
        self.pos[:n, 1] += p.f(0xA0)
        self.pos[:n] = c + s * (self.pos[:n] - c)
        self.scale[:n] *= s

    def _game_ripple(self):
        """FUN_8133374c: the whole board sways."""
        dx, dy, rx, ry = self.ripple_wave()
        n = self.count
        c = self.pos[:n].mean(0)
        self.pos[:n, 0] += dx
        self.pos[:n, 1] += dy
        R = rot_xyz(0, rx, ry)
        v = self.pos[:n] - c
        self.disp_vec[:n] = v @ R.T + c - self.pos[:n]
        self.disp_rot[:n] = (0, rx, ry)
        self.ripple_step()

    def _game_morph(self):
        """FUN_81336f9c / FUN_8133758c: the words slide past each other when the disc state changes."""
        p, s, ps, g = self.p, self.s, self.ps, self.g
        if not (s[0] == 3 and ps[0] == 3):
            self.morph_old = self.morph_new = s[2]
            return
        word = lambda v: g[27] if v == 1 else (g[29] if v == 2 else g[28])
        if ps[2] != s[2]:
            self.morph_dir = np.asarray(rom.f32(0x8137F048, 3), float)
            r = np.array([random.getrandbits(16) % 600 + 200 for _ in range(0x596)], float)
            self.morph_speed = np.trunc(p.f(0xCC) * r / 10.0)
            self.morph_old = ps[2]
            self.morph_t = 0
            self.morph_new = s[2]
        t = self.morph_t / p.h(0xD4)
        a_idx, b_idx = word(self.morph_old), word(self.morph_new)
        na, nb = len(a_idx), len(b_idx)
        sp = self.morph_speed
        if len(sp) < na + nb:
            sp = np.zeros(na + nb)
        pos_a = self.pos[a_idx] - self.morph_dir * (sp[:na] * t)[:, None]
        pos_b = self.pos[b_idx] + self.morph_dir * (sp[na:na + nb] * (1.0 - t))[:, None]
        al = np.concatenate([np.full(na, int(255 * (1.0 - t))), np.full(nb, int(255 * t))]).astype(np.int32)
        idx = np.concatenate([a_idx, b_idx])
        n = len(idx)
        self.extra.append((self.scale[idx].copy(), self.rot[idx].copy(), np.concatenate([pos_a, pos_b]), al,
                           np.tile(np.array(self.colors[0], float), (n, 1)),
                           np.tile(np.array(self.colors[1], float), (n, 1))))
        self.morph_t = min(p.h(0xD4), self.morph_t + 1)

    # -- the cubes to draw (FUN_81333250 + FUN_81333030) ---------------------------
    def instances(self, fade):
        """-> (model matrices (n,4,4), col0 (n,4), col1 (n,4), alpha (n,)) of the visible cubes."""
        parts = [(self.scale[:self.count], self.rot[:self.count], self.pos[:self.count],
                  self.alpha[:self.count], self.col0[:self.count], self.col1[:self.count])]
        parts += self.extra
        sc = np.concatenate([q[0] for q in parts])
        rt = np.concatenate([q[1] for q in parts])
        ps = np.concatenate([q[2] for q in parts])
        al = np.concatenate([q[3] for q in parts]).astype(np.float64)
        c0 = np.concatenate([q[4] for q in parts])
        c1 = np.concatenate([q[5] for q in parts])
        a = np.floor(255.0 * al * fade / 0xFE01)        # col0.a (255) * fade * alpha / 0xfe01
        keep = a > 10
        if not keep.any():
            return None
        sc, rt, ps, a, c0, c1 = sc[keep], rt[keep], ps[keep], a[keep], c0[keep], c1[keep]
        return build_matrices(ps, rt, sc), c0 / 255.0, c1 / 255.0, a / 255.0


def build_matrices(pos, rot, scale):
    """FUN_813073b4 (Rz Ry Rx + T) with the columns scaled (FUN_81307754), vectorised."""
    tab = np.sin(np.arange(4096) * 2 * np.pi / 4096)
    ia = (rot.astype(np.int64) & 0xFFFF) >> 4
    s = tab[ia]
    c = tab[(ia + 1024) & 4095]
    sx, sy, sz = s[:, 0], s[:, 1], s[:, 2]
    cx, cy, cz = c[:, 0], c[:, 1], c[:, 2]
    n = len(pos)
    m = np.zeros((n, 4, 4))
    m[:, 0, 0] = cz * cy
    m[:, 0, 1] = -sz * cx + sx * (cz * sy)
    m[:, 0, 2] = sz * sx + cx * (cz * sy)
    m[:, 1, 0] = sz * cy
    m[:, 1, 1] = cz * cx + sx * (sz * sy)
    m[:, 1, 2] = -cz * sx + cx * (sz * sy)
    m[:, 2, 0] = -sy
    m[:, 2, 1] = cy * sx
    m[:, 2, 2] = cy * cx
    m[:, :3, 0] *= scale[:, 0:1]
    m[:, :3, 1] *= scale[:, 1:2]
    m[:, :3, 2] *= scale[:, 2:3]
    m[:, :3, 3] = pos
    m[:, 3, 3] = 1.0
    return m


# --------------------------------------------------------------------------
# instanced drawing of the cubes (model res 0x68 'cube1')
# --------------------------------------------------------------------------
VS = """
#version 330
uniform mat4 u_vp;
in vec3 a_pos; in vec2 a_uv;
in vec4 i_m0; in vec4 i_m1; in vec4 i_m2; in vec4 i_c0; in vec4 i_c1; in float i_a;
out vec2 v_uv; out vec4 v_c0; out vec4 v_c1; out float v_a;
void main() {
    mat4 m = mat4(vec4(i_m0.x, i_m1.x, i_m2.x, 0.0), vec4(i_m0.y, i_m1.y, i_m2.y, 0.0),
                  vec4(i_m0.z, i_m1.z, i_m2.z, 0.0), vec4(i_m0.w, i_m1.w, i_m2.w, 1.0));
    gl_Position = u_vp * m * vec4(a_pos, 1.0);
    v_uv = a_uv; v_c0 = i_c0; v_c1 = i_c1; v_a = i_a;
}
"""
FS = """
#version 330
uniform sampler2D u_tex;
in vec2 v_uv; in vec4 v_c0; in vec4 v_c1; in float v_a;
out vec4 o;
void main() {
    vec4 t = texture(u_tex, v_uv);
    // TEV stage of cube1: C1 * (1 - tex) + C0 * tex, alpha = tex.a
    o = vec4(mix(v_c1.rgb, v_c0.rgb, t.rgb), t.a * v_a);
}
"""


class CubeRenderer:
    def __init__(self, ctx):
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
        import j3d
        self.ctx = ctx
        m = j3d.Model(_res(RES_CUBE))
        verts = []
        for shp in m.shapes:
            for v in shp["local"]:
                verts.append(list(v[0:3]) + list(v[10:12]))
        self.nverts = len(verts)
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.vbo = ctx.buffer(np.array(verts, "f4").tobytes())
        self.ibo = ctx.buffer(reserve=(N + 0x600) * 4 * 21)
        self.vao = ctx.vertex_array(self.prog, [
            (self.vbo, "3f 2f", "a_pos", "a_uv"),
            (self.ibo, "4f 4f 4f 4f 4f 1f/i", "i_m0", "i_m1", "i_m2", "i_c0", "i_c1", "i_a")])
        img = m.textures[0]
        info = m.texture_info[0]
        if info["format"] in ("IA8", "IA4"):
            pass
        self.tex = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tex.repeat_x = self.tex.repeat_y = False

    def draw(self, vp, inst):
        if inst is None:
            return
        mats, c0, c1, a = inst
        n = len(mats)
        data = np.concatenate([mats[:, 0, :], mats[:, 1, :], mats[:, 2, :], c0, c1, a[:, None]], 1).astype("f4")
        raw = data.tobytes()
        if len(raw) > self.ibo.size:
            self.ibo.orphan(len(raw))
        self.ibo.write(raw)
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<="
        ctx.fbo.depth_mask = False
        ctx.enable(moderngl.CULL_FACE)
        ctx.cull_face = "back"
        self.prog["u_vp"].write(np.ascontiguousarray(np.asarray(vp, "f4").T).tobytes())
        self.tex.use(0)
        self.prog["u_tex"].value = 0
        self.vao.render(moderngl.TRIANGLES, vertices=self.nverts, instances=n)
        ctx.fbo.depth_mask = True
