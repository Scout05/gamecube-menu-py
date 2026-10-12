"""Extract the IPL's JAudio waves (ipl_0.aw) to WAV files.

Reads the descrambled IPL ROM, parses the WSYS wave table (Banks/ipl.ws) and
decodes every wave from AFC ADPCM (9-byte frames, 16 samples each).
Waves that are exact-length pairs are stereo L/R and get merged as well.

usage: python extract_audio.py ipl_plain.bin out_dir
"""
import os
import struct
import sys
import wave

WSYS_ROM = 0xF8720   # Banks/ipl.ws
AW_ROM = 0xF8DA0     # ipl_0.aw  (RAM 0x813F8580)

AFC_COEFS = [
    (0, 0), (2048, 0), (0, 2048), (1024, 1024),
    (4096, -2048), (3584, -1536), (3072, -1024), (4608, -2560),
    (4200, -2248), (4800, -2300), (5120, -3072), (2048, -2048),
    (1024, -1024), (-1024, 1024), (-1024, 0), (-2048, 0),
]


def parse_wsys(rom):
    winf = struct.unpack_from(">I", rom, WSYS_ROM + 0x10)[0]
    group = WSYS_ROM + struct.unpack_from(">I", rom, WSYS_ROM + winf + 8)[0]
    aw_name = rom[group:group + 0x70].split(b"\0")[0].decode()
    count = struct.unpack_from(">I", rom, group + 0x70)[0]
    waves = []
    for i in range(count):
        e = WSYS_ROM + struct.unpack_from(">I", rom, group + 0x74 + 4 * i)[0]
        fmt, key = rom[e + 1], rom[e + 2]
        rate = struct.unpack_from(">f", rom, e + 4)[0]
        off, size, loop, lstart, lend, nsamp = struct.unpack_from(">6I", rom, e + 8)
        waves.append(dict(index=i, format=fmt, base_key=key, rate=int(rate),
                          offset=off, size=size, loop=loop == 0xFFFFFFFF,
                          loop_start=lstart, loop_end=lend, samples=nsamp))
    return aw_name, waves


def decode_afc(data, nsamples):
    out = []
    h1 = h2 = 0
    for f in range(0, len(data) - 8, 9):
        hdr = data[f]
        scale = 1 << (hdr >> 4)
        c1, c2 = AFC_COEFS[hdr & 0xF]
        for b in data[f + 1:f + 9]:
            for n in (b >> 4, b & 0xF):
                if n >= 8:
                    n -= 16
                s = ((n * scale) << 11) + c1 * h1 + c2 * h2
                s >>= 11
                s = -32768 if s < -32768 else 32767 if s > 32767 else s
                out.append(s)
                h2, h1 = h1, s
        if len(out) >= nsamples:
            break
    return out[:nsamples]


def write_wav(path, rate, channels):
    n = min(len(c) for c in channels)
    frames = bytearray()
    for i in range(n):
        for c in channels:
            frames += struct.pack("<h", c[i])
    with wave.open(path, "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def main(rom_path, out_dir):
    rom = open(rom_path, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    aw_name, waves = parse_wsys(rom)
    print(f"{aw_name}: {len(waves)} waves")
    pcm = {}
    for w in waves:
        if w["format"] != 0:
            print(f"  wave {w['index']}: unsupported format {w['format']}, skipped")
            continue
        raw = rom[AW_ROM + w["offset"]:AW_ROM + w["offset"] + w["size"]]
        pcm[w["index"]] = decode_afc(raw, w["samples"])
        name = f"wave{w['index']:02d}.wav"
        write_wav(os.path.join(out_dir, name), w["rate"], [pcm[w["index"]]])
        loop = f" loop {w['loop_start']}-{w['loop_end']}" if w["loop"] else ""
        print(f"  {name}  {w['rate']} Hz  {w['samples'] / w['rate']:.2f}s  key {w['base_key']}{loop}")

    # consecutive waves with identical size/length are L/R halves of one stereo sound
    i = 0
    while i < len(waves) - 1:
        a, b = waves[i], waves[i + 1]
        if a["size"] == b["size"] and a["samples"] == b["samples"] and a["index"] in pcm:
            name = f"stereo_{a['index']:02d}_{b['index']:02d}.wav"
            write_wav(os.path.join(out_dir, name), a["rate"], [pcm[a["index"]], pcm[b["index"]]])
            print(f"  {name}  (stereo)")
            i += 2
        else:
            i += 1


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
