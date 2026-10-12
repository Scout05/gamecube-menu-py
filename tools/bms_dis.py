"""Disassembler for the IPL's JAudio BMS sequence (iplrom.com).

Opcode set taken from the IPL's own sequencer (FUN_81356d80 + command table
at RAM 0x813f6b44 / handlers at 0x813f6c44).

usage: python bms_dis.py ipl_plain.bin > iplrom.txt
"""
import os
import struct
import sys

SEQ_ROM = 0x1AAEA0
SEQ_SIZE = 0xC40

# op -> (name, [arg types]) ; arg types: 0=u8 1=u16 2=u24 3=register(u8)
CMDS = {
    0xC1: ("open_track", [0, 2]), 0xC2: ("open_sibling", [0, 2]), 0xC3: ("call", [2]),
    0xC4: ("call_cond", None), 0xC5: ("return", []), 0xC6: ("return_cond", [0]),
    0xC7: ("jump?", [2]), 0xC8: ("jump_cond", None), 0xC9: ("loop_start", [1]),
    0xCA: ("loop_end", []), 0xCB: ("read_port", [0, 0]), 0xCC: ("write_port", [0, 3]),
    0xCD: ("port_flag0", [0]), 0xCE: ("port_flag1", [0]), 0xCF: ("wait_reg", [3]),
    0xD0: ("set_d0", [1, 1]), 0xD1: ("write_parent_port", [0, 3]), 0xD2: ("write_child_port", [0, 3]),
    0xD4: ("set_last_note", [0]), 0xD5: ("set_timebase_mode", [0]), 0xD6: ("set_bank?", [0]),
    0xD7: ("set_prog?", [0, 2]), 0xD8: ("d8", [1, 1, 1, 1, 1]), 0xD9: ("transpose", [0]),
    0xDA: ("close_track", [0]), 0xDB: ("db", [0]), 0xDC: ("update_flags", [1]),
    0xDD: ("set_slot", [0, 1]), 0xDE: ("set_flags_39d", [0]), 0xDF: ("int_enable", [0, 2]),
    0xE0: ("int_disable", [0]), 0xE1: ("e1", []), 0xE2: ("e2", []), 0xE3: ("int_return", []),
    0xE4: ("timer_int", [0, 1]), 0xE5: ("e5", []), 0xE6: ("e6", []), 0xE7: ("callback", [1]),
    0xE8: ("e8", []), 0xE9: ("e9", []), 0xEA: ("wait_long", [2]), 0xEB: ("set_env", [0, 0, 0, 0, 0]),
    0xEC: ("set_iir", [1, 1, 1, 1]), 0xED: ("ed", [2]), 0xEE: ("ee", [2]), 0xEF: ("ef", [0, 0, 0]),
    0xF0: ("osc_mode", [0]), 0xF1: ("iir_preset", [0]), 0xF2: ("f2", [0, 2, 0]),
    0xFA: ("fa", [1]), 0xFB: ("printf", None), 0xFC: ("nop", []),
    0xFD: ("timebase", [1]), 0xFE: ("tempo", [1]), 0xFF: ("end", []),
}
TABLE_ENTRIES = 0x17   # SE ids 0..0x16 dispatch through the table at 0x444


def load_arg_table(mem1_path):
    m = open(mem1_path, "rb").read()
    out = {}
    for i in range(64):
        v = struct.unpack_from(">I", m, 0x13F6B44 + 4 * i)[0]
        n, t = v >> 16, v & 0xFFFF
        out[0xC0 + i] = [(t >> (2 * k)) & 3 for k in range(n)]
    return out


class Reader:
    def __init__(self, data, pc):
        self.d, self.pc = data, pc

    def u8(self):
        v = self.d[self.pc]
        self.pc += 1
        return v

    def u16(self):
        v = struct.unpack_from(">H", self.d, self.pc)[0]
        self.pc += 2
        return v

    def u24(self):
        v = (self.d[self.pc] << 16) | (self.d[self.pc + 1] << 8) | self.d[self.pc + 2]
        self.pc += 3
        return v

    def arg(self, t):
        return [self.u8, self.u16, self.u24, lambda: ("r", self.u8())][t]()


def disasm(data, start, arg_types, seen, out, label=""):
    """Linear sweep from start until end/return/jump; collects branch targets."""
    r = Reader(data, start)
    targets = []
    out.append(f"\n; ---- {label} @ {start:04x}")
    while r.pc < len(data):
        pc = r.pc
        if pc in seen:
            out.append(f"{pc:04x}  ; (continues at already-listed code)")
            break
        seen.add(pc)
        op = r.u8()
        if op < 0x80:
            flags = r.u8()
            note = op if not flags & 0x80 else f"reg{op}"
            vel = r.u8()
            voice = flags & 7
            extra = ""
            if voice == 0:
                gate = r.u8()
                n = (flags >> 3) & 3
                length = 0
                for _ in range(n):
                    length = (length << 8) | r.u8()
                extra = f" gate={gate} len={length}"
            out.append(f"{pc:04x}  note    key={note} voice={voice} vel={vel} flags={flags:02x}{extra}")
            continue
        if op == 0x80:
            out.append(f"{pc:04x}  wait    {r.u8()}")
            continue
        if op == 0x88:
            out.append(f"{pc:04x}  wait    {r.u16()}")
            continue
        if 0x81 <= op <= 0x87:
            out.append(f"{pc:04x}  noteoff voice={op & 7}")
            continue
        if 0x89 <= op <= 0x8F:
            out.append(f"{pc:04x}  noteoff voice={op & 7} release={r.u8()}")
            continue
        if op == 0xF9:
            out.append(f"{pc:04x}  noteoff_reg {r.u8()}")
            continue
        if 0x90 <= op <= 0x9F:
            param = r.u8()
            vt = op & 0xC
            val = {0: lambda: ("r", r.u8()), 4: r.u8, 8: lambda: struct.unpack("b", bytes([r.u8()]))[0] << 8,
                   0xC: lambda: struct.unpack(">h", struct.pack(">H", r.u16()))[0]}[vt]()
            tt = op & 3
            time = {0: lambda: None, 1: lambda: ("r", r.u8()), 2: r.u8, 3: r.u16}[tt]()
            out.append(f"{pc:04x}  param   id={param} value={val} time={time}")
            continue
        if 0xA0 <= op <= 0xAF:
            sub = op & 0xF
            first = None
            if sub in (9, 10):
                first = r.u8()
            reg = r.u8()
            rest = []
            mode = (op & 0xC) if sub not in (9, 10, 0xB) else ((first or 0) & 0xC)
            if sub == 0xB:
                mode = 0
            if mode == 0:
                rest.append(("r", r.u8()))
            elif mode == 4:
                rest.append(r.u8())
            elif mode == 8:
                rest.append(r.u8() << 8)
            elif mode == 0xC:
                rest.append(r.u16())
            out.append(f"{pc:04x}  regop   {op:02x} first={first} reg={reg} {rest}")
            continue
        if 0xB0 <= op <= 0xBF:
            cmd = r.u8()
            out.append(f"{pc:04x}  ext     {op:02x} cmd={cmd:02x} (register-arg form)")
            # register-typed args: one u8 per arg
            types = arg_types.get(cmd, [])
            mask = r.u8() if (op & 7) or not (op & 8) else 0
            for _ in types:
                r.u8()
            continue
        if op in (0xC4, 0xC8):
            flags = r.u8()
            if not flags & 0x80:
                tgt = r.u24()
            else:
                reg = r.u8()
                tgt = ("reg", reg)
                if flags & 0x40:
                    tgt = ("table", reg, r.u8() if flags & 0x20 else r.u24())
            name = "call_cond" if op == 0xC4 else "jump_cond"
            out.append(f"{pc:04x}  {name} cond={flags & 0xF} target={tgt if not isinstance(tgt, int) else f'{tgt:04x}'}")
            if isinstance(tgt, int):
                targets.append((tgt, name))
            elif tgt[0] == "table":
                base = tgt[2]
                for k in range(TABLE_ENTRIES):
                    t = (data[base + 3 * k] << 16) | (data[base + 3 * k + 1] << 8) | data[base + 3 * k + 2]
                    out.append(f"      ; table[{k}] -> {t:04x}")
                    targets.append((t, f"table entry {k}"))
            if op == 0xC8 and (flags & 0xF) == 0:
                break
            continue
        if op == 0xFB:
            s = []
            while True:
                c = r.u8()
                if c == 0:
                    break
                s.append(chr(c))
            out.append(f"{pc:04x}  printf  {''.join(s)!r}")
            continue
        name, _ = CMDS.get(op, (f"op_{op:02x}", None))
        types = arg_types.get(op, [])
        args = [r.arg(t) for t in types]
        txt = " ".join(f"{a:x}" if isinstance(a, int) else str(a) for a in args)
        out.append(f"{pc:04x}  {name:8s} {txt}")
        if op in (0xC1, 0xC2):
            targets.append((args[1], f"track {args[0]:x}"))
        if op == 0xDF:
            targets.append((args[1], f"interrupt {args[0]}"))
        if op == 0xC3:
            targets.append((args[0], "call"))
        if op in (0xFF, 0xC5):
            break
    for t, why in targets:
        if t not in seen and t < len(data):
            disasm(data, t, arg_types, seen, out, why)


def main(rom_path, mem1_path):
    rom = open(rom_path, "rb").read()
    data = rom[SEQ_ROM:SEQ_ROM + SEQ_SIZE]
    arg_types = load_arg_table(mem1_path)
    out = []
    disasm(data, 0, arg_types, set(), out, "root")
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else
         os.environ.get("GCIPL_MEM1", os.path.expanduser("~/.local/share/dolphin-emu/Dump/mem1.raw")))
