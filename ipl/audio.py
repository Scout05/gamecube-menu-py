"""Real-time audio for the IPL re-creation.

All sound comes from the IPL's own JAudio sequence (iplrom.com) played by the
BMS port in ipl/bms.py, exactly like the console: the game code only writes
"ports" (sound-effect ids, music on/off, whoosh strength) and the sequence
decides what to play.
"""
import os
import threading

import numpy as np

try:
    import pygame
    from pygame._sdl2 import audio as sdl_audio
except ImportError:  # headless tools
    pygame = None
    sdl_audio = None

from .bms import OUT_RATE, Sequencer

ROOT = os.path.join(os.path.dirname(__file__), "..")


class Audio:
    def __init__(self, enabled=True, rom=None):
        self.log = []
        self.mono = False
        self.enabled = enabled and sdl_audio is not None
        self.lock = threading.Lock()
        self.pending = []
        self.seq = None
        self.device = None
        if not self.enabled:
            return
        if rom is None:
            from . import rom as _rom
            rom = _rom.load()
        self.seq = Sequencer(rom)
        self.seq.render(1600)   # let the sequence register its tracks (handles)
        try:
            self.device = sdl_audio.AudioDevice(
                devicename=None, iscapture=False, frequency=32000,
                audioformat=sdl_audio.AUDIO_S16, numchannels=2, chunksize=640,
                allowed_changes=0, callback=self._callback)
            self.device.pause(0)
        except Exception as exc:  # no audio device: keep running silently
            print("audio disabled:", exc)
            self.enabled = False

    def _callback(self, device, stream):
        n = len(stream) // 4
        with self.lock:
            cmds, self.pending = self.pending, []
        for fn in cmds:
            fn()
        out = self.seq.render(n)
        if self.mono:
            m = out.mean(axis=1, keepdims=True)
            out = np.repeat(m, 2, axis=1)
        pcm = (out * 32767.0).astype("<i2")
        stream[:] = pcm.tobytes()

    def _send(self, fn):
        if self.enabled:
            with self.lock:
                self.pending.append(fn)

    # -- the IPL's sound API ----------------------------------------------
    def play_se(self, se_id, volume=1.0):
        """FUN_8135aa80."""
        self.log.append(("se", se_id))
        self._send(lambda: self.seq.play_se(se_id))

    def whoosh(self, strength, kind=0):
        """FUN_8135a8e0."""
        self.log.append(("whoosh", round(strength, 2)))
        self._send(lambda: self.seq.whoosh(strength, kind))

    def menu_music(self, on=True):
        """FUN_8135ac20."""
        self.log.append(("music", on))
        self._send(lambda: self.seq.menu_music(on))

    def close(self):
        if self.device is not None:
            self.device.pause(1)
            self.device.close()
