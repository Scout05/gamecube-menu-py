"""Debug render of the menu cube (basecube) per material."""
import os
import sys

import moderngl
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ipl import mathx  # noqa: E402
from ipl.render import H, W, Renderer  # noqa: E402


def main(out):
    ctx = moderngl.create_standalone_context()
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    r = Renderer(ctx)
    view = r.view(-10.0)
    shots = []
    world = mathx.rot_trans(0, 0, 0, 350, 1000, 1000) @ mathx.scale(1.1, 1.1, 1.1)
    for label, only in [("all", None), ("m0", {0}), ("m1", {1}), ("m2", {2}), ("m3", {3}), ("inner m2+m3", {2, 3})]:
        fbo.clear(0.0, 0.0, 0.0, 1.0)
        r.draw(r.menu_cube, view, world, 1.0, only=only)
        img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
        ImageDraw.Draw(img).text((6, 6), label, fill=(255, 255, 0))
        shots.append(img)
    sheet = Image.new("RGB", (3 * W, 2 * H))
    for i, s in enumerate(shots):
        sheet.paste(s, ((i % 3) * W, (i // 3) * H))
    sheet.save(out)


if __name__ == "__main__":
    main(sys.argv[1])
