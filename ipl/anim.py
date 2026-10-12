"""J3D animation players: ANK1 (BCK joint animation) and IPK1 (material color keys)."""
import math
import struct


def _u16(b, o): return struct.unpack_from(">H", b, o)[0]
def _u32(b, o): return struct.unpack_from(">I", b, o)[0]


def _hermite(p0, p1, s0, s1, u):
    u2, u3 = u * u, u * u * u
    return (p0 * (2 * u3 - 3 * u2 + 1) + p1 * (-2 * u3 + 3 * u2)
            + s0 * (u3 - 2 * u2 + u) + s1 * (u3 - u2))


class Track:
    """count/index/tangent-mode triple pointing into a value table."""

    def __init__(self, values, count, index, tangent_mode, scale=1.0):
        self.scale = scale
        if count == 1:
            self.const = values[index] * scale
            self.keys = None
            return
        self.const = None
        stride = 4 if tangent_mode == 1 else 3
        self.keys = []
        for k in range(count):
            e = values[index + k * stride:index + (k + 1) * stride]
            t, v, tin = e[0], e[1] * scale, e[2] * scale
            tout = e[3] * scale if stride == 4 else tin
            self.keys.append((t, v, tin, tout))

    def eval(self, frame):
        if self.keys is None:
            return self.const
        ks = self.keys
        if frame <= ks[0][0]:
            return ks[0][1]
        if frame >= ks[-1][0]:
            return ks[-1][1]
        for a, b in zip(ks, ks[1:]):
            if a[0] <= frame <= b[0]:
                length = b[0] - a[0]
                u = (frame - a[0]) / length if length else 0.0
                return _hermite(a[1], b[1], a[3] * length, b[2] * length, u)
        return ks[-1][1]


class JointAnim:
    """ANK1 / J3D1bck1."""

    def __init__(self, data):
        a = 0x20
        assert data[a:a + 4] == b"ANK1"
        self.loop = data[a + 8]
        angle_shift = data[a + 9]
        self.duration = _u16(data, a + 0x0A)
        njoints = _u16(data, a + 0x0C)
        ns, nr, nt = _u16(data, a + 0x0E), _u16(data, a + 0x10), _u16(data, a + 0x12)
        jo, so, ro, to = (_u32(data, a + 0x14 + 4 * i) for i in range(4))
        scales = struct.unpack_from(f">{ns}f", data, a + so)
        rots = struct.unpack_from(f">{nr}h", data, a + ro)
        trans = struct.unpack_from(f">{nt}f", data, a + to)
        rot_scale = (1 << angle_shift) * math.pi / 32768.0
        self.joints = []
        for j in range(njoints):
            e = a + jo + 0x36 * j
            axes = []
            for ax in range(3):             # x, y, z; each: scale, rotation, translation
                base = e + ax * 18
                s = Track(scales, *struct.unpack_from(">3H", data, base))
                r = Track(rots, *struct.unpack_from(">3H", data, base + 6), scale=rot_scale)
                t = Track(trans, *struct.unpack_from(">3H", data, base + 12))
                axes.append((s, r, t))
            self.joints.append(axes)

    def sample(self, joint, frame):
        """-> (sx, sy, sz, rx, ry, rz, tx, ty, tz) in radians."""
        ax = self.joints[joint]
        return (ax[0][0].eval(frame), ax[1][0].eval(frame), ax[2][0].eval(frame),
                ax[0][1].eval(frame), ax[1][1].eval(frame), ax[2][1].eval(frame),
                ax[0][2].eval(frame), ax[1][2].eval(frame), ax[2][2].eval(frame))


class ColorAnim:
    """IPK1: per-material RGBA keyframes. Tables are f32; key times are in seconds
    (x30 = J3D frames, e.g. 2.1667 s = the 65-frame logo animation)."""

    FPS = 30.0

    def __init__(self, data):
        assert data[:4] == b"IPK1"
        self.loop = data[8]
        self.duration = _u16(data, 0x0A)
        mat_tab, chan_tab, ro, go, bo, ao = (_u32(data, 0x0C + 4 * i) for i in range(6))
        # the value tables run back to back; their lengths follow from the next offset
        bounds = [ro, go, bo, ao, len(data)]
        tables = []
        for i in range(4):
            n = (bounds[i + 1] - bounds[i]) // 4
            vals = list(struct.unpack_from(f">{n}f", data, bounds[i]))
            tables.append(vals)
        nmat = (chan_tab - mat_tab) // 2
        self.material_index = [_u16(data, mat_tab + 2 * i) for i in range(nmat)]
        self.tracks = []
        for m in range(nmat):
            e = chan_tab + 24 * m
            self.tracks.append([Track(tables[c], *struct.unpack_from(">3H", data, e + 6 * c)) for c in range(4)])

    def sample(self, slot, frame):
        sec = frame / self.FPS
        return tuple(max(0.0, min(255.0, t.eval(sec))) / 255.0 for t in self.tracks[slot])
