"""Run the intro headless and save a contact sheet + GIF for checking.

usage: python tools/render_intro.py [frames] [out_prefix]
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


def main(frames=420, prefix="models/_intro"):
    from ipl import rom as _rom
    rom = _rom.load()
    ctx = moderngl.create_standalone_context()
    fbo = ctx.simple_framebuffer((W, H))
    fbo.use()
    r = Renderer(ctx)
    audio = Audio(enabled=False)
    intro = Intro(load_script(rom), audio)
    params = MenuParams()
    shots, gif = [], []
    for f in range(frames):
        state = intro.tick(params)
        fbo.clear(0, 0, 0, 1)
        if intro.state == 1:
            r.draw_intro(intro, params)
        elif intro.state in (4, 5):
            r.draw_zoom(intro, params)
        img = Image.frombytes("RGB", (W, H), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
        if f % 2 == 0:
            gif.append(img.resize((W // 2, H // 2)))
        if f % 15 == 0:
            ImageDraw.Draw(img).text((6, 6), f"f{f} st{state} ph{intro.cube.phase}", fill=(255, 255, 0))
            shots.append(img.resize((W // 2, H // 2)))
        if state == 5:
            break
    cols = 6
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * W // 2, rows * H // 2))
    for i, s in enumerate(shots):
        sheet.paste(s, ((i % cols) * W // 2, (i // cols) * H // 2))
    sheet.save(prefix + "_sheet.png")
    gif[0].save(prefix + ".gif", save_all=True, append_images=gif[1:], duration=33, loop=0)
    print("frames", f + 1, "final state", intro.state, "sounds", audio.log[:20])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 420, sys.argv[2] if len(sys.argv) > 2 else "models/_intro")
