"""GPU-side J3D model: per-shape vertex buffers, GX materials, joint animation."""
import os
import sys

import moderngl
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import j3d  # noqa: E402

from . import gx  # noqa: E402
from .anim import JointAnim  # noqa: F401,E402


def _f4(m):
    return np.ascontiguousarray(np.asarray(m, dtype="f4").T).tobytes()


class GpuModel:
    def __init__(self, ctx, data, name=""):
        self.ctx = ctx
        self.j = j3d.Model(data, name)
        self.name = self.j.joint_names[0] if self.j.joint_names else name
        self.textures = []
        for img, info in zip(self.j.textures, self.j.texture_info):
            t = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
            t.repeat_x = info["wrap_s"] != 0
            t.repeat_y = info["wrap_t"] != 0
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.textures.append(t)
        self.white = ctx.texture((1, 1), 4, b"\xff\xff\xff\xff")
        shp_mat = self.j.shape_material()
        self.parts = []   # draw order from INF1: (material index, vao, program)
        for mat_i, shp_i in self.j.draws:
            local = self.j.shapes[shp_i]["local"]
            if not local:
                continue
            v = np.array(local, "f4")
            vbo = ctx.buffer(v.tobytes())
            mat = self.j.materials[mat_i]
            prog = gx.program_for(ctx, mat)
            vao = ctx.vertex_array(prog, [(vbo, "3f 3f 4f 2f 1f", "a_pos", "a_nrm", "a_clr", "a_uv", "a_mtx")],
                                   skip_errors=True)
            self.parts.append((mat_i, vao, prog))
        self.anim = None
        self.anim_frame = 0.0
        # per-material overrides the IPL code sets at runtime
        self.mat_color = {}       # mat index -> rgba (replaces the material color register)
        self.tev_color = {}       # mat index -> [c0, c1, c2] overrides (GXSetTevColor at draw time)
        self.alpha = 1.0          # global fade (0..1)
        self.amb = (40 / 255.0, 40 / 255.0, 40 / 255.0, 1.0)  # GXSetChanAmbColor (0x282828 in the IPL)

    # -- joints ---------------------------------------------------------------
    def joint_locals(self):
        if self.anim is None:
            return self.j.joints
        out = []
        for i, base in enumerate(self.j.joint_srt):
            if i < len(self.anim.joints):
                srt = self.anim.sample(i, self.anim_frame)
            else:
                srt = base
            out.append(j3d.joint_matrix(*srt))
        return out

    def drw(self):
        world = self.j.world_from_local(self.joint_locals())
        return self.j.drw_matrices(world)

    # -- drawing --------------------------------------------------------------
    def materials_with_cull(self, cull):
        return {i for i, m in enumerate(self.j.materials) if m["cull"] == cull}

    def draw(self, proj, view, model_mtx, light_view_pos=(400.0, 500.0, 200.0), only=None):
        ctx = self.ctx
        drw = self.drw()
        drw_bytes = b"".join(_f4(m) for m in drw) + _f4(np.eye(4)) * (gx.MAX_DRW - len(drw))
        for mat_i, vao, prog in self.parts:
            if only is not None and mat_i not in only:
                continue
            mat = self.j.materials[mat_i]
            mc = self.mat_color.get(mat_i, mat["mat_color"])
            alpha = self.alpha
            if alpha <= 0.0:
                continue
            gx.apply_state(ctx, mat, alpha)
            u = prog
            _set(u, "u_proj", _f4(proj))
            _set(u, "u_view", _f4(view))
            _set(u, "u_model", _f4(model_mtx))
            _set(u, "u_drw", drw_bytes)
            texm = b""
            for k in range(2):
                tg = mat["texgens"][k] if k < len(mat["texgens"]) else None
                m = tg["matrix"] if tg is not None and tg["matrix"] is not None else np.array(
                    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
                texm += np.asarray(m, "f4").tobytes()
            _set(u, "u_texmtx", texm)
            _setv(u, "u_light_pos", light_view_pos)
            _setv(u, "u_light_color", (1.0, 1.0, 1.0, 1.0))
            _setv(u, "u_amb_color", tuple(self.amb))
            _setv(u, "u_mat_color", tuple(mc))
            _setv(u, "u_lighting", bool(mat["lighting"]))
            _setv(u, "u_vtx_color", bool(mat["vertex_color"]))
            c = self.tev_color.get(mat_i, mat["tev_colors"])
            _setv(u, "u_c0", c[0])
            _setv(u, "u_c1", c[1])
            _setv(u, "u_c2", c[2])
            _setv(u, "u_prev", (0.0, 0.0, 0.0, 0.0))
            _setv(u, "u_konst", (1.0, 1.0, 1.0, 1.0))
            _setv(u, "u_alpha_mul", float(alpha))
            texs = [self.textures[t] if t < len(self.textures) else self.white for t in mat["textures"]]
            (texs[0] if texs else self.white).use(0)
            (texs[1] if len(texs) > 1 else self.white).use(1)
            _setv(u, "u_tex0", 0)
            _setv(u, "u_tex1", 1)
            vao.render(moderngl.TRIANGLES)


def _set(prog, name, raw):
    if name in prog:
        uni = prog[name]
        # Mesa trims unused trailing array elements (e.g. u_texmtx[1]), so write only what survived
        uni.write(raw[:max(uni.array_length, 1) * uni.element_size])


def _setv(prog, name, value):
    if name in prog:
        prog[name].value = value
