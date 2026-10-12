"""The Game Play menu (state 2): FUN_81327920 / FUN_81327ccc.

The console asks the drive (FUN_813020ec) and shows "Press START", "Please
insert a disc", "Reading disc..." or an error, both as text and as cube letters.
There is no drive here, so the state is always 0x12 (no disc).
"""
from ..ui import Fader, Layout, Strings
from .dotmatrix import DotMatrix

RES_LAYOUT = [0x15] * 6                                  # G_814ad390
RES_STRINGS = [0x17, 0x19, 0x18, 0x1B, 0x1A, 0x16]       # DAT_814af728

DISC_READY, DISC_READING, DISC_NONE, DISC_COVER, DISC_BAD, DISC_FATAL, DISC_BAD2 = 0x10, 0x11, 0x12, 0x13, 0x16, 0x17, 0x18


class GameMenu:
    def __init__(self, hub, cubes):
        self.hub = hub
        self.cubes = cubes
        self.dm = DotMatrix(language=lambda: hub.settings.language)
        self.layout = Layout(hub.r_bytes(RES_LAYOUT[0]))
        self.starting = 0      # G_814ad578
        self.fatal = 0         # G_814ad57c
        # message faders (DAT_814de4c8..): string 0 START, 3 insert disc, 1 error, 4 reading, 2 bad disc
        self.f_start, self.f_insert, self.f_error, self.f_reading, self.f_bad = (
            Fader(index=0), Fader(index=3), Fader(index=1), Fader(index=4), Fader(index=2))
        self.f_banner = Fader()

    def strings(self):
        return Strings(self.hub.r_bytes(RES_STRINGS[self.hub.settings.language]))

    def status(self):
        return self.hub.disc_state

    def flag(self):
        """FUN_81327ab4."""
        return self.fatal

    def which(self):
        st = self.status()
        return 0 if st == DISC_READY else (1 if st == DISC_NONE else 2)

    def enter(self):
        """FUN_81327a10."""
        if self.hub.faces["game"].done:
            self.dm.game(self.starting, self.which(), self.hub.settings.language)
        for f in (self.f_start, self.f_insert, self.f_error, self.f_reading, self.f_bad, self.f_banner):
            f.value = 0
            f.ticks = 0

    def messages(self):
        """FUN_813277a8."""
        st = 0x17 if self.fatal else self.status()
        self.f_start.step(0 if st == DISC_READY else 1)
        self.f_insert.step(0 if st in (DISC_NONE, DISC_COVER) else 1)
        self.f_bad.step(0 if st in (DISC_BAD, DISC_BAD2) else 1)
        self.f_error.step(0 if st == DISC_FATAL else 1)
        if st in (DISC_READY, DISC_NONE, DISC_COVER, DISC_BAD, DISC_BAD2, DISC_FATAL):
            self.f_reading.step(1)
        else:
            self.f_reading.step(0)

    def tick(self, pad):
        """FUN_81327920 / FUN_813276ac."""
        hub = self.hub
        nxt = 2
        face_done = hub.faces["game"].done
        if not self.starting and face_done and self.dm.p.h(0xD6) <= self.dm.progress and not self.fatal:
            if "B" in pad.pressed and (hub.p.zoom == 0x7FFF or hub.p.busy):
                hub.audio.play_se(6)
                nxt = 1
            if "START" in pad.pressed and self.status() == DISC_READY:
                hub.audio.play_se(0x16)
                self.starting = 1
        self.messages()
        if face_done:
            self.dm.game(self.starting, self.which(), hub.settings.language)
        self.f_banner.step(1)       # FUN_813025e8: no disc banner
        return nxt

    def draw(self, a0, a1, a2):
        """FUN_81327ccc."""
        hub = self.hub
        hub.draw_backdrop(a1)
        if hub.faces["game"].done:
            self.cubes.draw(hub.r.view2d_vp(), self.dm.instances(a0))
        ui = hub.ui
        ui.screen()
        st = self.strings()
        for f in (self.f_start, self.f_insert, self.f_reading, self.f_bad):
            fa = f.alpha()
            if fa > 0:
                ui.draw_string(st, f.index, self.layout, hub.bank, 0xFFFFFF00 | (a2 * fa // 255), offy=f.offset())
