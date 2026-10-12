"""Render IPL sound through the BMS port to WAV files for checking.

usage: python tools/render_audio.py
  -> audio/render_menu_music.wav, audio/render_jingle_<n>.wav, audio/render_se_<id>.wav
"""
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ipl.bms import OUT_RATE, Sequencer  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def save(path, stereo):
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(OUT_RATE))
        w.writeframes(pcm.tobytes())


def run(seq, seconds, events=()):
    """events: list of (time_s, callable)."""
    chunks = []
    total = int(seconds * OUT_RATE)
    block = 1600
    t = 0
    ev = sorted(events, key=lambda e: e[0])
    while t < total:
        while ev and ev[0][0] * OUT_RATE <= t:
            ev.pop(0)[1]()
        chunks.append(seq.render(block))
        t += block
    return np.concatenate(chunks)


def main():
    from ipl import rom as _rom
    rom = _rom.load()
    out = os.path.join(ROOT, "audio")
    # warm-up so all tracks register their handles
    for name, secs, events in [
        ("menu_music", 40.0, [(0.2, lambda s: s.menu_music(True))]),
        ("jingle_0", 7.0, [(0.2, lambda s: s.play_se(0))]),
        ("jingle_1", 7.0, [(0.2, lambda s: s.play_se(1))]),
        ("jingle_2", 7.0, [(0.2, lambda s: s.play_se(2))]),
    ]:
        seq = Sequencer(rom)
        t0 = time.time()
        audio = run(seq, secs, [(t, (lambda f=f, s=seq: f(s))) for t, f in events])
        save(os.path.join(out, f"render_{name}.wav"), audio)
        peak = float(np.abs(audio).max())
        print(f"{name}: {secs}s rendered in {time.time() - t0:.1f}s, peak {peak:.3f}, handles {sorted(hex(h) for h in seq.handles)}")
    seq = Sequencer(rom)
    run(seq, 0.2)
    for se in range(3, 0x17):
        s2 = Sequencer(rom)
        run(s2, 0.1)
        a = run(s2, 2.5, [(0.0, lambda s=s2, se=se: s.play_se(se))])
        save(os.path.join(out, f"render_se_{se:02x}.wav"), a)
        print(f"se {se:02x}: peak {float(np.abs(a).max()):.3f}")


if __name__ == "__main__":
    main()
