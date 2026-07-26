"""The ``modular-bleep`` generator — eurorack-ish bleeps parameterized by activity.

Two interlocking pentatonic bleep sequencers (one a beat behind the other), a
slow low-pass filter sweep, a sub pulse and a click — the sound of a patched
modular idling. Calm bands are slow and sparse with a nearly-closed filter;
busier bands run faster, denser and brighter. Uses the shared brainwave-band
traits (``band_traits``).

Deterministic and golden-file testable; only @strudel/web 1.0.3 primitives
(no ``setcps``/``fadeIn``/``unison``).
"""

from __future__ import annotations

from ...core.models import ActivitySignal
from ..brainwave import band_traits, clamp01

# Pentatonic degree sequences, sparse → busy, keyed by band density (1,2,3,4,6).
_SEQS = {
    1: "0 ~ ~ ~ 7 ~ ~ ~",
    2: "0 ~ 3 ~ 7 ~ 5 ~",
    3: "0 3 ~ 7 5 ~ 10 ~",
    4: "0 3 7 5 10 7 3 5",
    6: "0 3 7 10 12 10 7 [5 3] 0 7 [3 5] 10 12 7 [10 5] 3",
}
# Counter-melody offsets (the second sequencer), one per selected variant.
_COUNTERS = ("7 ~ 5 ~", "~ 10 ~ 7", "5 ~ ~ 3", "~ 7 ~ 12")
_SUBS = ("<c1 g0>", "<a0 e1>", "<f0 c1>", "<d1 a0>")


def _seed(signal: ActivitySignal) -> int:
    theme_bits = sum(ord(c) for c in "".join(signal.themes)[:12])
    return signal.volume * 7 + signal.participant_count * 13 + int(signal.volatility * 100) + theme_bits


def render(signal: ActivitySignal, intensity: float, band: str, fade_ms: int = 2000) -> str:
    """Render a modular-bleep Strudel program from the signal and derived energy."""
    intensity = clamp01(intensity)
    tr = band_traits(band)
    lpf = round(tr["lpf"])
    fast = round(0.6 + intensity * 1.0, 2)  # ~0.6x (delta) → ~1.6x (gamma)

    variant = _seed(signal) % len(_COUNTERS)
    seq = _SEQS[int(tr["density"])]
    counter, sub = _COUNTERS[variant], _SUBS[variant]

    lpf2 = min(lpf + 300, 2600)  # the counter-voice a touch brighter
    click_gain = round(0.08 + intensity * 0.12, 2)  # busier click as energy rises
    layers = [
        # Lead bleep — square through a slow filter sweep, short and clicky.
        (
            f'  n("{seq}").scale("C:minor:pentatonic").s("square")'
            f".decay(0.12).sustain(0).lpf(sine.range(300, {lpf}).slow(12))"
            ".delay(0.3).delaytime(0.1875).delayfeedback(0.4).pan(sine.slow(9)).gain(0.24)"
        ),
        # Counter-sequencer a beat behind — triangle, panned the other way.
        (
            f'  n("{counter}").scale("C:minor:pentatonic").s("triangle")'
            f".decay(0.18).sustain(0).lpf({lpf2}).late(0.25)"
            ".pan(sine.range(1, 0).slow(9)).room(0.4).gain(0.18)"
        ),
        # Sub pulse — the patch's low anchor.
        f'  note("{sub}").s("sine").lpf(120).gain(0.42).slow(2)',
        # A dry click on the offbeat for pulse; sparse when calm.
        f'  s("white").struct("~ x").decay(0.02).sustain(0).hpf(6000).gain({click_gain})',
    ]

    header = (
        f"// maelcom modular-bleep · band={band} · intensity={round(intensity, 3)} · "
        f"{signal.volume} change{'s' if signal.volume != 1 else ''}, "
        f"{signal.participant_count} voice{'s' if signal.participant_count != 1 else ''}"
    )
    body = ",\n".join(layers)
    return f"{header}\nstack(\n{body}\n).fast({fast})"
