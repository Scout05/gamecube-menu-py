"""Scene rendering for the IPL re-creation (moderngl)."""
import os

import moderngl
import numpy as np

from . import mathx, rom
from .anim import ColorAnim, JointAnim
from .model import GpuModel, _f4

ROOT = os.path.join(os.path.dirname(__file__), "..")
W, H = 640, 448          # GameCube EFB size
NEAR, FAR, FOVY = 50.0, 10000.0, 20.0
CAM_DIST = 224.0 / np.tan(np.radians(FOVY) / 2)   # 1270.37: 224 units = half the screen
LIGHT_WORLD = np.array([400.0, 500.0, 200.0, 1.0])

TILE_VS = """
#version 330
uniform mat4 u_mvp;
in vec3 a_pos; in vec2 a_uv; in float a_alpha;
out vec2 v_uv; out float v_a;
void main() { gl_Position = u_mvp * vec4(a_pos, 1.0); v_uv = a_uv; v_a = a_alpha; }
"""
TILE_FS = """
#version 330
uniform sampler2D u_tex; uniform vec3 u_color; uniform float u_fade;
in vec2 v_uv; in float v_a; out vec4 o;
void main() {
    vec2 m = mod(v_uv, 2.0);                      // GX_MIRROR wrap: the texture is one tile corner,
    float i = texture(u_tex, min(m, 2.0 - m)).r;  // mirrored over UV 0..2 into a whole tile
    o = vec4(u_color * i, v_a * u_fade * i);
}
"""


def res_bytes(index):
    return rom.res_bytes(index)


BACKDROP_VS = """
#version 330
uniform mat4 u_mvp;
in vec2 a_pos; out vec2 v_pos;
void main(){ gl_Position = u_mvp * vec4(a_pos, 0.0, 1.0); v_pos = a_pos; }
"""
BACKDROP_FS = """
#version 330
uniform sampler2D u_tex; uniform float u_alpha; uniform float u_center;
uniform vec4 u_spot0; uniform vec4 u_spot1;     // FUN_8130b26c: x, y, z, strength
in vec2 v_pos; out vec4 o;
float light(vec3 at, float cutoff, float bright){
    vec3 L = at - vec3(v_pos, 0.0);
    float d = length(L);
    float c = L.z / d;                                  // cos to the spot axis (0, 0, -1)
    float cr = cos(radians(cutoff));
    float spot = max(0.0, (-cr * c + c * c) / (1.0 - cr));     // GX_SP_COS2
    float k1 = (1.0 - bright) / (bright * 500.0);              // GX_DA_GENTLE, ref. distance 500
    return spot / (1.0 + k1 * d) * c;                          // * N.L (normal +z)
}
void main(){
    float lit = u_center * light(vec3(296.0, 224.0, 474.0), 36.0, 0.375);
    lit += u_spot0.w * light(u_spot0.xyz, 28.0, 0.525);
    lit += u_spot1.w * light(u_spot1.xyz, 28.0, 0.525);
    lit = min(lit, 1.0);
    vec4 t = texture(u_tex, v_pos / 32.0);
    o = vec4(vec3(lit) * t.rgb, u_alpha * t.a);
}
"""


class Renderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.backdrop = None
        ctx.front_face = "cw"
        self.proj = mathx.perspective(FOVY, 4.0 / 3.0, NEAR, FAR)
        load = lambda i: GpuModel(ctx, res_bytes(i))
        self.base_cube = load(0x23)    # boot_demo_base_cube (flash on impact)
        self.cover_cube = load(0x24)   # boot_demo_cover_cube (glass shell)
        self.mark = load(0x66)         # boot_demo_mark (the finished logo cube)
        self.menu_cube = load(0x67)    # basecube (the big menu cube)
        self.logo = load(0x6B)         # NINTENDO GAMECUBE wordmark
        self.small_cube = load(0xA8)   # s_cube (the rolling cube)
        self.face_cube = load(0xB2)    # cube_in_manu (the little cubes inside the menu cube)
        self.logo.anim = JointAnim(res_bytes(0x69))
        self.logo_colors = ColorAnim(res_bytes(0x6A))
        # trail tiles
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import gxtex
        img, _ = gxtex.decode_bti(res_bytes(0x65), 0)
        self.tile_tex = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
        self.tile_tex.repeat_x = self.tile_tex.repeat_y = False   # mirroring is done in TILE_FS
        self.tile_prog = ctx.program(vertex_shader=TILE_VS, fragment_shader=TILE_FS)
        self.tile_vbo = ctx.buffer(reserve=4 * 6 * 6 * 3 * 3 * 3 * 64)
        self.tile_vao = ctx.vertex_array(self.tile_prog, [(self.tile_vbo, "3f 2f 1f", "a_pos", "a_uv", "a_alpha")])

    # -- helpers ----------------------------------------------------------
    def view(self, cam_y):
        """FUN_81302df0: lookAt((0, y, d), up (0,1,0), (0, y, 0))."""
        return mathx.look_at((0.0, cam_y, CAM_DIST), (0.0, 1.0, 0.0), (0.0, cam_y, 0.0))

    def view2d(self):
        """FUN_813030b0: the 2D overlay camera (the menu camera at y = 0)."""
        return self.view(0.0)

    def view2d_vp(self):
        """projection x DAT_814af660 (the camera the dot-matrix cubes use)."""
        return self.proj @ self.view2d()

    def screen_mvp(self):
        """projection x camera x the 2D placement (x - 292, 224 - y)."""
        m = np.array([[1, 0, 0, -292.0], [0, -1, 0, 224.0], [0, 0, 1, 0], [0, 0, 0, 1]])
        return self.proj @ self.view2d() @ m

    def draw_backdrop(self, alpha, spots=(), center=1.0):
        """FUN_8130af54: 584x448 of tiled res 0x59 under a GX spotlight (COS2 36 deg, gentle attenuation)."""
        if self.backdrop is None:
            from .ui import bti_texture
            self.backdrop_tex = bti_texture(self.ctx, res_bytes(0x59))[0]
            self.backdrop_tex.repeat_x = self.backdrop_tex.repeat_y = True
            self.backdrop = self.ctx.program(vertex_shader=BACKDROP_VS, fragment_shader=BACKDROP_FS)
            q = np.array([0, 0, 584, 0, 0, 448, 584, 448], "f4")
            self.backdrop_vao = self.ctx.vertex_array(self.backdrop, [(self.ctx.buffer(q.tobytes()), "2f", "a_pos")])
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        self.backdrop["u_mvp"].write(_f4(self.screen_mvp()))
        self.backdrop["u_alpha"].value = alpha
        self.backdrop["u_center"].value = center
        sp = list(spots)[:2] + [(0.0, 0.0, 1.0, 0.0)] * (2 - len(list(spots)[:2]))
        self.backdrop["u_spot0"].value = tuple(sp[0])
        self.backdrop["u_spot1"].value = tuple(sp[1])
        self.backdrop_tex.use(0)
        self.backdrop["u_tex"].value = 0
        self.backdrop_vao.render(moderngl.TRIANGLE_STRIP)

    def light(self, view):
        return tuple((view @ LIGHT_WORLD)[:3])

    def draw(self, model, view, world, alpha=1.0, scale=None, only=None):
        if scale is not None:
            world = mathx.scale_cols(world, *scale)
        model.alpha = alpha
        model.draw(self.proj, view, world, self.light(view), only=only)

    def draw_tiles(self, cube, view, cube_world, fade):
        """FUN_813108b8 / FUN_81310ad0: the purple trail tiles."""
        cells = cube.trail_cells()
        if not cells or fade <= 0:
            return
        S = float(cube.unit)
        h = float(cube.inset) / 16.0
        verts = []
        for face, x, y, a in cells:
            A = (x - 16) * S
            B = (y - 16) * S
            al = a / 255.0
            uv = [(0, 0), (0, 2), (2, 2), (2, 0)]
            if face == 0:
                ps = [(-(A + h), 0, -(B + h)), (-(A + S), 0, -(B + h)), (-(A + S), 0, -(B + S)), (-(A + h), 0, -(B + S))]
            elif face == 1:
                ps = [(-(B + h), -(A + h), 0), (-(B + h), -(A + S), 0), (-(B + S), -(A + S), 0), (-(B + S), -(A + h), 0)]
            else:
                ps = [(0, -(B + h), -(A + h)), (0, -(B + h), -(A + S)), (0, -(B + S), -(A + S)), (0, -(B + S), -(A + h))]
            for i in (0, 1, 2, 0, 2, 3):
                verts.extend([*ps[i], *uv[i], al])
        data = np.array(verts, "f4").tobytes()
        if len(data) > self.tile_vbo.size:
            self.tile_vbo.orphan(len(data))
        self.tile_vbo.write(data)
        m = cube_world @ mathx.trans(1.5 * S, 1.5 * S, 1.5 * S)
        mvp = self.proj @ view @ m
        self.tile_prog["u_mvp"].write(_f4(mvp))
        self.tile_prog["u_color"].value = tuple(c / 255.0 for c in cube.color)
        self.tile_prog["u_fade"].value = fade
        self.tile_tex.use(0)
        self.tile_prog["u_tex"].value = 0
        ctx = self.ctx
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        ctx.disable(moderngl.CULL_FACE)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.fbo.depth_mask = False
        self.tile_vao.render(moderngl.TRIANGLES, vertices=len(verts) // 6)
        ctx.fbo.depth_mask = True

    # -- the boot intro ---------------------------------------------------
    def draw_logo(self, view, frame, alpha):
        self.logo.anim_frame = frame
        for slot, mi in enumerate(self.logo_colors.material_index):
            self.logo.mat_color[mi] = self.logo_colors.sample(slot, frame)
        self.draw(self.logo, view, np.eye(4), alpha)

    def draw_intro(self, intro, menu_params):
        """State 1 (and the first frames of state 4): FUN_8130c90c + FUN_8130d73c."""
        d, c = intro.demo, intro.cube
        view = self.view(intro.camera_y())
        world = intro.cube_world()
        # FUN_8130d73c
        self.draw_logo(view, c.logo_frame(), 1.0)
        flash = d.flash_alpha * min(d.impact_a + d.impact_b, 3000) // 3000
        if flash > 0:
            self.draw(self.base_cube, view, world, flash / 255.0, scale=(d.scale,) * 3)
        glass = d.flash_alpha * min(d.shake, 3000) // 3000
        inner, outer = self.cover_cube.materials_with_cull(1), self.cover_cube.materials_with_cull(2)
        if glass > 0:
            self.draw(self.cover_cube, view, world, glass / 255.0, scale=(1.01,) * 3, only=inner)
        trail_alpha = (255 - d.mark_alpha) / 255.0
        if trail_alpha > 0:
            self.draw_tiles(c, view, world, trail_alpha)
            self.draw(self.small_cube, view, c.world_matrix(world), trail_alpha, scale=(d.scale,) * 3)
        if d.mark_alpha > 0:
            self.draw(self.mark, view, world, d.mark_alpha / 255.0)
        if glass > 0:
            self.draw(self.cover_cube, view, world, glass / 255.0, scale=(1.01,) * 3, only=outer)

    def draw_zoom(self, intro, menu_params):
        """State 4: FUN_8130ed30 + FUN_8130e3c8 (spin down into the menu cube)."""
        d = intro.demo
        view = self.view(intro.camera_y())
        world = intro.menu_cube_world(intro.menu_wob)
        if d.zoom_frames < d.frame:
            menu_alpha = d.logo_alpha_max
        else:
            # the intro cube (now the glowing base cube) and the wordmark fade out
            menu_alpha = d.logo_alpha_max * d.frame // d.zoom_frames
            energy = min(d.impact_a + d.impact_b + d.shake, 3000)
            base = (d.zoom_frames - d.frame) * d.flash_alpha * energy // (d.zoom_frames * 3000)
            logo = max(0, base * d.logo_fade // d.logo_fade_len)
            if base > 0:
                self.draw(self.base_cube, view, intro.cube_world(), base / 255.0, scale=(d.scale,) * 3)
            if logo > 0:
                self.draw_logo(view, intro.cube.logo_frame(), logo / 255.0)
        if menu_alpha > 0:
            self.draw(self.menu_cube, view, world, menu_alpha / 255.0)
