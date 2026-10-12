"""Math helpers mirroring the IPL's own (s16 angles, 3x4 matrices as 4x4 numpy)."""
import math

import numpy as np

# The IPL looks sin/cos up in a 4096-entry table: angle (u16) >> 4.
_TAB = [math.sin(i * 2 * math.pi / 4096) for i in range(4096)]


def s16(v):
    v = int(v) & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def u8(v):
    return int(v) & 0xFF


def sin16(a):
    return _TAB[(int(a) & 0xFFFF) >> 4]


def cos16(a):
    return _TAB[((int(a) & 0xFFFF) >> 4) + 1024 & 4095]


def rot_trans(tx, ty, tz, rx, ry, rz):
    """FUN_813073b4: R = Rz * Ry * Rx with s16 angles, translation (tx, ty, tz)."""
    sx, cx = sin16(rx), cos16(rx)
    sy, cy = sin16(ry), cos16(ry)
    sz, cz = sin16(rz), cos16(rz)
    return np.array([
        [cz * cy, -sz * cx + sx * (cz * sy), sz * sx + cx * (cz * sy), tx],
        [sz * cy, cz * cx + sx * (sz * sy), -cz * sx + cx * (sz * sy), ty],
        [-sy, cy * sx, cy * cx, tz],
        [0, 0, 0, 1.0]])


def trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def scale(x, y, z):
    return np.diag([x, y, z, 1.0])


def rot_axis(axis, a):
    """FUN_81307208."""
    s, c = sin16(a), cos16(a)
    if axis in "xX":
        return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1.0]])
    if axis in "yY":
        return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1.0]])
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


def scale_cols(m, sx, sy, sz):
    """FUN_81307754: multiply matrix columns 0..2 by the scale vector."""
    m = m.copy()
    m[:3, 0] *= sx
    m[:3, 1] *= sy
    m[:3, 2] *= sz
    return m


def hermite(t, t0, p0, m0, t1, p1, m1):
    """FUN_81307d08 (the IPL's cubic Hermite, tangents per unit of t)."""
    u = t - t0
    inv = 1.0 / (t1 - t0)
    a = inv * u * u
    b = u * a * inv
    c = 3.0 * a * inv
    return (m1 * (b - a) + m0 * (u + (b - 2.0 * a))
            + p0 * (1.0 + (2.0 * b * inv - c)) + p1 * (-2.0 * b * inv + c))


def look_at(eye, up, target):
    eye, up, target = (np.asarray(v, float) for v in (eye, up, target))
    f = eye - target
    f /= np.linalg.norm(f)
    r = np.cross(up, f)
    r /= np.linalg.norm(r)
    u = np.cross(f, r)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = r, u, f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def perspective(fovy_deg, aspect, near, far):
    f = 1 / math.tan(math.radians(fovy_deg) / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])
