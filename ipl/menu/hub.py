"""The main menu hub (FUN_81312b9c): the glass cube, its four faces, the panels
drawn on its sides and the button help bar along the bottom.

States (G_814ad3f4):  0 hub, 1 Game Play face, 3 Memory Card face, 5 Options face,
7 Calendar face; 2/4/6/8 are those menus opened (the cube backs away and fades).
"""
import datetime

import numpy as np

from .. import rom
from ..mathx import cos16, rot_trans, s16, scale_cols, sin16
from ..ui import Fader, Layout, Strings, TextureBank, bti_texture, fourcc
from .faces import CalendarFace, GameFace, MemcardFace, OptionsFace, cdiv

# per-language resource tables (FUN_8130b518); language: 0 English, 1 German,
# 2 French, 3 Spanish, 4 Italian, 5 Dutch
RES = {
    "titles":    [0x39, 0x95, 0x8E, 0xA3, 0x9C, 0x84],   # G_814ad3fc  front face titles
    "game":      [0x38, 0x94, 0x8D, 0xA2, 0x9B, 0x83],   # G_814ad400  Game Play panel
    "memcard":   [0x3B, 0x97, 0x90, 0xA5, 0x9E, 0x86],   # G_814ad404  Memory Card panel
    "options":   [0x8A, 0x98, 0x91, 0xA6, 0x9F, 0x87],   # G_814ad408  Options panel
    "calendar":  [0x88, 0x92, 0x8B, 0xA0, 0x99, 0x81],   # G_814ad40c  Calendar panel
    "messages":  [0x6E, 0x70, 0x6F, 0x72, 0x71, 0x6D],   # G_814ad41c  STH0 system messages
    "help":      [0x75, 0x77, 0x76, 0x79, 0x78, 0x74],   # PTR_814b60c0 STH0 help bar text
    "weekday":   [0x31, 0x47, 0x3E, 0xAB, 0x50, 0x28],   # + 0..6
    "mono":      [0x2F, 0x45, 0x3C, 0xA9, 0x4E, 0x26],   # G_814ad42c
    "stereo":    [0x30, 0x46, 0x3D, 0xAA, 0x4F, 0x27],   # G_814ad430
}
RES_HELP_LAYOUT = 0x73      # G_814ad414
RES_MSG_LAYOUT = 0x6C       # G_814ad418
RES_BANK = 0xA7             # G_814ad410
RES_DIGITS = 0x5B           # PTR_814b60d8[0..9]

WHITE = 0xFFFFFFFF
SHADOW = 0x282828FF

# placements of the 2D layouts on the cube (DAT_8137ec34.. + the overrides in FUN_81314304)
FRONT = ((1, -1, 1), (0, 0, 0), (-144.0, 144.0, 144.0))
P_MEMCARD = ((1, -1, 1), (0x4000, 0, 0), (-144.0, -144.0, 144.0))
P_GAME = ((1, -1, 1), (-0x4000, 0, 0), (-144.0, 144.0, -144.0))
P_CALENDAR = ((1, -1, 1), (0, 0x4000, 0), (144.0, 144.0, 144.0))
P_OPTIONS = ((1, -1, 1), (0, -0x4000, 0), (-144.0, 144.0, -144.0))

SE_MOVE, SE_BACK, SE_OK = 3, 4, 5


def placement(p):
    """FUN_8130764c."""
    (sx, sy, sz), (rx, ry, rz), (tx, ty, tz) = p
    return scale_cols(rot_trans(tx, ty, tz, rx, ry, rz), sx, sy, sz)


def with_alpha(c, a):
    return (c & 0xFFFFFF00) | (max(0, min(255, int(a))) & 0xFF)


def sq(a):
    """The panels set a colour's alpha to a, then scale it by a/255 again."""
    return a * a // 255


class Pad:
    """FUN_81302700: held / newly pressed buttons, and directions with auto-repeat."""
    REPEAT_DELAY = 0x23
    REPEAT_RATE = 10

    def __init__(self):
        self.held = set()
        self.pressed = set()
        self.dirs = set()
        self._prev_dirs = frozenset()
        self._last = frozenset()
        self._count = 0
        self.repeating = False

    def update(self, held, pressed):
        dirs = frozenset(d for d in ("UP", "DOWN", "LEFT", "RIGHT") if d in held)
        self.repeating = False      # G_814ad300: this press came from auto-repeat
        if dirs and dirs == self._last:
            self._count += 1
            if self._count == self.REPEAT_DELAY:
                self._prev_dirs = frozenset()
                self.repeating = True
            elif self._count > self.REPEAT_DELAY + self.REPEAT_RATE:
                self._prev_dirs = frozenset()
                self._count -= self.REPEAT_RATE
                self.repeating = True
        else:
            self._count = 0
        self._last = dirs
        self.dirs = set(dirs - self._prev_dirs)
        self._prev_dirs = dirs
        self.held = set(held)
        self.pressed = set(pressed)
        if "B" in self.pressed:
            self.pressed.discard("A")
            self.pressed.discard("START")


class Settings:
    """The SRAM bits the menus show and change (kept in settings.json)."""

    def __init__(self, path=None):
        self.path = path
        self.language = 0
        self.stereo = 1
        self.offset = 0          # screen position, -32..32
        self.clock_offset = 0.0  # seconds the Calendar moved the clock away from the computer's
        if path:
            self.load()

    def load(self):
        import json
        try:
            d = json.load(open(self.path))
        except (OSError, ValueError):
            return
        self.language = int(d.get("language", 0)) % 6
        self.stereo = 1 if d.get("stereo", 1) else 0
        self.offset = max(-32, min(32, int(d.get("offset", 0))))
        self.clock_offset = float(d.get("clock_offset", 0.0))

    def save(self):
        import json
        if not self.path:
            return
        try:
            with open(self.path, "w") as f:
                json.dump({"language": self.language, "stereo": self.stereo, "offset": self.offset,
                           "clock_offset": self.clock_offset}, f, indent=1)
        except OSError:
            pass


class Hub:
    def __init__(self, ctx, renderer, ui, audio, params, settings=None):
        self.ctx = ctx
        self.r = renderer
        self.ui = ui
        self.audio = audio
        self.p = params
        self.settings = settings or Settings()
        self.state = 0
        self.next = 0
        self.scale = 1.0            # the intro cube's final scale (demo +0x04)
        self.disc_state = 0x12      # FUN_813020ec: 0x12 = no disc
        self.time_override = None   # datetime for tests
        self.clock = None           # menu.calendar.Clock
        # SDA params not in MenuParams yet
        self.zoom_in = 0x730        # +0x180
        self.zoom_out = 0x578       # +0x182
        self.push_z = rom.f32(0x814ADE04)   # +0x08: how far the cube backs away
        self.push_rz = rom.f32(0x814ADE08)  # +0x188
        self.bank = TextureBank(ctx, self.r_bytes(RES_BANK))
        self.help_layout = Layout(self.r_bytes(RES_HELP_LAYOUT))
        self.msg_layout = Layout(self.r_bytes(RES_MSG_LAYOUT))
        self.help_strings = [Strings(self.r_bytes(i)) for i in RES["help"]]
        self.digits = [bti_texture(ctx, self.r_bytes(RES_DIGITS + i)) for i in range(10)]
        self.load_language()
        # faces
        self.faces = {
            "memcard": MemcardFace(),
            "game": GameFace(),
            "options": OptionsFace(),
            "calendar": CalendarFace(self.clock_hms),
        }
        # faders (FUN_81311014)
        F = Fader
        self.icons = [F() for _ in range(6)]                      # 611c..616c
        self.help = [F(index=i) for i in (0, 1, 2, 4, 0xD, 8, 0xB, 6)]   # 617c,618c,619c,61ac,61bc,61cc,61dc,61ec
        delays = (0, 0x14, 0x46)
        self.sub_faders = {k: [F(delay=d) for d in delays] for k in ("options", "memcard", "calendar", "game")}
        self.game_banner, self.game_nodisc, self.game_question = F(), F(), F()
        self.message = F(index=4)
        self.lang_faders = [F() for _ in range(6)]
        self.button_fade = 0        # G_814ad434
        self.frame = 0              # G_814ad436
        self.card_glow = [0, 0]     # G_814ad420/422
        self.card_fill = [0, 0]     # G_814ad424/428
        self.submenus = {}          # filled by the app: state -> object with tick()/draw()
        self.help_lang = self.settings.language     # G_814ad438

    # -- resources ------------------------------------------------------------
    @staticmethod
    def r_bytes(i):
        from ..render import res_bytes
        return res_bytes(i)

    def load_language(self):
        """FUN_81310f58."""
        lang = self.settings.language
        L = lambda k: Layout(self.r_bytes(RES[k][lang]))
        self.l_titles, self.l_game, self.l_memcard = L("titles"), L("game"), L("memcard")
        self.l_options, self.l_calendar = L("options"), L("calendar")
        self.messages = Strings(self.r_bytes(RES["messages"][lang]))
        self.weekdays = [bti_texture(self.ctx, self.r_bytes(RES["weekday"][lang] + i)) for i in range(7)]
        self.img_mono = bti_texture(self.ctx, self.r_bytes(RES["mono"][lang]))
        self.img_stereo = bti_texture(self.ctx, self.r_bytes(RES["stereo"][lang]))

    def set_language(self, lang):
        """FUN_8130b488: switch every text resource to another language."""
        self.settings.language = lang
        self.help_lang = lang
        self.load_language()

    def now(self):
        if self.time_override:
            return self.time_override
        return self.clock.now() if self.clock else datetime.datetime.now()

    def clock_hms(self):
        t = self.now()
        return t.second, t.minute, t.hour

    # -- per-frame logic (FUN_81312b9c) ---------------------------------------
    def rotate(self):
        """FUN_813120f0: turn the cube a step towards the face of the next state."""
        p = self.p
        st = p.turn_step
        n = self.next

        def settle(v):
            if v > 0:
                v = st * cdiv(v, st) - st
            if v < 0:
                v = st + st * cdiv(v, st)
            return v

        if n == 0:
            p.yaw, p.pitch = settle(p.yaw), settle(p.pitch)
        elif n in (1, 2):
            p.yaw = min(p.yaw + st, 0x4000)
            p.pitch = settle(p.pitch)
        elif n in (3, 4):
            p.yaw = max(p.yaw - st, -0x4000)
            p.pitch = settle(p.pitch)
        elif n in (5, 6):
            p.yaw = settle(p.yaw)
            p.pitch = min(p.pitch + st, 0x4000)
        elif n in (7, 8):
            p.yaw = settle(p.yaw)
            p.pitch = max(p.pitch - st, -0x4000)

    def animate_faces(self):
        """FUN_813123b0."""
        s = self.state
        for face, (on, sub) in (("memcard", (3, 4)), ("game", (1, 2)), ("options", (5, 6)), ("calendar", (7, 8))):
            f = self.faces[face]
            if s == on:
                f.active()
            elif s == sub:
                f.submenu()
            else:
                f.idle()

    def card_glow_step(self):
        """FUN_8131247c (no memory cards are emulated yet: both slots stay dim)."""
        for i in range(2):
            ready = False
            self.card_glow[i] = max(0x3C, min(0xFF, self.card_glow[i] + (4 if ready else -4)))

    def help_faders(self):
        """FUN_81312580 + FUN_81312a94 + FUN_81312044."""
        s = self.state
        h617c, h618c, h619c, h61ac, h61bc, h61cc, h61dc, h61ec = self.help
        on = set()
        if s == 0:
            on = {h617c}
        elif s in (1, 3, 5, 7):
            on = {h618c, h619c}
        elif s == 2:
            if self.sub_flag(2) == 0:                 # FUN_81327ab4
                on = {h61cc}
        elif s == 4:
            on = {h61ec, h61ac, h61cc if self.sub_flag(4) == 0 else h61dc}   # FUN_8131bf50
        elif s in (6, 8):
            if self.sub_flag(s) == 0:                 # FUN_813287d4 / FUN_81329680: 0 = editing
                on = {h61bc, h61cc}
            else:
                on = {h61ac, h61dc}
            on.add(h61ec)
        for f in self.help:
            f.step(0 if f in on else 1)
        i611c, i612c, i613c, i614c, i615c, i616c = self.icons
        if s == 0:
            on = {i614c}
        elif s in (1, 3, 5, 7):
            on = {i615c, i616c}
        elif s == 2:
            on = {i612c} if self.sub_flag(2) == 0 else set()
        else:
            on = {i611c, i612c, i613c}
        for f in self.icons:
            f.step(0 if f in on else 1)
        for i, f in enumerate(self.lang_faders):
            f.step(0 if i == self.help_lang else 1)
        for k, st in (("memcard", 4), ("game", 2), ("options", 6), ("calendar", 8)):
            for f in self.sub_faders[k]:
                f.step(0 if s == st else 1)

    def sub_flag(self, state):
        """The submenu's FUN_81327ab4 / FUN_8131bf50 / FUN_813287d4 / FUN_81329680."""
        sm = self.submenus.get(state)
        return sm.flag() if sm is not None else 1

    def game_faders(self):
        """FUN_81312044."""
        if True:   # FUN_813025e8() == 0: no disc
            self.game_banner.step(1)
            if self.disc_state == 0x12:
                self.game_nodisc.step(0)
                self.game_question.step(1)
            else:
                self.game_nodisc.step(1)
                self.game_question.step(0)

    def handle_input(self, pad):
        """FUN_81311868."""
        p, s = self.p, self.state
        at = lambda yaw, pitch: (p.yaw == yaw and p.pitch == pitch and p.zoom == 0) or p.busy
        rest = (p.yaw == 0 and p.pitch == 0) or p.busy
        moves = (("UP", 3, (-0x4000, None), 1), ("DOWN", 1, (0x4000, None), 3),
                 ("LEFT", 7, (None, -0x4000), 5), ("RIGHT", 5, (None, 0x4000), 7))
        for d, back_from, (yaw, pitch), to in moves:
            if d not in pad.dirs:
                continue
            if s == back_from:
                ok = (p.yaw == yaw if yaw is not None else p.pitch == pitch) and p.zoom == 0 or p.busy
                if ok:
                    self.audio.play_se(SE_BACK)
                    self.next = 0
            elif s == 0 and rest:
                self.audio.play_se(SE_MOVE)
                self.next = to
        face_at = {1: ("yaw", 0x4000), 3: ("yaw", -0x4000), 5: ("pitch", 0x4000), 7: ("pitch", -0x4000)}
        if s in face_at:
            attr, v = face_at[s]
            ready = (getattr(p, attr) == v and p.zoom == 0) or p.busy
            if "A" in pad.pressed and ready:
                self.audio.play_se(SE_OK)
                self.next = s + 1
            if "B" in pad.pressed and ready:
                self.audio.play_se(SE_BACK)
                self.next = 0

    def cube_world(self):
        """FUN_81311d50 (DAT_814b6058, before the cube's own scale)."""
        p = self.p
        m = p.wobble() @ rot_trans(0, 0, 0, p.yaw, p.pitch, 0)
        if p.zoom != 0:
            m = rot_trans(0.0, 0.0, self.push_z * (1.0 - cos16(p.zoom)), 0, 0,
                          int(p.zoom * self.push_rz)) @ m
        return m

    def tick(self, pad):
        p = self.p
        self.rotate()
        self.animate_faces()
        self.card_glow_step()
        self.help_faders()
        prev, nxt = self.state, self.next
        if prev == 2 and nxt == 1:
            for f in (self.game_banner, self.game_nodisc, self.game_question):
                f.value = 0
                f.ticks = 0
        if (prev, nxt) in ((1, 2), (3, 4), (5, 6), (7, 8)):
            sm = self.submenus.get(nxt)
            if sm is not None and hasattr(sm, "enter"):
                sm.enter()
        self.state = nxt
        s = self.state
        if s in (0, 1, 3, 5, 7):
            if self.zoom_out < p.zoom:
                p.zoom -= self.zoom_out
            if p.zoom <= self.zoom_out:
                p.zoom = 0
            self.handle_input(pad)
            p.time = s16(p.time + 7)
            self.game_faders()
        else:
            if p.zoom < 0x7FFF - self.zoom_in:
                p.zoom += self.zoom_in
            if 0x7FFF - self.zoom_in <= p.zoom:
                p.zoom = 0x7FFF
            p.time = s16(p.time + 7)
            sm = self.submenus.get(s)
            if sm is not None:
                self.next = sm.tick(pad)
            elif "B" in pad.pressed:
                self.audio.play_se(SE_BACK)
                self.next = s - 1
        world = self.cube_world()
        for f in self.faces.values():
            f.place(world)

    # -- drawing (FUN_8130e5c0) -------------------------------------------------
    def cube_alpha(self):
        return 255 * (0x7FFF - self.p.zoom) // 0x7FFF

    def draw(self, cam_y=-10.0):
        r = self.r
        view = r.view(cam_y)
        world = self.cube_world()
        alpha = self.cube_alpha()
        cube = scale_cols(world, 1.1 * self.scale, 1.1 * self.scale, 1.1 * self.scale)
        inner = r.menu_cube.materials_with_cull(1)
        outer = r.menu_cube.materials_with_cull(2)
        if alpha:
            r.draw(r.menu_cube, view, cube, alpha / 255.0, only=inner)
        self.draw_face_cubes(view)
        if alpha:
            r.draw(r.menu_cube, view, cube, alpha / 255.0, only=outer)
        self.draw_hud(view, world, alpha)

    def draw_face_cubes(self, view):
        model = self.r.face_cube
        mat = 0
        for name in ("memcard", "game", "options", "calendar"):
            for c in self.faces[name].draw_list():
                col = tuple(x / 255.0 for x in c.color)
                model.tev_color[mat] = [col] + list(model.j.materials[mat]["tev_colors"][1:])
                self.r.draw(model, view, scale_cols(c.mtx, c.scale, c.scale, c.scale), c.alpha / 255.0)

    # -- HUD (FUN_8131567c) -----------------------------------------------------
    def draw_hud(self, view, world, alpha):
        self.draw_panels(view, world, alpha)
        self.draw_submenus()
        self.draw_help(255)

    def on_cube(self, view, world, p):
        self.ui.set_matrix(view @ world @ placement(p))

    def draw_titles(self, view, world, a):
        """FUN_81312f88 / the first part of FUN_81314304."""
        self.on_cube(view, world, FRONT)
        self.ui.draw_layout(self.l_titles, self.bank, with_alpha(WHITE, sq(a)))

    def draw_panels(self, view, world, alpha):
        """FUN_81314304."""
        p = self.p

        def near(v):
            if -5000 < v < 5000:
                return abs(v)
            return 5000

        a, b = near(p.yaw), near(p.pitch)
        if b < a:
            self.draw_titles(view, world, 255 - a * 255 // 5000)
        elif a < b:
            self.draw_titles(view, world, 255 - b * 255 // 5000)
        elif a == 0:
            self.draw_titles(view, world, 255)

        def side(v):
            return cdiv(alpha * (255 - cdiv((0x4000 - abs(v)) * 255, 5000)), 255) & 0xFF

        if p.yaw < -0x2E39:
            self.panel_memcard(view, world, side(p.yaw))
        if p.yaw > 0x2E39:
            self.panel_game(view, world, side(p.yaw))
        if p.pitch < -0x2E39:
            self.panel_calendar(view, world, side(p.pitch))
        if p.pitch > 0x2E39:
            self.panel_options(view, world, side(p.pitch))

    def panel_memcard(self, view, world, a):
        """The Memory Card side: titl + FUN_8131358c for slots A and B."""
        ui, L = self.ui, self.l_memcard
        self.on_cube(view, world, P_MEMCARD)
        ui.draw_frames_named("fram", L, self.bank, with_alpha(WHITE, sq(a)))
        ui.draw_named("titl", L, self.bank, with_alpha(WHITE, sq(a)))
        for slot in range(2):
            ui.draw_named(fourcc("texa") + slot, L, self.bank, with_alpha(WHITE, a))
            glow = self.card_glow[slot]
            c = with_alpha(WHITE, cdiv(a * glow, 255))
            ui.draw_frames_named(fourcc("mca1") + slot * 0x100, L, self.bank, c)
            fill = cdiv(self.card_fill[slot] * (glow - 0x3C), 0xC3) & 0xFF
            if fill:
                for f_i, f in enumerate(L.frames):
                    if f["name"] == fourcc("mca2") + slot * 0x100:
                        y = f["y"] - f["h"] // 2 + f["h"] - fill * f["h"] // 255
                        ui.draw_frame(L, self.bank, f_i, c, f["x"] - f["w"] // 2, y, f["w"], fill * f["h"] // 255)

    def panel_game(self, view, world, a):
        """FUN_81313334."""
        ui, L = self.ui, self.l_game
        self.on_cube(view, world, P_GAME)
        white, shadow = with_alpha(WHITE, sq(a)), with_alpha(SHADOW, a)
        ui.draw_frames_named("fram", L, self.bank, white)
        ui.draw_named("titl", L, self.bank, white)
        for f, zname, name in ((self.game_nodisc, "zodi", "nodi"), (self.game_question, "zust", "qust")):
            fa = f.alpha()
            if fa > 0:
                ui.draw_named(zname, L, self.bank, with_alpha(SHADOW, cdiv(a * fa, 255)))
                ui.draw_named(name, L, self.bank, with_alpha(WHITE, cdiv(sq(a) * fa, 255)))

    def panel_calendar(self, view, world, a):
        """FUN_81313708."""
        ui, L = self.ui, self.l_calendar
        self.on_cube(view, world, P_CALENDAR)
        t = self.now()
        wday = (t.weekday() + 1) % 7         # OSCalendarTime: 0 = Sunday
        mon = t.month                        # tm_mon + 1
        d = self.digits
        values = {
            "al1": d[2], "al2": d[(t.year // 100) % 10], "al3": d[(t.year // 10) % 10], "al4": d[t.year % 10],
            "al5": d[(mon // 10) % 10], "al6": d[mon % 10], "al7": d[t.day // 10], "al8": d[t.day % 10],
            "im1": d[t.hour // 10], "im2": d[t.hour % 10], "im3": d[t.minute // 10], "im4": d[t.minute % 10],
            "im5": d[t.second // 10], "im6": d[t.second % 10],
        }
        white, shadow = with_alpha(WHITE, sq(a)), with_alpha(SHADOW, a)
        ui.draw_frames_named("fram", L, self.bank, white)
        for col, marks, date, week, time in ((shadow, ("zolo", "zlas", "zare", "zitl"), "zal", "zeek", "zim"),
                                             (white, ("colo", "slas", "pare", "titl"), "cal", "week", "tim")):
            for n in marks:
                ui.draw_named(n, L, self.bank, col)
            for i in range(8):
                ui.draw_named(f"{date}{i + 1}", L, self.bank, col, tex=values[f"al{i + 1}"])
            ui.draw_named(week, L, self.bank, col, tex=self.weekdays[wday])
            for i in range(6):
                ui.draw_named(f"{time}{i + 1}", L, self.bank, col, tex=values[f"im{i + 1}"])

    def panel_options(self, view, world, a):
        """FUN_81313f20."""
        ui, L, st = self.ui, self.l_options, self.settings
        self.on_cube(view, world, P_OPTIONS)
        white, shadow = with_alpha(WHITE, sq(a)), with_alpha(SHADOW, a)
        ui.draw_frames_named("fram", L, self.bank, white)
        for n in ("zxt1", "zxt2", "zitl"):
            ui.draw_named(n, L, self.bank, shadow)
        for n in ("txt1", "txt2", "titl"):
            ui.draw_named(n, L, self.bank, white)
        off = st.offset
        if off > 0:
            ui.draw_named("zrwr", L, self.bank, shadow)
            ui.draw_named("arwr", L, self.bank, white)
        elif off < 0:
            ui.draw_named("zrwl", L, self.bank, shadow)
            ui.draw_named("arwl", L, self.bank, white)
        v = min(32, abs(max(-32, off)))
        ui.draw_named("zum1", L, self.bank, shadow, tex=self.digits[v // 10])
        ui.draw_named("zum2", L, self.bank, shadow, tex=self.digits[v % 10])
        ui.draw_named("num1", L, self.bank, white, tex=self.digits[v // 10])
        ui.draw_named("num2", L, self.bank, white, tex=self.digits[v % 10])
        img = self.img_stereo if st.stereo else self.img_mono
        ui.draw_named("zond", L, self.bank, shadow, tex=img)
        ui.draw_named("sond", L, self.bank, white, tex=img)
        for n in ("zxt3", "zang"):
            ui.draw_named(n, L, self.bank, shadow)
        for n in ("txt3", "lang"):
            ui.draw_named(n, L, self.bank, white)

    def draw_submenus(self):
        """FUN_81314d70."""
        for key, state in (("memcard", 4), ("game", 2), ("options", 6), ("calendar", 8)):
            al = [f.alpha() for f in self.sub_faders[key]]
            sm = self.submenus.get(state)
            if al[0] > 0 and sm is not None:
                sm.draw(*al)

    def draw_backdrop(self, a):
        """FUN_8130af54: the tiled backdrop under a spotlight behind every menu screen."""
        if a > 0:
            self.r.draw_backdrop(a / 255.0)

    def draw_help(self, a):
        """FUN_8131508c: the button help bar."""
        ui, L = self.ui, self.help_layout
        ui.screen()
        i611c, i612c, i613c, i614c, i615c, i616c = (f.alpha() for f in self.icons)
        t = self.frame & 0xFFFF
        if (t % 256) < 128:
            pulse = 255 - (t % 128) * 155 // 127
        else:
            pulse = (t % 128) * 155 // 127 + 100
        pulse = pulse * a // 255
        glows = ((0x969696FF, (i611c, i614c)), (0xD20046FF, (i612c, i615c)), (0x00C88CFF, (i613c, i616c)))
        for k in range(2):
            for j, (col, fa) in enumerate(glows):
                ui.draw_named(fourcc("bac1") + 3 * k + j, L, self.bank, with_alpha(col, pulse * fa[k] // 255))
        stick = (i611c, i614c)
        for k in range(2):
            phase = (t >> 7) % 8
            u = (t % 128) * 255 // 127
            if phase % 2:
                u = 255 - u
            u = u * a // 255
            frame = ((phase + 1) // 2 + 3) % 4 + 1
            ui.draw_named(fourcc("stk0") + 5 * k + frame, L, self.bank, with_alpha(WHITE, (255 - u) * stick[k] // 255))
            ui.draw_named(fourcc("stk0") + 5 * k, L, self.bank, with_alpha(WHITE, u * stick[k] // 255))
        b = (255 * a // 255) * self.button_fade // 255
        ui.draw_named("butb", L, self.bank, with_alpha(WHITE, b * i612c // 255))
        ui.draw_named("dot2", L, self.bank, with_alpha(WHITE, b * i612c // 255))
        ui.draw_named("butc", L, self.bank, with_alpha(WHITE, b * i615c // 255))
        ui.draw_named("dot5", L, self.bank, with_alpha(WHITE, b * i615c // 255))
        ui.draw_named("buta", L, self.bank, with_alpha(WHITE, b * i613c // 255))
        ui.draw_named("dot3", L, self.bank, with_alpha(WHITE, b * i613c // 255))
        ui.draw_named("butd", L, self.bank, with_alpha(WHITE, b * i616c // 255))
        ui.draw_named("dot6", L, self.bank, with_alpha(WHITE, b * i616c // 255))
        ui.draw_named("dot1", L, self.bank, with_alpha(WHITE, b * i611c // 255))
        ui.draw_named("dot4", L, self.bank, with_alpha(WHITE, b * i614c // 255))
        # FUN_81314f44: the captions, cross-faded per language
        for lang, lf in enumerate(self.lang_faders):
            la = a * lf.alpha() // 255
            if la <= 0:
                continue
            for f in self.help:
                fa = f.alpha()
                if fa > 0:
                    ui.draw_string(self.help_strings[lang], f.index, L, self.bank,
                                   with_alpha(WHITE, la * fa // 255), offy=f.offset())
        self.button_fade = min(255, self.button_fade + 4)
        self.frame += 1
