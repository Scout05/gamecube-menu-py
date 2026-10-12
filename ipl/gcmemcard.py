"""GameCube memory card images (Dolphin .raw), for the Memory Card menu.

Layout: 8 KiB blocks. Block 0 header, 1-2 directory (two copies), 3-4 block
allocation table (two copies); the copy with the higher update counter is live.
Each directory entry is 0x40 bytes; a file's data is a chain of blocks in the BAT.
Every write keeps a one-time backup next to the image (<name>.bak).
"""
import os
import shutil
import struct
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import gxtex  # noqa: E402

BLOCK = 0x2000
EPOCH = 946684800          # 2000-01-01, the GameCube's time base

# card states the menu uses (DAT_814b7282)
NO_CARD, READY, NEEDS_FORMAT, BROKEN, WRONG_DEVICE = 0, 4, 6, 5, 10


def checksums(data):
    """The card's two 16-bit checksums (CARD's __CARDCheckSum)."""
    w = np.frombuffer(bytes(data), ">u2").astype(np.uint32)
    a = int(w.sum()) & 0xFFFF
    b = int((w ^ 0xFFFF).sum()) & 0xFFFF
    if a == 0xFFFF:
        a = 0
    if b == 0xFFFF:
        b = 0
    return a, b


class Entry:
    """One directory entry (struct CARDDir)."""

    def __init__(self, raw):
        self.raw = bytearray(raw)
        (self.game, self.maker, _pad, self.banner_fmt, self.name, self.modtime, self.image_off,
         self.icon_fmt, self.anim_speed, self.permission, self.copies, self.first_block, self.blocks,
         _pad2, self.comment_off) = struct.unpack(">4s2sBB32sIIHHBBHHHI", raw)

    @property
    def empty(self):
        return self.game == b"\xff\xff\xff\xff"

    @property
    def filename(self):
        return self.name.split(b"\0")[0].decode("latin-1")

    @property
    def copyable(self):
        return not self.permission & 0x08

    @property
    def movable(self):
        return not self.permission & 0x10

    def key(self):
        return self.game, self.maker, self.name


class Card:
    def __init__(self, path):
        self.path = path
        self.state = NO_CARD
        self.backed_up = False
        self.reload()

    # -- reading ------------------------------------------------------------------
    def reload(self):
        try:
            self.data = bytearray(open(self.path, "rb").read())
        except OSError:
            self.state = NO_CARD
            return
        if len(self.data) < 5 * BLOCK or len(self.data) % BLOCK:
            self.state = WRONG_DEVICE
            return
        self.nblocks = len(self.data) // BLOCK
        dirs = [self.data[BLOCK * i:BLOCK * (i + 1)] for i in (1, 2)]
        bats = [self.data[BLOCK * i:BLOCK * (i + 1)] for i in (3, 4)]
        ok_dir = [checksums(d[:0x1FFC]) == struct.unpack_from(">HH", d, 0x1FFC) for d in dirs]
        ok_bat = [checksums(b[4:]) == struct.unpack_from(">HH", b, 0) for b in bats]
        if not any(ok_dir) or not any(ok_bat):
            self.state = NEEDS_FORMAT
            return
        dc = [struct.unpack_from(">h", d, 0x1FFA)[0] if ok else -0x8000 for d, ok in zip(dirs, ok_dir)]
        bc = [struct.unpack_from(">h", b, 4)[0] if ok else -0x8000 for b, ok in zip(bats, ok_bat)]
        self.dir_i = 1 + (1 if dc[1] > dc[0] else 0)
        self.bat_i = 3 + (1 if bc[1] > bc[0] else 0)
        self.encoding = struct.unpack_from(">H", self.data, 0x24)[0]
        self.size_mbits = struct.unpack_from(">H", self.data, 0x22)[0]
        self.entries = []
        d = self.dir_block()
        for i in range(127):
            e = Entry(d[0x40 * i:0x40 * (i + 1)])
            self.entries.append(None if e.empty else e)
        self.state = READY
        self._art = {}

    def dir_block(self):
        return self.data[BLOCK * self.dir_i:BLOCK * (self.dir_i + 1)]

    def bat_block(self):
        return self.data[BLOCK * self.bat_i:BLOCK * (self.bat_i + 1)]

    def fat(self, b):
        return struct.unpack_from(">H", self.data, BLOCK * self.bat_i + 0x0A + 2 * (b - 5))[0]

    @property
    def free_blocks(self):
        return struct.unpack_from(">H", self.data, BLOCK * self.bat_i + 6)[0]

    @property
    def user_blocks(self):
        return self.nblocks - 5

    @property
    def free_entries(self):
        return sum(1 for e in self.entries if e is None)

    def file_data(self, e):
        out = bytearray()
        b = e.first_block
        for _ in range(e.blocks):
            if b < 5 or b >= self.nblocks:
                break
            out += self.data[BLOCK * b:BLOCK * (b + 1)]
            b = self.fat(b)
        return out

    # -- banners, icons, comments ---------------------------------------------------
    def art(self, i):
        """-> dict(banner=RGBA 96x32 or None, icons=[RGBA 32x32], delays=[frames], pingpong, title, info)."""
        if i in self._art:
            return self._art[i]
        e = self.entries[i]
        data = bytes(self.file_data(e))
        o = e.image_off
        res = {"banner": None, "icons": [], "delays": [], "pingpong": bool(e.banner_fmt & 4)}
        try:
            bf = e.banner_fmt & 3
            if bf == 1:
                pal = gxtex.decode_palette(data[o + 0xC00:o + 0xE00], 2, 256)
                res["banner"] = gxtex.decode(data[o:o + 0xC00], 0x9, 96, 32, pal)
                o += 0xE00
            elif bf == 2:
                res["banner"] = gxtex.decode(data[o:o + 0x1800], 0x5, 96, 32)
                o += 0x1800
            frames = []
            for k in range(8):
                fmt = (e.icon_fmt >> (2 * k)) & 3
                speed = (e.anim_speed >> (2 * k)) & 3
                if speed == 0:
                    break
                frames.append((fmt, speed))
            shared = None
            if any(f == 1 for f, _ in frames):
                size = sum(0x400 if f in (1, 3) else (0x800 if f == 2 else 0) for f, _ in frames)
                size += 0x200 * sum(1 for f, _ in frames if f == 3)
                shared = gxtex.decode_palette(data[o + size:o + size + 0x200], 2, 256)
            for fmt, speed in frames:
                if fmt == 2:
                    img = gxtex.decode(data[o:o + 0x800], 0x5, 32, 32)
                    o += 0x800
                elif fmt == 1:
                    img = gxtex.decode(data[o:o + 0x400], 0x9, 32, 32, shared)
                    o += 0x400
                elif fmt == 3:
                    pal = gxtex.decode_palette(data[o + 0x400:o + 0x600], 2, 256)
                    img = gxtex.decode(data[o:o + 0x400], 0x9, 32, 32, pal)
                    o += 0x600
                else:
                    img = res["icons"][-1] if res["icons"] else None
                if img is not None:
                    res["icons"].append(img)
                    res["delays"].append(speed * 4)
        except Exception:
            pass
        c = e.comment_off
        enc = "shift_jis" if self.encoding else "latin-1"
        res["title"] = data[c:c + 32].split(b"\0")[0].decode(enc, "replace")
        res["info"] = data[c + 32:c + 64].split(b"\0")[0].decode(enc, "replace")
        self._art[i] = res
        return res

    # -- writing ----------------------------------------------------------------------
    def _backup(self):
        if not self.backed_up and os.path.exists(self.path):
            bak = self.path + ".bak"
            if not os.path.exists(bak):
                shutil.copy2(self.path, bak)
            self.backed_up = True

    def _commit(self, entries, fat, free):
        """Write new directory + BAT into the *other* copies, with higher update counters."""
        self._backup()
        d = bytearray(BLOCK)
        d[:] = b"\xff" * BLOCK
        for i, e in enumerate(entries):
            if e is not None:
                d[0x40 * i:0x40 * (i + 1)] = e.raw
        cnt = (struct.unpack_from(">h", self.data, BLOCK * self.dir_i + 0x1FFA)[0] + 1) & 0xFFFF
        struct.pack_into(">H", d, 0x1FFA, cnt)
        struct.pack_into(">HH", d, 0x1FFC, *checksums(d[:0x1FFC]))
        new_dir = 3 - self.dir_i
        self.data[BLOCK * new_dir:BLOCK * (new_dir + 1)] = d
        b = bytearray(self.bat_block())
        cnt = (struct.unpack_from(">h", b, 4)[0] + 1) & 0xFFFF
        struct.pack_into(">H", b, 4, cnt)
        struct.pack_into(">H", b, 6, free)
        for k, v in fat.items():
            struct.pack_into(">H", b, 0x0A + 2 * (k - 5), v)
        struct.pack_into(">HH", b, 0, *checksums(b[4:]))
        new_bat = 7 - self.bat_i
        self.data[BLOCK * new_bat:BLOCK * (new_bat + 1)] = b
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.reload()

    def erase(self, i):
        e = self.entries[i]
        if e is None:
            return -1
        fat = {}
        b = e.first_block
        for _ in range(e.blocks):
            if b < 5:
                break
            nxt = self.fat(b)
            fat[b] = 0
            b = nxt
        entries = list(self.entries)
        entries[i] = None
        self._commit(entries, fat, self.free_blocks + len(fat))
        return 0

    def has_file(self, e):
        return any(x is not None and x.key() == e.key() for x in self.entries)

    def write_file(self, e, data):
        """Copy a file (entry + data) in; returns the new directory index or <0."""
        if self.free_entries == 0 or self.free_blocks < e.blocks:
            return -9
        free = [b for b in range(5, self.nblocks) if self.fat(b) == 0]
        if len(free) < e.blocks:
            return -9
        chain = free[:e.blocks]
        for k, b in enumerate(chain):
            self.data[BLOCK * b:BLOCK * (b + 1)] = data[BLOCK * k:BLOCK * (k + 1)].ljust(BLOCK, b"\0")
        fat = {b: (chain[k + 1] if k + 1 < len(chain) else 0xFFFF) for k, b in enumerate(chain)}
        ne = Entry(bytes(e.raw))
        struct.pack_into(">H", ne.raw, 0x36, chain[0])
        ne.raw[0x35] = (ne.raw[0x35] + 1) & 0xFF
        entries = list(self.entries)
        slot = entries.index(None)
        entries[slot] = ne
        self._commit(entries, fat, self.free_blocks - e.blocks)
        return slot

    def format(self):
        self._backup()
        hdr = bytes(self.data[:BLOCK])
        n = self.nblocks
        self.data = bytearray(hdr) + bytearray(b"\0" * (BLOCK * (n - 1)))
        for blk in (1, 2):
            d = bytearray(b"\xff" * BLOCK)
            struct.pack_into(">H", d, 0x1FFA, blk - 1)
            struct.pack_into(">HH", d, 0x1FFC, *checksums(d[:0x1FFC]))
            self.data[BLOCK * blk:BLOCK * (blk + 1)] = d
        for blk in (3, 4):
            b = bytearray(BLOCK)
            struct.pack_into(">HHH", b, 4, blk - 3, n - 5, 4)
            struct.pack_into(">HH", b, 0, *checksums(b[4:]))
            self.data[BLOCK * blk:BLOCK * (blk + 1)] = b
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.reload()
        return 0


def find_cards(root):
    """cards/A*.raw or MemoryCardA*.raw -> slot A, likewise B."""
    out = [None, None]
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for slot, letter in enumerate("AB"):
        for n in names:
            if not n.lower().endswith(".raw"):
                continue
            base = n[:-4]
            if base.upper().startswith(letter) or base.upper().startswith("MEMORYCARD" + letter):
                out[slot] = os.path.join(root, n)
                break
    return out


def modtime_now():
    return int(time.time()) - EPOCH
