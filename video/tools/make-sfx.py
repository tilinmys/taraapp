"""Synthesise the film's soft sound effects into assets/sfx/ (48 kHz mono, deterministic).

No music: just air, taps, whooshes, the pin, a pulse on the danger sign and a resolve.
Run from the video folder: python tools/make-sfx.py
"""
import os
import wave

import numpy as np

SR = 48000
OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "sfx")
rng = np.random.default_rng(7)


def t(sec):
    return np.arange(int(SR * sec)) / SR


def lowpass(x, cutoff):
    """One-pole lowpass; cutoff may be a per-sample array (a sweep)."""
    cutoff = np.broadcast_to(cutoff, x.shape)
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    prev = 0.0
    for i in range(len(x)):
        prev = (1 - a[i]) * x[i] + a[i] * prev
        y[i] = prev
    return y


def fade(x, a=0.005, r=0.02):
    n_a, n_r = int(SR * a), int(SR * r)
    x[:n_a] *= np.linspace(0, 1, n_a)
    x[-n_r:] *= np.linspace(1, 0, n_r)
    return x


def save(name, x, peak_db):
    x = x / (np.max(np.abs(x)) or 1) * 10 ** (peak_db / 20)
    with wave.open(os.path.join(OUT, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def whoosh(sec, lo, hi):
    n = t(sec)
    env = np.sin(np.pi * (n / sec)) ** 2  # swell then release
    sweep = lo + (hi - lo) * np.sin(np.pi * n / sec)  # filter opens and closes
    noise = rng.standard_normal(len(n))
    band = lowpass(noise, sweep) - lowpass(noise, sweep * 0.25)
    return fade(band * env)


os.makedirs(OUT, exist_ok=True)

# air: a quiet breath of wind under the globe
n = t(8.0)
air = lowpass(rng.standard_normal(len(n)), 700.0) * (0.75 + 0.25 * np.sin(2 * np.pi * 0.18 * n))
save("air.wav", fade(air, 1.2, 1.6), -24)

# whooshes: the camera dive, the smaller moves, the whip
save("whoosh-dive.wav", whoosh(1.5, 300, 3200), -12)
save("whoosh-soft.wav", whoosh(0.6, 400, 2600), -16)
save("whoosh-whip.wav", whoosh(0.35, 600, 5000), -13)

# tap: a short soft tick, like a fingertip on glass
n = t(0.08)
tap = np.sin(2 * np.pi * 1900 * n) * np.exp(-n * 90) + 0.3 * rng.standard_normal(len(n)) * np.exp(-n * 250)
save("tap.wav", fade(tap, 0.0005, 0.01), -14)

# pin: a pitch-dropping pop with a small thump as it lands
n = t(0.3)
pitch = 300 + 600 * np.exp(-n * 45)
pop = np.sin(2 * np.pi * np.cumsum(pitch) / SR) * np.exp(-n * 22) + 0.6 * np.sin(2 * np.pi * 90 * n) * np.exp(-n * 18)
save("pin-pop.wav", fade(pop, 0.001, 0.05), -10)

# shimmer: the logo building, a few bright partials landing one after another
n = t(1.4)
shim = np.zeros(len(n))
for k, f in enumerate([2093, 2637, 3136, 4186]):
    d = n - 0.1 * k
    on = d >= 0
    shim[on] += np.sin(2 * np.pi * f * d[on]) * np.exp(-d[on] * 3.2) * (1 - np.exp(-d[on] * 200))
save("shimmer.wav", fade(shim, 0.005, 0.2), -20)

# pulse: two low heartbeats under the danger sign
n = t(1.0)
pulse = np.zeros(len(n))
for start in (0.0, 0.32):
    d = n - start
    on = d >= 0
    pulse[on] += np.sin(2 * np.pi * 62 * d[on]) * np.exp(-d[on] * 9) * (1 - np.exp(-d[on] * 300))
save("pulse.wav", fade(pulse, 0.002, 0.1), -9)

# resolve: a soft major chord for the end card
n = t(3.0)
chord = sum(np.sin(2 * np.pi * f * n) for f in (261.6, 392.0, 523.3, 659.3))
chord *= (1 - np.exp(-n * 6)) * np.exp(-n * 0.9)
save("resolve.wav", fade(chord, 0.01, 0.4), -16)

print("wrote", sorted(os.listdir(OUT)))
