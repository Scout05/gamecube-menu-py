"""Export every J3D model from the unpacked Yay0 blobs to OBJ/MTL/PNG.

usage: python export_models.py yay0_dir out_dir
"""
import glob
import os
import sys

import numpy as np
from PIL import Image

import j3d


def export(model, out_dir, base):
    os.makedirs(out_dir, exist_ok=True)
    tex_files = []
    for i, img in enumerate(model.textures):
        fn = f"{base}_{model.texture_names[i]}.png"
        Image.fromarray(img, "RGBA").save(os.path.join(out_dir, fn))
        tex_files.append(fn)
    with open(os.path.join(out_dir, base + ".mtl"), "w") as f:
        for i, fn in enumerate(tex_files):
            f.write(f"newmtl tex{i}\nKd 1 1 1\nmap_Kd {fn}\nmap_d {fn}\n\n")
        f.write("newmtl notex\nKd 1 1 1\n")
    shp_mat = model.shape_material()
    with open(os.path.join(out_dir, base + ".obj"), "w") as f:
        f.write(f"mtllib {base}.mtl\n")
        k = 1
        for si, shp in enumerate(model.shapes):
            t = np.array(shp["tris"])
            if not len(t):
                continue
            mat = shp_mat.get(si, 0)
            tex = model.material_texture[mat] if mat < len(model.material_texture) else None
            f.write(f"o shape{si}\nusemtl {'tex%d' % tex if tex is not None else 'notex'}\n")
            for v in t:
                f.write("v %.5f %.5f %.5f %.4f %.4f %.4f\n" % (v[0], v[1], v[2], v[6], v[7], v[8]))
                f.write("vn %.4f %.4f %.4f\n" % (v[3], v[4], v[5]))
                f.write("vt %.5f %.5f\n" % (v[10], 1 - v[11]))
            for i in range(0, len(t), 3):
                a, b, c = k + i, k + i + 1, k + i + 2
                f.write(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")
            k += len(t)


def main(src, out_dir):
    for p in sorted(glob.glob(os.path.join(src, "*.bin"))):
        data = open(p, "rb").read()
        if data[:4] != b"J3D1":
            continue
        base = os.path.splitext(os.path.basename(p))[0]
        m = j3d.Model(data, base)
        name = m.joint_names[0] if m.joint_names else base
        ntri = sum(len(s["tris"]) // 3 for s in m.shapes)
        export(m, out_dir, f"{base}_{name}")
        texs = ", ".join(f"{n} {i['width']}x{i['height']} {i['format']}" for n, i in zip(m.texture_names, m.texture_info))
        print(f"{base}  {name:20s} joints {len(m.joints):2d} shapes {len(m.shapes):2d} mats {len(m.material_texture):2d} tris {ntri:5d}  [{texs}]")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
