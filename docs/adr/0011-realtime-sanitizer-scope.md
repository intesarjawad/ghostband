# 0011: Keep real-time sanitizer coverage explicit

Status: implemented; live verification pending

## Context

I need to detect unsafe operations reached during audio rendering separately
from data races and protocol memory errors. Apple clang lacks the required
RTSan and libFuzzer runtimes. The existing verification records no successful
RTSan probe because the pinned Homebrew LLVM `23.1.5` compiler was absent.

## Decision

I mark `render_audio` in `engine/src/audio_output.mm` with
`[[clang::nonblocking]]` and call it from the AVAudioSourceNode block.
`GHOSTBAND_SANITIZE=realtime` instruments the engine libraries and executables,
including this Objective-C++ translation unit. I leave upstream model libraries
uninstrumented. Intercepted unsafe operations reached within the annotated
boundary remain subject to RTSan; GPU inference and AVFoundation internals
outside that boundary are outside this check's scope.

I use `make rtsan` for the scripted `120` second live session defined in the
Makefile. `scripts/run_rtsan.sh` sets `RTSAN_OPTIONS=halt_on_error=1`, requires
the session and engine to exit successfully, and rejects sanitizer diagnostics.
The session exercises slider changes, note resets, reference switches and tap
capture. I represent loop operations through note-offs, note-ons and scheduled
reference seeks because the protocol has no loop command.

I reject treating Apple clang's ThreadSanitizer run, protocol fuzzing or
simulation as a substitute for RTSan: they check different failure modes.
I also reject treating an uninstrumented upstream library as fully checked.
If the pinned compiler rejects Objective-C++ instrumentation but accepts the
annotated C++ function, I will move the render body into a sanitized `.cpp`
file called by an unsanitized Objective-C++ block. I have not applied this
fallback because that compiler failure has not been reproduced.

## Consequences

I retain client timestamps, status gaps, underrun deltas and tap sample
positions in the live trace, omitting PCM. Client send times do not establish
server execution times or prove that a nearby command caused an underrun.
A clean sanitizer session would not establish audio deadline compliance.

I keep separate checks: `make tsan` for the device-free SPSC stress test,
`make fuzz-smoke` for `60` seconds of libFuzzer with ASan and UBSan, and
`make fuzz` for `600` seconds. These durations come from the Makefile, not
completed timing measurements. I have no completed live RTSan result or
sanitizer overhead measurement; the normal check target's ThreadSanitizer
coverage does not close that gap.
