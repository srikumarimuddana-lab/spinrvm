#!/usr/bin/env python3
"""Generate assets/sounds/ride_offer.caf — the iOS ride-offer notification sound.

iOS notification sounds must be Linear PCM (or IMA4/µ-law/a-law) inside an
.aiff/.wav/.caf container, max 30 s — MP3 is NOT supported, so the bundled
ride_offer.mp3 (used on Android and for the in-app loop) cannot be reused
directly. This script synthesizes an ascending two-chime alert, repeated to
fill the 15 s offer window (iOS never loops a push sound), and
writes it as a CAF (PCM s16 big-endian, mono, 44.1 kHz) using only the
Python standard library, so it runs anywhere.

To use a custom branded sound instead (e.g. converted from the same MP3),
overwrite the output with ffmpeg and rebuild the app:

    ffmpeg -i ride_offer.mp3 -ar 44100 -ac 1 -c:a pcm_s16be ride_offer.caf

Keep the filename `ride_offer.caf`: it is referenced by
  - services/notifeeService.ts        (ios.sound)
  - backend/features.py APNs payload  (aps.sound for dispatch pushes)
  - plugins/withRideOfferSound.js     (copies it into the Xcode project)
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

SAMPLE_RATE = 44100
OUT_PATH = Path(__file__).resolve().parent.parent / "assets" / "sounds" / "ride_offer.caf"


FADE_OUT_SECONDS = 0.05  # the 15 s cut lands mid-note; ramp to zero so it ends without a click
RING_SECONDS = 15.0  # the offer window: iOS plays a push sound once, never loops it
PEAK = 0.98          # normalise to just under full scale — the level ceiling a file can reach


def synth_chime() -> list[float]:
    """Two repetitions of an ascending A5→C#6→E6 arpeggio with decay."""
    notes = [880.00, 1108.73, 1318.51]  # A5, C#6, E6 (A-major triad)
    note_len = 0.28
    gap = 0.04
    motif_pause = 0.25
    samples: list[float] = []

    for rep in range(2):
        for freq in notes:
            n = int(SAMPLE_RATE * note_len)
            for i in range(n):
                t = i / SAMPLE_RATE
                # Exponential decay envelope with a short linear attack to
                # avoid a click at note onset. Decay constant 3.5 (was 6.0):
                # the note holds its level longer, so it reads louder at the
                # same peak.
                attack = min(1.0, i / (SAMPLE_RATE * 0.008))
                env = attack * math.exp(-3.5 * t / note_len)
                v = math.sin(2 * math.pi * freq * t)
                v += 0.35 * math.sin(2 * math.pi * freq * 2 * t)  # 2nd harmonic
                samples.append(env * v)
            samples.extend([0.0] * int(SAMPLE_RATE * gap))
        samples.extend([0.0] * int(SAMPLE_RATE * motif_pause))

    return samples


def synth_ring() -> list[float]:
    """The chime repeated to exactly RING_SECONDS, peak-normalised.

    iOS plays a remote-notification sound once and never loops it (Android's
    channel `loopSound` has no iOS equivalent), so the repetition has to be
    baked into the file. iOS caps notification sounds at 30 s.
    """
    chime = synth_chime()
    total = int(SAMPLE_RATE * RING_SECONDS)
    samples = [chime[i % len(chime)] for i in range(total)]
    peak = max(abs(s) for s in samples)
    samples = [s * PEAK / peak for s in samples]
    fade = int(SAMPLE_RATE * FADE_OUT_SECONDS)
    for i in range(fade):
        samples[total - fade + i] *= 1.0 - (i + 1) / fade  # ends at exactly 0.0
    return samples


def write_caf(samples: list[float], path: Path) -> None:
    pcm = b"".join(
        struct.pack(">h", max(-32768, min(32767, int(s * 32767)))) for s in samples
    )

    header = b"caff" + struct.pack(">HH", 1, 0)  # magic, version 1, flags 0

    # 'desc' chunk: format description (32 bytes).
    #
    # Format flags = 0 is CORRECT here and must stay 0. The CAF container's
    # mFormatFlags use the CAF flag enum, NOT the CoreAudio
    # AudioStreamBasicDescription (ASBD) flags. Per Apple's "Apple Core Audio
    # Format Specification 1.0", the only two LPCM flags defined for CAF are:
    #     kCAFLinearPCMFormatFlagIsFloat        = 1 << 0
    #     kCAFLinearPCMFormatFlagIsLittleEndian = 1 << 1
    # Integer PCM is signed by definition and big-endian is the default
    # (absence of the LittleEndian flag), so 16-bit signed big-endian PCM — the
    # ">h" packing above — is flags = 0. Do NOT apply ASBD values like
    # kAudioFormatFlagIsSignedInteger(4)/IsPacked(8)/IsBigEndian(2): setting
    # bit 1 here would declare little-endian and mis-describe these big-endian
    # bytes, which is what makes a CAF play as garbage or get rejected.
    desc = (
        b"desc"
        + struct.pack(">q", 32)
        + struct.pack(">d", float(SAMPLE_RATE))  # sample rate
        + b"lpcm"                                 # format id
        + struct.pack(">I", 0)                    # format flags (CAF: 0 = signed big-endian int)
        + struct.pack(">I", 2)                    # bytes per packet (mono s16)
        + struct.pack(">I", 1)                    # frames per packet
        + struct.pack(">I", 1)                    # channels per frame
        + struct.pack(">I", 16)                   # bits per channel
    )

    # 'data' chunk: 4-byte edit count + PCM payload.
    data = b"data" + struct.pack(">q", 4 + len(pcm)) + struct.pack(">I", 0) + pcm

    path.write_bytes(header + desc + data)


def main() -> None:
    samples = synth_ring()
    write_caf(samples, OUT_PATH)
    duration = len(samples) / SAMPLE_RATE
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes, {duration:.2f}s)")


if __name__ == "__main__":
    main()
