"""The menu tunables block (RAM 0x814b5ea0, G_814ad3f0), byte for byte.

FUN_81311464 fills it at boot and FUN_813113ec overrides a few fields for PAL;
the rest of the menu code reads it through FUN_81312f74(). Keeping the real layout
lets the ports below use the same offsets as the decompiled code.
"""
import struct

from .. import rom

SIZE = 0x1C0


def _k(addr):
    """A 4-byte SDA2 constant (K_814adxxx) as raw bits."""
    return rom.u32(addr)


_F = {   # float-ish literals the decompiler printed as strings
    "A ": 0x41200000, "AP": 0x41500000, "Ap": 0x41700000, "BH": 0x42480000,
    "@@": 0x40400000, "?fff": 0x3F666666, "CH": 0x43480000, "C`": 0x43600000,
}


class Block:
    def __init__(self):
        self.b = bytearray(SIZE)
        self._init()

    # -- raw access ---------------------------------------------------------
    def w32(self, off, v):
        struct.pack_into(">I", self.b, off, v & 0xFFFFFFFF)

    def w16(self, off, v):
        struct.pack_into(">H", self.b, off, v & 0xFFFF)

    def f(self, off):
        return struct.unpack_from(">f", self.b, off)[0]

    def h(self, off):
        return struct.unpack_from(">h", self.b, off)[0]

    def hu(self, off):
        return struct.unpack_from(">H", self.b, off)[0]

    # -- FUN_81311464 -------------------------------------------------------
    def _init(self):
        w32, w16, k = self.w32, self.w16, _k
        w16(0x16, 0x2EE)
        w32(0x08, k(0x814ADE04))
        w16(0x180, 0x730)
        w16(0x182, 0x578)
        w16(0x184, 0)
        u1 = k(0x814ADE08)
        w32(0x188, u1)
        w32(0x00, u1)
        u2 = k(0x814ADE0C)
        w32(0x04, u2)
        for off, v in ((0x1A, 0x32), (0x1C, 0x19), (0x1E, 0x19), (0x20, 0x15E), (0x22, 1000), (0x24, 1000),
                       (0x26, 0x19), (0x28, 0x32), (0x2A, 0xF), (0x2C, 6), (0x2E, 0xC568), (0x30, 0xC000), (0x32, 0)):
            w16(off, v)
        u3 = _F["A "]
        w32(0x34, u3)
        w32(0x38, k(0x814ADE10))
        u4 = k(0x814ADE14)
        w32(0x3C, u4)
        u5 = k(0x814ADE18)
        w32(0x40, u5)
        w32(0x44, u1)
        u6 = _F["AP"]
        w32(0x48, u6)
        w32(0x4C, u4)
        w32(0x50, u5)
        w32(0x54, u1)
        w32(0x58, k(0x814ADE1C))
        w32(0x5C, k(0x814ADDD4))
        w32(0x60, u5)
        for off in (0x64, 0x68, 0x6C, 0x70):
            w32(off, 0xFFFFFFFE)
        w32(0x74, 0xFFFFFFFF)
        w32(0x78, 0xFFFFFFFF)
        u7 = k(0x814ADE20)
        w32(0xF4, u7)
        u8 = k(0x814ADE24)
        w32(0xF8, u8)
        w32(0xFC, u1)
        w32(0x100, u8)
        w32(0x104, k(0x814ADDF8))
        u9 = k(0x814ADE28)
        w32(0x108, u9)
        w32(0x10C, _F["Ap"])
        w32(0x110, u9)
        w32(0x114, k(0x814ADE2C))
        w32(0x118, u6)
        w32(0x11C, _F["BH"])
        w32(0x120, u6)
        w16(0x124, 0x1E)
        w16(0x126, 10)
        w16(0xEC, 0x19)
        w32(0xF0, u3)
        w32(0x7C, u1)
        w32(0x80, k(0x814ADE34))
        w32(0x84, u4)
        w32(0x88, u5)
        w32(0x8C, u1)
        w32(0x90, u7)
        w32(0x94, u4)
        w32(0x98, u5)
        u1b = k(0x814ADE38)
        w32(0x9C, u1b)
        w32(0xA0, u1b)
        w32(0xA4, k(0x814ADE3C))
        w16(0xA8, 10)
        w16(0xAA, 10)
        w32(0xAC, u8)
        w16(0xB0, 100)
        u1c = k(0x814ADE40)
        w32(0xB4, u1c)
        w32(0xB8, u2)
        w32(0xBC, k(0x814ADE44))
        w32(0xC0, _F["@@"])
        w32(0xC4, k(0x814ADE4C))
        w16(0xC8, 0x78)
        w32(0xCC, k(0x814ADE50))
        w32(0xD0, u1c)
        w16(0xD4, 10)
        w16(0xD6, 0x3A)
        w32(0xD8, _F["?fff"])
        w16(0xDC, 200)
        w16(0xDE, 200)
        for off in (0xE0, 0xE4, 0xE8):
            w32(off, _F["CH"])
        w16(0x128, 0xFFFF)
        for off, v in ((0x12A, 0x82), (0x12E, 0x32), (0x132, 0xA0), (0x12C, 0x32), (0x130, 0x14), (0x134, 0x46),
                       (0x136, 0xB4), (0x13A, 0xFF), (0x13E, 0), (0x138, 0x14), (0x13C, 100), (0x140, 0x32),
                       (0x142, 0x78), (0x146, 0xDC), (0x14A, 0x23), (0x144, 0), (0x148, 0x50), (0x14C, 0x19),
                       (0x14E, 0x28), (0x152, 0x78), (0x156, 0), (0x150, 0), (0x154, 0x28), (0x158, 10),
                       (0x15A, 0xF0), (0x15E, 0xFF), (0x162, 0), (0x15C, 100), (0x160, 0x32), (0x164, 0x14),
                       (0x166, 0xEB), (0x16A, 0xB9), (0x16E, 0), (0x168, 0x46), (0x16C, 0x28), (0x170, 0),
                       (0x172, 0xA0), (0x176, 0x5A), (0x17A, 0), (0x174, 0x32), (0x178, 0x28), (0x17C, 0),
                       (0x17E, 1)):
            w16(off, v)
        w32(0x18C, k(0x814ADE5C))
        w32(0x190, k(0x814ADE60))
        w32(0x194, k(0x814ADE64))
        w32(0x198, _F["C`"])
        w32(0x19C, k(0x814ADE6C))
        w32(0x1A0, k(0x814ADE70))
        for off, v in ((0x1A4, 0), (0x1A6, 200), (0x1A8, 0x8C), (0x1AA, 0xD2), (0x1AC, 0), (0x1AE, 0x46),
                       (0x1B0, 0), (0x1B2, 0x14), (0x1B4, 0x46)):
            w16(off, v)
        # FUN_813113ec (PAL)
        w32(0x38, k(0x814ADDDC))
        w32(0x3C, k(0x814ADDE0))
        w32(0x48, k(0x814ADDE4))
        w32(0x4C, k(0x814ADDE8))
        w32(0x58, _F["A "])
        w32(0xF4, k(0x814ADDEC))
        u = k(0x814ADDF0)
        w32(0xF8, u)
        w32(0xFC, k(0x814ADDF4))
        w32(0x100, u)
        w32(0x104, k(0x814ADDF8))
        w32(0x108, _F["AP"])
        w32(0x10C, _F["Ap"])
        w32(0x110, _F["AP"])
        w32(0xA4, k(0x814ADDD4))
        u = k(0x814ADDD8)
        w32(0x84, u)
        w32(0x94, u)


BLOCK = None


def block():
    global BLOCK
    if BLOCK is None:
        BLOCK = Block()
    return BLOCK
