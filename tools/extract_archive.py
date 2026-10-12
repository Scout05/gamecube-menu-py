"""Extract all 185 entries of the IPL's resource archive (ROM 0x82040).

Entry i lives at archive + table[i]; Yay0 entries are decompressed.
Writes res/NNN_<type>.bin and res/index.txt.

usage: python extract_archive.py ipl_plain.bin res
"""
import os
import struct
import sys

from yay0 import yay0_decompress

ARCHIVE_ROM = 0x82040        # RAM 0x81381820
BS2_ROM, BS2_RAM = 0x820, 0x81300000


def kind(data):
    head = data[:8]
    if head[:4] == b"J3D1":
        return head[4:8].decode()
    if head[:4] in (b"STH0", b"GLH0", b"TXH0", b"CAFD", b"IPK1"):
        return head[:4].decode()
    if len(data) >= 0x20 and data[0] == 0 and data[1] in range(0, 15) and data[4] == 0:
        fmt, w, h = struct.unpack_from(">HHH", data)
        return f"img{fmt}_{w}x{h}"
    return "raw"


def main(rom_path, out_dir):
    rom = open(rom_path, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    count = struct.unpack_from(">I", rom, ARCHIVE_ROM)[0]
    offs = list(struct.unpack_from(f">{count}I", rom, ARCHIVE_ROM + 4))
    index = []
    for i, off in enumerate(offs):
        a = ARCHIVE_ROM + off
        packed = rom[a:a + 4] == b"Yay0"
        if packed:
            data = yay0_decompress(rom, a)
        else:
            later = [o for o in offs if o > off]
            end = ARCHIVE_ROM + min(later) if later else a
            data = rom[a:end]
        k = kind(data)
        name = f"{i:03d}_{k}.bin"
        open(os.path.join(out_dir, name), "wb").write(data)
        ram = a - BS2_ROM + BS2_RAM
        index.append(f"{i:3d} 0x{i:02x}  ram {ram:08x}  {'Yay0' if packed else '    '}  {len(data):7d}  {name}")
    open(os.path.join(out_dir, "index.txt"), "w").write("\n".join(index) + "\n")
    print("\n".join(index))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
