"""Menu tunables (RAM 0x814b5ea0), defaults from FUN_81311464."""
from .mathx import cos16, rot_trans, s16, sin16


class MenuParams:
    def __init__(self):
        self.time = 0            # 0x10 wobble clock (+7 per frame)
        self.yaw = 0             # 0x12
        self.pitch = 0           # 0x14
        self.turn_step = 750     # 0x16
        self.zoom = 0            # 0x18
        # wobble frequencies / amplitudes (0x1a..0x32)
        self.w_rx_f, self.w_ry_f, self.w_rz_f = 50, 25, 25        # 0x1a 0x1c 0x1e
        self.w_rx_a, self.w_ry_a, self.w_rz_a = 350, 1000, 1000   # 0x20 0x22 0x24
        self.w_tx_f, self.w_ty_f = 25, 50                          # 0x26 0x28
        self.w_tx_a, self.w_ty_a = 15, 6                           # 0x2a 0x2c
        self.w_ry_ph, self.w_tx_ph = s16(0xC568), s16(0xC000)      # 0x2e 0x30
        self.busy = 0            # 0x32

    def wobble(self, demo=None, advance=False):
        """FUN_8130ea34: the menu cube's idle float, blended with the intro
        orientation while the cube is still spinning fast."""
        t = self.time
        mix = 0.0
        shake = 0.0
        t2 = 0
        if demo is not None:
            mix = (1.0 - demo.shake_mix) * (demo.speed / demo.spin_max)
            shake = float(demo.shake)
            t2 = demo.time
            if demo.frame == 1:
                t = 0
        keep = 1.0 - mix
        ty = self.w_ty_a * sin16(self.w_ty_f * t)
        tx = self.w_tx_a * sin16(self.w_tx_ph + self.w_tx_f * t)
        rz = int(mix * (-5461.0 + shake * sin16(int(70.0 * t2))) + keep * (self.w_rz_a * cos16(self.w_rz_f * t)))
        ry = int(mix * (-6371.0 + shake * sin16(int(60.0 * t2))) + keep * (self.w_ry_a * cos16(self.w_ry_ph + self.w_ry_f * t)))
        rx = int(mix * (8191.0 + shake * sin16(int(90.0 * t2))) + keep * (self.w_rx_a * cos16(self.w_rx_f * t)))
        if advance:
            t = s16(t + 7)
        self.time = t
        return rot_trans(tx, ty, 0.0, rx, ry, rz)
