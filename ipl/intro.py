"""Boot intro: rolling cube, big cube, logo, spin-up and zoom into the menu.

Line-by-line port of the IPL's boot demo (BS2 0x8130c000..0x81310f58).
Original function names are kept in comments so the logic can be checked
against decomp/annotated/*.c.
"""
from .mathx import (cos16, hermite, rot_axis, rot_trans, s16, scale_cols, sin16,
                    trans, u8)

# The path script at RAM 0x81381160: (direction, trail type, trail life, 0).
# Directions 0..3 roll +x, +y, -x, -y on the current face; 4..6 hop onto
# face (d-4); 7 ends the walk; 8 is the end-of-script marker.
SCRIPT_ROM = 0x81381160 - 0x81300000 + 0x820
SCRIPT_END = 8


def load_script(rom):
    """Read the path script from the descrambled IPL ROM."""
    out = []
    o = SCRIPT_ROM
    while True:
        d, t, life = rom[o], rom[o + 1], rom[o + 2]
        out.append((d, t, life))
        if d == SCRIPT_END or len(out) > 80:
            break
        o += 4
    return out


class Demo:
    """Boot-demo state (RAM 0x814af878). Constants are the values the IPL uses."""

    def __init__(self):
        # tunables (set once at boot)
        self.scale = 1.0          # 0x04
        self.spin_max = 5000      # 0x0c
        self.spin_acc_step = 1.5  # 0x18
        self.zoom_frames = 30     # 0x28  camera/crossfade length in state 4
        self.turns = 3            # 0x2a  extra turns while spinning down
        self.settle_angle = 5000  # 0x2c
        self.ph_spin = 15         # 0x30
        self.ph_decel = 80        # 0x34
        self.ph_settle = 50       # 0x38
        self.ph_fade = 100        # 0x40
        self.decel_speed = 5000.0  # 0x44
        self.squash_x = 0.5       # 0x48
        self.stretch_y = 1.0      # 0x4c
        self.flash_alpha = 255    # 0x59
        self.logo_alpha_max = 255  # 0x5a
        self.impact1 = 4000       # 0x60
        self.impact2 = 12000      # 0x62
        self.logo_fade_len = 30   # 0x68
        self.press_len = 30       # 0x6e
        self.decay1 = 0.93        # 0x8c
        self.decay2 = 0.925       # 0x90
        self.bounce1 = 0.015      # 0x94
        self.bounce2 = 0.005      # 0x98
        self.impact_phase_step = 1000  # 0x9c
        self.iso = (0x2000, -6420, -5461)  # 0x9e..0xa2: corner-on orientation
        self.whoosh_min = 0.4     # 0xb8
        self.whoosh_min_menu = 0.5  # 0xbc
        self.overlay4_len = 30    # 0x8a
        self.overlay5_len = 30    # 0x86
        self.reset()

    def reset(self):
        """FUN_8130c130."""
        self.angle = 0            # 0x08 (s16 yaw of the big cube)
        self.angle_saved = 0      # 0x0a
        self.angle_from = 0       # 0x2e
        self.speed = 0.0          # 0x10
        self.accel = 0.0          # 0x14
        self.ease_speed = 0.0     # 0xa4
        self.ease_t = 60          # 0xa8
        self.ease_from = 0        # 0xac
        self.time = 2000          # 0x20
        self.shake = 0            # 0x22
        self.shake_mix = 0.0      # 0x24
        self.frame = 1            # 0x3c
        self.restart = 1          # 0xae
        self.mark_alpha = 0       # 0x5b
        self.impact_a = 0         # 0x5c
        self.impact_b = 0         # 0x5e
        self.impact_phase = 0     # 0x64
        self.logo_fade = self.logo_fade_len  # 0x66
        self.overlay3 = 0         # 0x6a
        self.overlay5 = 0         # 0x84
        self.overlay4 = 0         # 0x88
        self.leaving = 0          # 0xb2
        self.error = 0            # 0xb0
        self.prev_angle = 0       # 0xaa


class RollingCube:
    """The little cube that rolls the logo onto the big cube (RAM 0x814b5d58)."""

    def __init__(self, script):
        self.script = script
        # FUN_8130f1c0: parameters
        self.flags = 0
        self.drop_height = 100.0   # 0x2c
        self.drop_frames = 4       # 0x22
        self.drop_wait = 13        # 0x23
        self.roll_frames = 8       # 0x14
        self.hop_frames = 15       # 0x16
        self.step_pause = 0        # 0x18
        self.alpha = 255           # 0x0b
        self.grow_frames = 15      # 0x35
        self.grow_hold = 6         # 0x36
        self.spin_quarters = 6     # 0x37
        self.jump_height = 130.0   # 0x38
        self.jump_frames = 8       # 0x3d
        self.jump_hold = 17        # 0x3e
        self.jump_v0 = 30.0        # 0x50
        self.fade_frames = 33      # 0x54
        self.logo_frames = 50      # 0x57
        self.logo_delay = 4        # 0x58
        self.unit = 54             # 0x59  cell / small-cube size
        self.color = (100, 80, 190)  # 0x5a..0x5c trail tint
        self.se_delay = (0, 6, 5)  # 0x60, 0x62, 0x64 per intro variant
        # grid: 3 faces x 32 x 32 cells -> [life, type, state]
        self.grid = [[[[0, 0, 0] for _ in range(32)] for _ in range(32)] for _ in range(3)]
        self.phase = 0
        self.next_phase = 0
        self.rolling_active = 1    # G_814ad3e4
        self.flash_pending1 = 1    # G_814acff8
        self.flash_pending2 = 1    # G_814acff9
        self.sound_pending = 1     # G_814acffa
        self.start()

    def start(self):
        """FUN_8130f06c."""
        self.x, self.y = 16, 18
        self.face = 0
        self.step = 0
        self.dir = self.script[0][0]
        self.inset = 0          # 0x1e
        self.done = 0           # 0x1f
        self.phase = self.next_phase = 0
        self.vel = 0.0                                           # 0x24
        self.grav = 2.0 * self.drop_height / (self.drop_frames ** 2)  # 0x28
        self.height = self.drop_height                           # 0x30
        self.jump_h = self.jump_height                           # 0x48 / 0x4c
        self.jump_v = self.jump_v0                               # 0x40
        self.jump_g = max(0.0, 2.0 * self.jump_h / self.jump_frames ** 2 - self.jump_v / self.jump_frames)  # 0x44
        self.jump_y = self.jump_h                                # 0x4c
        self.drop_count = 0     # 0x21
        self.jump_count = 0     # 0x3c
        self.t = 0              # 0x12
        self.step_done_at = 0   # 0x1a
        self.grow = 0           # 0x34
        self.fade = 0           # 0x55
        self.logo_count = 0     # 0x56
        self.se_count = 0       # 0x5e
        self.sound_pending = 1

    # -- grid -------------------------------------------------------------
    def decay(self):
        """FUN_8130f2f0."""
        for face in self.grid:
            for col in face:
                for c in col:
                    if c[2] == 2:
                        c[0] = c[1] = c[2] = 0
                    elif c[2] == 1:
                        c[2] = 0
                    elif c[1] and c[0] != 255:
                        c[0] -= 1
                        if c[0] == 0:
                            c[2] = 2

    def move(self):
        """FUN_8130f418: apply the finished roll, then stamp the new cell."""
        d = self.dir
        if d == 0:
            self.x = min(self.x + 1, 32)
        elif d == 1:
            self.y = min(self.y + 1, 32)
        elif d == 2:
            self.x = max(self.x - 1, 0)
        elif d == 3:
            self.y = max(self.y - 1, 0)
        elif 4 <= d < 7:
            nf = d - 4
            if (nf + 3 - self.face) % 3 == 1 and self.y == 16:
                self.y = self.x
                self.x = 16
                self.face = nf
            if (nf + 3 - self.face) % 3 == 2 and self.x == 16:
                self.x = self.y
                self.y = 16
                self.face = nf
        self.decay()
        _, typ, life = self.script[self.step]
        cell = self.grid[self.face][self.x][self.y]
        cell[0], cell[1], cell[2] = life, typ, 1

    # -- phases -----------------------------------------------------------
    def drop(self, demo):
        """FUN_8130faf0."""
        self.height -= self.vel
        if self.height < 0.0:
            self.height = 0.0
            if self.flash_pending1:
                demo.impact_a = demo.impact1
                demo.impact_phase = 0x7FFF
                self.flash_pending1 = 0
        self.vel += self.grav
        if self.drop_frames + self.drop_wait < self.drop_count:
            self.flash_pending1 = 1
            return 1
        self.drop_count += 1
        return 0

    def roll_progress(self):
        if self.dir < 4:
            return 0x4000, (self.t << 14) // (self.roll_frames - self.step_pause)
        return 0x8000, (self.t << 15) // (self.hop_frames - self.step_pause)

    def roll_step_done(self):
        """FUN_8130fa48."""
        lim, p = self.roll_progress()
        if lim < p and self.step_done_at == 0:
            self.step_done_at = self.t
        return 0 < self.step_done_at and self.step_done_at + self.step_pause <= self.t

    def play_intro_sound(self, variant, audio):
        """FUN_81310494: the jingle (hold Z on 1 or all 4 pads for the variants)."""
        delay = {0: self.se_delay[0], 1: self.se_delay[2], 2: self.se_delay[1]}[variant]
        if self.sound_pending and delay < self.se_count:
            audio.play_se({0: 0, 1: 2, 2: 1}[variant])
            self.sound_pending = 0
        if self.sound_pending:
            self.se_count += 1

    def update(self, demo, play_sound, variant, audio):
        """FUN_81310584."""
        self.phase = self.next_phase
        if play_sound:
            self.play_intro_sound(variant, audio)
        ph = self.phase
        if ph == 0:
            self.next_phase = self.drop(demo)
        elif ph == 1:
            if self.roll_step_done():
                self.t = 0
                self.step_done_at = 0
                self.step += 1
                if self.script[self.step][0] == SCRIPT_END:
                    self.done = 1
                    self.decay()
                else:
                    self.move()
                    self.dir = self.script[self.step][0]
            if self.dir == 7:
                self.next_phase = 2
            else:
                self.next_phase = 1
                self.t += 1
        elif ph == 2:
            self.rolling_active = 0
            self.grow = u8(self.grow + 1)
            self.next_phase = 3 if self.grow_frames + self.grow_hold < self.grow else 2
        elif ph == 3:
            self.jump_y -= self.jump_v
            if self.jump_y < 0.0:
                self.jump_y = 0.0
                if self.flash_pending2:
                    demo.impact_b = demo.impact2
                    demo.impact_phase = 0x7FFF
                    self.flash_pending2 = 0
            self.jump_v += self.jump_g
            if self.jump_frames + self.jump_hold < self.jump_count:
                self.flash_pending2 = 1
                self.next_phase = 4
            else:
                self.jump_count += 1
                self.logo_count = min(self.logo_count + 1, self.logo_frames + self.logo_delay)
                self.next_phase = 3
        elif ph == 4:
            self.fade += 1
            if self.fade > self.fade_frames:
                self.fade = self.fade_frames
                self.done = 1
            demo.mark_alpha = self.fade * 255 // self.fade_frames
            self.logo_count += 1
            lim = self.logo_frames + self.logo_delay
            if self.logo_count < lim:
                self.done = 0
            else:
                self.logo_count = lim
            if demo.impact_b != 0:
                self.done = 0
        return 1

    def logo_frame(self):
        """FUN_81310060 tail: frame of the GAMECUBE logo animation."""
        if self.logo_delay < self.logo_count:
            return 60.0 * (self.logo_count - self.logo_delay) / self.logo_frames
        return 0.0

    # -- transforms (all relative to the big cube's model matrix) ---------
    def roll_matrix(self):
        """FUN_8130f6b8 + FUN_8130f7f0."""
        S = self.unit
        lim, p = self.roll_progress()
        p = min(p, lim)
        m = rot_axis("x", s16(p)) @ trans(-S / 2, S / 2, -S / 2)
        yaw = {0: -0x4000, 1: 0x7FFF, 2: 0x4000, 3: 0}.get(self.dir, 0)
        m = trans(S / 2, 0, S / 2) @ m
        m = rot_trans(-S / 2, 0, -S / 2, 0, yaw, 0) @ m
        m = trans(-(self.x - 16) * S, 0, -(self.y - 16) * S) @ m
        if self.face == 1:
            m = rot_trans(0, 0, 0, 0x4000, 0, 0x4000) @ m
        elif self.face == 2:
            m = rot_trans(0, 0, 0, 0, -0x4000, -0x4000) @ m
        return m

    def grow_matrix(self):
        """FUN_8130fbc4."""
        S = self.unit
        g = min(1.0 + 0.5 * self.grow / self.grow_frames, 1.5)
        quarter = self.spin_quarters * 0x4000
        ang = min(quarter, quarter * self.grow // self.grow_frames)
        y = max(S / 2 - (self.grow * (1.0 + g) * S / self.grow_frames) * 0.5, -(g * S) * 0.5)
        m = scale_cols(rot_trans(0, 0, 0, 0, 0, 0), g, g, g)
        m = rot_trans(g * -S * 0.5, y, g * -S * 0.5, s16(ang), 0, 0) @ m
        m = rot_trans(0, 0, 0, 0, -0x4000, -0x4000) @ m
        return m

    def world_matrix(self, cube_world):
        """FUN_81310060 / FUN_8130fde0: small cube -> world."""
        S = self.unit
        corner = trans(1.5 * S, 1.5 * S, 1.5 * S)
        if self.phase in (0, 1):
            m = cube_world @ corner @ self.roll_matrix()
            if self.phase == 0:
                m = trans(0, self.height, 0) @ m
            return m
        m = cube_world @ corner @ self.grow_matrix()
        if self.phase == 2:
            left = max(0, self.grow_frames - self.grow)
            h = self.jump_height
            y = h - (left ** 4 * h) / (self.grow_frames ** 4)
            m = trans(0, min(y, h), 0) @ m
        else:
            m = trans(0, self.jump_y, 0) @ m
        return m

    def trail_cells(self):
        """FUN_813108b8: (face, x, y, alpha 0..255) for every visible tile."""
        out = []
        for f, face in enumerate(self.grid):
            for x, col in enumerate(face):
                for y, c in enumerate(col):
                    if not c[1]:
                        continue
                    a = 255
                    if c[2] == 1:
                        a = self.t * 255 // self.roll_frames
                    elif c[2] == 2:
                        a = (self.roll_frames - self.t) * 255 // self.roll_frames
                    out.append((f, x, y, max(0, min(255, a))))
        return out


class Intro:
    """Top-level boot flow: state 1 (intro) -> 4 (zoom to menu) -> 5 (menu)."""

    def __init__(self, script, audio):
        self.demo = Demo()
        self.cube = RollingCube(script)
        self.audio = audio
        self.state = 1
        self.variant = 0
        self.disc_state = 0x12      # "no disc": go to the menu after the logo
        self.screen_fade = 0        # G_814ad2e8
        self.spin_button = False    # A held: spin the big cube during the intro
        self.menu_wob = None

    # -- big cube physics -------------------------------------------------
    def spin(self, accelerate):
        """FUN_8130c250."""
        d = self.demo
        ease_from, ease_speed = d.ease_from, d.ease_speed
        before = d.angle
        if not accelerate:
            if not (self.spin_button and self.cube.rolling_active):
                d.accel *= 0.76
                d.speed *= 0.95
                if d.accel < 0.1:
                    d.accel = 0.0
                if d.speed < 0.01:
                    d.speed = 0.0
                d.angle = s16(int(hermite(float(d.ease_t), 0.0, float(ease_from), ease_speed, 50.0, 0.0, 0.0)))
                d.ease_t = min(d.ease_t + 1, 50)
            else:
                d.accel += d.spin_acc_step
                d.speed += d.accel
                d.angle = s16(int(d.angle + d.speed))
                ease_from, ease_speed = d.angle, d.speed
                d.ease_t = 0
        else:
            d.accel += d.spin_acc_step
            d.speed += d.accel
            d.angle = s16(int(d.angle + d.speed))
        f = min(d.speed / 5000.0, 1.0)
        if f < 0.01:
            f = 0.0
        if self.cube.rolling_active or accelerate:
            crossed = (before < 0 <= d.angle) or (before > 0 > d.angle)
            if crossed and d.whoosh_min < f:
                self.audio.whoosh(f)
        d.prev_angle = before
        d.ease_from, d.ease_speed = ease_from, ease_speed

    def shake_update(self):
        """FUN_8130c5e4 + FUN_8130c518."""
        d = self.demo
        f = float(d.shake)
        f = f * 0.945 if d.accel <= 0.0 else 36.0 + f
        if f < 1.0:
            f = 0.0
        if f > 0.0:
            d.time = s16(d.time + 7)
        d.shake = int(min(f, 5000.0))
        a = d.impact_a * d.decay1
        b = d.impact_b * d.decay2
        if a < 10.0:
            a = 0.0
        if b < 80.0:
            b = 0.0
        if a > 0.0 or b > 0.0:
            d.impact_phase = s16(d.impact_phase + d.impact_phase_step)
        d.impact_a, d.impact_b = int(a), int(b)

    def cube_world(self):
        """FUN_8130c694 + the squash in FUN_8130c90c -> big cube model matrix."""
        d = self.demo
        mix = d.shake_mix * d.speed / d.spin_max
        m = rot_trans(0, 0, 0, *d.iso)
        keep = 1.0 - mix
        base = 1000.0 * mix
        rz = int(base + keep * d.shake * sin16(d.time * 0x46))
        ry = int(base + keep * d.shake * sin16(d.time * 0x3C))
        rx = int(base + keep * d.shake * sin16(d.time * 0x5A))
        m = rot_trans(0, 0, 0, rx, ry, rz) @ m
        ph = d.impact_phase
        ty = d.bounce1 * d.impact_a * sin16(ph) + d.bounce2 * d.impact_b * sin16(ph)
        m = rot_trans(0, ty, 0, int(-d.impact_b * sin16(ph)), 0, int(d.impact_a * sin16(ph))) @ m
        m = rot_trans(0, 0, 0, 0, d.angle, 0) @ m
        k = d.speed / d.spin_max
        sx = 1.0 - (1.0 - d.squash_x) * k
        sy = 1.0 + d.stretch_y * k
        return scale_cols(m, sx, sy, sx)

    # -- states -----------------------------------------------------------
    def state1(self):
        """FUN_8130cb74: the intro proper."""
        d, c = self.demo, self.cube
        if not c.done:
            self.spin(False)
        self.shake_update()
        c.update(d, True, self.variant, self.audio)
        if d.speed <= d.spin_max:
            if not d.leaving:
                if self.disc_state in (0x12, 0x13):
                    d.leaving = 1
                return 1
            if c.done:
                self.screen_fade = max(0, self.screen_fade - 6)
                if self.screen_fade == 0:
                    self.spin(True)
                    self.shake_update()
                    if d.spin_max < d.speed:
                        d.angle = d.angle_saved
                        return 4
            return 1
        d.angle = d.angle_saved
        return 4

    def zoom_angle(self):
        """FUN_8130e70c: spin down and settle while flying into the menu."""
        d = self.demo
        before = d.angle
        f = d.frame
        p0, p1, p2 = d.ph_spin, d.ph_spin + d.ph_decel, d.ph_spin + d.ph_decel + d.ph_settle
        if 0 <= f < p0:
            d.angle = s16(int(d.angle + d.speed))
            d.angle_from = d.angle
        elif p0 <= f < p1:
            # the IPL really negates here (8130e7c8: neg r3,r3); at 5000/frame nobody notices
            start = -(d.angle_from + d.turns * 0x10000)
            d.angle = s16(int(hermite(float(f), float(p0), float(start), d.decel_speed,
                                      float(p1), float(d.settle_angle), 0.0)))
            if p0 < f:
                d.speed = float(s16(d.angle - before))
        elif p1 <= f < p2:
            d.angle = s16(int(hermite(float(f), float(p1), float(d.settle_angle), 0.0, float(p2), 0.0, 0.0)))
            d.speed = float(s16(d.angle - before))
        elif f == p2:
            d.angle = 0
            d.speed = 0.0
        return before

    def state4(self, menu_wobble):
        """FUN_8130ed30."""
        d = self.demo
        if d.restart:
            d.angle_from = d.angle
            d.restart = 0
        self.zoom_angle()
        d.frame += 1
        if d.speed <= 0.0 and d.angle == 0:
            d.restart = 1
        return 4 if (d.speed > 0.0 or d.angle != 0) else 5

    def menu_cube_world(self, menu_wobble):
        """Matrix of the menu cube during state 4 (DAT_814afd28 + scale)."""
        d = self.demo
        m = rot_trans(0, 0, 0, 0, d.angle, 0) @ menu_wobble
        k = d.speed / d.spin_max
        f3 = 1.1 * (1.0 - k)
        sx = d.scale * (f3 + d.squash_x * k)
        sy = d.scale * (f3 + (1.0 + d.stretch_y) * k)
        return scale_cols(m, sx, sy, sx)

    def camera_y(self, cfg_intro=-55.0, cfg_menu=-10.0):
        """FUN_81302df0."""
        if self.state == 5:
            return cfg_menu
        if self.state == 4:
            if self.demo.zoom_frames < self.demo.frame:
                return cfg_menu
            return cfg_intro - self.demo.frame * (cfg_intro - cfg_menu) / self.demo.zoom_frames
        return cfg_intro

    def tick(self, menu_params):
        if self.state == 1:
            self.state = self.state1()
            self.menu_wob = menu_params.wobble(self.demo, advance=False)
        elif self.state == 4:
            nxt = self.state4(None)
            self.menu_wob = menu_params.wobble(self.demo, advance=True)
            self.state = nxt
        return self.state
