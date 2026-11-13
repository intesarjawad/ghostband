# 0008: Use the upstream recorder with its five-minute cap

Status: accepted

## Context

I need to save the generated band audio without writing files from the audio
callback. `RealtimeRunner::start_recording` in
`third_party/magenta-realtime/core/src/realtime_runner.cpp` allocates stereo
buffers for `5 * 60 * 48000` samples per channel. Its audio reader copies into
those buffers only when the complete callback block fits.

## Decision

I use the upstream recorder through `engine/src/recording.cpp` and accept its
five-minute capacity at 48 kHz. I open the destination before arming recording,
then stop capture and copy the retained samples into a stereo float WAV on
the control thread. In `engine/src/session.mm`, I pause audio callbacks before
stopping and copying a recording, then resume playback if it was active.

I reject file writes in the render callback because filesystem work has no
bounded completion time. I reject a separate streaming recorder and queue
because the upstream bounded capture already supplies the required take
storage; a streaming design would add overflow, shutdown and writer-lifetime
rules. I have not measured a performance or memory advantage for either
alternative.

## Consequences

I record the runner's output before reference audio and metronome mixing in
`RenderExtensions::render`. The WAV therefore contains the generated band,
not the complete audible reference mix.

I do not get unlimited recording or automatic rollover. Once the buffer lacks
room for a complete callback block, that block is not retained; the recorder
does not automatically clear its active state at capacity. I preserve the
recorded count when stopping and reset it when starting another take.

I use `engine/tests/recording_bench_test.cpp` as the deterministic evidence:
it asserts a twenty-second capture of `960000` sample frames, partial and
empty takes, lifecycle errors and the upstream five-minute cap. Its unloaded
runner does not establish recording quality with live model output. I have
not measured the playback interruption caused by stopping and exporting a
take, or peak resident memory during capture.
