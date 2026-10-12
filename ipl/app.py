"""Interactive window: boot intro -> zoom -> main menu (ipl/menu).

Controls (keyboard / gamepad):
  hold Z (or gamepad Z/RB) while starting  -> hidden intro variants
      Z           -> variant 1 (pad 1 Z)     ; Z + Shift -> variant 2 (all four pads)
  A  : Enter / X key / gamepad A   (hold during the intro to spin the cube)
  B  : Backspace / C key / gamepad B      START: Space / S key / gamepad button 7
  arrows / stick : menu cube
  F11: fullscreen, Esc: quit
"""
import os
import sys
import time

import moderngl
import numpy as np
import pygame

from . import mathx, rom
from .audio import Audio
from .intro import Intro, load_script
from .menu.calendar import CalendarMenu, Clock
from .menu.dotmatrix import CubeRenderer
from .menu.hub import Hub, Pad, Settings
from .menu.gameplay import GameMenu
from .menu.memcard import MemcardMenu
from .menu.options import OptionsMenu
from .menu_params import MenuParams
from .render import H, W, Renderer
from .ui import UI

ROOT = os.path.join(os.path.dirname(__file__), "..")

BLIT_VS = """
#version 330
in vec2 a_pos; out vec2 v_uv;
void main(){ v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
"""
BLIT_FS = """
#version 330
uniform sampler2D u_tex; in vec2 v_uv; out vec4 o;
void main(){ o = texture(u_tex, v_uv); }
"""


class Input:
    def __init__(self):
        pygame.joystick.init()
        self.pads = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        self.held = set()
        self.pressed = set()

    def poll(self, events):
        self.pressed = set()
        keys = pygame.key.get_pressed()
        held = set()
        if keys[pygame.K_RETURN] or keys[pygame.K_x]:
            held.add("A")
        if keys[pygame.K_BACKSPACE] or keys[pygame.K_c]:
            held.add("B")
        if keys[pygame.K_z]:
            held.add("Z")
        if keys[pygame.K_SPACE] or keys[pygame.K_s]:
            held.add("START")
        if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]:
            held.add("SHIFT")
        for name, k in (("LEFT", pygame.K_LEFT), ("RIGHT", pygame.K_RIGHT), ("UP", pygame.K_UP), ("DOWN", pygame.K_DOWN)):
            if keys[k]:
                held.add(name)
        for p in self.pads:
            try:
                if p.get_button(0):
                    held.add("A")
                if p.get_button(1):
                    held.add("B")
                if p.get_numbuttons() > 5 and p.get_button(5):
                    held.add("Z")
                if p.get_numbuttons() > 7 and p.get_button(7):
                    held.add("START")
                x, y = p.get_axis(0), p.get_axis(1)
                if x < -0.5:
                    held.add("LEFT")
                if x > 0.5:
                    held.add("RIGHT")
                if y < -0.5:
                    held.add("UP")
                if y > 0.5:
                    held.add("DOWN")
                if p.get_numhats():
                    hx, hy = p.get_hat(0)
                    if hx < 0:
                        held.add("LEFT")
                    if hx > 0:
                        held.add("RIGHT")
                    if hy > 0:
                        held.add("UP")
                    if hy < 0:
                        held.add("DOWN")
            except pygame.error:
                pass
        self.pressed = held - self.held
        self.held = held


class App:
    def __init__(self, fps=60, scale=2):
        pygame.init()
        self.fps = fps
        self.size = (W * scale, int(W * scale * 3 / 4))
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
        pygame.display.set_mode(self.size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
        pygame.display.set_caption("GameCube IPL (Python re-creation)")
        self.ctx = moderngl.create_context()
        self.screen = self.ctx.screen
        self.color = self.ctx.texture((W, H), 4)
        self.color.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.depth = self.ctx.depth_renderbuffer((W, H))
        self.fbo = self.ctx.framebuffer([self.color], self.depth)
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4")
        self.blit_prog = self.ctx.program(vertex_shader=BLIT_VS, fragment_shader=BLIT_FS)
        self.blit_vao = self.ctx.vertex_array(self.blit_prog, [(self.ctx.buffer(quad.tobytes()), "2f", "a_pos")])
        self.rom = rom.load()
        self.audio = Audio(rom=self.rom)
        self.input = Input()
        self.renderer = Renderer(self.ctx)
        self.params = MenuParams()
        self.ui = UI(self.ctx, self.renderer)
        self.pad = Pad()
        self.settings = Settings(os.path.join(ROOT, "settings.json"))
        self.clock = Clock(self.settings)
        self.cubes = CubeRenderer(self.ctx)
        self.restart()

    def restart(self):
        if getattr(self, "intro", None) is not None and self.intro.state == 5:
            self.audio.menu_music(False)
        self.intro = Intro(load_script(self.rom), self.audio)
        self.params = MenuParams()
        self.hub = Hub(self.ctx, self.renderer, self.ui, self.audio, self.params, self.settings)
        self.hub.clock = self.clock
        self.hub.submenus[8] = CalendarMenu(self.hub, self.clock, self.cubes)
        self.hub.submenus[6] = OptionsMenu(self.hub, self.cubes, self.apply_settings)
        self.hub.submenus[2] = GameMenu(self.hub, self.cubes)
        self.hub.submenus[4] = MemcardMenu(self.hub, os.environ.get("GCIPL_CARDS", os.path.join(ROOT, "cards")))
        self.apply_settings(self.hub.settings)

    def apply_settings(self, st, preview_offset=None):
        """What the console does when SRAM changes: sound mode and VIShift."""
        self.audio.mono = not st.stereo
        self.screen_offset = st.offset if preview_offset is None else preview_offset
        if preview_offset is None:
            st.save()
        self.input.poll([])
        held = self.input.held
        if "Z" in held:
            self.intro.variant = 2 if "SHIFT" in held else 1

    def update(self):
        self.intro.spin_button = "A" in self.input.held
        if self.intro.state in (1, 4):
            before = self.intro.state
            self.intro.tick(self.params)
            if before == 4 and self.intro.state == 5:
                self.audio.menu_music(True)     # FUN_8130bdb8 case 5: FUN_8135ac20(0)
                self.hub.scale = self.intro.demo.scale
        else:
            self.pad.update(self.input.held, self.input.pressed)
            self.hub.tick(self.pad)

    def draw(self):
        self.fbo.use()
        self.fbo.depth_mask = True   # glClear honours the depth mask; a translucent material may have left it off
        self.fbo.clear(0.0, 0.0, 0.0, 1.0)
        r = self.renderer
        if self.intro.state == 1:
            r.draw_intro(self.intro, self.params)
        elif self.intro.state == 4:
            r.draw_zoom(self.intro, self.params)
            # FUN_8130e3c8: the face titles fade in on the menu cube (FUN_81312f88)
            d = self.intro.demo
            end = d.ph_decel + d.ph_settle + d.ph_spin
            start = end - d.ph_fade
            if d.frame >= start and self.intro.menu_wob is not None:
                a = 255 if d.frame > end else (d.frame - start) * 255 // (end - start)
                world = mathx.rot_trans(0, 0, 0, 0, d.angle, 0) @ self.intro.menu_wob
                self.hub.draw_titles(r.view(self.intro.camera_y()), world, a)
        else:
            self.hub.draw()
        # present 640x448 as 4:3, letterboxed in the window
        self.screen.use()
        self.screen.clear(0.0, 0.0, 0.0, 1.0)
        ww, wh = pygame.display.get_window_size()
        tw = min(ww, wh * 4 // 3)
        th = tw * 3 // 4
        shift = int(getattr(self, "screen_offset", 0) * 2 * tw / W)   # VI offset, in 2-pixel steps
        self.screen.viewport = ((ww - tw) // 2 + shift, (wh - th) // 2, tw, th)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.BLEND)
        self.ctx.disable(moderngl.CULL_FACE)
        self.color.use(0)
        self.blit_vao.render(moderngl.TRIANGLE_STRIP)
        self.screen.viewport = (0, 0, ww, wh)
        pygame.display.flip()

    def run(self, max_frames=None):
        clock = pygame.time.Clock()
        running = True
        frames = 0
        while running:
            frames += 1
            if max_frames and frames > max_frames:
                break
            events = pygame.event.get()
            for e in events:
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE:
                        running = False
                    elif e.key == pygame.K_F11:
                        pygame.display.toggle_fullscreen()
                    elif e.key == pygame.K_r:
                        self.restart()
            self.input.poll(events)
            self.update()
            self.draw()
            clock.tick(self.fps)
        self.audio.close()
        pygame.quit()


def main():
    fps = 50 if "--pal50" in sys.argv else 60
    max_frames = None
    if "--frames" in sys.argv:
        max_frames = int(sys.argv[sys.argv.index("--frames") + 1])
    App(fps=fps).run(max_frames)


if __name__ == "__main__":
    main()
