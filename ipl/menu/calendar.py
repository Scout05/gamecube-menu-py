"""The Calendar menu (state 8): FUN_81329590 and friends.

Sub-states (G_814ad5ac): 0 choose Date/Time, 1 edit the date, 2 edit the time.
The console writes the new time to its RTC (FUN_81328ca4); here it is kept as an
offset from the computer's clock (Clock.offset), which every clock in the app uses.
"""
import datetime

from .. import rom
from ..ui import Layout, Strings
from .dotmatrix import DotMatrix

SE_CURSOR, SE_VALUE, SE_EDGE, SE_ENTER, SE_OK, SE_CANCEL, SE_LEAVE = 0xB, 9, 0xD, 8, 0xC, 7, 6

DAYS = [rom.u32(0x813814C0 + 4 * i) for i in range(12)]
DAYS_LEAP = [rom.u32(0x813814F0 + 4 * i) for i in range(12)]

RES_LAYOUT = [0x0E, 0x0E, 0x0E, 0x0E, 0x0E, 0x0E]          # G_814ad394 (shared)
RES_STRINGS = [0x10, 0x12, 0x11, 0x14, 0x13, 0x0F]         # DAT_814af740 by language


class Clock:
    """The console clock: the computer's time plus whatever the Calendar menu set."""

    def __init__(self, settings=None):
        self.settings = settings
        secs = settings.clock_offset if settings is not None else 0.0
        self.offset = datetime.timedelta(seconds=secs)

    def now(self):
        return datetime.datetime.now() + self.offset

    def set(self, dt):
        self.offset = dt - datetime.datetime.now()
        if self.settings is not None:
            self.settings.clock_offset = self.offset.total_seconds()
            self.settings.save()


class CalendarMenu:
    def __init__(self, hub, clock, cubes):
        self.hub = hub
        self.clock = clock
        self.cubes = cubes
        self.dm = DotMatrix(language=lambda: hub.settings.language)
        self.layout = Layout(hub.r_bytes(RES_LAYOUT[0]))
        self.sub = 0          # G_814ad5ac (requested) / G_814ad5a8 (current)
        self.cursor = 0       # G_814ad5b0: 0 date, 1 time
        self.date_field = 0   # G_814ad5b4: 0 year, 1 month, 2 day (in edit order)
        self.time_field = 0   # G_814ad5b8: 0 hour, 1 minute, 2 second
        self.values = [2000, 0, 1]   # DAT_814de600
        self.date_saved = [2000, 0, 1]   # DAT_814de614.. (year mon day)
        self.time_saved = [0, 0, 0]
        self.pulse = 100      # G_814ad5bc
        self.pulse_d = 2

    def strings(self):
        return Strings(self.hub.r_bytes(RES_STRINGS[self.hub.settings.language]))

    def enter(self):
        """FUN_81328dfc (part): every time the menu opens."""
        self.sub = 0
        self.cursor = 0
        self.date_field = 0
        self.time_field = 0

    # -- input -------------------------------------------------------------------
    def ready(self):
        """FUN_81329b6c(0): the display has finished assembling."""
        return self.dm.p.h(0xD6) <= self.dm.progress

    def tick_select(self, pad):
        """FUN_81328e60."""
        nxt = 8
        if not self.ready():
            return 8
        audio = self.hub.audio
        if "UP" in pad.dirs:
            if self.cursor != 0:
                audio.play_se(SE_CURSOR)
            self.cursor = 0
        if "DOWN" in pad.dirs:
            if self.cursor != 1:
                audio.play_se(SE_CURSOR)
            self.cursor = 1
        if "A" in pad.pressed:
            audio.play_se(SE_ENTER)
            t = self.clock.now()
            if self.cursor == 1:
                self.values = [t.hour, t.minute, 0]
                self.sub = 2
            else:
                self.values = [t.year, t.month - 1, t.day]
                self.sub = 1
        if "B" in pad.pressed and (self.hub.p.zoom == 0x7FFF or self.hub.p.busy):
            audio.play_se(SE_LEAVE)
            nxt = 7
        return nxt

    def _field_step(self, pad):
        """Left/right moves between fields; the PAL order is day, month, year (FUN_8132912c)."""
        lang = self.hub.settings.language + 4
        d = 1 if lang in (0, 2) else -1      # FUN_8130b474: PAL languages walk right-to-left
        return d

    def fix_date(self):
        """FUN_81328fe4."""
        v = self.values
        if v[0] < 2000:
            v[0] = 2099
        if v[0] > 2099:
            v[0] = 2000
        if v[1] < 0:
            v[1] = 11
        if v[1] > 11:
            v[1] = 0
        y = v[0]
        leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
        days = (DAYS_LEAP if leap else DAYS)[v[1]]
        if days < v[2]:
            v[2] = days
            if self.date_field == 2:
                v[2] = 1
        if v[2] < 1:
            v[2] = days

    def tick_date(self, pad):
        """FUN_8132912c."""
        audio = self.hub.audio
        side = pad.dirs & {"LEFT", "RIGHT"}
        if side and not (pad.dirs & {"UP", "DOWN"}):
            audio.play_se(10)
            d = self._field_step(pad)
            self.date_field += -d if "LEFT" in side else d
            self.date_field %= 3
        if "DOWN" in pad.dirs:
            self.values[self.date_field] -= 1
            audio.play_se(SE_VALUE)
            self.fix_date()
        if "UP" in pad.dirs:
            self.values[self.date_field] += 1
            audio.play_se(SE_VALUE)
            self.fix_date()
        if "A" in pad.pressed:
            audio.play_se(SE_OK)
            self.commit()
            self.sub = 0
        if "B" in pad.pressed:
            audio.play_se(SE_CANCEL)
            self.sub = 0
        return 8

    def fix_time(self):
        """FUN_81329338."""
        v, f = self.values, self.time_field
        top = 23 if f == 0 else 59
        if v[f] < 0:
            v[f] = top
        if v[f] > top:
            v[f] = 0

    def tick_time(self, pad):
        """FUN_81329404."""
        audio = self.hub.audio
        side = pad.dirs & {"LEFT", "RIGHT"}
        if side and not (pad.dirs & {"UP", "DOWN"}):
            audio.play_se(10)
            self.time_field += -1 if "LEFT" in side else 1
            self.time_field %= 3
        if "DOWN" in pad.dirs:
            audio.play_se(SE_VALUE)
            self.values[self.time_field] -= 1
            self.fix_time()
        if "UP" in pad.dirs:
            audio.play_se(SE_VALUE)
            self.values[self.time_field] += 1
            self.fix_time()
        if "A" in pad.pressed:
            audio.play_se(SE_OK)
            self.commit()
            self.sub = 0
        if "B" in pad.pressed:
            audio.play_se(SE_CANCEL)
            self.sub = 0
        return 8

    def commit(self):
        """FUN_81328ca4: write the edited date or time to the clock."""
        t = self.clock.now()
        if self.sub == 2:
            h, m, s = self.values
            new = t.replace(hour=h, minute=m, second=s, microsecond=0)
        else:
            y, mo, d = self.values
            new = t.replace(year=y, month=mo + 1, day=d)
        self.clock.set(new)

    def tick(self, pad):
        """FUN_81329590."""
        cur = self.sub
        if cur == 1:
            nxt = self.tick_date(pad)
        elif cur == 2:
            nxt = self.tick_time(pad)
        else:
            nxt = self.tick_select(pad)
        if self.hub.faces["calendar"].done:
            self.dm.calendar(cur, self.cursor, self.date_field, self.time_field, list(self.values), self.clock.now())
        self.pulse += self.pulse_d
        if self.pulse > 0x78:
            self.pulse_d = -2
        if self.pulse < 0x32:
            self.pulse_d = 2
        return nxt

    def flag(self):
        """FUN_81329680: 1 while choosing Date/Time, 0 while editing."""
        return 1 if self.sub == 0 else 0

    # -- drawing (FUN_8132969c) ------------------------------------------------------
    def draw(self, a0, a1, a2):
        hub = self.hub
        hub.draw_backdrop(a1)
        ui = hub.ui
        ui.screen()
        st = self.strings()
        ui.draw_string(st, 0, self.layout, hub.bank, 0xFFFFFF00 | a2)
        col = 0xFFFF0000 | (a2 & 0xFF)      # FUN_813218c0 +0x10..0x14 = (0xff, 0xff, 0)
        ui.draw_string(st, 1, self.layout, hub.bank, col)
        ui.draw_string(st, 2, self.layout, hub.bank, col)
        if hub.faces["calendar"].done:
            self.cubes.draw(hub.r.view2d_vp(), self.dm.instances(a0))
