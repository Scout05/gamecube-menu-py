"""Descramble a GameCube IPL ROM dump (same algorithm Dolphin uses).

The region 0x100..0x1B0000 of the ROM is XORed with a bitstream from three
LFSRs. Output is a plain ROM image with BS1/BS2 readable.

usage: python descramble.py IPL.bin ipl_plain.bin
"""
import sys

SCRAMBLED_START = 0x100
SCRAMBLED_SIZE = 0x1AFE00


def keystream(size):
    out = bytearray(size)
    acc = nacc = 0
    t, u, v, x = 0x2953, 0xD9C2, 0x3FF1, 1
    i = 0
    while i < size:
        t0 = t & 1
        t1 = (t >> 1) & 1
        u0 = u & 1
        u1 = (u >> 1) & 1
        v0 = v & 1
        x ^= t1 ^ v0
        x ^= u0 | u1
        x ^= (t0 ^ u1 ^ v0) & (t0 ^ u0)
        if t0 == u0:
            v >>= 1
            if v0:
                v ^= 0xB3D0
        if t0 == 0:
            u >>= 1
            if u0:
                u ^= 0xFB10
        t >>= 1
        if t0:
            t ^= 0xA740
        acc = ((acc << 1) | x) & 0xFF
        nacc += 1
        if nacc == 8:
            out[i] = acc
            i += 1
            nacc = 0
    return out


def descramble(rom):
    rom = bytearray(rom)
    ks = keystream(SCRAMBLED_SIZE)
    seg = rom[SCRAMBLED_START:SCRAMBLED_START + SCRAMBLED_SIZE]
    rom[SCRAMBLED_START:SCRAMBLED_START + SCRAMBLED_SIZE] = bytes(a ^ b for a, b in zip(seg, ks))
    return bytes(rom)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(descramble(data))
    print(f"wrote {dst}")
