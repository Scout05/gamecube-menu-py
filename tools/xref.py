"""Find code references to a RAM address range in the IPL's mem1 dump.

Catches lis/addi (and lis/load-store) pairs plus r13/r2 small-data accesses,
which Ghidra misses because it does not know the SDA bases.

usage: python xref.py 0x814af980 [0x814af9c0]
"""
import os
import struct
import sys

MEM1 = os.environ.get("GCIPL_MEM1", os.path.expanduser("~/.local/share/dolphin-emu/Dump/mem1.raw"))
R13, R2 = 0x814B4FC0, 0x814B5B20
CODE = (0x81300000, 0x81384000)
DFORM = {14: "addi", 32: "lwz", 34: "lbz", 36: "stw", 38: "stb", 40: "lhz", 42: "lha",
         44: "sth", 48: "lfs", 50: "lfd", 52: "stfs", 54: "stfd", 24: "ori"}


def scan(lo, hi, mem=None):
    m = mem or open(MEM1, "rb").read()
    out = []
    regs = {}
    for a in range(CODE[0], CODE[1], 4):
        w = struct.unpack_from(">I", m, a - 0x80000000)[0]
        op = w >> 26
        rd, ra, imm = (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if op == 15 and ra == 0:  # lis
            regs[rd] = (imm << 16, a)
            continue
        if op in DFORM:
            target = None
            if ra == 13:
                target = R13 + simm
            elif ra == 2:
                target = R2 + simm
            elif ra in regs and a - regs[ra][1] < 0x80:
                base = regs[ra][0]
                target = (base | imm) if op == 24 else (base + simm) & 0xFFFFFFFF
            if target is not None and lo <= target < hi:
                out.append((a, DFORM[op], target))
        if op == 18 or (op == 19 and (w >> 1) & 0x3FF in (16, 528)):
            regs.clear()  # branch: forget tracked lis values
    return out


if __name__ == "__main__":
    lo = int(sys.argv[1], 16)
    hi = int(sys.argv[2], 16) if len(sys.argv) > 2 else lo + 4
    for a, op, t in scan(lo, hi):
        print(f"{a:08x}  {op:5s} -> {t:08x}")
