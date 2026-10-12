"""The user's own IPL dump: find it, descramble it, read BS2 constants and archive resources.

No Nintendo data ships with this project. The dump is looked up in this order:
  1. the GCIPL_IPL environment variable (path to IPL.bin)
  2. IPL.bin in the project folder
  3. Dolphin's usual GC/EUR/IPL.bin locations
The descrambled copy is cached outside the project (~/.cache/gcipl) so later starts are instant.
RAM 0x81300000 = ROM 0x820.
"""
import functools
import hashlib
import os
import struct
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
from descramble import descramble  # noqa: E402
from yay0 import yay0_decompress  # noqa: E402

# SHA-1 of the PAL Rev 1.0 IPL this port was written against (scrambled / descrambled).
PAL10_SHA1 = "6f305c37dc1fbe332883bb8153eee26d3d325629"
PAL10_PLAIN_SHA1 = "9909490c6b2ac8428c8d347c3967aa8a930ee058"
ARCHIVE_ROM = 0x82040
ROM_SIZE = 0x200000

_rom = None


def _dolphin_dirs():
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, ".local", "share", "dolphin-emu"),
        os.path.join(home, ".dolphin-emu"),
        os.path.join(home, ".var", "app", "org.DolphinEmu.dolphin-emu", "data", "dolphin-emu"),
        os.path.join(home, "Library", "Application Support", "Dolphin"),
        os.path.join(home, "Documents", "Dolphin Emulator"),
    ]
    if os.environ.get("APPDATA"):
        dirs.append(os.path.join(os.environ["APPDATA"], "Dolphin Emulator"))
    return dirs


def find_dump():
    cands = []
    if os.environ.get("GCIPL_IPL"):
        cands.append(os.environ["GCIPL_IPL"])
    cands.append(os.path.join(ROOT, "IPL.bin"))
    cands += [os.path.join(d, "GC", "EUR", "IPL.bin") for d in _dolphin_dirs()]
    for p in cands:
        if os.path.isfile(p):
            return p
    sys.exit("No IPL dump found. Dump the BIOS from your own PAL GameCube and either put it at\n"
             f"  {os.path.abspath(os.path.join(ROOT, 'IPL.bin'))}\n"
             "or set GCIPL_IPL=/path/to/IPL.bin (Dolphin's GC/EUR/IPL.bin is found automatically).")


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA") \
        or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "gcipl")


def load():
    """The descrambled IPL as bytes."""
    global _rom
    if _rom is not None:
        return _rom
    path = find_dump()
    raw = open(path, "rb").read()
    if len(raw) != ROM_SIZE:
        sys.exit(f"{path}: expected a 2 MiB IPL dump, got {len(raw)} bytes")
    sha = hashlib.sha1(raw).hexdigest()
    if sha == PAL10_PLAIN_SHA1:
        _rom = raw                       # already descrambled
        return _rom
    if sha != PAL10_SHA1:
        print(f"warning: {path} is not the PAL Rev 1.0 IPL (sha1 {sha}); things may break", file=sys.stderr)
    cache = os.path.join(_cache_dir(), f"{sha}.plain")
    if os.path.isfile(cache):
        _rom = open(cache, "rb").read()
        return _rom
    print("Descrambling the IPL (first start only)...", file=sys.stderr)
    _rom = descramble(raw)
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        with open(cache, "wb") as f:
            f.write(_rom)
    except OSError:
        pass
    return _rom


def rom():
    return load()


@functools.lru_cache(maxsize=None)
def res_bytes(index):
    """Entry `index` of the IPL resource archive (ROM 0x82040), Yay0-decompressed."""
    r = rom()
    count = struct.unpack_from(">I", r, ARCHIVE_ROM)[0]
    offs = struct.unpack_from(f">{count}I", r, ARCHIVE_ROM + 4)
    a = ARCHIVE_ROM + offs[index]
    if r[a:a + 4] == b"Yay0":
        return bytes(yay0_decompress(r, a))
    later = [o for o in offs if o > offs[index]]
    return r[a:ARCHIVE_ROM + min(later)] if later else r[a:a]


def off(addr):
    return addr - 0x81300000 + 0x820


def f32(addr, n=None):
    if n is None:
        return struct.unpack_from(">f", rom(), off(addr))[0]
    return list(struct.unpack_from(f">{n}f", rom(), off(addr)))


def u32(addr):
    return struct.unpack_from(">I", rom(), off(addr))[0]


def s16(addr, n=None):
    if n is None:
        return struct.unpack_from(">h", rom(), off(addr))[0]
    return list(struct.unpack_from(f">{n}h", rom(), off(addr)))


def rgba(addr):
    """A GXColor (4 bytes) as an 0xRRGGBBAA int."""
    return u32(addr)


def place(addr):
    """The 9-float placement record read by FUN_8130764c:
    scale xyz, s16 rotation xyz (packed in floats 3..4), translation xyz."""
    sx, sy, sz = f32(addr, 3)
    rx, ry, rz = s16(addr + 12, 3)
    tx, ty, tz = f32(addr + 20, 3)
    return (sx, sy, sz), (rx, ry, rz), (tx, ty, tz)
