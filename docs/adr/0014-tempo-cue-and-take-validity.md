# 0014: Strike the bass on every beat, and refuse takes the engine could not keep up with

Status: accepted for the pitch-state cue and the take checks. Whether the cue meets
the sync and tempo gates is not yet measured: it needs a full `make takes` run on a Mac
with no other heavy work.

## Context

The first full measurement ([sync.md](../benchmarks/sync.md),
[tempo.md](../benchmarks/tempo.md), [identity.md](../benchmarks/identity.md)) passed
identity and failed sync and tempo. In onset mode 0 the band held no tempo: Ode to Joy,
charted at 160 bpm, came out near 79 bpm, and no tempo factor passed in either mode.
Four causes were found.

1. MRT2 at v2.0.3 has no tempo input. Each generated frame is conditioned on 12 style
   tokens, one state per MIDI pitch and a drum flag (`MODEL.md`,
   `populate_condition_tokens` in `core/src/mlx_engine.cpp`). The pitch state is the only
   channel that can carry a pulse.
2. In onset mode 0 every sounding pitch is token 3, "on, the model places onsets". A
   chord held for a bar says nothing about where the beats fall, so the model chooses
   its own tempo. Onset mode 1 marks the first frame of a note as an onset (token 2) and
   the rest as continuation (token 1), but the scheduler sent each chord as one long note,
   so mode 1 also told the model to hold the bass, not to strike it on the beat.
3. The measurement scored beats that were never played. Three bundled charts open with
   a pickup shorter than a beat (Greensleeves, Oh! Susanna, Londonderry Air), so their
   counted beats sit at fractional chart positions, yet the scheduler logged and the
   script scored the integer ones. The last counted beat also falls after the recording
   stops.
4. Takes were recorded while other work loaded the Mac. When the engine cannot generate a
   40 ms frame in time the audio callback plays silence and the tap records it. Many
   sync and identity takes contain such gaps; a gap inside a chord change hides the
   change from the detector and counts as a miss.

## Decision

- The band ships in onset mode 1 (`SHIPPED_ONSET_MODE` in `session.py`), sent to the
  engine on every connect. The scheduler strikes the chord's bass on every counted beat
  and releases it 60 percent of the way to the next, so each beat starts with an onset
  token; the upper chord tones and the melody keep their own note lengths. This is the
  fallback the pitch-state mapping in the design reserved for a failed sync gate.
- A first section shorter than a bar is a pickup. `Chart.beat_phase` puts the counted
  beats on the grid that starts at its end. The pulse, the take's beat log and the tempo
  score all use that grid.
- The tempo score ignores counted beats after the recording ends.
- The tempo reference is calibrated on each chart's own FluidSynth render. Beat This
  counts Ode to Joy at 160 bpm and Oh! Susanna's 2/4 in half notes even there, so scored
  against the chart's counted beats the charts themselves would fail at every factor and
  the gate would measure the tracker, not the band. `scripts/measure_tempo.py` runs Beat
  This on `fixtures/<chart>.wav`, keeps the beats it finds as chart positions and places
  them in each take through the take's beat log. The F-measure floor and the 5 percent
  tempo tolerance are unchanged; the expected tempo is the render's tracked tempo times
  the factor. A render scored as a take against its own reference scores 1.0, which a
  test checks on every bundled chart. The gate is not loosened to accept any half or
  double tempo: a band that plays at a level other than the one the tracker reads on the
  chart still fails.
- The sync score uses the chord detector on the scheduler's beat log, not a beat
  tracker, so it needs no such calibration.
- Each take records the frames the engine dropped while it played (`dropped_frames`).
  `scripts/record_takes.py` deletes a take with any and reports it as failed, so a
  rerun records it again.

## Consequences

- Onset mode 0 stays in the tempo protocol as the comparison.
- Fixtures are 30 s long, so beats of longer charts past 30 s have no reference and
  are not scored.
- The published sync and tempo results predate this change and are kept as measured.
  They are replaced only by a complete rerun with no dropped frames.
- On a busy Mac `record_takes.py` now fails takes instead of measuring the load. A gate
  run needs the machine to itself.
- The metronome click still accents integer chart positions, so after a pickup it is
  off the counted beat.
