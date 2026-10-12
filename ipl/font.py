"""The IPL's ROM font (Dolphin OS ANSI font, ROM 0x1FCF00 -> Yay0)."""
import os
import struct
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..")


class Font:
    def __init__(self, data):
        (self.type, self.first, self.last, self.invalid, self.ascent, self.descent, self.width,
         self.leading, self.cell_w, self.cell_h, size, fmt, self.cols, self.rows, sw, sh,
         wt, img) = struct.unpack_from(">HHHHHHHHHHIHHHHHHI", data, 0)
        n = self.last - self.first + 1
        self.widths = list(data[wt:wt + n])
        # sheet is stored 2 bits/pixel in I4 tile order (8x8 tiles)
        packed = np.frombuffer(data[img:img + sw * sh // 4], np.uint8)
        px = np.stack([(packed >> 6) & 3, (packed >> 4) & 3, (packed >> 2) & 3, packed & 3], -1).reshape(-1)
        tiles = px.reshape(sh // 8, sw // 8, 8, 8).transpose(0, 2, 1, 3).reshape(sh, sw)
        self.sheet = (tiles * 85).astype(np.uint8)

    @classmethod
    def load(cls):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from yay0 import yay0_decompress
        from . import rom as _rom
        rom = _rom.load()
        return cls(yay0_decompress(rom, 0x1FCF00))

    def glyph(self, ch):
        c = ord(ch) if isinstance(ch, str) else ch
        if c < self.first or c > self.last:
            c = self.invalid
        i = c - self.first
        x = (i % self.cols) * self.cell_w
        y = (i // self.cols) * self.cell_h
        return self.sheet[y:y + self.cell_h, x:x + self.cell_w], self.widths[i]

    def text_width(self, text, scale=1.0, spacing=0):
        return int(sum((self.glyph(c)[1] + spacing) * scale for c in text))

    def render(self, text, scale=1.0, spacing=0):
        """Return an (h, w) uint8 coverage image of a single line."""
        w = max(1, self.text_width(text, 1.0, spacing))
        out = np.zeros((self.cell_h, w + self.cell_w), np.uint8)
        x = 0
        for c in text:
            g, gw = self.glyph(c)
            region = out[:, x:x + self.cell_w]
            np.maximum(region, g[:, :region.shape[1]], out=region)
            x += gw + spacing
        out = out[:, :max(1, x)]
        if scale != 1.0:
            from PIL import Image
            im = Image.fromarray(out).resize((max(1, int(out.shape[1] * scale)), max(1, int(out.shape[0] * scale))),
                                             Image.BILINEAR)
            out = np.asarray(im)
        return out
