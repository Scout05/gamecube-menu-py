"""Render every model to a contact sheet PNG (offscreen, moderngl).

usage: python preview.py yay0_dir out.png
"""
import glob
import math
import os
import sys

import moderngl
import numpy as np
from PIL import Image, ImageDraw

import j3d

VS = """
#version 330
uniform mat4 mvp; uniform mat4 model;
in vec3 pos; in vec3 nrm; in vec4 clr; in vec2 uv;
out vec3 n; out vec4 c; out vec2 t;
void main(){ gl_Position = mvp*vec4(pos,1); n = mat3(model)*nrm; c = clr; t = uv; }
"""
FS = """
#version 330
uniform sampler2D tex; uniform bool has_tex;
in vec3 n; in vec4 c; in vec2 t; out vec4 o;
void main(){
  vec4 base = has_tex ? texture(tex, t) : vec4(1);
  float l = 0.45 + 0.55*abs(dot(normalize(n), normalize(vec3(0.4,0.7,0.6))));
  o = vec4(base.rgb * c.rgb * l, 1);
}
"""


def perspective(fovy, aspect, near, far):
    f = 1 / math.tan(fovy / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])


def look_at(eye, target, up=(0, 1, 0)):
    eye, target, up = map(np.asarray, (eye, target, up))
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def render(ctx, prog, model, size=320):
    fbo = ctx.simple_framebuffer((size, size))
    fbo.use()
    fbo.clear(0.12, 0.10, 0.18, 1.0)
    ctx.enable(moderngl.DEPTH_TEST)
    allv = np.concatenate([np.array(s["tris"]) for s in model.shapes if s["tris"]])
    lo, hi = allv[:, :3].min(0), allv[:, :3].max(0)
    center, radius = (lo + hi) / 2, np.linalg.norm(hi - lo) / 2 or 1
    eye = center + np.array([0.9, 0.7, 1.4]) / np.linalg.norm([0.9, 0.7, 1.4]) * radius * 2.6
    mvp = perspective(math.radians(40), 1, radius * 0.1, radius * 10) @ look_at(eye, center)
    prog["mvp"].write(mvp.T.astype("f4").tobytes())
    prog["model"].write(np.eye(4, dtype="f4").tobytes())
    texs = []
    for img in model.textures:
        t = ctx.texture((img.shape[1], img.shape[0]), 4, np.ascontiguousarray(img).tobytes())
        t.repeat_x = t.repeat_y = True
        texs.append(t)
    mats = model.shape_material()
    for si, shp in enumerate(model.shapes):
        if not shp["tris"]:
            continue
        v = np.array(shp["tris"], "f4")
        vbo = ctx.buffer(v.tobytes())
        vao = ctx.vertex_array(prog, [(vbo, "3f 3f 4f 2f", "pos", "nrm", "clr", "uv")])
        ti = model.material_texture[mats.get(si, 0)] if model.material_texture else None
        prog["has_tex"].value = ti is not None and ti < len(texs)
        if prog["has_tex"].value:
            texs[ti].use(0)
        vao.render(moderngl.TRIANGLES)
        vao.release()
        vbo.release()
    img = Image.frombytes("RGB", (size, size), fbo.read()).transpose(Image.FLIP_TOP_BOTTOM)
    fbo.release()
    for t in texs:
        t.release()
    return img


def main(src, out):
    ctx = moderngl.create_standalone_context()
    prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
    tiles = []
    for p in sorted(glob.glob(os.path.join(src, "*.bin"))):
        data = open(p, "rb").read()
        if data[:4] != b"J3D1":
            continue
        m = j3d.Model(data)
        img = render(ctx, prog, m)
        ImageDraw.Draw(img).text((6, 6), m.joint_names[0], fill=(255, 255, 255))
        tiles.append(img)
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 320, rows * 320))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * 320, (i // cols) * 320))
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
