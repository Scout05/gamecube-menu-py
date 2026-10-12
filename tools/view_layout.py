"""Render GLH0 layouts flat (2D) with the TXH0 bank to PNGs: python tools/view_layout.py OUTDIR id [id...]"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import moderngl  # noqa: E402
import numpy as np  # noqa: E402
import pygame  # noqa: E402
from PIL import Image  # noqa: E402

from ipl.ui import UI, Layout, TextureBank  # noqa: E402


def rb(i):
    return open(glob.glob(os.path.join(os.path.dirname(__file__), "..", "res", f"{i:03d}_*.bin"))[0], "rb").read()


class FakeR:
    def __init__(self):
        self.proj = np.eye(4)

    def view2d(self):
        return np.eye(4)


def main():
    out = sys.argv[1]
    pygame.init()
    pygame.display.set_mode((64, 64), pygame.OPENGL | pygame.HIDDEN)
    ctx = moderngl.create_context()
    W, H = 640, 480
    fbo = ctx.simple_framebuffer((W, H))
    ui = UI(ctx, FakeR())
    bank = TextureBank(ctx, rb(167))
    for i in map(int, sys.argv[2:]):
        L = Layout(rb(i))
        fbo.use()
        fbo.clear(0.15, 0.1, 0.3, 1.0)
        # pixel coords (1/16 units) -> clip
        ui.mvp = np.array([[2 / W / 1, 0, 0, -1], [0, -2 / H, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]]) @ np.diag([1, 1, 1, 1.0])
        ui.mvp = ui.mvp @ np.diag([1.0, 1.0, 1, 1])
        for e in L.elements:
            ui.draw_element(L, bank, e, 0xFFFFFFFF)
        for t in L.texts:
            ui.draw_text(t, t["text"], 0xFFFFFFFF)
        for k, f in enumerate(L.frames):
            ui.draw_frame(L, bank, k, 0xFFFFFF80, f["x"] - f["w"] // 2, f["y"] - f["h"] // 2, f["w"], f["h"])
        img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
        img.save(os.path.join(out, f"layout_{i:03d}.png"))


if __name__ == "__main__":
    main()
