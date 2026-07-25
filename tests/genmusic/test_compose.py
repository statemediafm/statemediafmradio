"""Tests for composing an ActivitySignal into a StrudelProgram."""

from __future__ import annotations

import re

import pytest

from maelcom.core.models import ActivitySignal, StrudelProgram
from maelcom.genmusic.compose import compose


def _signal(volume: int, participants: int, volatility: float = 0.2) -> ActivitySignal:
    voices = {f"dev{i}": v for i, v in enumerate(["rhodes", "upright-bass", "vibraphone"][:participants])}
    return ActivitySignal(
        window_s=3600.0,
        volume=volume,
        volatility=volatility,
        participant_count=participants,
        themes=["scheduler", "telemetry"],
        actor_voices=voices,
    )


def test_compose_returns_program_with_matching_band():
    program = compose(_signal(3, 2))
    assert isinstance(program, StrudelProgram)
    assert program.style == "Entrainment 0.1"  # the default ambient generator
    assert 0.0 <= program.intensity <= 1.0
    # The band is exactly the one the intensity falls in.
    from maelcom.genmusic.brainwave import band_for_intensity

    assert program.brainwave_band == band_for_intensity(program.intensity)


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        compose(_signal(3, 2), style="space-dub")


def test_intensity_override_is_respected():
    program = compose(_signal(3, 2), intensity=0.9)
    assert program.intensity == 0.9
    assert program.brainwave_band == "gamma"


def test_quiet_repo_idles_low_busy_repo_energizes():
    quiet = compose(_signal(1, 1, volatility=0.0))
    busy = compose(_signal(50, 8, volatility=0.9))
    assert quiet.intensity < busy.intensity
    assert quiet.brainwave_band in {"delta", "theta"}
    assert busy.brainwave_band in {"beta", "gamma"}


def test_program_text_is_valid_looking_strudel():
    text = compose(_signal(20, 5, volatility=0.5), style="lofi").text
    assert "stack(" in text and ").fast(" in text
    # @strudel/web has neither, and using them makes the whole program fail.
    assert "setcps" not in text and "fadeIn" not in text
    # Metadata appears only in the leading header comment — never inline, so it
    # can't comment out a layer-separating comma.
    lines = text.splitlines()
    assert lines[0].startswith("// maelcom lofi")
    for line in lines[1:]:
        assert "//" not in line


def test_no_style_uses_unsupported_strudel_functions():
    # Guard every style against the setcps/fadeIn trap that silently kills audio.
    for style in ("ScratchPad", "lofi"):
        text = compose(_signal(8, 3, volatility=0.4), style=style).text
        assert "setcps" not in text and "fadeIn" not in text, style


def test_tintinnabuli_is_dark_g_dorian_and_low():
    text = compose(_signal(8, 3, volatility=0.4), style="ScratchPad").text
    assert "ScratchPad" in text and text.rstrip().endswith(".slow(2)")  # largo
    assert 'scale("G1:dorian")' in text  # confined to G Dorian
    assert ":minor" not in text  # no minor keys (major/modal is fine)
    # Everything is low (octave 1) except the incidental major glints, which may
    # rise to ~440 Hz (A4) — the only octave-2+ voice allowed.
    highs = set(re.findall(r"[A-G]#?[2-9]:(?:dorian|major|minor)", text))
    assert highs <= {"G4:major"}


def test_tintinnabuli_low_pass_bed_and_voices_stay_dark():
    text = compose(_signal(8, 3), style="ScratchPad").text
    for line in text.splitlines():
        if ":major" in line:  # the bright glints are the deliberate exception (rule 15)
            continue
        for c in re.findall(r"\.lpf\((\d+)\)", line):
            assert int(c) <= 700  # bed & voices stay dark (dog-safe)
    assert "sine.range(220,480)" in text  # the pad's slow, low filter LFO


def test_tintinnabuli_voices_come_and_go_and_trade_off():
    text = compose(_signal(8, 3), style="ScratchPad").text
    assert "[~]" in text  # gated voices leave whole-bar rests — they come and go
    assert text.count(".sustain(0.12)") >= 2  # multiple dark-piano voices (leader/canon/response)


def test_tintinnabuli_base_canons_are_theta_slow():
    text = compose(_signal(8, 3, volatility=0.4), style="ScratchPad").text
    voice_fasts = {int(x) for x in re.findall(r"\.detune\(0\.06\)\.fast\((\d+)\)", text)}
    assert voice_fasts == {1}  # slow, meditative base canons — never doubled up


def test_tintinnabuli_canon_voices_fade_in_and_out():
    text = compose(_signal(8, 3), style="ScratchPad").text
    # canon voices' gain is a slow sine LFO (long fade in/out)
    m = re.search(r"\.gain\(sine\.range\([0-9.]+,0\.32\)\.slow\((\d+)\)\)", text)
    assert m and int(m.group(1)) >= 40  # slow fades


def test_tintinnabuli_sub_bass_hit_is_synced_to_the_drop():
    text = compose(_signal(8, 3), style="ScratchPad").text
    # the sub-bass pedal hit lives inside the drop block (rule 19): a deep,
    # long-release G that fires once across the 3-bar drop.
    assert re.search(r'note\("<g1 ~ ~>"\)\.s\("sine"\)\.attack\([0-9.]+\)\.release\(8\)\.lpf\(100\)', text)
    # the drop's own key-tonic sub-root sits right beside it
    drop_line = next(ln for ln in text.splitlines() if 'note("<g1 ~ ~>")' in ln)
    assert "sine" in drop_line


def test_tintinnabuli_evolves_tonally_across_movements():
    text = compose(_signal(8, 3), style="ScratchPad").text
    keys = set(re.findall(r'scale\("([A-G]#?)1:dorian"\)', text))
    assert "G" in keys and len(keys) >= 2  # modulates to adjacent Dorian keys


def test_tintinnabuli_canon_call_response_and_rare_tintinnabuli():
    text = compose(_signal(8, 3), style="ScratchPad").text
    assert "arrange(" in text and text.rstrip().endswith(").slow(2)")
    assert ".late(" in text  # canon imitation — a voice offset in time
    assert "[6, stack(" in text  # a brief tintinnabuli passage (~every 180 bars)
    assert 's("white")' not in text  # no drums / snare / noise percussion


def test_tintinnabuli_rhythm_evolves_with_turnaround_pause_drop():
    text = compose(_signal(8, 3), style="ScratchPad").text
    # each ~30-bar movement ends with a turnaround (4), a pause (1) and a drop (3)
    assert "[4, stack(" in text and "[1, stack(" in text and "[3, stack(" in text


def test_tintinnabuli_glints_are_rare_one_bar_every_32():
    text = compose(_signal(8, 3), style="ScratchPad").text
    assert 'scale("G4:major")' in text
    assert re.search(r'scale\("G4:major"\)\.s\("sawtooth"\)\.detune\(0\.1\)\.lpf\(1400\)', text)
    assert "[31, silence]" in text  # one bar of glint, then silent for 31 more


def test_tintinnabuli_delayed_chime_every_64_bars():
    text = compose(_signal(8, 3), style="ScratchPad").text
    # one beat of chime with a many-repeat delay + a theta-rate LFO, then 63 bars off
    assert re.search(r"\.delay\(0\.9\)\.delaytime\(sine\.range\([0-9.,]+\)\.fast\(24\)\)\.delayfeedback\(", text)
    assert "[63, silence]" in text


def test_tintinnabuli_sometimes_has_a_16_bar_silence():
    # deterministic per signal; scan a range of signals for the occasional rest
    seen = any("[16, silence]" in compose(_signal(v, 3), style="ScratchPad").text for v in range(1, 12))
    assert seen  # rule 18: sometimes, a 16-bar full silence


def test_tintinnabuli_news_burst_adds_consonant_swell():
    calm = compose(_signal(2, 2), style="ScratchPad").text  # few news items
    burst = compose(_signal(30, 5), style="ScratchPad").text  # a burst of news
    assert "[0,2,4]" not in calm  # baseline: no accent
    assert "[0,2,4]" in burst  # a consonant tonic-triad swell (in-key) on a burst


def test_no_triangle_or_square_above_c2():
    # Timbre rule: triangle/square are reserved for low (<=C2), short sounds; the
    # styles here have no such notes, so they use none at all.
    for style in ("ScratchPad", "lofi"):
        text = compose(_signal(8, 3, volatility=0.4), style=style).text
        assert '"triangle"' not in text and '"square"' not in text, style


def test_compose_is_deterministic():
    a = compose(_signal(12, 3, volatility=0.4)).text
    b = compose(_signal(12, 3, volatility=0.4)).text
    assert a == b


def test_tuning_retunes_all_notes_via_global_detune():
    import math

    base = compose(_signal(8, 3), style="Entrainment 0.1", tuning_a=440.0).text
    assert ".detune(" not in base  # 440 is standard — no retune appended

    for a in (432.0, 435.0):
        text = compose(_signal(8, 3), style="Entrainment 0.1", tuning_a=a).text
        cents = round(1200 * math.log2(a / 440.0), 3)
        assert text.rstrip().endswith(f".detune({cents})")  # one global retune of all notes
        assert cents < 0  # 432/435 are flatter than 440


def test_ambient_models_are_registered():
    from maelcom.genmusic.styles import AMBIENT_MODELS, STYLES

    assert list(AMBIENT_MODELS[:2]) == ["Entrainment 0.1", "ScratchPad"]  # defaults, first is default
    for model in AMBIENT_MODELS:
        assert model in STYLES  # each selectable model has a renderer


def test_entrainment_renders_a_journey_over_a_drifting_frame():
    prog = compose(_signal(8, 3, volatility=0.4), style="Entrainment 0.1")
    assert prog.style == "Entrainment 0.1"
    text = prog.text
    assert "Entrainment 0.1" in text and "journey" in text
    assert "setcps" not in text and "fadeIn" not in text  # the traps
    # a multi-phase arrangement of 16-bar phases (so no voice loops past 16 bars)
    assert "arrange(" in text and text.count("[16, stack(") >= 20
    assert 'note("a1").s("sine")' in text  # a stable bass pedal every phase
    assert 'note("<[a2' in text  # the multivoice major-chord drone
    # deterministic
    assert compose(_signal(8, 3, volatility=0.4), style="Entrainment 0.1").text == text


def test_entrainment_frame_drifts_down_toward_relaxation():
    # the header records the frame's start→end; busy (gamma 40) descends a long way
    busy = compose(_signal(40, 8, volatility=0.9), intensity=0.95, style="Entrainment 0.1").text
    m = re.search(r"frame (\d+(?:\.\d+)?)→(\d+(?:\.\d+)?) Hz", busy)
    assert m and float(m.group(2)) <= float(m.group(1))  # never drifts up


def test_entrainment_a_pedal_resolves_to_d_no_walking():
    text = compose(_signal(20, 5, volatility=0.6), style="Entrainment 0.1").text
    assert ":minor" not in text
    assert "c#3" in text  # the A-major color
    # the harmony only ever sits on A (a2) or the resolved D (d2) — no walking
    chord_roots = re.findall(r'note\("<\[([a-g]#?\d)', text)
    assert set(chord_roots) <= {"a2", "d2"}
    assert chord_roots.count("a2") > chord_roots.count("d2")  # A is home; D is the resolution
    # the deep bass pedal follows the root (a1, and d1 an octave down when resolved)
    assert 'note("a1").s("sine")' in text and 'note("d1").s("sine")' in text
    # entrainment rides a binaural beat (an exact freq() pair, hard L/R) where viable
    assert "freq(220)" in text and ".pan(0)" in text and ".pan(1)" in text
    assert re.search(r"\.pan\(sine\.range\(", text)  # slow spatial movement


def test_entrainment_gamma_falls_back_to_filter_pulse():
    text = compose(_signal(50, 8, volatility=0.9), intensity=0.97, style="Entrainment 0.1").text
    # gamma (40 Hz) exceeds the binaural limit at the start, so a filter pulse carries it
    assert re.search(r"lpf\(sine\.range\([0-9,]+\)\.fast\(\d+\)\)", text)


def test_entrainment_chimes_resolve_and_noise_is_a_slow_tide():
    text = compose(_signal(8, 3), style="Entrainment 0.1").text
    # chimes: occasional 1-, 2-, or 3-tone gestures resolving to A(0) or D(3)
    cells = re.findall(r'n\("(<[^"]*>)"\)\.scale\("a4:major"\)', text)
    assert len(cells) > 0
    for cell in cells:
        toks = [t for t in cell.strip("<> ").split() if t != "~"]
        assert len(toks) in (1, 2, 3, 5)  # a 1-, 2-, 3-, or 5-tone chime
        assert toks[-1] in ("0", "3")  # resolves to the tonic (A) or the upcoming D
    for m in re.finditer(r'a4:major"\)(\.s\("(\w+)"\)|\.s\("(\w+)"\)\.lpf)', text):
        assert (m.group(2) or m.group(3)) in ("sine", "square")  # never triangle/sawtooth
    # any white/colored noise is a slow tide — long attack/release, no fast flutter
    noise_lines = [ln for ln in text.splitlines() if 's("white")' in ln]
    assert noise_lines
    for ln in noise_lines:
        assert ".attack(8).release(10)" in ln and "rand" not in ln and ".fast(" not in ln


def test_synth_beat_is_always_present():
    # The lofi beat is synthesized (sine kick, noise snare + hats, plucky
    # melody), so it sounds without samples even for a quiet, solo repo.
    solo = compose(_signal(1, 1, volatility=0.0), style="lofi").text
    assert "scale(" in solo  # melody
    assert '.s("sine")' in solo  # kick
    assert 's("white").struct' in solo  # snare on the backbeat
    assert 's("white*' in solo  # hats
    assert '"bd' not in solo and "RolandTR909" not in solo  # no sample drums


def test_hats_get_denser_with_intensity():
    calm = compose(_signal(2, 1), intensity=0.2, style="lofi").text
    busy = compose(_signal(40, 8, volatility=0.9), intensity=0.9, style="lofi").text
    assert "white*4" in calm
    assert "white*8" in busy
