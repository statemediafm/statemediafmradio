# Entrainment — design & research plan

A State Media FM **ambient generator** (one of the user-selectable models alongside
_ScratchPad_) that turns the day's activity into long-form ambient music designed
around **brainwave entrainment**: the sound pulses and beats at the frequency of
the current brainwave band to gently nudge the listener toward that state, and is
composed with the long-form techniques of Max Richter's _Sleep_, Eno, Stars of
the Lid and Harold Budd.

> **Framing / responsibility.** Entrainment efficacy evidence is genuinely
> **mixed** (a 2023 review of 14 studies found only 5 supporting it; binaural
> meta-analyses land around g ≈ 0.40–0.45 but with notable nulls; individual
> response varies widely). Likewise, **"superlearning" / accelerated-learning
> claims (learning 3–25× faster) are debunked/unsupported** (§5) — the
> Baroque-largo aesthetic is a sound *design rationale*, not a learning guarantee.
> Entrainment is shipped as **ambient music with a plausible neuro-rationale — not
> a therapeutic, clinical, or learning claim.** No health/learning promises in UI
> copy. Sub-bass is kept to safe, moderate levels.

Status: **v0.1 shipped** (binaural + isochronic carrier that tracks the band).
Everything below the roadmap is research grounding for what comes next.

---

## 1. Vision

- **Input:** the same `ActivitySignal → intensity → brainwave band` pipeline the
  rest of State Media FM uses. Busier collaboration → higher band → a more alert target;
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

## 5. Superlearning & a study / learning mode

"Superlearning" (Ostrander & Schroeder, 1979) popularized **Georgi Lozanov's
Suggestopedia**: the premise that the main barrier to learning is *psychological*
(anxiety, inhibition), removed via relaxation + suggestion + **music** to reach a
**relaxed-alert** state, with content paced to slow rhythmic breathing.

> **⚠️ The grand claims are debunked.** "Learn 3–25× faster" is **not** supported —
> Suggestopedia failed to replicate; a 1988 US Army Research Institute review found
> no support; Wagner & Tilney (1983) found no vocabulary advantage and no reliable
> alpha boost. It's widely classed as pseudoscience/overstated. **The defensible
> kernel:** relaxation + reduced anxiety + focused attention + pleasant, low-arousal
> instrumental music can *modestly* help *some* learners settle and sustain
> attention. Ship it as music-with-a-rationale, never a learning guarantee.

**The useful part is the aesthetic spec.** Superlearning prescribes **Baroque
largo at ~60 BPM** — ~1 beat/sec ≈ resting heart rate, a "calm alertness" cue.
Baroque's structural traits are exactly what a low-distraction study bed wants:
a steady **basso continuo** (regular, predictable pulse); **one tempo, one dynamic
level** per movement (no swells to hijack attention); a **well-defined key** with
consonant, standardized harmony; slow largo/adagio, small consonant ensemble.
(Cited pieces: slow movements of Bach, Vivaldi, Corelli, Handel, Telemann;
Pachelbel's Canon; "Air on the G String.") Lozanov's "concert" technique has an
**active** phase (material read expressively over the music) and a **passive**
phase (relaxed listening, for consolidation).

**Learning brainwave states:** **alpha ~8–13 Hz** = relaxed-alert receptivity (the
state superlearning targets); **theta ~4–8 Hz** = memory **encoding**, with
**theta–gamma coupling** ordering/binding what gets recalled; **delta/slow-wave** =
offline **consolidation during sleep** (that's the _Sleep_ territory of §3, not a
waking study tool). Most "study" tracks target **~10 Hz alpha** (default) or
**~6 Hz theta** (memory/creativity).

**Binaural-and-memory evidence (honest):** real but small and inconsistent —
meta-analyses ~**g ≈ 0.40–0.45** (Garcia-Argibay 2019; Basu & Banerjee 2023), but
notable **nulls/negatives** (a 2023 home-use study found binaural beats *worsened*
performance). Helpful moderators: **longer exposure (~15–30 min)**, **pre-task**
exposure, and the right band+carrier. Isochronic drives a stronger cortical
response than binaural and works on speakers.

**Music-while-studying, generally:** **lyrics** are the main culprit (semantic
interference on verbal tasks) → **no vocals**; least disruptive = **familiar,
low-arousal, slow, non-lyrical**; novelty/deviance breaks concentration; large
individual differences.

### → Study/Learning mode — parameters

| Parameter | Value | Rationale |
|---|---|---|
| Tempo | **60 BPM** (1 Hz pulse) | heart-rate largo; calm-alertness cue |
| Entrainment target | **~10 Hz alpha** default; **~6 Hz theta** preset | most-used study bands |
| Method | **isochronic** primary (speaker-safe); **binaural** (~200 Hz carrier, Δ = target) headphones-only | isochronic entrains on speakers |
| Harmony | consonant, single key, slow harmonic rhythm (change every 2–4 bars) | Baroque simplicity; no surprise |
| Dynamics | flat, quiet, narrow range | avoid attention capture |
| Instruments | soft strings/pad + a plucked "basso continuo" arpeggio; no percussion transients; **no lyrics** | Baroque-ish, unobtrusive |
| Session | fade-in; steady 15–30 min blocks | matches effective exposure durations |

**Strudel translation:** a **1 Hz** gain/`lpf` LFO on the pad/sub as the heartbeat
pulse; a steady quarter-note plucked arpeggio, one consonant chord per ~2 bars, as
basso continuo; a soft-edged **10 Hz** (alpha) isochronic amplitude gate on a mid
tone/bus (speaker-safe), with a **6 Hz** theta preset and a headphone-gated
binaural pair (200/210 Hz → 10 Hz); optional very-slow **~0.1 Hz** pad swell as a
paced-breathing cue. Constrain to one mode/key; cap dynamic range.

---

## 6. Roadmap (versioned, incremental — we build up rule by rule)

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
- **0.6 — Study / learning mode (§5).** A selectable relaxed-alert preset: a
  60 BPM largo "heartbeat" pulse, a consonant single-key Baroque-ish bed (plucked
  basso continuo), 10 Hz alpha isochronic (6 Hz theta option; headphone binaural),
  flat dynamics, no percussion/lyrics, 15–30 min blocks. Framed as
  music-with-rationale, not a learning claim.
- **Later.** Headphone detection & a binaural/speaker toggle in the UI; per-state
  presets exposed; smooth cross-model transitions.

---

## 7. The generative journey (built in `styles/entrainment.py`)

**Shipped — "basic mode":** deterministic **~13–15 min journeys** as an `arrange`
of 24–29 **16-bar phases**. Each phase = a **stable major-key drone baseline** (a
tonic pedal — the bass never walks; it carries the entrainment via a **filter** or
**pan** pulse at the band rate, so an effect holds the tempo, no pulsing tone or
melody) + **middle harmonics** (a mid A-major-pentatonic pad, voicing rotating each
phase) + **occasional chimes** (sparse, high, long-echoed). The frame drifts slowly
downward toward relaxation; **no voice repeats past 16 bars** (each phase re-derives
its material by a small consonant step); heavy reverb/delay tails and long
attack/release bridge every seam. No melody voice.

**Reserved for a fuller mode** (previously built, see git history / below): a
larger **associative grammar** of occasional motifs.

**Frame (every phase, continuous):**
- a low **drone** (root+fifth, slow filter breath, slow spatial pan);
- a soft modal **pad** (A-minor-pentatonic; ducks anti-phase to the noise wave);
- an **entrainment carrier** at the phase's band frequency, delivered per phase as
  one of three modalities (seeded) — **amplitude** (isochronic tone), a **filter
  pulse** (cutoff LFO at the band rate), or a **pan pulse** — so *an effect can
  hold the entrainment tempo*, not only a tone/clock/drum.
- The **frame frequency drifts slowly downward** toward relaxation across the
  journey (small seeded steps, floored at ~2 Hz).

**Occasional motifs** (scheduled): `binaural` (sessions of minutes), `sub_wave`
(long slow sub swells), `noise_wave` (white/brown/airy colored-noise washes),
`chime` (echoed bells), `water` (rising-pitch drops), `rainstick` (scattered
noise grains), `arp` (plucked basso continuo), `drum` (rare soft heartbeat).

**Grammar rules (the "associative" part):** each occasional motif has
`(probability, min/max length, cooldown)`; rarer/longer things (binaural, sub,
drum) have low probability, longer minimum length and longer cooldowns. An
**affinity** map biases additions toward motifs that associate with what's already
sounding (chime↔rainstick/water, drum↔arp, sub↔binaural, noise↔rainstick). A hard
**cap of 3** simultaneous occasional motifs keeps the space uncluttered, and the
active set changes by **~one element per phase**.

**Smoothness (never interrupt attention):** the drone + pad are always present;
every motif uses long attack/release and heavy reverb/delay whose tails bridge the
`arrange` seams; the frame steps are small; density changes are gradual. Aesthetic:
low frequencies, low-passed-saw harmonics, reverb + delay echoes, colored-noise
waves, anti-phase **ducking** (pad vs noise) and slow **spatial** auto-pan — "a
space that slowly reveals itself."

---

## 8. Open questions

- **cps calibration.** The engine doesn't expose cps to read back, so isochronic
  _rate_ is calibrated via `_CYCLE_S` (binaural/monaural are exact). Confirm by ear
  and pin the constant, or find a way to measure the cycle period.
- **Headphone vs speaker.** No reliable web API to detect headphones; likely a UI
  toggle (default speaker-safe monaural/isochronic; binaural opt-in).
- **How much the band should _drive_ vs a fixed session arc.** Activity-driven Δ
  (State Media FM-native) vs a Richter-style scripted descent — probably both, blended.

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

Superlearning / Suggestopedia: en.wikipedia.org/wiki/Suggestopedia · link.springer.com/referenceworkentry/10.1007/978-1-4419-1428-6_611 ·
debunking (no support for accelerated learning): link.gale.com/apps/doc/A91803906/AONE · Wagner & Tilney 1983 (TESOL Q, no vocab advantage): onlinelibrary.wiley.com/doi/abs/10.2307/3586420 ·
Baroque-largo-60-BPM rationale: baroquemusic.org/506Web.html · sleeplearning.com/info/baroque-music ·
binaural & memory/cognition meta-analyses (g≈0.40–0.45) + nulls: link.springer.com/article/10.1007/s00426-022-01706-7 · brain.fm/blog/binaural-beats-for-studying ·
theta–gamma coupling & memory: biorxiv.org/content/10.1101/191189v2 · roxiva.com/theta-and-gamma-brainwaves-work-together ·
music-while-studying (lyrics interfere; arousal/mood): journalofcognition.org/articles/10.5334/joc.273 · ncbi.nlm.nih.gov/pmc/articles/PMC11045806 · journals.sagepub.com/doi/10.1177/03057356261421209
