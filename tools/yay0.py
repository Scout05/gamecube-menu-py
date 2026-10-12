"""Find and decompress every Yay0 block in the descrambled IPL ROM.

usage: python yay0.py ipl_plain.bin out_dir
"""
import os
import re
import struct
import sys

BS2_ROM = 0x820
BS2_RAM = 0x81300000


def yay0_decompress(src, off=0):
    magic, size, link_off, chunk_off = struct.unpack_from(">4sIII", src, off)
    assert magic == b"Yay0"
    dst = bytearray()
    mask_pos = off + 16
    link_pos = off + link_off
    chunk_pos = off + chunk_off
    mask = bits = 0
    while len(dst) < size:
        if bits == 0:
            mask = struct.unpack_from(">I", src, mask_pos)[0]
            mask_pos += 4
            bits = 32
        if mask & 0x80000000:
            dst.append(src[chunk_pos])
            chunk_pos += 1
        else:
            link = struct.unpack_from(">H", src, link_pos)[0]
            link_pos += 2
            dist = (link & 0xFFF) + 1
            count = link >> 12
            if count == 0:
                count = src[chunk_pos] + 0x12
                chunk_pos += 1
            else:
                count += 2
            for _ in range(count):
                dst.append(dst[-dist])
        mask = (mask << 1) & 0xFFFFFFFF
        bits -= 1
    return bytes(dst)


def main(rom_path, out_dir):
    rom = open(rom_path, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    for m in re.finditer(b"Yay0", rom):
        off = m.start()
        try:
            data = yay0_decompress(rom, off)
        except Exception as e:  # stray "Yay0" bytes that are not a header
            print(f"{off:06x}: skip ({e})")
            continue
        ram = off - BS2_ROM + BS2_RAM
        magic = data[:4]
        tag = magic.decode("ascii", "replace") if all(32 <= c < 127 for c in magic) else magic.hex()
        name = f"{ram:08x}.bin"
        open(os.path.join(out_dir, name), "wb").write(data)
        print(f"rom {off:06x} ram {ram:08x}  -> {len(data):7d} bytes  head {tag}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
