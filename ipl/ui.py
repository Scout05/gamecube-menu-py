"""The IPL's 2D UI engine: TXH0 texture banks, GLH0 layouts, STH0 string tables.

Ported from BS2 0x81308184..0x8130ab14:
  FUN_81309e54  draw a layout image element (with the edge/stretch rules of FUN_81309cec)
  FUN_81309948  draw a frame (9-patch from 4 corner textures)
  FUN_81308538  draw a text element with the ROM font
  FUN_8130a39c  draw string #i of an STH0 table into its layout text box
  FUN_8130a87c  / FUN_8130a964 / FUN_8130a9e8  fade objects
Coordinates are the IPL's: 1/16 pixel, origin top-left of the 592x448 frame.
"""
import os
import struct
import sys

import moderngl
import numpy as np

from .font import Font
from .model import _f4

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gxtex  # noqa: E402

VS = """
#version 330
uniform mat4 u_mvp;
in vec2 a_pos; in vec2 a_uv; in vec4 a_clr;
out vec2 v_uv; out vec4 v_clr;
void main(){ gl_Position = u_mvp * vec4(a_pos / 16.0, 0.0, 1.0); v_uv = a_uv; v_clr = a_clr; }
"""
FS = """
#version 330
uniform sampler2D u_tex; uniform bool u_use_tex_color;
in vec2 v_uv; in vec4 v_clr; out vec4 o;
void main(){
  vec4 t = texture(u_tex, v_uv);
  vec3 rgb = u_use_tex_color ? t.rgb * v_clr.rgb : v_clr.rgb;
  o = vec4(rgb, t.a * v_clr.a);
}
"""


def rgba(c):
    """0xRRGGBBAA int or 4-tuple of 0..255 -> float tuple."""
    if isinstance(c, int):
        return ((c >> 24) & 255) / 255.0, ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0
    return tuple(x / 255.0 for x in c)


def fourcc(s):
    return struct.unpack(">I", s.encode("ascii"))[0] if isinstance(s, str) else s


def _intensity_to_alpha(img, fmt):
    if fmt in ("I4", "I8"):
        # intensity textures: colour comes from the vertex/material colour, alpha = intensity
        img = img.copy()
        img[..., 3] = img[..., 0]
        img[..., :3] = 255
    return img


def bti_texture(ctx, data, off=0):
    """A standalone BTI image (the 'img2' resources: digits, weekdays, labels) -> (texture, w, h)."""
    img, info = gxtex.decode_bti(data, off)
    img = _intensity_to_alpha(img, info["format"])
    tex = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
    tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
    tex.repeat_x = tex.repeat_y = False
    return tex, info["width"], info["height"]


class TextureBank:
    """TXH0: 'TXH0', u16 count, then 0x20-byte BTI headers (offsets relative to each header)."""

    def __init__(self, ctx, data):
        assert data[:4] == b"TXH0"
        n = struct.unpack_from(">H", data, 4)[0]
        self.textures = []
        for i in range(n):
            img, info = gxtex.decode_bti(data, 0x20 + 0x20 * i)
            img = _intensity_to_alpha(img, info["format"])
            tex = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
            self.textures.append((tex, info["width"], info["height"]))


class Layout:
    """GLH0."""

    def __init__(self, data):
        assert data[:4] == b"GLH0"
        self.data = data
        eo, to, fo = struct.unpack_from(">III", data, 4)
        ne, nt, nf = struct.unpack_from(">HHH", data, 0x10)
        self.elements = []
        for i in range(ne):
            o = eo + 0x10 * i
            name, x, y, w, h, tex, f1, f2 = struct.unpack_from(">IHHHHHBB", data, o)
            self.elements.append(dict(name=name, x=x, y=y, w=w, h=h, tex=tex, f1=f1, f2=f2))
        self.texts = []
        for i in range(nt):
            o = to + 0x24 * i
            name, x, y, w, h, c0, c1, ax, ay, sp, lsp, size, frame, ln, soff = struct.unpack_from(
                ">IHHHHIIBBhhHHHI", data, o)
            s = data[o + soff:o + soff + ln].decode("latin-1") if soff else ""
            self.texts.append(dict(name=name, x=x, y=y, w=w, h=h, c0=c0, c1=c1, ax=ax, ay=ay, spacing=sp,
                                   line=lsp, size=size, frame=frame, text=s))
        self.frames = []
        for i in range(nf):
            o = fo + 0x30 * i
            name, x, y, w, h = struct.unpack_from(">IHHHH", data, o)
            corners = struct.unpack_from(">4H", data, o + 0x14)
            flags = data[o + 0x1C]
            color = struct.unpack_from(">I", data, o + 0x28)[0]
            self.frames.append(dict(name=name, x=x, y=y, w=w, h=h, corners=corners, flags=flags, color=color))

    def element(self, name):
        n = fourcc(name)
        return [e for e in self.elements if e["name"] == n]


class Strings:
    """STH0: 'STH0', u16 count, then (u16 text box, u16 length, u32 self-relative offset)."""

    def __init__(self, data):
        assert data[:4] == b"STH0"
        n = struct.unpack_from(">H", data, 4)[0]
        self.entries = []
        for i in range(n):
            o = 8 + 8 * i
            box, ln, off = struct.unpack_from(">HHI", data, o)
            self.entries.append((box, data[o + off:o + off + ln].decode("latin-1")))


def edge(f1, f2, lo, center, hi, tex_size, mask_lo, mask_hi):
    """FUN_81309cec: where an image edge lands and which part of the texture shows (s1.14)."""
    span = hi - lo
    if f1 & mask_lo and f1 & mask_hi:
        return lo, hi, 0, 0x4000
    if f1 & mask_lo:
        if (f2 & 0x18) == 0 and tex_size < span:
            return lo, lo + tex_size, 0, 0x4000
        return lo, hi, 0, (span * 0x4000) // tex_size
    if f1 & mask_hi:
        if (f2 & 0x18) == 0 and tex_size < span:
            return hi - tex_size, hi, 0, 0x4000
        return lo, hi, ((lo + (tex_size - hi)) * 0x4000) // tex_size, 0x4000
    if (f2 & 0x18) == 0 and tex_size < span:
        a = center - tex_size // 2
        return a, a + tex_size, 0, 0x4000
    d = (span * 0x2000) // tex_size
    return lo, hi, 0x2000 - d, 0x2000 + d


class UI:
    """Batched 2D drawing with the IPL's layout semantics."""

    def __init__(self, ctx, renderer):
        self.ctx = ctx
        self.r = renderer
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.vbo = ctx.buffer(reserve=1 << 20)
        self.vao = ctx.vertex_array(self.prog, [(self.vbo, "2f 2f 4f", "a_pos", "a_uv", "a_clr")])
        self.white = ctx.texture((1, 1), 4, b"\xff\xff\xff\xff")
        self.font = Font.load()
        sheet = self.font.sheet
        img = np.zeros(sheet.shape + (4,), np.uint8)
        img[..., :3] = 255
        img[..., 3] = sheet
        self.font_tex = ctx.texture((sheet.shape[1], sheet.shape[0]), 4, img.tobytes())
        self.font_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.mvp = None
        self.screen()

    # -- transforms ---------------------------------------------------------
    def screen(self):
        """FUN_813030b0: 2D in screen pixels (x - 292, 224 - y) in front of the camera."""
        m = np.array([[1, 0, 0, -292.0], [0, -1, 0, 224.0], [0, 0, 1, 0], [0, 0, 0, 1]])
        self.mvp = self.r.proj @ self.r.view2d() @ m

    def set_matrix(self, world):
        """Draw the UI on a 3D plane (e.g. a face of the menu cube)."""
        self.mvp = self.r.proj @ world

    # -- primitives -------------------------------------------------------
    def quads(self, tex, verts, use_tex_color=False):
        if not verts:
            return
        data = np.array(verts, "f4").tobytes()
        if len(data) > self.vbo.size:
            self.vbo.orphan(len(data) * 2)
        self.vbo.write(data)
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        self.prog["u_mvp"].write(_f4(self.mvp))
        self.prog["u_use_tex_color"].value = use_tex_color
        (tex or self.white).use(0)
        self.prog["u_tex"].value = 0
        self.vao.render(moderngl.TRIANGLES, vertices=len(verts) // 8)

    @staticmethod
    def quad(x0, y0, x1, y1, s0, t0, s1, t1, c):
        r, g, b, a = c
        out = []
        for (x, y, s, t) in ((x0, y0, s0, t0), (x1, y0, s1, t0), (x1, y1, s1, t1),
                             (x0, y0, s0, t0), (x1, y1, s1, t1), (x0, y1, s0, t1)):
            out += [x, y, s, t, r, g, b, a]
        return out

    # -- layout elements (FUN_81309e54) --------------------------------------
    def draw_element(self, layout, bank, e, color, offx=0, offy=0, tex_override=None):
        c = rgba(color)
        if c[3] <= 0:
            return
        x = offx + e["x"]
        y = offy + e["y"]
        left = x - e["w"] // 2
        top = y - e["h"] // 2
        if isinstance(tex_override, tuple):     # FUN_8130a318: an image that is not in the bank
            tex, tw, th = tex_override
        else:
            idx = e["tex"] if tex_override is None else tex_override
            tex, tw, th = bank.textures[idx] if idx < len(bank.textures) else (None, 0x60, 0x20)
        if e["f2"] & 4:
            tw, th = th, tw
        y0, y1, t0, t1 = edge(e["f1"], e["f2"], top, y, top + e["h"], th, 2, 1)
        x0, x1, s0, s1 = edge(e["f1"], e["f2"], left, x, left + e["w"], tw, 8, 4)
        if e["f2"] & 1:
            s0, s1 = s1, s0
        if e["f2"] & 2:
            t0, t1 = t1, t0
        k = 1.0 / 0x4000
        if e["f2"] & 4:   # rotated 90 degrees
            v = []
            for (px, py, s, t) in ((x0, y0, t0, s1), (x1, y0, t0, s0), (x1, y1, t1, s0),
                                   (x0, y0, t0, s1), (x1, y1, t1, s0), (x0, y1, t1, s1)):
                v += [px, py, s * k, t * k, *c]
            self.quads(tex, v, use_tex_color=True)
        else:
            self.quads(tex, self.quad(x0, y0, x1, y1, s0 * k, t0 * k, s1 * k, t1 * k, c), use_tex_color=True)

    def draw_layout(self, layout, bank, color, offx=0, offy=0):
        """FUN_8130a158."""
        for e in layout.elements:
            self.draw_element(layout, bank, e, color, offx, offy)

    def draw_named(self, name, layout, bank, color, offx=0, offy=0, tex=None):
        """FUN_8130a1f8 / FUN_8130a284 (and FUN_8130a318 with an explicit image)."""
        n = fourcc(name)
        for e in layout.elements:
            if e["name"] == n:
                self.draw_element(layout, bank, e, color, offx, offy, tex_override=tex)

    def draw_frames_named(self, name, layout, bank, color, dy=0):
        """FUN_81309c3c: every frame with this name, at its layout position."""
        n = fourcc(name)
        for k, f in enumerate(layout.frames):
            if f["name"] == n:
                self.draw_frame(layout, bank, k, color, f["x"] - f["w"] // 2, f["y"] - f["h"] // 2 + dy, f["w"], f["h"])

    # -- frames (FUN_81309948) ----------------------------------------------
    def draw_frame(self, layout, bank, index, color, x, y, w, h):
        f = layout.frames[index]
        fc = f["color"]
        alpha = min(255, (rgba(color)[3] * 255) * (fc & 0xFF) / 255.0)
        c = (((fc >> 24) & 255) / 255.0, ((fc >> 16) & 255) / 255.0, ((fc >> 8) & 255) / 255.0, alpha / 255.0)
        tex0, cw, ch = bank.textures[f["corners"][0]]
        cw16, ch16 = cw * 16, ch * 16
        x0, y0 = x - cw16, y - ch16
        x1, y1 = x + w, y + h
        # 4 corners + 4 edges + centre (the IPL stretches corner textures for the edges)
        for ci, (ax, ay) in enumerate(((x0, y0), (x1, y0), (x0, y1), (x1, y1))):
            tex = bank.textures[f["corners"][ci]][0]
            fl = (f["flags"] >> (6 - 2 * ci)) & 3
            s0, s1 = (1.0, 0.0) if fl & 1 else (0.0, 1.0)
            t0, t1 = (1.0, 0.0) if fl & 2 else (0.0, 1.0)
            self.quads(tex, self.quad(ax, ay, ax + cw16, ay + ch16, s0, t0, s1, t1, c))
        tex = bank.textures[f["corners"][0]][0]
        self.quads(tex, self.quad(x, y0, x1, y0 + ch16, 1.0, 0.0, 1.0, 1.0, c))
        self.quads(tex, self.quad(x, y1, x1, y1 + ch16, 1.0, 1.0, 1.0, 0.0, c))
        self.quads(tex, self.quad(x0, y, x0 + cw16, y1, 0.0, 1.0, 1.0, 1.0, c))
        self.quads(tex, self.quad(x1, y, x1 + cw16, y1, 1.0, 1.0, 0.0, 1.0, c))
        self.quads(tex, self.quad(x, y, x1, y1, 1.0, 1.0, 1.0, 1.0, c))

    # -- text (FUN_81308a78 / FUN_81308538) --------------------------------
    def _advance(self, box, ch):
        f = self.font
        g = ord(ch) - 0x20 if 0x20 < ord(ch) < 0x100 else 0
        return box["spacing"] * 16 + (box["size"] * f.widths[g] * 16) // f.cell_w

    def _lines(self, box, text, wrap=True):
        lines, cur, width = [], "", 0
        for ch in text:
            if ch == "\n":
                lines.append((cur, width))
                cur, width = "", 0
                continue
            adv = self._advance(box, ch)
            if wrap and width + adv - (box["spacing"] * 16 if box["spacing"] > 0 else 0) > box["w"] and cur:
                lines.append((cur, width))
                cur, width = "", 0
            cur += ch
            width += adv
        lines.append((cur, width))
        return lines

    def measure(self, box, text):
        lines = self._lines(box, text)
        return max(w for _, w in lines), len(lines)

    def draw_text(self, box, text, color, color2=None, offx=0, offy=0, outline=None, wrap=True):
        f = self.font
        lines = self._lines(box, text, wrap=wrap)
        line_h = box["line"] * 16
        asc = (f.ascent * box["size"] * 16) // f.cell_h + (f.descent * box["size"] * 16) // f.cell_h
        total = asc + line_h * (len(lines) - 1)
        x, y = box["x"] + offx, box["y"] + offy
        ay = box["ay"]
        if ay == 1:
            top = y + box["h"] // 2 - total
        elif ay == 0:
            top = y - total // 2
        else:
            top = y - box["h"] // 2
        c0 = rgba(color)
        c1 = rgba(color2) if color2 is not None else c0
        verts = []
        if outline is not None:
            oc = rgba(outline)
            oc = (oc[0], oc[1], oc[2], oc[3] / 6.0)
            for dy in range(-64, 65, 32):
                for dx in range(-64, 65, 32):
                    verts += self._text_verts(box, lines, top, x + dx, dy, oc, oc)
        verts += self._text_verts(box, lines, top, x, 0, c0, c1)
        self.quads(self.font_tex, verts)

    def _text_verts(self, box, lines, top, x, dy, c0, c1):
        f = self.font
        size16 = box["size"] * 16
        ax = box["ax"] & 0x7F
        verts = []
        for li, (line, width) in enumerate(lines):
            if ax == 1:
                cx = x + box["w"] // 2 - width
            elif ax == 0:
                cx = x - width // 2
            else:
                cx = x - box["w"] // 2
            cy = top + li * box["line"] * 16 + dy
            for ch in line:
                code = ord(ch)
                g = code - 0x20 if 0x20 < code < 0x100 else 0
                col, row = g % f.cols, g // f.cols
                s0 = col * f.cell_w / f.sheet.shape[1]
                t0 = row * f.cell_h / f.sheet.shape[0]
                s1 = s0 + f.cell_w / f.sheet.shape[1]
                t1 = t0 + f.cell_h / f.sheet.shape[0]
                r0, g0, b0, a0 = c0
                r1, g1, b1, a1 = c1
                X0, Y0, X1, Y1 = cx, cy, cx + size16, cy + size16
                for (px, py, s, t, cc) in ((X0, Y0, s0, t0, c0), (X1, Y0, s1, t0, c0), (X1, Y1, s1, t1, c1),
                                           (X0, Y0, s0, t0, c0), (X1, Y1, s1, t1, c1), (X0, Y1, s0, t1, c1)):
                    verts += [px, py, s, t, *cc]
                cx += self._advance(box, ch)
        return verts

    def draw_string(self, strings, i, layout, bank, color, outline=None, offx=0, offy=0):
        """FUN_8130a39c: string #i of an STH0 table, in its layout text box (with its frame)."""
        box_i, text = strings.entries[i]
        box = layout.texts[box_i]
        # FUN_813092d8: the size of the text with only its own line breaks
        lines = self._lines(box, text, wrap=False)
        w = max(lw for _, lw in lines)
        n = len(lines)
        h = box["size"] * 16 + (n - 1) * box["line"] * 16
        if box["frame"] != 0xFFFF and box["frame"] < len(layout.frames):
            fr = layout.frames[box["frame"]]
            self.draw_frame(layout, bank, box["frame"], color, fr["x"] + offx - w // 2, fr["y"] + offy - h // 2, w, h)
        if (box["ax"] & 0x7F) == 0:
            # centred boxes grow to fit their text, so they never wrap
            box = dict(box, w=w + 0x100, h=h + 0x100)
        self.draw_text(box, text, color, color, offx, offy, outline=outline)


class Fader:
    """The IPL's 16-byte fade object (FUN_8130aaec / FUN_8130a87c / FUN_8130a964)."""

    def __init__(self, length=20, delay=0, max_alpha=255, slide=0, index=0):
        self.value = 0
        self.length = length
        self.delay = delay
        self.max_alpha = max_alpha
        self.slide = slide
        self.index = index
        self.ticks = 0

    def step(self, mode):
        """mode 0 = fade in, 1 = fade out, 2 = pulse around the middle."""
        if mode == 1:
            self.value -= 1
            if self.value < self.delay:
                self.value = 0
        elif mode == 0:
            self.value += 1
            if self.value > self.length + self.delay:
                self.value = self.length + self.delay
        elif mode == 2:
            if self.value < self.delay:
                self.value += 1
            else:
                mid = self.length // 2
                d = self.value - self.delay
                if d > mid:
                    self.value -= 1
                elif d < mid:
                    self.value += 1
        self.ticks += 1

    def alpha(self):
        if self.value < self.delay:
            return 0
        return self.max_alpha * (self.value - self.delay) // self.length

    def offset(self):
        if self.value < self.delay:
            return self.slide * 16
        return self.slide * 16 * (self.delay + self.length - self.value) // self.length
