"""Make Ghidra's IPL decompilation readable.

* `*(T *)(unaff_r2 + -0x7aa0)` -> the float/double constant value from RAM
* `(unaff_r13 + -0x7bd0)`      -> `G_814ad3f0` (absolute SDA global)
Writes decomp/*.c -> decomp/annotated/*.c
"""
import glob
import os
import re
import struct

MEM1 = os.environ.get("GCIPL_MEM1", os.path.expanduser("~/.local/share/dolphin-emu/Dump/mem1.raw"))
R13, R2 = 0x814B4FC0, 0x814B5B20
mem = open(MEM1, "rb").read()


def rd(addr, n):
    o = addr - 0x80000000
    return mem[o:o + n]


def r2_const(m):
    typ, off = m.group(1), int(m.group(2), 16) * (-1 if m.group(0).count("-0x") else 1)
    addr = R2 + off
    if typ == "float":
        v = struct.unpack(">f", rd(addr, 4))[0]
        return f"{v!r}f"
    if typ == "double":
        raw = rd(addr, 8)
        if raw[:4] == b"\x43\x30\x00\x00":
            return "(double)0x4330000000000000" if raw[4:] == b"\0\0\0\0" else "(double)0x4330000080000000"
        return repr(struct.unpack(">d", raw)[0])
    return m.group(0)


R2_RE = re.compile(r"\*\((float|double) \*\)\(unaff_r2 \+ -?(0x[0-9a-f]+)\)")
R13_RE = re.compile(r"\(unaff_r13 \+ (-?)(0x[0-9a-f]+)\)")
R2_ADDR_RE = re.compile(r"\(unaff_r2 \+ (-?)(0x[0-9a-f]+)\)")


def r13_sub(m):
    off = int(m.group(2), 16) * (-1 if m.group(1) else 1)
    return f"G_{R13 + off:08x}"


def r2_addr(m):
    off = int(m.group(2), 16) * (-1 if m.group(1) else 1)
    addr = R2 + off
    s = rd(addr, 64).split(b"\0")[0]
    if len(s) >= 2 and all(32 <= c < 127 for c in s):
        return f'"{s.decode()}"'
    return f"K_{addr:08x}"


def main():
    src = os.path.join(os.path.dirname(__file__), "..", "decomp")
    out = os.path.join(src, "annotated")
    os.makedirs(out, exist_ok=True)
    for p in glob.glob(os.path.join(src, "bs2_*.c")):
        t = open(p, encoding="utf-8", errors="replace").read()
        t = R2_RE.sub(r2_const, t)
        t = R13_RE.sub(r13_sub, t)
        t = R2_ADDR_RE.sub(r2_addr, t)
        open(os.path.join(out, os.path.basename(p)), "w", encoding="utf-8").write(t)
    print("annotated", len(glob.glob(os.path.join(out, "*.c"))), "files")


if __name__ == "__main__":
    main()
