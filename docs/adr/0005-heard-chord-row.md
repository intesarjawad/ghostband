# 0005: Name the played chord from the engine tap, not from the chart

Status: accepted; live audio verification pending

## Context

I need to know whether the band plays the chart, not only whether the
scheduler asked for it. The chart and the scheduler's commands show intent. A
model that ignores or blurs a voicing would still match them. In
`conductor/ghostband/session.py`, the chart's expected symbol and what was
heard must be reported side by side for each beat.

## Decision

I name one triad per completed beat from the mono audio the engine taps before
band gain, reference mixing and clicks (`engine/src/audio_tap.h`). The tap
carries blocks of 4800 float32 samples as base64 JSON events.
`conductor/ghostband/heard.py` validates each block, requires the next block
to follow the previous sample and restarts its window after a gap or a new
take. It uses only frames that lie at least one block inside the buffer, so
the row trails the playhead by about one beat plus at most one block.

`conductor/ghostband/evaluation/chord_detector.py` does the naming. It never
sees a chart. It averages constant-Q chroma over a beat and picks the best of
24 major and minor triad templates, or `N.C.` when mean chroma falls below a
silence floor. `majmin` reduces a chart symbol to the same vocabulary so the
two sides compare directly. The same detector serves offline measurement and
the live row.

I reject comparing the chart with the scheduler's output as a check, because
it cannot fail when the audio is wrong. I reject a richer chord vocabulary
because template matching on a single beat cannot separate sevenths and
extensions reliably, and I have not measured a better detector.

## Consequences

I accept that the row reports major and minor triads only: a chart's seventh,
suspended or augmented chord is compared by its triad. A beat with no complete
span of audio produces no event. A dropped tap block discards the partial
beats in the window rather than naming a chord from a gap. Detection runs on
the conductor, so analysis load does not touch the audio callback.

The tests cover tap decoding, beat completion, the triad detector and a
session replay of fixture audio. Agreement between the heard row and the chart for real model output
is not measured here.
