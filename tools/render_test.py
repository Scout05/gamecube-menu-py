"""Render IPL models through the GX emulation using matrices captured from RAM.

usage: python tools/render_test.py out.png
"""
import math
import os
import struct
import sys

import moderngl
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ipl.anim import ColorAnim, JointAnim  # noqa: E402
from ipl.model import GpuModel  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
MEM1 = os.environ.get("GCIPL_MEM1", os.path.expanduser("~/.local/share/dolphin-emu/Dump/mem1.raw"))
W, H = 640, 448


def res(i):
    from ipl import rom
    return rom.res_bytes(i)


def perspective(fovy_deg, aspect, near, far):
    f = 1 / math.tan(math.radians(fovy_deg) / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])


def mtx34(mem, addr):
    m = np.eye(4)
    m[:3, :] = np.array(struct.unpack_from(">12f", mem, addr - 0x80000000)).reshape(3, 4)
    return m


def main(out):
    mem = open(MEM1, "rb").read()
    ctx = moderngl.create_standalone_context()
    ctx.front_face = "cw"
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    proj = perspective(20.0, 4 / 3, 50.0, 10000.0)
    eye = np.eye(4)
    shots = []

    def shot(label, draw):
        fbo.clear(0.0, 0.0, 0.0, 1.0)
        draw()
        img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
        shots.append((label, img))

    base = GpuModel(ctx, res(0x23))
    cover = GpuModel(ctx, res(0x24))
    mark = GpuModel(ctx, res(0x66))
    basecube = GpuModel(ctx, res(0x67))
    logo = GpuModel(ctx, res(0x6B))
    logo.anim = JointAnim(res(0x69))
    ipk = ColorAnim(res(0x6A))

    shot("menu cube (live RAM matrices)", lambda: (
        base.draw(proj, eye, mtx34(mem, 0x814af9b8 + 4)),
        mark.draw(proj, eye, mtx34(mem, 0x814afb18 + 4)),
        cover.draw(proj, eye, mtx34(mem, 0x814afbc8 + 4))))
    shot("basecube", lambda: basecube.draw(proj, eye, mtx34(mem, 0x814afa68 + 4)))
    for fr in (10, 25, 40, 64):
        def d(fr=fr):
            logo.anim_frame = fr
            for slot, mi in enumerate(ipk.material_index):
                logo.mat_color[mi] = ipk.sample(slot, fr)
            logo.draw(proj, eye, mtx34(mem, 0x814afc40 + 4))
        shot(f"logo frame {fr}", d)
    cols = 2
    rows = (len(shots) + 1) // 2
    sheet = Image.new("RGB", (cols * W, rows * H))
    from PIL import ImageDraw
    for i, (label, img) in enumerate(shots):
        ImageDraw.Draw(img).text((8, 8), label, fill=(255, 255, 0))
        sheet.paste(img, ((i % cols) * W, (i // cols) * H))
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1])
