# Entrainment — design & research plan

A Maelcom **ambient generator** (one of the user-selectable models alongside
_ScratchPad_) that turns the day's activity into long-form ambient music designed
around **brainwave entrainment**: the sound pulses and beats at the frequency of
the current brainwave band to gently nudge the listener toward that state, and is
composed with the long-form techniques of Max Richter's _Sleep_, Eno, Stars of
the Lid and Harold Budd.

> **Framing / responsibility.** Entrainment efficacy evidence is genuinely
> **mixed** (a 2023 review of 14 studies found only 5 supporting it; individual
> response varies widely). Entrainment is shipped as **ambient music with a
> plausible neuro-rationale — not a therapeutic or clinical claim.** No health
> promises in UI copy. Sub-bass is kept to safe, moderate levels.

Status: **v0.1 shipped** (binaural + isochronic carrier that tracks the band).
Everything below the roadmap is research grounding for what comes next.

---

## 1. Vision

- **Input:** the same `ActivitySignal → intensity → brainwave band` pipeline the
  rest of Maelcom uses. Busier collaboration → higher band → a more alert target;
  a quiet day → theta/delta → relaxation.
- **Output:** a calm, evolving ambient bed whose **entrainment carrier tracks the
  band**, layered with synthesized new-age instruments (water drops, rain sticks,
  sub-bass, distant train-horn chimes) arranged with co-prime, non-repeating,
  slowly-eroding long-form structure.
- **Two contexts:** a **focus** mode (alert bands, speaker-safe isochronic/monaural)
  and a **relaxation/sleep** mode (low bands, immersive binaural for headphones),
  selectable and/or inferred from band + time of day.

---

## 2. Entrainment engine (research → parameters)

### Delivery methods (pick by context)

| Method | How | Headphones? | Strength | Musicality | Use for |
|---|---|---|---|---|---|
| **Binaural** | two pure sines, one per ear, Δ apart; brain perceives the difference | **Required** | indirect (~35% @10 Hz) | blends into drones | immersive relaxation / sleep |
| **Monaural** | two sines mixed into **both** ears (`pan(0.5)`) → a real acoustic beat | No | direct, arrives intact | audible pulsing | speaker relaxation |
| **Isochronic** | one tone gated on/off at the target rate | No | strongest (~65% @10 Hz) | intrusive if hard-edged | focus / speakers |

Rules of thumb from the research:
- **Carrier ~200 Hz** sine gives the strongest _felt_ beat and sits under a drone;
  keep carriers **< ~1 kHz**. (v0.1 uses 110 Hz — low but "felt"; raise toward 200.)
- **Binaural beat breaks down above ~30 Hz** (the two tones separate into distinct
  pitches). So **gamma (~40 Hz) must use isochronic or monaural**, never binaural.
- Keep any isochronic gate **soft-edged** (a `sine` LFO, not a hard `square`) so
  focus modes stay musical.
- **Slew Δ slowly** between states (ramp over 1–3 minutes) to "carry" the listener —
  descending toward sleep, ascending toward focus.

### Target-frequency table (band → beat/carrier/method)

| State | Band | Beat (Hz) | Carrier (Hz) | Preferred method |
|---|---|---|---|---|
| Deep sleep | delta | 1.5–3 (≈2) | 150–200 | binaural (night/headphones) |
| Relaxation / meditation | theta | 5–7 (≈6) | 180–220 | binaural or monaural |
| Calm / relaxed focus | alpha | 9–11 (≈10) | 200–250 | binaural / isochronic |
| Alert focus | low beta | 14–18 (≈15) | 220–300 | isochronic / monaural |
| High cognition | gamma | ≈40 | 250–400 | isochronic / monaural (binaural fails) |

**Implementation:** presets `{beatHz, carrierHz, method}`; `freqL = carrier`,
`freqR = carrier + beatHz`. In Strudel: `freq()` + hard `pan(0)/pan(1)` for
binaural, both `pan(0.5)` for monaural, `gain(sine.range(a,b).fast(N))` for the
isochronic throb. Only the isochronic **rate** depends on tempo (the `_CYCLE_S`
constant — the engine won't expose cps to read back); the binaural/monaural beat
is an exact Hz difference and is tempo-independent.

---

## 3. New-age / long-form composition (research → technique)

- **Drones + glacial harmony:** sustained root/fifth beds; chords move over
  **minutes, not bars**. Modal/pentatonic pools, consonant.
- **Space & silence as content:** sparse events, long releases, big `room`; let
  tails ring and overlap (Budd / Stars of the Lid treat reverb as a structural
  layer).
- **Co-prime generative loops (Eno, _Music for Airports_):** several single-note
  loops of **incommensurable lengths** (e.g. 7, 11, 13, 17) never re-sync, so the
  surface never exactly repeats over long spans. Natural in Strudel via different
  `slow()` factors / pattern lengths.
- **Slow timbral evolution:** automate `lpf` cutoff and detune with very slow
  `sine.range().slow(n)`; gradual swells rather than sections.
- **Richter _Sleep_ session arc:** _Sleep_ (8+ hours, meant to be slept through,
  composed with neuroscientist David Eagleman) starts with faint motifs + a slow
  near-heartbeat pulse, then **erodes** — layers drop, releases lengthen, `lpf`
  closes — toward a pure drone-fog for the deep phase; form mirrors sleep stages.
  We emulate this as a **session arc**: begin with motif cells over the delta/theta
  bed, then progressively strip layers toward drone as a session deepens.

---

## 4. Instrument synthesis recipes (no samples available)

**(a) Water drop** — Minnaert bubble resonance; pitch **rises** as the bubble shrinks.
- `sine` `freq()` ~600–2000 Hz, one voice; **pitch envelope UP** (~+1 octave over
  15–30 ms); amp env attack ~1 ms, no sustain, decay ~30–120 ms; light `room` tail.
- Randomize pitch & timing per drop; optional tiny noise-burst→resonant `lpf` transient.

**(b) Rain stick** — filtered noise, not tones.
- `s("white")` → `hpf` ~1–2 kHz + `lpf` ~6–8 kHz for the grain hiss; dense stream
  of tiny randomized-`gain` grains; a slow `sine.range()` gain envelope for the
  tilt swell.

**(c) Sub-bass** — audible/felt sweet spot **~30–60 Hz** (avoid < 25 Hz: inaudible
on laptop/phone speakers; low freqs are least safe when loud, so keep gain moderate).
- `sine` `freq(40)`, **slow attack (0.5–2 s)**, long release; slow gain LFO
  `sine.range(0.4,0.8).slow(8)` to breathe; optional 2nd osc ~0.1–0.3 Hz apart for
  slow beating.

**(d) Distant train-horn chime** — signature is a **detuned tone cluster**, not one note.
- Nathan K5LA horn ≈ **B major-6**: D♯3 311, F♯3 370, G♯3 415, B3 494, D♯4 622 Hz.
  Stack as simultaneous saws through `lpf` ~2–3 kHz; **detune each partial ±a few
  Hz** — the beating is what reads as a horn.
- **Distance:** lower `lpf`, heavy `room`, lower gain, slow attack (50–150 ms), long
  release. **Doppler:** slow downward `freq` glide + a slow `pan` sweep to imply a
  passing train.

---

## 5. Roadmap (versioned, incremental — we build up rule by rule)

- **0.1 — Entrainment core (done).** Binaural pair (`freq`+`pan`) + isochronic
  throb + grounding sub; beat tracks the band (delta 2 → gamma 40 Hz). 5 founding
  rules in `RULES`.
- **0.2 — Method by band + carrier.** Raise carrier toward ~200 Hz; switch gamma
  (and high beta) to isochronic/monaural (binaural fails > 30 Hz); add a monaural
  (both `pan(0.5)`) speaker mode; soften the isochronic gate.
- **0.3 — New-age bed.** Co-prime single-note drone loops (7/11/13/17) over the
  carrier; modal pool; very slow `lpf`/detune evolution; big reverb.
- **0.4 — Instruments.** Add the four synths above as sparse, randomized events
  (water drops & rain sticks for texture; train-horn chimes as rare far-off
  events; sub as the bed).
- **0.5 — Session arc (Richter).** Erosion over time: start with motif cells,
  progressively strip toward drone-fog; slow Δ slew between states; optional
  time-of-day / focus-vs-relax mode.
- **Later.** Headphone detection & a binaural/speaker toggle in the UI; per-state
  presets exposed; smooth cross-model transitions.

---

## 6. Open questions

- **cps calibration.** The engine doesn't expose cps to read back, so isochronic
  _rate_ is calibrated via `_CYCLE_S` (binaural/monaural are exact). Confirm by ear
  and pin the constant, or find a way to measure the cycle period.
- **Headphone vs speaker.** No reliable web API to detect headphones; likely a UI
  toggle (default speaker-safe monaural/isochronic; binaural opt-in).
- **How much the band should _drive_ vs a fixed session arc.** Activity-driven Δ
  (Maelcom-native) vs a Richter-style scripted descent — probably both, blended.

---

## Sources

Binaural basics & limits: binauralbeatsmeditation.com/oster-curve · eneuro.org/content/7/2/ENEURO.0232-19.2020 ·
2023 review (mixed evidence): ncbi.nlm.nih.gov/pmc/articles/PMC10198548 · 6 Hz theta: PMC5487409 · 40 Hz gamma: PMC4995205 ·
methods compared: brain.fm/blog/binaural-beats-vs-monaural-beats-vs-isochronic-tones · en.wikipedia.org/wiki/Isochronic_tones ·
bands: diygenius.com/the-5-types-of-brain-waves · purebinaural.com/brainwave-frequencies ·
Eno generative: synthtopia.com/content/2019/04/24/twenty-techniques-for-generative-music-inspired-by-brian-eno ·
Music for Airports: reverbmachine.com/blog/deconstructing-brian-eno-music-for-airports ·
sub-bass/hearing: en.wikipedia.org/wiki/Sub-bass · hearinghealthfoundation.org (low-frequency safety) ·
train horn chord: train-horn.com/guides/train-horn-chord-trumpet-tuning ·
water/rain synthesis: audiokinetic.com/en/blog/generating-rain-with-pure-synthesis · Minnaert resonance (Wikipedia) ·
Max Richter _Sleep_ / Eagleman: grainsmusic.com/articles/the-science-of-sleep-dr-eagleman · en.wikipedia.org/wiki/Sleep_(album) ·
Stars of the Lid / Budd: en.wikipedia.org/wiki/Stars_of_the_Lid · themusictheoryprofessor.com (Eno/Budd waveform techniques)
