"""Tests for the verified-primitive IR and its Strudel emitter."""

from __future__ import annotations

from statemediafm.genmusic.ir import (
    FORBIDDEN,
    VERIFIED_METHODS,
    Mod,
    Piece,
    Seg,
    Voice,
    emit,
    used_methods,
)


def _piece(voice: Voice) -> Piece:
    return Piece(header="// t", voices=(voice,), fast=1.0)


def test_emitter_only_produces_whitelisted_methods():
    v = Voice(
        name="v", kind="chord", sound="sawtooth", chord="<Cm9 Gm7>", late=0.02,
        fx=(("room", 0.7), ("delay", 0.5)),
        mods=(Mod("lpf", 200, 800, 31), Mod("pan", 0.3, 0.7, 23), Mod("gain", 0.1, 0.2, 29)),
        segments=(Seg(2, "x ~ x x"), Seg(1, None)),
    )
    text = emit(_piece(v))
    assert used_methods(text) <= VERIFIED_METHODS
    assert not any(bad in text for bad in FORBIDDEN)


def test_emit_is_well_formed_and_deterministic():
    v = Voice(name="b", kind="note", sound="sine", segments=(Seg(1, "c1 ~ e1 ~"),))
    a = emit(_piece(v))
    b = emit(_piece(v))
    assert a == b  # byte-identical → golden-testable
    assert a.startswith("//") and "stack(" in a and a.rstrip().endswith(")")


def test_swing_splits_onbeats_from_laid_back_offbeats():
    # A step on every position; swing must lay the off-beats (i%4>=2) back via a
    # stacked, constant .late — never swingBy.
    v = Voice(name="b", kind="note", sound="sawtooth", swing=0.1,
              segments=(Seg(1, "c1 c1 c1 c1 c1 c1 c1 c1"),))
    text = emit(_piece(v))
    assert ".late(0.1)" in text and "swingBy" not in text
    assert text.count("stack(") == 2  # the top-level stack + one swing split
    assert 'note("c1 c1 ~ ~ c1 c1 ~ ~")' in text  # on-beat half
    assert 'note("~ ~ c1 c1 ~ ~ c1 c1")' in text  # laid-back off-beat half


def test_swing_skips_the_split_when_no_offbeat_hits():
    # Hits only on beats (i%4==0) → nothing to lay back → no wasteful empty stack,
    # so only the top-level stack remains.
    v = Voice(name="b", kind="note", sound="sine", swing=0.1,
              segments=(Seg(1, "c1 ~ ~ ~ c1 ~ ~ ~"),))
    assert emit(_piece(v)).count("stack(") == 1


def test_lfo_mods_emit_long_global_slow_signals():
    v = Voice(name="c", kind="perc", sound="white",
              mods=(Mod("lpf", 400, 900, 31),), segments=(Seg(1, "x ~ x ~"),))
    text = emit(_piece(v))
    assert "lpf(sine.range(400, 900).slow(31))" in text


def test_mod_overrides_static_param():
    # A gain LFO means no static .gain is emitted (the LFO owns the level).
    v = Voice(name="c", kind="note", sound="sine", gain=0.5,
              mods=(Mod("gain", 0.1, 0.2, 29),), segments=(Seg(1, "c1 ~"),))
    text = emit(_piece(v))
    assert "gain(sine.range(0.1, 0.2).slow(29))" in text
    assert ".gain(0.5)" not in text
