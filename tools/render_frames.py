"""Render selected intro frames at full size.

usage: python tools/render_frames.py out.png f1 f2 f3 ...
"""
import os
import sys

import moderngl
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ipl.audio import Audio  # noqa: E402
from ipl.intro import Intro, load_script  # noqa: E402
from ipl.menu_params import MenuParams  # noqa: E402
from ipl.render import H, W, Renderer  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main(out, wanted):
    from ipl import rom as _rom
    rom = _rom.load()
    ctx = moderngl.create_standalone_context()
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    r = Renderer(ctx)
    intro = Intro(load_script(rom), Audio(enabled=False))
    params = MenuParams()
    shots = []
    for f in range(max(wanted) + 1):
        intro.tick(params)
        if f not in wanted:
            continue
        fbo.clear(0, 0, 0, 1)
        if intro.state == 1:
            r.draw_intro(intro, params)
        else:
            r.draw_zoom(intro, params)
        img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
        d = intro.demo
        ImageDraw.Draw(img).text((6, 6), f"f{f} st{intro.state} ang{d.angle} spd{d.speed:.0f} shake{d.shake} mark{d.mark_alpha}",
                                 fill=(255, 255, 0))
        shots.append(img)
    cols = 2
    rows = (len(shots) + 1) // cols
    sheet = Image.new("RGB", (cols * W, rows * H))
    for i, s in enumerate(shots):
        sheet.paste(s, ((i % cols) * W, (i // cols) * H))
    sheet.save(out)


if __name__ == "__main__":
    main(sys.argv[1], [int(x) for x in sys.argv[2:]])
