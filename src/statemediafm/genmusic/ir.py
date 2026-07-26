"""A tiny musical IR and a Strudel emitter restricted to verified primitives.

Genre generators build a :class:`Piece` of :class:`Voice` tracks (pure data);
:func:`emit` renders it to a Strudel program. The point is reliability: the
emitter only ever produces methods in :data:`VERIFIED_METHODS` (things confirmed
to sound in ``@strudel/web`` 1.0.3 over this project), and a test asserts the
emitted program uses nothing outside that set. If the emitter can't express
something, it can't ship a silently-broken program.

Musical concepts are built from verified parts:

* **swing** — split a pattern into on-beat and off-beat halves and lay the
  off-beats back with a constant ``.late`` inside a ``stack`` (no ``swingBy``).
* **evolution** — long, mutually-prime LFOs (``sine.range(...).slow(period)``)
  sampled from the global clock, so a held program keeps moving for hours.
* **phrase shape** — an ``arrange`` of per-voice segments (loop/turnaround/…).

Everything is a frozen dataclass, so pieces are deterministic and golden-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The whitelist: methods the emitter is allowed to produce. Kept deliberately
# small — each has been exercised and confirmed audible in @strudel/web 1.0.3.
VERIFIED_METHODS = frozenset(
    {
        "s", "struct", "voicing", "scale", "lpf", "hpf", "room", "roomsize",
        "delay", "delaytime", "delayfeedback", "decay", "sustain", "attack",
        "gain", "pan", "slow", "fast", "late", "range",
    }
)
VERIFIED_FUNCS = frozenset({"note", "n", "chord", "s", "sine", "silence", "stack", "arrange"})
# Emitting any of these makes the whole program fail silently — never allowed.
FORBIDDEN = ("setcps", "setcpm", "fadeIn", "unison(", "swingBy", "lpenv", "lpq(")

# Off-beat mask on a 16th grid: the back half of each beat (the "&" and "a"),
# which is what a laid-back dub swing delays.
def _is_offbeat(i: int) -> bool:
    return i % 4 >= 2


@dataclass(frozen=True)
class Mod:
    """A slow global LFO on a param, sampled from Strudel's global clock so it
    evolves for the whole session and never resets. ``param`` in {lpf,pan,gain}."""

    param: str
    lo: float
    hi: float
    period: int  # cycles; use long, mutually-prime periods for long evolution


@dataclass(frozen=True)
class Seg:
    """One ``arrange`` segment: ``cycles`` bars of ``body`` (a mini-notation
    string), or a rest when ``body`` is None."""

    cycles: int
    body: str | None


@dataclass(frozen=True)
class Voice:
    """One instrument track. ``kind`` picks the source: ``note`` (pitched
    sequence), ``perc`` (a sound struck by a struct), or ``chord`` (a voiced
    progression struck by a struct)."""

    name: str
    kind: str  # "note" | "perc" | "chord"
    sound: str  # waveform ("sawtooth"/"sine"/…) or noise ("white")
    segments: tuple[Seg, ...]
    chord: str = ""  # progression, for kind == "chord"
    scale: str = ""  # e.g. "c1:minor:pentatonic" — makes note steps key-relative degrees
    gain: float = 0.5
    lpf: float | None = None
    hpf: float | None = None
    attack: float | None = None
    decay: float | None = None
    sustain: float | None = None
    swing: float = 0.0  # lay-back of off-beats (cycles), via split + late
    late: float = 0.0  # constant lay-back (rubato), cycles
    slow: float = 1.0
    fx: tuple[tuple[str, float], ...] = ()  # (("delay",0.5),("room",0.7),…)
    mods: tuple[Mod, ...] = ()


@dataclass(frozen=True)
class Piece:
    header: str
    voices: tuple[Voice, ...]
    fast: float


def _num(x: float) -> str:
    """Deterministic number formatting: ints stay ints, floats keep their value."""
    if isinstance(x, int) or float(x).is_integer():
        return str(int(x))
    return repr(round(float(x), 4))


def _tokens(body: str) -> list[str]:
    return body.split(" ")


def _mask(body: str, keep_offbeat: bool) -> str:
    """Blank on- or off-beat steps to rests, for the swing split. Only valid on a
    plain token sequence (no ``<``/``[`` grouping)."""
    return " ".join(
        tok if _is_offbeat(i) == keep_offbeat else "~" for i, tok in enumerate(_tokens(body))
    )


def _swingable(body: str) -> bool:
    return "<" not in body and "[" not in body


def _core(v: Voice, body: str) -> str:
    """Emit one pattern for voice ``v`` over ``body`` — source + envelope +
    filters + fx + LFO mods + gain. Swing and constant lay-back are added by the
    caller so they wrap the whole (possibly stacked) pattern."""
    if v.kind == "note":
        if v.scale:  # key-relative degrees: n("0 3 -5").scale("c1:minor:pentatonic")
            s = f'n("{body}").scale("{v.scale}").s("{v.sound}")'
        else:
            s = f'note("{body}").s("{v.sound}")'
    elif v.kind == "chord":
        s = f'chord("{v.chord}").voicing().s("{v.sound}").struct("{body}")'
    else:  # perc
        s = f's("{v.sound}").struct("{body}")'
    if v.attack is not None:
        s += f".attack({_num(v.attack)})"
    if v.decay is not None:
        s += f".decay({_num(v.decay)})"
    if v.sustain is not None:
        s += f".sustain({_num(v.sustain)})"
    mod_params = {m.param for m in v.mods}
    if v.lpf is not None and "lpf" not in mod_params:
        s += f".lpf({_num(v.lpf)})"
    if v.hpf is not None and "hpf" not in mod_params:
        s += f".hpf({_num(v.hpf)})"
    for name, val in v.fx:
        s += f".{name}({_num(val)})"
    for m in v.mods:
        s += f".{m.param}(sine.range({_num(m.lo)}, {_num(m.hi)}).slow({_num(m.period)}))"
    if "gain" not in mod_params:
        s += f".gain({_num(v.gain)})"
    return s


def _segment_pattern(v: Voice, body: str) -> str:
    """A segment pattern with swing + rubato applied. Swing splits the pattern and
    lays the off-beats back inside a ``stack`` (verified parts only); the chord/
    grouped patterns fall back to constant lay-back."""
    if v.swing > 0 and _swingable(body):
        on_body, off_body = _mask(body, keep_offbeat=False), _mask(body, keep_offbeat=True)
        on_has = any(t != "~" for t in on_body.split(" "))
        off_has = any(t != "~" for t in off_body.split(" "))
        if on_has and off_has:  # split: lay the off-beats back inside a stack
            pat = f"stack({_core(v, on_body)}, {_core(v, off_body)}.late({_num(v.swing)}))"
        elif off_has:  # everything is off-beat → just lay it back
            pat = f"{_core(v, off_body)}.late({_num(v.swing)})"
        else:  # nothing off-beat to swing
            pat = _core(v, body)
    else:
        pat = _core(v, body)
    if v.late:
        pat += f".late({_num(v.late)})"
    return pat


def _emit_voice(v: Voice) -> str:
    parts = []
    for seg in v.segments:
        if seg.body is None:
            parts.append(f"[{seg.cycles}, silence]")
        else:
            parts.append(f"[{seg.cycles}, {_segment_pattern(v, seg.body)}]")
    arr = f"arrange({', '.join(parts)})"
    if v.slow != 1.0:
        arr += f".slow({_num(v.slow)})"
    return "  " + arr


def emit(piece: Piece) -> str:
    """Render a :class:`Piece` to a Strudel program string."""
    body = ",\n".join(_emit_voice(v) for v in piece.voices)
    return f"{piece.header}\nstack(\n{body}\n).fast({_num(piece.fast)})"


def used_methods(text: str) -> set[str]:
    """The set of ``.method(`` names in an emitted program (for the whitelist test)."""
    return set(re.findall(r"\.([a-zA-Z][a-zA-Z0-9]*)\(", text))
