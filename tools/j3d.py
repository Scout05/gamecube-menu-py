"""Minimal J3D (BMD, early 'bmd1' revision as used by the GameCube IPL) loader.

Loads geometry into flat triangle lists per shape (positions already in model
space via the joint hierarchy), plus textures from TEX1.
"""
import math
import struct

import numpy as np

import gxtex

GX_POS, GX_NRM, GX_CLR0, GX_CLR1, GX_TEX0 = 9, 10, 11, 12, 13
PRIMS = {0x80: "quads", 0x90: "tris", 0x98: "strip", 0xA0: "fan"}


def u16(b, o): return struct.unpack_from(">H", b, o)[0]
def u32(b, o): return struct.unpack_from(">I", b, o)[0]


def name_table(b, o, count=None):
    """Standard table: u16 count, pad, (hash, offset)*. The early TEX1 table has no header."""
    if count is None:
        n, first = u16(b, o), o + 4
    else:
        n, first = count, o
    names = []
    for i in range(n):
        off = u16(b, first + 4 * i + 2)
        names.append(b[o + off:b.index(b"\0", o + off)].decode("ascii", "replace"))
    return names


def joint_matrix(sx, sy, sz, rx, ry, rz, tx, ty, tz):
    cx, sx_ = math.cos(rx), math.sin(rx)
    cy, sy_ = math.cos(ry), math.sin(ry)
    cz, sz_ = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx_], [0, sx_, cx]])
    Ry = np.array([[cy, 0, sy_], [0, 1, 0], [-sy_, 0, cy]])
    Rz = np.array([[cz, -sz_, 0], [sz_, cz, 0], [0, 0, 1]])
    m = np.eye(4)
    m[:3, :3] = Rz @ Ry @ Rx @ np.diag([sx, sy, sz])
    m[:3, 3] = [tx, ty, tz]
    return m


class Model:
    def __init__(self, data, name=""):
        self.data = data
        self.name = name
        self.sections = {}
        o = 0x20
        for _ in range(u32(data, 0x0C)):
            tag = data[o:o + 4].decode()
            self.sections[tag] = o
            o += u32(data, o + 4)
        self._vtx1()
        self._jnt1()
        self._drw1_evp1()
        self._inf1()
        self._tex1()
        self._mat1()
        self._shp1()

    # --- sections -------------------------------------------------------
    def _vtx1(self):
        b, s = self.data, self.sections["VTX1"]
        size = u32(b, s + 4)
        fmt_off = s + u32(b, s + 8)
        offs = [u32(b, s + 0x0C + 4 * i) for i in range(13)]
        self.arrays = {}
        o = fmt_off
        fmts = []
        while u32(b, o) != 0xFF:
            attr, cnt, typ = struct.unpack_from(">III", b, o)
            shift = b[o + 12]
            fmts.append((attr, cnt, typ, shift))
            o += 16
        nz = sorted(x for x in offs if x) + [size]
        for attr, cnt, typ, shift in fmts:
            slot = {GX_POS: 0, GX_NRM: 1, GX_CLR0: 3, GX_CLR1: 4}.get(attr, 5 + attr - GX_TEX0)
            start = offs[slot]
            end = next(x for x in nz if x > start)
            raw = b[s + start:s + end]
            if attr in (GX_CLR0, GX_CLR1):
                if typ == 5:    # RGBA8
                    arr = np.frombuffer(raw[:len(raw) // 4 * 4], np.uint8).reshape(-1, 4) / 255.0
                elif typ == 1:  # RGB8
                    arr = np.frombuffer(raw[:len(raw) // 3 * 3], np.uint8).reshape(-1, 3) / 255.0
                    arr = np.hstack([arr, np.ones((len(arr), 1))])
                else:
                    raise NotImplementedError(f"color type {typ}")
            else:
                ncomp = {GX_POS: (2, 3), GX_NRM: (3, 9)}.get(attr, (1, 2))[cnt]
                dt = {0: "u1", 1: "i1", 2: ">u2", 3: ">i2", 4: ">f4"}[typ]
                item = np.dtype(dt).itemsize * ncomp
                arr = np.frombuffer(raw[:len(raw) // item * item], dt).reshape(-1, ncomp).astype(np.float64)
                if typ != 4:
                    arr = arr / float(1 << shift)
            self.arrays[attr] = arr

    def _jnt1(self):
        b, s = self.data, self.sections["JNT1"]
        n = u16(b, s + 8)
        entries, remap, names = u32(b, s + 0x0C), u32(b, s + 0x10), u32(b, s + 0x14)
        self.joint_names = name_table(b, s + names) if names else [""] * n
        self.joints = []
        self.joint_srt = []   # (sx, sy, sz, rx, ry, rz, tx, ty, tz), rotations in radians
        for i in range(n):
            e = s + entries + 0x40 * u16(b, s + remap + 2 * i)
            sx, sy, sz = struct.unpack_from(">3f", b, e + 4)
            rx, ry, rz = (v / 32768.0 * math.pi for v in struct.unpack_from(">3h", b, e + 0x10))
            tx, ty, tz = struct.unpack_from(">3f", b, e + 0x18)
            self.joint_srt.append((sx, sy, sz, rx, ry, rz, tx, ty, tz))
            self.joints.append(joint_matrix(sx, sy, sz, rx, ry, rz, tx, ty, tz))

    def _drw1_evp1(self):
        b = self.data
        s = self.sections["DRW1"]
        n = u16(b, s + 8)
        wo, do = u32(b, s + 0x0C), u32(b, s + 0x10)
        self.drw = [(b[s + wo + i] != 0, u16(b, s + do + 2 * i)) for i in range(n)]
        s = self.sections["EVP1"]
        n = u16(b, s + 8)
        self.envelopes = []
        if n:
            co, io, wo, mo = (u32(b, s + 0x0C + 4 * i) for i in range(4))
            k = 0
            for i in range(n):
                cnt = b[s + co + i]
                self.envelopes.append([(u16(b, s + io + 2 * (k + j)),
                                        struct.unpack_from(">f", b, s + wo + 4 * (k + j))[0]) for j in range(cnt)])
                k += cnt
            self.inv_bind = {}
            nj = len(self.joints)
            for j in range(nj):
                m = np.eye(4)
                m[:3, :] = np.array(struct.unpack_from(">12f", b, s + mo + 48 * j)).reshape(3, 4)
                self.inv_bind[j] = m

    def _inf1(self):
        """Walk the scene graph: joint world matrices + (material, shape) draw order."""
        b, s = self.data, self.sections["INF1"]
        o = s + u32(b, s + 0x14)
        self.world = [np.eye(4) for _ in self.joints]
        self.parents = [-1] * len(self.joints)
        self.draws = []
        stack, last_joint, cur_joint, cur_mat = [], None, None, 0
        while True:
            typ, idx = u16(b, o), u16(b, o + 2)
            o += 4
            if typ == 0x00:
                break
            if typ == 0x01:
                stack.append((cur_joint, cur_mat))
                cur_joint = last_joint if last_joint is not None else cur_joint
            elif typ == 0x02:
                cur_joint, cur_mat = stack.pop()
            elif typ == 0x10:
                parent = self.world[cur_joint] if cur_joint is not None else np.eye(4)
                self.parents[idx] = cur_joint if cur_joint is not None else -1
                self.world[idx] = parent @ self.joints[idx]
                last_joint = idx
            elif typ == 0x11:
                cur_mat = idx
            elif typ == 0x12:
                self.draws.append((cur_mat, idx))

    def _tex1(self):
        self.textures, self.texture_info, self.texture_names = [], [], []
        if "TEX1" not in self.sections:
            return
        b, s = self.data, self.sections["TEX1"]
        n = u16(b, s + 8)
        hdrs, names = u32(b, s + 0x0C), u32(b, s + 0x10)
        self.texture_names = name_table(b, s + names, n) if names else [f"tex{i}" for i in range(n)]
        for i in range(n):
            img, info = gxtex.decode_bti(b, s + hdrs + 0x20 * i)
            self.textures.append(img)
            self.texture_info.append(info)

    def _mat1(self):
        """Early (bmd1) MAT1. Layout worked out from the IPL's own models:

        header tables (offsets at +0x0C): 0 entries, 1 remap, 2 names, 3 cull,
        4 matColor, 5 chanNum, 6 colorChan, 7 texGenNum, 8 texGen, 9 texMtx,
        10 texNo, 11 tevOrder, 12 tevColor(S10), 13 tevStageNum, 14 tevStage,
        15 (fog, unused), 16 alphaComp, 17 blend, 18 zMode, 19 zComp/dither.
        Entry (0x98): bytes 0..7 = flag, cull, chanNum, texGenNum, tevStageNum,
        zcomp, zmode, dither; u16 slots: 0x08 matColor[2], 0x0C colorChan[4],
        0x14 texGen[8], 0x24 texMtx[8], 0x38 texNo[8], 0x48 tevOrder[16]?,
        0x66.. tevColor (0x68 = C0, 0x6A = C1, 0x6C = C2), 0x70 tevStage[16],
        0x92 alphaComp, 0x94 blend.
        """
        b, s = self.data, self.sections["MAT1"]
        n = u16(b, s + 8)
        T = [s + u32(b, s + 0x0C + 4 * i) for i in range(22)]

        def idx(e, off):
            v = u16(b, e + off)
            return None if v == 0xFFFF else v

        self.materials = []
        for i in range(n):
            e = T[0] + 0x98 * u16(b, T[1] + 2 * i)
            m = {}
            m["cull"] = u32(b, T[3] + 4 * b[e + 1])
            mc = idx(e, 0x08)
            m["mat_color"] = tuple(c / 255 for c in b[T[4] + 4 * mc:T[4] + 4 * mc + 4]) if mc is not None else (1, 1, 1, 1)
            cc = idx(e, 0x0C)
            if cc is not None:
                en, src = b[T[6] + 8 * cc], b[T[6] + 8 * cc + 1]
                m["lighting"], m["vertex_color"] = bool(en), src == 1
            else:
                m["lighting"], m["vertex_color"] = False, False
            ntg = b[T[7] + b[e + 3]]
            m["texgens"] = []
            for k in range(ntg):
                g = idx(e, 0x14 + 2 * k)
                if g is None:
                    break
                typ, src, mtx = b[T[8] + 4 * g:T[8] + 4 * g + 3]
                tm = idx(e, 0x24 + 2 * k)
                mat = None
                if tm is not None:
                    mat = np.array(struct.unpack_from(">12f", b, T[9] + 0x34 * tm)).reshape(3, 4)
                m["texgens"].append(dict(type=typ, src=src, mtx=mtx, matrix=mat))
            m["textures"] = []
            for k in range(8):
                t = idx(e, 0x38 + 2 * k)
                if t is None:
                    break
                m["textures"].append(u16(b, T[10] + 2 * t))
            m["tev_colors"] = []
            for k in range(3):
                c = idx(e, 0x68 + 2 * k)
                m["tev_colors"].append(tuple(v / 255 for v in struct.unpack_from(">4h", b, T[12] + 8 * c)) if c is not None else (0, 0, 0, 0))
            nst = b[T[13] + b[e + 4]]
            m["stages"] = []
            for k in range(nst):
                o = idx(e, 0x48 + 2 * k)
                st = idx(e, 0x70 + 2 * k)
                if st is None:
                    break
                order = tuple(b[T[11] + 4 * o:T[11] + 4 * o + 3]) if o is not None else (0xFF, 0xFF, 0xFF)
                r = b[T[14] + 0x14 * st:T[14] + 0x14 * st + 0x14]
                m["stages"].append(dict(order=order, color_in=tuple(r[1:5]), color_op=r[5], color_bias=r[6],
                                        color_scale=r[7], color_clamp=r[8], color_reg=r[9],
                                        alpha_in=tuple(r[10:14]), alpha_op=r[14], alpha_bias=r[15],
                                        alpha_scale=r[16], alpha_clamp=r[17], alpha_reg=r[18]))
            ac = idx(e, 0x92)
            m["alpha_compare"] = tuple(b[T[16] + 8 * ac:T[16] + 8 * ac + 5]) if ac is not None else (7, 0, 1, 7, 0)
            bl = idx(e, 0x94)
            m["blend"] = tuple(b[T[17] + 4 * bl:T[17] + 4 * bl + 4]) if bl is not None else (0, 1, 0, 3)
            zm = b[e + 6]
            m["zmode"] = tuple(b[T[18] + 4 * zm:T[18] + 4 * zm + 3])
            self.materials.append(m)
        self.material_texture = [m["textures"][0] if m["textures"] else None for m in self.materials]

    def _shp1(self):
        b, s = self.data, self.sections["SHP1"]
        n = u16(b, s + 8)
        init, remap = u32(b, s + 0x0C), u32(b, s + 0x10)
        attr_tab, mtx_tab = u32(b, s + 0x18), u32(b, s + 0x1C)
        dl_data, mtx_data, pkt_loc = u32(b, s + 0x20), u32(b, s + 0x24), u32(b, s + 0x28)
        self.shapes = []
        for i in range(n):
            e = s + init + 0x28 * u16(b, s + remap + 2 * i)
            mtx_type = b[e]
            npkt, aoff, first_md, first_pkt = struct.unpack_from(">4H", b, e + 2)
            desc = []
            o = s + attr_tab + aoff
            while u32(b, o) != 0xFF:
                desc.append((u32(b, o), u32(b, o + 4)))
                o += 8
            verts, local = [], []
            slots = [0] * 10   # matrix slots persist across packets (0xFFFF = keep previous)
            for p in range(npkt):
                size, off = struct.unpack_from(">II", b, s + pkt_loc + 8 * (first_pkt + p))
                md = s + mtx_data + 8 * (first_md + p)
                mcount, mfirst = u16(b, md + 2), u32(b, md + 4)
                for k in range(mcount):
                    v = u16(b, s + mtx_tab + 2 * (mfirst + k))
                    if v != 0xFFFF:
                        slots[k] = v
                w, l = self._read_dl(b[s + dl_data + off:s + dl_data + off + size], desc, list(slots))
                verts += w
                local += l
            self.shapes.append(dict(matrix_type=mtx_type, tris=verts, local=local))

    def _matrix_for(self, drw_index):
        weighted, idx = self.drw[drw_index]
        if not weighted:
            return self.world[idx]
        m = np.zeros((4, 4))
        for j, w in self.envelopes[idx]:
            m += w * (self.world[j] @ self.inv_bind[j])
        m[3, 3] = 1
        return m

    def _read_dl(self, dl, desc, mtable):
        """Returns (world-space triangles, joint-local triangles with draw-matrix index)."""
        out, out_local = [], []
        o = 0
        while o < len(dl):
            op = dl[o]
            o += 1
            if op == 0:
                continue
            kind = PRIMS.get(op & 0xF8)
            if kind is None:
                break
            count = u16(dl, o)
            o += 2
            prim, prim_local = [], []
            for _ in range(count):
                v = {}
                for attr, typ in desc:
                    if typ == 1:
                        if attr in (GX_POS, GX_NRM, GX_CLR0, GX_CLR1) or attr >= GX_TEX0:
                            raise NotImplementedError("direct vertex data")
                        v[attr] = dl[o]
                        o += 1
                    elif typ == 2:
                        v[attr] = dl[o]
                        o += 1
                    elif typ == 3:
                        v[attr] = u16(dl, o)
                        o += 2
                drw_i = mtable[v.get(0, 0) // 3]
                m = self._matrix_for(drw_i)
                pos = np.append(self.arrays[GX_POS][v[GX_POS]][:3], 1.0)
                nrm = self.arrays[GX_NRM][v[GX_NRM]][:3] if GX_NRM in v else np.zeros(3)
                clr = self.arrays[GX_CLR0][v[GX_CLR0]] if GX_CLR0 in v else np.ones(4)
                uv = self.arrays[GX_TEX0][v[GX_TEX0]][:2] if GX_TEX0 in v else np.zeros(2)
                p = m @ pos
                nn = m[:3, :3] @ nrm
                ln = np.linalg.norm(nn)
                prim.append(np.concatenate([p[:3], nn / ln if ln else nn, clr, uv]))
                prim_local.append(np.concatenate([pos[:3], nrm, clr, uv, [drw_i]]))
            out += _triangulate(kind, prim)
            out_local += _triangulate(kind, prim_local)
        return out, out_local

    # --- animation support ------------------------------------------------
    def world_from_local(self, local_mats):
        """local_mats: per-joint 4x4 local transforms -> world (model space) matrices."""
        world = [None] * len(local_mats)

        def get(i):
            if world[i] is None:
                p = self.parents[i]
                world[i] = local_mats[i] if p < 0 else get(p) @ local_mats[i]
            return world[i]
        for i in range(len(local_mats)):
            get(i)
        return world

    def drw_matrices(self, world):
        """Matrices for every DRW1 entry given joint world matrices."""
        out = []
        for weighted, idx in self.drw:
            if not weighted:
                out.append(world[idx])
            else:
                m = np.zeros((4, 4))
                for j, w in self.envelopes[idx]:
                    m += w * (world[j] @ self.inv_bind[j])
                m[3, 3] = 1
                out.append(m)
        return out

    # --- export ---------------------------------------------------------
    def shape_material(self):
        mats = {}
        for mat, shp in self.draws:
            mats[shp] = mat
        return mats


def _triangulate(kind, v):
    tris = []
    if kind == "tris":
        tris = v
    elif kind == "quads":
        for i in range(0, len(v) - 3, 4):
            tris += [v[i], v[i + 1], v[i + 2], v[i], v[i + 2], v[i + 3]]
    elif kind == "strip":
        for i in range(len(v) - 2):
            tris += [v[i], v[i + 1], v[i + 2]] if i % 2 == 0 else [v[i + 1], v[i], v[i + 2]]
    elif kind == "fan":
        for i in range(1, len(v) - 1):
            tris += [v[0], v[i], v[i + 1]]
    return tris
