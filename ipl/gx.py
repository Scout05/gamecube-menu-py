"""Tiny GameCube GX emulation on top of moderngl.

Turns a decoded J3D material (see tools/j3d.py) into a GLSL program that
reproduces its texgens, color channel and TEV stages, plus the blend / cull /
depth state the material asks for.
"""
import moderngl
import numpy as np

MAX_DRW = 48

VERTEX_SHADER = """
#version 330
uniform mat4 u_proj;
uniform mat4 u_view;          // world -> view (camera)
uniform mat4 u_model;         // model -> world
uniform mat4 u_drw[%(MAX_DRW)d];  // per draw-matrix (joint) transforms, model space
uniform mat3x4 u_texmtx[2];
uniform vec3 u_light_pos;     // view space
uniform vec4 u_light_color;
uniform vec4 u_amb_color;
uniform vec4 u_mat_color;
uniform bool u_lighting;
uniform bool u_vtx_color;
in vec3 a_pos; in vec3 a_nrm; in vec4 a_clr; in vec2 a_uv; in float a_mtx;
out vec4 v_ras; out vec2 v_tc0; out vec2 v_tc1;
%(TEXGEN_DECL)s
void main() {
    mat4 mv = u_view * u_model * u_drw[int(a_mtx)];
    vec4 pv = mv * vec4(a_pos, 1.0);
    vec3 nv = normalize(mat3(mv) * a_nrm);
    gl_Position = u_proj * pv;
    vec4 mat = u_vtx_color ? a_clr : u_mat_color;
    vec4 ras = mat;
    if (u_lighting) {
        float d = max(dot(nv, normalize(u_light_pos - pv.xyz)), 0.0);
        ras.rgb = mat.rgb * clamp(u_amb_color.rgb + u_light_color.rgb * d, 0.0, 1.0);
    }
    v_ras = ras;
%(TEXGEN_CODE)s
}
"""

FRAGMENT_SHADER = """
#version 330
uniform sampler2D u_tex0; uniform sampler2D u_tex1;
uniform vec4 u_c0; uniform vec4 u_c1; uniform vec4 u_c2; uniform vec4 u_prev;
uniform vec4 u_konst;
uniform float u_alpha_mul;    // extra fade the IPL code applies (material alpha register)
in vec4 v_ras; in vec2 v_tc0; in vec2 v_tc1;
out vec4 o_color;
void main() {
    vec4 tex0 = texture(u_tex0, v_tc0);
    vec4 tex1 = texture(u_tex1, v_tc1);
    vec4 prev = u_prev, c0 = u_c0, c1 = u_c1, c2 = u_c2;
    vec4 tex; vec3 r3; float r1;
%(TEV)s
    o_color = vec4(prev.rgb, prev.a * u_alpha_mul);
%(ALPHA_TEST)s
}
"""

CC = ["prev.rgb", "vec3(prev.a)", "c0.rgb", "vec3(c0.a)", "c1.rgb", "vec3(c1.a)", "c2.rgb", "vec3(c2.a)",
      "tex.rgb", "vec3(tex.a)", "v_ras.rgb", "vec3(v_ras.a)", "vec3(1.0)", "vec3(0.5)", "u_konst.rgb", "vec3(0.0)"]
CA = ["prev.a", "c0.a", "c1.a", "c2.a", "tex.a", "v_ras.a", "1.0", "0.0"]
REG = ["prev", "c0", "c1", "c2"]
SCALE = ["1.0", "2.0", "4.0", "0.5"]
BIAS = ["0.0", "0.5", "-0.5", "0.0"]

# GX blend factors -> moderngl
BLEND_FACTOR = {0: moderngl.ZERO, 1: moderngl.ONE, 2: moderngl.SRC_COLOR, 3: moderngl.ONE_MINUS_SRC_COLOR,
                4: moderngl.SRC_ALPHA, 5: moderngl.ONE_MINUS_SRC_ALPHA, 6: moderngl.DST_ALPHA,
                7: moderngl.ONE_MINUS_DST_ALPHA}
COMPARE = ["false", "<", "==", "<=", ">", "!=", ">=", "true"]


def _texgen(mat):
    decl, code = [], []
    for i, g in enumerate(mat["texgens"][:2]):
        if g["src"] == 1:       # GX_TG_NRM: environment map from view-space normal
            src = "vec4(nv, 1.0)"
        elif g["src"] == 0:     # GX_TG_POS
            src = "vec4(pv.xyz, 1.0)"
        else:                   # GX_TG_TEXn (4 = TEX0): (s, t, 1, 1)
            src = "vec4(a_uv, 1.0, 1.0)"
        if g["matrix"] is not None:
            code.append(f"    v_tc{i} = (vec4({src}) * u_texmtx[{i}]).xy;")
        else:
            code.append(f"    v_tc{i} = {src}.xy;")
    for i in range(len(mat["texgens"][:2]), 2):
        code.append(f"    v_tc{i} = vec2(0.0);")
    return "\n".join(decl), "\n".join(code)


def _tev(mat):
    lines = []
    for st in mat["stages"]:
        tc, tm, _ch = st["order"]
        tex = "tex0" if tm in (0, 0xFF) else "tex1"
        lines.append(f"    tex = {tex};")
        a, b, c, d = (CC[x] for x in st["color_in"])
        sign = "-" if st["color_op"] == 1 else "+"
        expr = f"({d} {sign} mix({a}, {b}, {c}) + {BIAS[st['color_bias']]}) * {SCALE[st['color_scale']]}"
        lines.append(f"    r3 = {expr};")
        lines.append(f"    r3 = {'clamp(r3, 0.0, 1.0)' if st['color_clamp'] else 'clamp(r3, -4.0, 4.0)'};")
        aa, ab, ac, ad = (CA[x] for x in st["alpha_in"])
        sign = "-" if st["alpha_op"] == 1 else "+"
        expr = f"({ad} {sign} mix({aa}, {ab}, {ac}) + {BIAS[st['alpha_bias']]}) * {SCALE[st['alpha_scale']]}"
        lines.append(f"    r1 = {expr};")
        lines.append(f"    r1 = {'clamp(r1, 0.0, 1.0)' if st['alpha_clamp'] else 'clamp(r1, -4.0, 4.0)'};")
        lines.append(f"    {REG[st['color_reg']]}.rgb = r3;")
        lines.append(f"    {REG[st['alpha_reg']]}.a = r1;")
    return "\n".join(lines)


def _alpha_test(mat):
    c0, r0, op, c1, r1 = mat["alpha_compare"]
    if c0 == 7 and c1 == 7:
        return ""
    t0 = "true" if c0 == 7 else ("false" if c0 == 0 else f"(o_color.a {COMPARE[c0]} {r0 / 255.0})")
    t1 = "true" if c1 == 7 else ("false" if c1 == 0 else f"(o_color.a {COMPARE[c1]} {r1 / 255.0})")
    joiner = {0: "&&", 1: "||", 2: "!=", 3: "=="}[op]
    return f"    if (!({t0} {joiner} {t1})) discard;"


_cache = {}


def program_for(ctx, mat):
    decl, code = _texgen(mat)
    vs = VERTEX_SHADER % dict(MAX_DRW=MAX_DRW, TEXGEN_DECL=decl, TEXGEN_CODE=code)
    fs = FRAGMENT_SHADER % dict(TEV=_tev(mat), ALPHA_TEST=_alpha_test(mat))
    key = (vs, fs)
    if key not in _cache:
        _cache[key] = ctx.program(vertex_shader=vs, fragment_shader=fs)
    return _cache[key]


def apply_state(ctx, mat, alpha_mul=1.0):
    """Set GL blend / cull / depth to match the GX material."""
    mode, src, dst, _logic = mat["blend"]
    if mode == 1 or alpha_mul < 1.0:
        ctx.enable(moderngl.BLEND)
        if mode == 1:
            ctx.blend_func = BLEND_FACTOR.get(src, moderngl.SRC_ALPHA), BLEND_FACTOR.get(dst, moderngl.ONE_MINUS_SRC_ALPHA)
        else:
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
    else:
        ctx.disable(moderngl.BLEND)
    # GX cull: 0 none, 1 front, 2 back. GX treats clockwise as front (ctx.front_face = "cw").
    cull = mat["cull"]
    if cull == 0:
        ctx.disable(moderngl.CULL_FACE)
    else:
        ctx.enable(moderngl.CULL_FACE)
        ctx.cull_face = "front" if cull == 1 else "back"
    z_enable, z_func, z_update = mat["zmode"]
    if alpha_mul < 1.0:
        # the IPL's fade path (FUN_813060b8 area): extra TEV alpha stage + GXSetZMode(0, LEQUAL, 0)
        z_enable, z_update = 0, 0
    if z_enable:
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = {0: "0", 1: "<", 2: "==", 3: "<=", 4: ">", 5: "!=", 6: ">=", 7: "1"}[z_func]
    else:
        ctx.disable(moderngl.DEPTH_TEST)
    ctx.fbo.depth_mask = bool(z_update)
