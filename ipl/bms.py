"""JAudio BMS sequencer + sample synthesizer, ported from the GameCube IPL.

Sequencer: FUN_81356d80 (note/wait loop), command table at RAM 0x813f6b44,
handlers 0x813f6c44, parameter ramps FUN_81353e60, register ops FUN_81354180,
interrupts FUN_81355620/FUN_813555e0, ports FUN_81354920.
Synth: instrument lookup FUN_8135a300 (IBNK), envelopes FUN_813525e0,
pitch FUN_81358bc0. Output 32 kHz stereo, processed in 80-sample frames like
the GameCube's AX/DSP (400 updates per second).
"""
import math
import struct

import numpy as np

OUT_RATE = 32028.5          # the GameCube's real DSP output rate
FRAME = 80                  # samples per audio frame (sequencer + envelope update)
FRAMES_PER_SEC = OUT_RATE / FRAME
ENV_UNIT = FRAMES_PER_SEC / 600.0   # envelope time units are 1/600 s

# ROM offsets (descrambled IPL)
IBNK_ROM = 0xF7E40
WSYS_ROM = 0xF8720
AW_ROM = 0xF8DA0
SEQ_ROM = 0x1AAEA0
SEQ_SIZE = 0xC40

# command argument formats (from the IPL's table): op -> list of types
# 0 = u8, 1 = u16, 2 = u24, 3 = register (u8 index)
ARGS = {
    0xC1: [0, 2], 0xC2: [0, 2], 0xC3: [2], 0xC4: [], 0xC5: [], 0xC6: [0], 0xC7: [2], 0xC8: [],
    0xC9: [1], 0xCA: [], 0xCB: [0, 0], 0xCC: [0, 3], 0xCD: [0], 0xCE: [0], 0xCF: [3],
    0xD0: [1, 1], 0xD1: [0, 3], 0xD2: [0, 3], 0xD3: [3, 3], 0xD4: [0], 0xD5: [0], 0xD6: [0],
    0xD7: [0, 2], 0xD8: [1, 1, 1, 1, 1], 0xD9: [0], 0xDA: [0], 0xDB: [0], 0xDC: [1],
    0xDD: [0, 1], 0xDE: [0], 0xDF: [0, 2], 0xE0: [0], 0xE1: [], 0xE2: [], 0xE3: [],
    0xE4: [0, 1], 0xE5: [], 0xE6: [], 0xE7: [1], 0xE8: [], 0xE9: [], 0xEA: [2],
    0xEB: [0, 0, 0, 0, 0], 0xEC: [1, 1, 1, 1], 0xED: [2], 0xEE: [2], 0xEF: [0, 0, 0],
    0xF0: [0], 0xF1: [0], 0xF2: [0, 2, 0], 0xFA: [1], 0xFB: [], 0xFC: [],
    0xFD: [1], 0xFE: [1], 0xFF: [],
}

AFC_COEFS = [(0, 0), (2048, 0), (0, 2048), (1024, 1024), (4096, -2048), (3584, -1536), (3072, -1024),
             (4608, -2560), (4200, -2248), (4800, -2300), (5120, -3072), (2048, -2048), (1024, -1024),
             (-1024, 1024), (-1024, 0), (-2048, 0)]

# oscillator presets copied by commands D6/D7/D8 (RAM 0x813f7248.. tables)
VIBRATO_TABLE = [(0, 0, 0), (0, 20, 32767), (0, 20, 0), (0, 20, -16384), (0, 20, 0), (13, 0, 1)]
TREMOLO_TABLE = [(0, 0, 32767), (0, 20, 0), (0, 20, -32767), (0, 20, 0), (0, 20, 32767), (13, 0, 1)]
QUICK_RELEASE = [(0, 10, 0), (15, 0, 0)]


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s8(v):
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


# ---------------------------------------------------------------------------
# bank / waves
# ---------------------------------------------------------------------------
def decode_afc(data, nsamples):
    out = np.zeros(nsamples + 16, np.int32)
    h1 = h2 = 0
    n = 0
    for f in range(0, len(data) - 8, 9):
        hdr = data[f]
        scale = 1 << (hdr >> 4)
        c1, c2 = AFC_COEFS[hdr & 0xF]
        for b in data[f + 1:f + 9]:
            for nib in (b >> 4, b & 0xF):
                if nib >= 8:
                    nib -= 16
                s = (((nib * scale) << 11) + c1 * h1 + c2 * h2) >> 11
                s = -32768 if s < -32768 else 32767 if s > 32767 else s
                out[n] = s
                n += 1
                h2, h1 = h1, s
        if n >= nsamples:
            break
    return (out[:nsamples].astype(np.float32) / 32768.0)


class Wave:
    def __init__(self, rom, entry):
        e = WSYS_ROM + entry
        self.base_key = rom[e + 2]
        self.rate = struct.unpack_from(">f", rom, e + 4)[0]
        off, size, loop, ls, le, n = struct.unpack_from(">6I", rom, e + 8)
        self.loop = loop == 0xFFFFFFFF
        self.loop_start, self.loop_end = ls, le
        self.samples = decode_afc(rom[AW_ROM + off:AW_ROM + off + size], n)
        # guard sample for interpolation
        self.samples = np.append(self.samples, self.samples[ls] if self.loop else 0.0).astype(np.float32)
        self.length = n


class Osc:
    """One oscillator definition (IBNK OSC or a sequencer preset)."""

    def __init__(self, target=0, rate=1.0, attack=None, release=None, width=1.0, vertex=0.0):
        self.target, self.rate, self.attack, self.release = target, rate, attack, release
        self.width, self.vertex = width, vertex

    def copy(self):
        return Osc(self.target, self.rate, self.attack, self.release, self.width, self.vertex)


class Bank:
    """IBNK (Banks/ipl.bnk) + WSYS (Banks/ipl.ws) + ipl_0.aw."""

    def __init__(self, rom):
        self.rom = rom
        # waves
        winf = struct.unpack_from(">I", rom, WSYS_ROM + 0x10)[0]
        group = WSYS_ROM + struct.unpack_from(">I", rom, WSYS_ROM + winf + 8)[0]
        count = struct.unpack_from(">I", rom, group + 0x70)[0]
        winf_waves = [Wave(rom, struct.unpack_from(">I", rom, group + 0x74 + 4 * i)[0]) for i in range(count)]
        # WBCT -> SCNE -> C-DF gives the wave id of each WINF entry; instruments use those ids
        wbct = WSYS_ROM + struct.unpack_from(">I", rom, WSYS_ROM + 0x14)[0]
        scne = WSYS_ROM + struct.unpack_from(">I", rom, wbct + 0x0C)[0]
        cdf = WSYS_ROM + struct.unpack_from(">I", rom, scne + 0x0C)[0]
        n = struct.unpack_from(">I", rom, cdf + 4)[0]
        self.waves = {}
        for i in range(n):
            e = WSYS_ROM + struct.unpack_from(">I", rom, cdf + 8 + 4 * i)[0]
            wave_id = struct.unpack_from(">H", rom, e + 2)[0]
            self.waves[wave_id] = winf_waves[i]
        self.winf_waves = winf_waves
        # instruments
        b = IBNK_ROM
        self.programs = {}
        for prog in range(240):
            p = struct.unpack_from(">I", rom, b + 0x24 + 4 * prog)[0]
            if p == 0 or rom[b + p:b + p + 4] != b"INST":
                continue
            ib = b + p
            pitch, vol = struct.unpack_from(">ff", rom, ib + 8)
            oscs = []
            for k in range(2):
                op = struct.unpack_from(">I", rom, ib + 0x10 + 4 * k)[0]
                oscs.append(self._osc(op) if op else None)
            nkeys = struct.unpack_from(">I", rom, ib + 0x28)[0]
            keys = []
            for k in range(nkeys):
                kr = b + struct.unpack_from(">I", rom, ib + 0x2C + 4 * k)[0]
                high = rom[kr]
                nv = struct.unpack_from(">I", rom, kr + 4)[0]
                vels = []
                for v in range(nv):
                    vr = b + struct.unpack_from(">I", rom, kr + 8 + 4 * v)[0]
                    vhigh = rom[vr]
                    wave = struct.unpack_from(">H", rom, vr + 6)[0]
                    rvol, rpitch = struct.unpack_from(">ff", rom, vr + 8)
                    vels.append((vhigh, wave, rvol, rpitch))
                keys.append((high, vels))
            self.programs[prog] = dict(pitch=pitch, volume=vol, oscs=oscs, keys=keys)

    def _env(self, off):
        if off == 0:
            return None
        out = []
        a = IBNK_ROM + off
        for i in range(32):
            mode, t, v = struct.unpack_from(">hHh", self.rom, a + 6 * i)
            out.append((mode, t, v))
            if mode in (0xE, 0xF):
                break
        return out

    def _osc(self, off):
        a = IBNK_ROM + off
        target = self.rom[a]
        rate = struct.unpack_from(">f", self.rom, a + 4)[0]
        att, rel = struct.unpack_from(">II", self.rom, a + 8)
        width, vertex = struct.unpack_from(">ff", self.rom, a + 16)
        return Osc(target, rate, self._env(att), self._env(rel), width, vertex)

    def lookup(self, prog, key, vel):
        inst = self.programs.get(prog)
        if inst is None:
            return None
        for high, vels in inst["keys"]:
            if key <= high:
                for vhigh, wave, rvol, rpitch in vels:
                    if vel <= vhigh:
                        return inst, wave, rvol, rpitch
                return None
        return None


# ---------------------------------------------------------------------------
# envelope evaluator (FUN_813525e0)
# ---------------------------------------------------------------------------
class EnvState:
    def __init__(self, osc):
        self.osc = osc
        self.state = 1 if osc is not None and osc.attack is not None else 0  # 1 = start attack
        self.index = 0
        self.remaining = 0.0
        self.value = 1.0 if osc is None or osc.attack is None else 0.0
        self.target = self.value
        self.step = 0.0
        self.curve = 0
        self.table = osc.attack if osc is not None else None
        self.done = False

    def release(self):
        if self.osc is None:
            return
        if self.osc.release is not None:
            self.table = self.osc.release
            self.index = 0
            self.remaining = 0.0
            self.target = self.value
            self.state = 2
        else:
            self.state = 5  # no release table: hold

    def tick(self):
        osc = self.osc
        if osc is None or self.table is None:
            return 1.0
        if self.state == 3:   # sustain (mode 0x0E)
            return self.value * osc.width + osc.vertex
        self.remaining -= osc.rate if self.state != 7 else 1.0
        while self.remaining <= 0.0:
            self.value = self.target
            if self.index >= len(self.table):
                self.state = 3
                return self.value * osc.width + osc.vertex
            mode, t, v = self.table[self.index]
            if mode == 0xD:
                self.index = v
                continue
            if mode == 0xF:
                self.done = True
                self.state = 0
                return self.value * osc.width + osc.vertex
            if mode == 0xE:
                self.state = 3
                return self.value * osc.width + osc.vertex
            self.curve = mode
            self.target = v / 32768.0
            if t == 0:
                self.value = self.target
                self.index += 1
                continue
            self.remaining = t * ENV_UNIT
            self.step = (self.target - self.value) / self.remaining
            self.index += 1
        x = self.target - self.step * self.remaining
        self.value = x
        if self.curve == 1:
            x = x * x if x > 0 else -x * x
        elif self.curve == 2:
            x = math.sqrt(x) if x > 0 else -math.sqrt(-x)
        return x * osc.width + osc.vertex


# ---------------------------------------------------------------------------
# voices
# ---------------------------------------------------------------------------
SEMI = [2.0 ** (i / 12.0) for i in range(-64, 64)]


def semitone_ratio(semis):
    return 2.0 ** (semis / 12.0)


class Voice:
    def __init__(self, track, wave, base_pitch, volume, oscs, gate_frames):
        self.track = track
        self.wave = wave
        self.base_pitch = base_pitch   # playback step (input samples per output sample)
        self.volume = volume
        self.envs = [EnvState(o) for o in oscs]
        self.pos = 0.0
        self.gate = gate_frames        # auto key-off countdown (-1 = until note-off)
        self.released = False
        self.alive = True
        self.pitch_mul = 1.0

    def key_off(self):
        if not self.released:
            self.released = True
            for e in self.envs:
                e.release()

    def render(self, nframes_out):
        """Render FRAME samples; returns mono float32 array or None when finished."""
        if not self.alive:
            return None
        vol_env = 1.0
        pitch_env = 1.0
        any_volume_osc = False
        for e in self.envs:
            v = e.tick()
            if e.osc is not None and e.osc.target == 1:
                pitch_env *= v
            else:
                vol_env *= v
                if e.osc is not None:
                    any_volume_osc = True
                    if self.released and e.done:
                        self.alive = False
        if self.gate > 0:
            self.gate -= 1
            if self.gate == 0:
                self.key_off()
        if self.released and not any_volume_osc:
            self.alive = False
        t = self.track
        step = self.base_pitch * pitch_env * t.out_pitch
        w = self.wave
        idx = self.pos + step * np.arange(nframes_out, dtype=np.float64)
        if w.loop:
            span = w.loop_end - w.loop_start
            over = idx >= w.loop_end
            if span > 0:
                idx = np.where(over, w.loop_start + np.mod(idx - w.loop_start, span), idx)
        else:
            if idx[0] >= w.length:
                self.alive = False
                return None
        i0 = np.minimum(idx.astype(np.int64), w.length - 1)
        frac = (idx - np.floor(idx)).astype(np.float32)
        s = w.samples[i0] * (1.0 - frac) + w.samples[np.minimum(i0 + 1, w.length)] * frac
        if not w.loop:
            s[idx >= w.length] = 0.0
        self.pos += step * nframes_out
        if w.loop and self.pos >= w.loop_end and w.loop_end > w.loop_start:
            self.pos = w.loop_start + math.fmod(self.pos - w.loop_start, w.loop_end - w.loop_start)
        return s * (self.volume * max(0.0, vol_env) * t.out_volume)


# ---------------------------------------------------------------------------
# sequencer tracks
# ---------------------------------------------------------------------------
class Track:
    def __init__(self, seq, parent, pc, index=0):
        self.seq = seq
        self.parent = parent
        self.pc = pc
        self.index = index
        self.children = [None] * 16
        self.stack = []
        self.loop_counts = []
        self.wait = 0
        self.alive = True
        self.regs = [0] * 0x40
        self.regs32 = [0] * 4
        self.ports = [[0, 0, 0] for _ in range(16)]   # [received, sent, value]
        self.params = [[0.0, 0.0, 0.0, 0.0] for _ in range(18)]  # value, target, steps, delta
        self.params[0][:2] = [1.0, 1.0]
        self.params[3][:2] = [0.5, 0.5]
        self.voices = [None] * 8
        self.osc_mode = [0xF, 0xF]
        self.osc = [Osc(0, 1.0, None, QUICK_RELEASE, 1.0, 0.0), Osc(0, 0.0, None, None, 1.0, 0.0)]
        self.transpose = 0
        self.transpose_own = 0
        self.last_note = 0
        self.tempo = 120
        self.timebase = 0x30
        self.handle = 0
        self.int_enabled = 0
        self.int_pending = 0
        self.int_vector = [0] * 8
        self.in_int = 0
        self.saved = None
        self.timer = [0, 0, 0]
        if parent is None:
            self.regs[6] = 0xF0
            self.pitch_range = 12
            self.regs[0x23 - 0x20] = 0
        else:
            self.regs[6] = parent.regs[6]
            self.pitch_range = parent.pitch_range
            self.tempo = parent.tempo
            self.timebase = parent.timebase
        self.out_volume = 1.0
        self.out_pitch = 1.0
        self.out_pan = 0.5
        self.out_fx = 0.0
        self.mix_volume = 1.0

    # -- byte stream ----------------------------------------------------
    def u8(self):
        v = self.seq.data[self.pc]
        self.pc += 1
        return v

    def u16(self):
        return (self.u8() << 8) | self.u8()

    def u24(self):
        return (self.u8() << 16) | (self.u8() << 8) | self.u8()

    # -- registers (FUN_81354620 / FUN_81354020) --------------------------
    def reg_read(self, r):
        r &= 0xFF
        if r in (0x20, 0x21):
            v = self.regs[6]
            return (s16(v) >> 8) & 0xFF if r == 0x20 else v & 0xFF
        if r == 0x22:
            return ((self.reg_read(0) & 0xFFFF) << 8) | self.reg_read(1)
        if 0x28 <= r < 0x2C:
            # 32-bit registers overlay the u16 registers 0x10..0x17 (track+0x28c..)
            k = 0x10 + 2 * (r - 0x28)
            return ((self.regs[k] & 0xFFFF) << 16) | (self.regs[k + 1] & 0xFFFF)
        if r == 0x2C:
            v = 0
            for i in range(15, -1, -1):
                c = self.children[i]
                v = (v << 1) | (1 if c is not None and c.alive else 0)
            return v
        if r == 0x2D:
            v = 0
            for i in range(7, -1, -1):
                v = (v << 1) | (1 if self.voices[i] is None or not self.voices[i].alive else 0)
            return v
        if r == 0x30:
            return self.loop_counts[-1] if self.loop_counts else 0
        if r < 0x40:
            v = s16(self.regs[r])
            return v & 0xFF if r <= 2 else v
        return 0

    def reg_write(self, r, v):
        r &= 0xFF
        v = int(v)
        raw = v & 0xFFFF
        if r < 3:
            v8 = v & 0xFF
            raw = v8
            result = (v8 | 0xFF00) if v8 & 0x80 else v8
        elif r == 0x22:
            self.reg_write(0, (v >> 8) & 0xFF)
            r = 1
            raw = v & 0xFF
            result = v & 0xFFFF
        elif r in (0x20, 0x21):
            if r == 0x20:
                self.regs[6] = ((v & 0xFF) << 8) | (self.regs[6] & 0xFF)
            else:
                self.regs[6] = (self.regs[6] & 0xFF00) | (v & 0xFF)
            self.program_changed()
            self.regs[3] = v & 0xFFFF
            return
        elif 0x28 <= r < 0x2C:
            k = 0x10 + 2 * (r - 0x28)
            self.regs[k], self.regs[k + 1] = (v >> 16) & 0xFFFF, v & 0xFFFF
            return
        else:
            result = raw
        if r < 0x40:
            self.regs[r] = raw
        self.regs[3] = result & 0xFFFF
        if r == 6:
            self.program_changed()

    def program_changed(self):
        """FUN_81358e40: a program change switches both oscillators back to the instrument's."""
        self.osc_mode = [0xF, 0xF]

    def cond(self, c):
        """FUN_81353cc0: conditions test register 3."""
        v = self.regs[3] & 0xFFFF
        c &= 0xF
        return (c == 0 or (c == 1 and v == 0) or (c == 2 and v != 0) or (c == 3 and v == 1)
                or (c == 4 and v >= 0x8000) or (c == 5 and v < 0x8000))

    def arg(self, t):
        if t == 0:
            return self.u8()
        if t == 1:
            return self.u16()
        if t == 2:
            return self.u24()
        return self.reg_read(self.u8())

    # -- parameters (FUN_81353e60) ---------------------------------------
    def set_param(self, pid, value, steps):
        p = self.params[pid]
        p[1] = value
        if steps is not None:
            p[2] = float(steps)
        if p[2] > 0:
            p[3] = (p[1] - p[0]) / p[2]
        else:
            p[0] = p[1]
            p[3] = 0.0
            p[2] = 1.0

    def update_params(self):
        for p in self.params:
            if p[2] > 0.0:
                p[0] += p[3]
                p[2] -= 1.0

    def update_outputs(self):
        """FUN_81355000: combine own params with the parent's."""
        vol = self.params[0][0]
        pitch_semis = 4.0 * self.params[1][0] * self.pitch_range
        pitch = semitone_ratio(pitch_semis)
        if self.parent is None:
            self.out_volume, self.out_pitch = vol, pitch
            self.out_pan, self.out_fx = self.params[3][0], self.params[2][0]
        else:
            self.out_volume = self.parent.out_volume * vol
            self.out_pitch = self.parent.out_pitch * pitch
            self.out_pan = self.params[3][0]
            self.out_fx = self.params[2][0] if self.params[2][0] else self.parent.out_fx

    # -- interrupts -------------------------------------------------------
    def raise_int(self, n):
        if self.int_enabled & (1 << n):
            self.int_pending |= 1 << n

    def dispatch_int(self):
        if self.in_int:
            return
        for n in range(8):
            bit = 1 << n
            if self.int_enabled & bit and self.int_pending & bit:
                self.saved = (self.pc, self.wait)
                self.pc = self.int_vector[n]
                self.in_int = bit
                self.wait = 0
                self.int_pending ^= bit
                return

    # -- notes ------------------------------------------------------------
    def note_on(self, slot, key, vel, gate_ticks):
        if self.voices[slot] is not None:
            self.voices[slot].key_off()
            self.voices[slot] = None
        prog_reg = self.regs[6]
        prog = prog_reg & 0xFF
        hit = self.seq.bank.lookup(prog, key, vel)
        if hit is None:
            return
        inst, wave_id, rvol, rpitch = hit
        wave = self.seq.bank.waves[wave_id]
        pitch = inst["pitch"] * rpitch * (wave.rate / OUT_RATE) * semitone_ratio(key - wave.base_key)
        volume = rvol * inst["volume"] * (vel / 127.0) ** 2
        oscs = list(inst["oscs"])
        for k in range(2):
            m = self.osc_mode[k]
            if m in (0xE, 0xF):
                continue
            if 4 <= m < 8 and inst["oscs"][m - 4] is not None:
                o = self.osc[k].copy()
                src = inst["oscs"][m - 4]
                o.attack, o.release = src.attack, src.release
                oscs[k] = o
            elif m >= 8 and inst["oscs"][m - 8] is not None:
                oscs[k] = inst["oscs"][m - 8].copy()
            else:
                oscs[k] = self.osc[k].copy()
        gate_frames = -1
        if gate_ticks is not None and gate_ticks >= 0:
            gate_frames = max(1, int(gate_ticks * FRAMES_PER_SEC / self.seq.ticks_per_sec))
        v = Voice(self, wave, pitch, volume, oscs, gate_frames)
        self.voices[slot] = v
        self.seq.voices.append(v)
        # channel pool: a track only keeps a few releasing voices (oldest is stolen)
        tails = [x for x in self.seq.voices if x.track is self and x.released and x.alive]
        for old in tails[:-self.seq.max_tails]:
            old.alive = False

    def note_off(self, slot):
        v = self.voices[slot]
        if v is not None:
            v.key_off()
            self.voices[slot] = None

    # -- child tracks -----------------------------------------------------
    def open_track(self, n, addr):
        n &= 0xF
        if self.children[n] is not None:
            self.children[n].close()
        t = Track(self.seq, self, addr, n)
        self.children[n] = t

    def close(self):
        for c in self.children:
            if c is not None:
                c.close()
        self.children = [None] * 16
        for v in self.voices:
            if v is not None:
                v.key_off()
        self.voices = [None] * 8
        self.alive = False
        if self.handle:
            self.seq.handles.pop(self.handle, None)

    # -- one sequencer tick (FUN_81356d80) --------------------------------
    def step(self):
        if not self.alive:
            return
        self.raise_int(7)
        if self.timer[0]:
            self.timer[0] -= 1
            if self.timer[0] == 0:
                self.raise_int(6)
                if self.timer[2] == 0:
                    self.timer[0] = self.timer[1]
                else:
                    self.timer[2] -= 1
                    if self.timer[2]:
                        self.timer[0] = self.timer[1]
        guard = 0
        while True:
            self.dispatch_int()
            if self.wait == -1:
                if self.voices_idle(0):
                    self.wait = 0
                else:
                    break
            if self.wait > 0:
                self.wait -= 1
                if self.wait > 0:
                    break
            r = self.run()
            if r == "end":
                self.close()
                return
            if r == "wait":
                break
            guard += 1
            if guard > 64:
                break
        self.update_params()
        self.update_outputs()
        for c in list(self.children):
            if c is not None:
                c.step()
                if not c.alive:
                    self.children[c.index] = None

    def voices_idle(self, slot):
        v = self.voices[slot]
        return v is None or not v.alive

    def run(self):
        """Execute commands until a wait. Returns 'wait', 'end' or 'again'."""
        while True:
            op = self.u8()
            if op < 0x80:
                return self.do_note(op)
            hi = op & 0xF0
            if hi == 0x80 or op == 0xF9:
                n = 1
                rel = 0
                if op == 0xF9:
                    b = self.u8()
                    slot = self.reg_read(b & 7) & 0xFF
                    op = 0x88 + slot if b & 0x80 else 0x80 + slot
                low = op & 0xF
                if low == 8:
                    n = 2
                    low = 0
                elif low > 8:
                    low -= 8
                    rel = self.u8()
                    if rel > 100:
                        rel = (rel - 98) * 20
                if low == 0:
                    w = 0
                    for _ in range(n):
                        w = (w << 8) | self.u8()
                    self.wait = w
                    if w:
                        return "wait"
                    continue
                self.note_off(low)
                continue
            if hi == 0x90:
                self.do_param(op)
                continue
            if hi == 0xA0:
                self.do_regop(op)
                continue
            if hi == 0xB0:
                cmd = self.u8()
                if op & 8:
                    cmd = self.reg_read(cmd) & 0xFF
                types = list(ARGS.get(cmd, []))
                if not (op & 8) or (op & 7):
                    mask = self.u8()
                    for k in range((op & 7) + 1):
                        if mask & 0x80 and k < len(types):
                            types[k] = 3
                        mask = (mask << 1) & 0xFF
                r = self.command(cmd, types)
            else:
                r = self.command(op, ARGS.get(op, []))
            if r:
                return r

    def do_note(self, key):
        flags = self.u8()
        if flags & 0x80:
            key = self.reg_read(key) & 0xFF
        key = (key + self.transpose) & 0xFF
        vel = self.u8()
        if vel > 0x7F:
            vel = self.reg_read(vel - 0x80) & 0xFF
        tie = (flags >> 5) & 2
        slot = flags & 7
        length = None
        if slot == 0:
            gate = self.u8()
            if gate > 0x7F:
                gate = self.reg_read(gate - 0x80) & 0xFF
            n = (flags >> 3) & 3
            length = 0
            for _ in range(n):
                length = (length << 8) | self.u8()
            if n == 1 and length > 0x7F:
                length = self.reg_read(length - 0x80)
        else:
            if (flags >> 3) & 3:
                slot = self.reg_read(slot - 1) & 0xFF
            gate = 100
        gate_ticks = None
        if length is not None:
            gate_ticks = length * gate / 100.0 * (120.0 / max(1, self.tempo)) * self.seq.tempo_scale
        self.note_on(slot & 7, key, vel, gate_ticks)
        self.last_note = key
        if length is not None:
            self.wait = length if length else -1
            return "wait"
        return None

    def do_param(self, op):
        pid = self.u8()
        vt = op & 0xC
        if vt == 0:
            raw = self.reg_read(self.u8())
        elif vt == 4:
            raw = self.u8()
        elif vt == 8:
            raw = s8(self.u8()) << 8
        else:
            raw = s16(self.u16())
        tt = op & 3
        steps = None
        if tt == 1:
            steps = self.reg_read(self.u8())
        elif tt == 2:
            steps = self.u8()
        elif tt == 3:
            steps = self.u16()
        if pid < 18:
            self.set_param(pid, s16(raw) / 32768.0, steps if tt else 0)

    def do_regop(self, op):
        """FUN_81354180."""
        sub = op & 0xF
        mode, kind = op & 0xC, op & 3
        extra = 0
        if sub == 0xB:
            mode, kind = 0, 0xB
        if sub == 0xA:
            b = self.u8()
            mode, kind, extra = b & 0xC, 10, ((b >> 4) & 0xF) + 4
        if sub == 9:
            b = self.u8()
            mode, kind = b & 0xC, b & 0xF0
            if mode == 8:
                mode = 0x10
        dest = self.u8()
        base = 0
        if kind == 10:
            base = self.reg_read(self.u8())
        if mode == 0:
            val = self.reg_read(self.u8())
        elif mode == 4:
            val = self.u8()
        elif mode == 8:
            val = s16(self.u8() << 8)
        elif mode == 0xC:
            val = s16(self.u16())
        else:
            val = -1
        cur = s16(self.reg_read(dest))
        if kind == 0:
            res = val
        elif kind == 1:
            res = cur + (s8(val) if mode == 4 else val)
        elif kind == 2:
            self.reg_write(4, ((cur * val) >> 16) & 0xFFFF)
            self.reg_write(5, (cur * val) & 0xFFFF)
            return
        elif kind == 3:
            self.regs[3] = (cur - val) & 0xFFFF
            return
        elif kind == 0xB:
            res = cur - val
        elif kind == 10:
            res = self.table_read(base, val, extra)
        elif kind == 0x10:
            sh = s8(val) if mode == 4 else val
            res = (cur & 0xFFFF) << sh if sh >= 0 else (cur & 0xFFFF) >> -sh
        elif kind == 0x20:
            sh = s8(val) if mode == 4 else val
            res = cur << sh if sh >= 0 else cur >> -sh
        elif kind == 0x30:
            res = cur & val
        elif kind == 0x40:
            res = cur | val
        elif kind == 0x50:
            res = cur ^ val
        elif kind == 0x60:
            res = -cur
        elif kind == 0x90:
            r = self.seq.rand()
            res = r % (val & 0xFFFF) if val & 0xFFFF else r
        else:
            res = val
        self.reg_write(dest, res)

    def table_read(self, base, index, width):
        d = self.seq.data
        if width == 4:
            return d[base + index]
        if width == 5:
            return (d[base + 2 * index] << 8) | d[base + 2 * index + 1]
        if width == 6:
            o = base + 3 * index
            return (d[o] << 16) | (d[o + 1] << 8) | d[o + 2]
        o = base + 4 * index
        return struct.unpack_from(">I", d, o)[0]

    # -- commands (handlers at 0x813f6c44) --------------------------------
    def command(self, op, types):
        if op in (0xC4, 0xC8):
            return self.cmd_jump(op)
        args = [self.arg(t) for t in types]
        a = args[0] if args else 0
        b = args[1] if len(args) > 1 else 0
        if op == 0xC1:
            self.open_track(a, b)
        elif op == 0xC2:
            if self.parent is not None:
                self.parent.open_track(a, b)
        elif op == 0xC3:
            self.stack.append(self.pc)
            self.pc = a
        elif op == 0xC5:
            if self.stack:
                self.pc = self.stack.pop()
        elif op == 0xC6:
            if self.cond(a) and self.stack:
                self.pc = self.stack.pop()
        elif op == 0xC7:
            self.pc = a
        elif op == 0xC9:
            self.stack.append(self.pc)
            self.loop_counts.append(a)
        elif op == 0xCA:
            if not self.loop_counts:
                return "end"
            n = self.loop_counts[-1]
            if n:
                n -= 1
            if n == 0:
                self.loop_counts.pop()
                self.stack.pop()
            else:
                self.loop_counts[-1] = n
                self.pc = self.stack[-1]
        elif op == 0xCB:
            port = self.ports[a & 0xF]
            port[0] = 0
            self.reg_write(b, port[2])
        elif op == 0xCC:
            self.ports[a & 0xF][2] = b & 0xFFFF
            self.ports[a & 0xF][1] = 1
        elif op == 0xCD:
            self.reg_write(3, self.ports[a & 0xF][0])
        elif op == 0xCE:
            self.reg_write(3, self.ports[a & 0xF][1])
        elif op == 0xCF:
            self.wait = a
            if a:
                return "wait"
        elif op == 0xD0:
            self.handle = (a << 16) | b
        elif op == 0xD1:
            if self.parent is not None:
                self.seq.write_port(self.parent, a & 0xF, b)
        elif op == 0xD2:
            c = self.children[(a >> 4) & 0xF]
            if c is not None:
                self.seq.write_port(c, a & 0xF, b)
        elif op == 0xD4:
            self.last_note = (a + self.transpose) & 0xFF
        elif op == 0xD5:
            self.timebase_mode = a
        elif op == 0xD6:
            if a == 1:
                self.osc[0] = Osc(0, 1.0, TREMOLO_TABLE, TREMOLO_TABLE, 0.0, 1.0)
            elif a == 0:
                self.osc[1] = Osc(1, 0.8, VIBRATO_TABLE, VIBRATO_TABLE, 0.0, 1.0)
            elif a == 2:
                self.osc[1] = Osc(0, 1.0, TREMOLO_TABLE, TREMOLO_TABLE, 0.0, 1.0)
        elif op == 0xD7:
            pass   # custom envelope tables from sequence data (unused by the IPL sequence)
        elif op == 0xD8:
            att, dec, sus_t, sus_v, rel = (s16(x) for x in args)
            attack = [(0, att & 0xFFFF, 32767), (0, dec & 0xFFFF, 32767), (0, sus_t & 0xFFFF, sus_v), (14, 0, 0)]
            release = [(0, rel & 0xFFFF, 0), (15, 0, 0)]
            self.osc[0] = Osc(0, 1.0, attack, release, 1.0, 0.0)
        elif op == 0xD9:
            self.transpose_own = s8(a)
            self.transpose = (self.transpose_own + (self.parent.transpose if self.parent else 0))
        elif op == 0xDA:
            if a < 16 and self.children[a] is not None:
                self.children[a].close()
                self.children[a] = None
        elif op == 0xDF:
            self.int_enabled |= 1 << (a & 7)
            self.int_vector[a & 7] = b
        elif op == 0xE0:
            self.int_enabled &= ~(1 << (a & 7))
        elif op == 0xE1:
            self.in_int = 0
        elif op == 0xE2:
            self.in_int = 1
        elif op == 0xE3:
            if self.saved is not None:
                self.pc, self.wait = self.saved
                self.saved = None
            self.in_int = 0
            return "again"
        elif op == 0xE4:
            self.timer = [b, b, a]
        elif op == 0xE5:
            if self.handle:
                self.seq.handles[self.handle] = self
        elif op == 0xE6:
            if self.handle:
                self.seq.handles.pop(self.handle, None)
        elif op == 0xE7:
            self.reg_write(3, 0xFFFF)
        elif op in (0xE8, 0xE9):
            for i, v in enumerate(self.voices):
                if v is not None:
                    v.key_off()
                    self.voices[i] = None
        elif op == 0xEA:
            self.wait = a
            if a:
                return "wait"
        elif op == 0xEB:
            pass   # mixing ratios (aux sends) - folded into the fx parameter
        elif op == 0xF0:
            self.osc_mode[(a >> 4) & 1] = a & 0xF
        elif op == 0xFA:
            self.reg_write(3, self.seq.rand() & 0xFF)
        elif op == 0xFB:
            while self.u8() != 0:
                pass
        elif op == 0xFD:
            self.timebase = a
            if self.parent is None:
                self.seq.retime()
        elif op == 0xFE:
            self.tempo = a
            if self.parent is None:
                self.seq.retime()
        elif op == 0xFF:
            return "end"
        return None

    def cmd_jump(self, op):
        """C4 (call) / C8 (jump), FUN_813557a0."""
        flags = self.u8()
        if not flags & 0x80:
            target = self.u24()
        else:
            target = self.reg_read(self.u8()) & 0xFFFF
            if flags & 0x40:
                base = self.u24() if not flags & 0x20 else self.reg_read(self.u8()) & 0xFFFF
                o = base + target * 3
                d = self.seq.data
                target = (d[o] << 16) | (d[o + 1] << 8) | d[o + 2]
        if self.cond(flags & 0xF):
            if op == 0xC4:
                self.stack.append(self.pc)
            self.pc = target
        return None


class Sequencer:
    """The whole IPL sound system: sequence + bank + mixer."""

    def __init__(self, rom, reverb=True):
        self.data = rom[SEQ_ROM:SEQ_ROM + SEQ_SIZE]
        self.bank = Bank(rom)
        self.handles = {}
        self.voices = []
        self.ticks_per_sec = 120 * 0x30 / 60.0
        self.tick_acc = 0.0
        self.tempo_scale = 1.0
        self._rand = 0x1234
        self.root = Track(self, None, 0)
        self.root.handle = 0x20001
        self.handles[0x20001] = self.root
        self.master = 0.9
        self.max_tails = 2
        self.reverb = Reverb(OUT_RATE) if reverb else None
        self.retime()

    def rand(self):
        self._rand = (self._rand * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
        return (self._rand >> 16) & 0xFFFF

    def retime(self):
        r = self.root
        self.ticks_per_sec = r.tempo * r.timebase / 60.0

    # -- the app side (FUN_81354920) ---------------------------------------
    def write_port(self, track, port, value):
        track.ports[port][2] = value & 0xFFFF
        track.ports[port][0] = 1
        if port == 0:
            track.raise_int(3)
        elif port == 1:
            track.raise_int(4)

    def port(self, handle, port, value):
        t = self.handles.get(handle)
        if t is not None:
            self.write_port(t, port, value)

    def play_se(self, se):
        """FUN_8135aa80."""
        if se < 3:
            self.port(0x21002, 0, se)
        elif se < 0x17:
            self.port(0x20002, 0, se)
        elif se > 0x17:
            self.port(0x20001, 0, se)

    def menu_music(self, on=True):
        """FUN_8135ac20(0): start the menu theme; writing port 1 fades it out."""
        self.port(0x21001, 0 if on else 1, 1)

    def whoosh(self, strength, kind=0):
        """FUN_8135a8e0: intro-music ducking + the spin whoosh."""
        f = min(1.0, max(0.0, strength))
        g = math.sqrt(f)
        self.port(0x21002, 1, int(32767 - 32767 * g) if kind != 1 else 0)
        if f > 0.0:
            t = self.handles.get(0x21003)
            if t is not None:
                steps = min(100, int(10.0 / f))
                lo = int(2048.0 * (0.1 + f)) if kind == 0 else int(2048.0 * 3.0 * (0.15 + f))
                hi = int(2048.0 * 1.6 * (0.1 + f)) if kind == 0 else int(2048.0 * 3.6 * (0.15 + f))
                t.ports[2][2], t.ports[3][2] = lo & 0xFFFF, hi & 0xFFFF
                t.ports[0][2] = steps
                self.write_port(t, 0, steps)

    # -- rendering ------------------------------------------------------------
    def render(self, nsamples):
        """Return (nsamples, 2) float32 stereo at OUT_RATE."""
        out = np.zeros((nsamples, 2), np.float32)
        aux = np.zeros(nsamples, np.float32)
        pos = 0
        while pos < nsamples:
            n = min(FRAME, nsamples - pos)
            self.tick_acc += self.ticks_per_sec / FRAMES_PER_SEC * (n / FRAME)
            while self.tick_acc >= 1.0:
                self.tick_acc -= 1.0
                self.root.step()
            alive = []
            for v in self.voices:
                s = v.render(n)
                if s is None:
                    continue
                alive.append(v)
                pan = min(1.0, max(0.0, v.track.out_pan))
                l, r = math.cos(pan * math.pi / 2), math.sin(pan * math.pi / 2)
                out[pos:pos + n, 0] += s * l
                out[pos:pos + n, 1] += s * r
                fx = v.track.out_fx
                if fx > 0:
                    aux[pos:pos + n] += s * fx
            self.voices = [v for v in alive if v.alive]
            pos += n
        if self.reverb is not None:
            wet = self.reverb.process(aux)
            out += wet
        return np.clip(out * self.master, -1.0, 1.0)


class _Delay:
    """Ring buffer that reads/writes whole blocks (block length <= delay length)."""

    def __init__(self, n):
        self.buf = np.zeros(n, np.float32)
        self.i = 0

    def read(self, n):
        b, i = self.buf, self.i
        end = i + n
        if end <= len(b):
            return b[i:end].copy()
        return np.concatenate([b[i:], b[:end - len(b)]])

    def write(self, x):
        b, i, n = self.buf, self.i, len(x)
        end = i + n
        if end <= len(b):
            b[i:end] = x
        else:
            k = len(b) - i
            b[i:] = x[:k]
            b[:n - k] = x[k:]
        self.i = end % len(b)


class Reverb:
    """Freeverb-style hall standing in for the IPL's AX aux reverb (param 2 = send)."""

    def __init__(self, rate, size=0.84, damp=0.25, wet=0.6):
        sc = rate / 44100.0
        self.combs = [(_Delay(int(n * sc)), _Delay(int((n + 23) * sc))) for n in (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)]
        self.allp = [(_Delay(int(n * sc)), _Delay(int((n + 23) * sc))) for n in (556, 441, 341, 225)]
        self.fb, self.damp, self.wet = size, damp, wet
        self.lp = np.zeros((len(self.combs), 2), np.float32)

    def process(self, x):
        n = len(x)
        out = np.zeros((n, 2), np.float32)
        pos = 0
        while pos < n:
            m = min(160, n - pos)
            s = x[pos:pos + m] * 0.015
            for ch in range(2):
                acc = np.zeros(m, np.float32)
                for k, pair in enumerate(self.combs):
                    d = pair[ch]
                    y = d.read(m)
                    # one-pole damping applied block-wise (cheap approximation)
                    lp = self.lp[k, ch]
                    filt = y * (1 - self.damp) + lp * self.damp
                    self.lp[k, ch] = filt[-1]
                    d.write(s + filt * self.fb)
                    acc += y
                for pair in self.allp:
                    d = pair[ch]
                    b = d.read(m)
                    d.write(acc + b * 0.5)
                    acc = b - acc
                out[pos:pos + m, ch] = acc
            pos += m
        return out * self.wet
