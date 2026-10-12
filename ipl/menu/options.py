"""The Options menu (state 6): FUN_81328648 and friends.

Sub-states (G_814ad590): 0 choose a row, 1 Sound, 2 Screen Position, 3 Language.
"""
from ..ui import Fader, Layout, Strings, fourcc
from .dotmatrix import DotMatrix

SE_CURSOR, SE_VALUE, SE_EDGE, SE_ENTER, SE_OK, SE_CANCEL, SE_LEAVE = 0xB, 9, 0xD, 8, 0xC, 7, 6

RES_LAYOUT = 0x7A                                  # G_814ad3a0 -> G_814ad588
RES_STRINGS = [0x7C, 0x7E, 0x7D, 0x80, 0x7F, 0x7B]  # DAT_814af788: one STH0 per language

LABEL_COLOR = (0x00, 0xFF, 0x78)                   # DAT_814d9f00 +0x10/+0x12/+0x14
LABEL_DY = (-15.0, -4.0, 6.0)                      # +0x24 / +0x28 / +0x2c
LIST_DY = 8.0                                      # +0x30


class OptionsMenu:
    def __init__(self, hub, cubes, apply):
        self.hub = hub
        self.cubes = cubes
        self.apply = apply          # callback(settings) after Sound / Screen Position / Language change
        st = hub.settings
        self.dm = DotMatrix(language=lambda: hub.settings.language)
        self.layout = Layout(hub.r_bytes(RES_LAYOUT))
        self.strings = [Strings(hub.r_bytes(r)) for r in RES_STRINGS]
        xs = {}
        for e in self.layout.elements:
            for i in range(6):
                if e["name"] == fourcc("lan1") + i:
                    xs[i] = e["x"] >> 4
        self.dm.lang_x = [xs.get(i, 0) for i in range(6)]        # DAT_814de5f0
        self.sub = 0                # G_814ad590 / G_814ad58c
        self.cursor = 0             # G_814ad594
        self.offset = st.offset     # G_814ad596
        self.sound = st.stereo      # G_814ad598
        self.lang = st.language     # G_814ad59a
        self.list_faders = [Fader() for _ in range(6)]    # DAT_814de530
        self.text_faders = [Fader() for _ in range(6)]    # DAT_814de590
        self.pulse, self.pulse_d = 100, 2

    def flag(self):
        """FUN_813287d4: 1 while choosing a row."""
        return 1 if self.sub == 0 else 0

    def ready(self):
        return self.dm.p.h(0xD6) <= self.dm.progress

    # -- input ------------------------------------------------------------------------
    def tick_select(self, pad):
        """FUN_81328104."""
        audio, st = self.hub.audio, self.hub.settings
        if not self.ready():
            return 6
        if "UP" in pad.dirs and self.cursor > 0:
            audio.play_se(SE_CURSOR)
            self.cursor -= 1
        if "DOWN" in pad.dirs and self.cursor < 2:
            audio.play_se(SE_CURSOR)
            self.cursor += 1
        if "A" in pad.pressed:
            audio.play_se(SE_ENTER)
            if self.cursor == 1:
                self.sub = 2
                self.offset = st.offset
            elif self.cursor == 0:
                self.sound = st.stereo
                self.sub = 1
            else:
                self.sub = 3
                self.lang = st.language
        if "B" in pad.pressed and (self.hub.p.zoom == 0x7FFF or self.hub.p.busy):
            audio.play_se(SE_LEAVE)
            return 5
        return 6

    def _edge(self, pad):
        if not pad.repeating:
            self.hub.audio.play_se(SE_EDGE)

    def tick_sound(self, pad):
        """FUN_8132827c."""
        audio, st = self.hub.audio, self.hub.settings
        if "LEFT" in pad.dirs:
            if self.sound == 0:
                self._edge(pad)
            else:
                audio.play_se(SE_VALUE)
            self.sound = 0
        if "RIGHT" in pad.dirs:
            if self.sound == 1:
                self._edge(pad)
            else:
                audio.play_se(SE_VALUE)
            self.sound = 1
        if "A" in pad.pressed:
            if st.stereo != self.sound:
                st.stereo = self.sound
                self.apply(st)
            audio.play_se(SE_OK)
            self.sub = 0
        if "B" in pad.pressed:
            audio.play_se(SE_CANCEL)
            self.sound = st.stereo
            self.sub = 0
        return 6

    def tick_offset(self, pad):
        """FUN_813283a0 (the picture moves while the value changes, like VIShift)."""
        audio, st = self.hub.audio, self.hub.settings
        if "LEFT" in pad.dirs:
            self.offset -= 1
            if self.offset < -0x20:
                self.offset = -0x20
                self._edge(pad)
            else:
                audio.play_se(SE_VALUE)
            self.apply(st, preview_offset=self.offset)
        if "RIGHT" in pad.dirs:
            self.offset += 1
            if self.offset < 0x21:
                audio.play_se(SE_VALUE)
            else:
                self.offset = 0x20
                self._edge(pad)
            self.apply(st, preview_offset=self.offset)
        if "A" in pad.pressed:
            audio.play_se(SE_OK)
            st.offset = self.offset
            self.apply(st)
            self.sub = 0
        if "B" in pad.pressed:
            audio.play_se(SE_CANCEL)
            self.offset = st.offset
            self.apply(st)
            self.sub = 0
        return 6

    def tick_lang(self, pad):
        """FUN_81328510 (the help bar previews the language being chosen)."""
        audio, st, hub = self.hub.audio, self.hub.settings, self.hub
        if "LEFT" in pad.dirs:
            self.lang -= 1
            if self.lang < 0:
                self.lang = 0
                self._edge(pad)
            else:
                audio.play_se(SE_VALUE)
            hub.help_lang = self.lang
        if "RIGHT" in pad.dirs:
            self.lang += 1
            if self.lang < 6:
                audio.play_se(SE_VALUE)
            else:
                self.lang = 5
                self._edge(pad)
            hub.help_lang = self.lang
        if "A" in pad.pressed:
            audio.play_se(SE_OK)
            if st.language != self.lang:
                st.language = self.lang
                hub.set_language(self.lang)
                self.apply(st)
            self.sub = 0
        if "B" in pad.pressed:
            audio.play_se(SE_CANCEL)
            self.lang = st.language
            hub.help_lang = self.lang
            self.sub = 0
        return 6

    def tick(self, pad):
        """FUN_81328648."""
        cur = self.sub
        if cur == 2:
            nxt = self.tick_offset(pad)
        elif cur == 0:
            nxt = self.tick_select(pad)
        elif cur == 1:
            nxt = self.tick_sound(pad)
        else:
            nxt = self.tick_lang(pad)
        if self.hub.faces["options"].done:
            self.dm.options(cur, self.cursor, self.offset, self.sound, self.lang, self.hub.settings.language)
        for i in range(6):
            self.list_faders[i].step(0 if i == self.lang else 2)
            self.text_faders[i].step(1 if i != self.lang else 0)
        self.pulse += self.pulse_d
        if self.pulse > 0x78:
            self.pulse_d = -2
        if self.pulse < 0x32:
            self.pulse_d = 2
        return nxt

    # -- drawing (FUN_81328b20 / FUN_81328814) --------------------------------------------
    def draw(self, a0, a1, a2):
        hub = self.hub
        hub.draw_backdrop(a1)
        ui, L, bank = hub.ui, self.layout, hub.bank
        ui.screen()
        for i in range(6):
            fa = a2 * self.text_faders[i].alpha() // 255
            if fa <= 0:
                continue
            st = self.strings[i]
            ui.draw_string(st, 0, L, bank, 0xFFFFFF00 | fa)
            col = (LABEL_COLOR[0] << 24) | (LABEL_COLOR[1] << 16) | (LABEL_COLOR[2] << 8) | fa
            for k in range(3):
                ui.draw_string(st, k + 1, L, bank, col, offy=int(16 * LABEL_DY[k]))
        for i in range(6):
            fa = a2 * self.list_faders[i].alpha() // 255
            ui.draw_named(fourcc("lan1") + i, L, bank, 0xFFFFFF00 | fa, offy=int(16 * LIST_DY))
        for i in range(5):
            ui.draw_named(fourcc("sla1") + i, L, bank, 0xFFFFFF00 | (a2 * 128 // 255), offy=int(16 * LIST_DY))
        if hub.faces["options"].done:
            self.cubes.draw(hub.r.view2d_vp(), self.dm.instances(a0))
