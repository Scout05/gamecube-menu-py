"""GameCube GX texture decoding -> RGBA numpy arrays."""
import struct

import numpy as np

# format id: (block width, block height, bits per pixel)
BLOCK = {
    0x0: (8, 8, 4),    # I4
    0x1: (8, 4, 8),    # I8
    0x2: (8, 4, 8),    # IA4
    0x3: (4, 4, 16),   # IA8
    0x4: (4, 4, 16),   # RGB565
    0x5: (4, 4, 16),   # RGB5A3
    0x6: (4, 4, 32),   # RGBA8
    0x8: (8, 8, 4),    # C4
    0x9: (8, 4, 8),    # C8
    0xA: (4, 4, 16),   # C14X2
    0xE: (8, 8, 4),    # CMPR
}
NAMES = {0: "I4", 1: "I8", 2: "IA4", 3: "IA8", 4: "RGB565", 5: "RGB5A3", 6: "RGBA8",
         8: "C4", 9: "C8", 0xA: "C14X2", 0xE: "CMPR"}


def data_size(fmt, w, h):
    bw, bh, bpp = BLOCK[fmt]
    bx = (w + bw - 1) // bw
    by = (h + bh - 1) // bh
    return bx * by * bw * bh * bpp // 8


def _rgb565(v):
    v = v.astype(np.uint32)
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return np.stack([r, g, b, np.full_like(r, 255)], -1).astype(np.uint8)


def _rgb5a3(v):
    v = v.astype(np.uint32)
    opaque = (v & 0x8000) != 0
    r = np.where(opaque, ((v >> 10) & 0x1F) * 255 // 31, ((v >> 8) & 0xF) * 17)
    g = np.where(opaque, ((v >> 5) & 0x1F) * 255 // 31, ((v >> 4) & 0xF) * 17)
    b = np.where(opaque, (v & 0x1F) * 255 // 31, (v & 0xF) * 17)
    a = np.where(opaque, 255, ((v >> 12) & 0x7) * 255 // 7)
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def _ia8(v):
    v = v.astype(np.uint32)
    i = v & 0xFF
    a = v >> 8
    return np.stack([i, i, i, a], -1).astype(np.uint8)


def decode_palette(raw, pal_fmt, count):
    v = np.frombuffer(raw[:count * 2], ">u2")
    return {0: _ia8, 1: _rgb565, 2: _rgb5a3}[pal_fmt](v)


def _unblock(px, w, h, bw, bh):
    """px: array (nblocks*bw*bh, 4) in block order -> (h, w, 4)."""
    bx = (w + bw - 1) // bw
    by = (h + bh - 1) // bh
    img = px.reshape(by, bx, bh, bw, 4).transpose(0, 2, 1, 3, 4).reshape(by * bh, bx * bw, 4)
    return img[:h, :w]


def _cmpr(raw, w, h):
    bx = (w + 7) // 8
    by = (h + 7) // 8
    out = np.zeros((by * 8, bx * 8, 4), np.uint8)
    o = 0
    for ty in range(by):
        for tx in range(bx):
            for sub in range(4):
                c0, c1, bits = struct.unpack_from(">HHI", raw, o)
                o += 8
                p = _rgb565(np.array([c0, c1])).astype(np.int32)
                if c0 > c1:
                    p2 = (2 * p[0] + p[1]) // 3
                    p3 = (p[0] + 2 * p[1]) // 3
                    p2[3] = p3[3] = 255
                else:
                    p2 = (p[0] + p[1]) // 2
                    p2[3] = 255
                    p3 = np.array([0, 0, 0, 0])
                pal = np.stack([p[0], p[1], p2, p3]).astype(np.uint8)
                idx = np.array([(bits >> (30 - 2 * i)) & 3 for i in range(16)]).reshape(4, 4)
                y = ty * 8 + (sub // 2) * 4
                x = tx * 8 + (sub % 2) * 4
                out[y:y + 4, x:x + 4] = pal[idx]
    return out[:h, :w]


def decode(raw, fmt, w, h, palette=None):
    """Return (h, w, 4) uint8 RGBA."""
    if fmt == 0xE:
        return _cmpr(raw, w, h)
    bw, bh, bpp = BLOCK[fmt]
    n = data_size(fmt, w, h)
    raw = np.frombuffer(raw[:n], np.uint8)
    if fmt in (0x0, 0x8):
        v = np.stack([raw >> 4, raw & 0xF], -1).reshape(-1)
    elif fmt in (0x1, 0x2, 0x9):
        v = raw
    elif fmt in (0x3, 0x4, 0x5, 0xA):
        v = raw.view(">u2")
    if fmt == 0x0:
        i = (v * 17).astype(np.uint8)
        px = np.stack([i, i, i, i], -1)
    elif fmt == 0x1:
        px = np.stack([v, v, v, v], -1)
    elif fmt == 0x2:
        i = ((v & 0xF) * 17).astype(np.uint8)
        a = ((v >> 4) * 17).astype(np.uint8)
        px = np.stack([i, i, i, a], -1)
    elif fmt == 0x3:
        px = _ia8(v)
    elif fmt == 0x4:
        px = _rgb565(v)
    elif fmt == 0x5:
        px = _rgb5a3(v)
    elif fmt == 0x6:
        # each 4x4 block: 32 bytes AR, then 32 bytes GB
        blk = raw.reshape(-1, 2, 16, 2)
        a, r = blk[:, 0, :, 0], blk[:, 0, :, 1]
        g, b = blk[:, 1, :, 0], blk[:, 1, :, 1]
        px = np.stack([r, g, b, a], -1).reshape(-1, 4)
    elif fmt in (0x8, 0x9, 0xA):
        if palette is None:
            i = (v * (17 if fmt == 0x8 else 1)).astype(np.uint8)
            px = np.stack([i, i, i, np.full_like(i, 255)], -1)
        else:
            px = palette[np.minimum(v & 0x3FFF, len(palette) - 1)]
    return _unblock(px.reshape(-1, 4), w, h, bw, bh)


def decode_bti(buf, hdr_off, data_base=None):
    """Decode a 0x20-byte BTI header at hdr_off. Offsets are relative to the header."""
    fmt, w, h, ws, wt, pal_fmt, pal_n, pal_off, mips, img_off = _bti(buf, hdr_off)
    base = hdr_off if data_base is None else data_base
    pal = None
    if fmt in (0x8, 0x9, 0xA) and pal_n:
        pal = decode_palette(buf[base + pal_off:], pal_fmt, pal_n)
    img = decode(buf[base + img_off:], fmt, w, h, pal)
    return img, dict(format=NAMES.get(fmt, hex(fmt)), width=w, height=h, wrap_s=ws, wrap_t=wt, mips=mips)


def _bti(buf, o):
    fmt, _alpha, w, h, ws, wt = struct.unpack_from(">BBHHBB", buf, o)
    pal_fmt = buf[o + 9]
    pal_n = struct.unpack_from(">H", buf, o + 0x0A)[0]
    pal_off = struct.unpack_from(">I", buf, o + 0x0C)[0]
    mips = buf[o + 0x18]
    img_off = struct.unpack_from(">I", buf, o + 0x1C)[0]
    return fmt, w, h, ws, wt, pal_fmt, pal_n, pal_off, mips, img_off
