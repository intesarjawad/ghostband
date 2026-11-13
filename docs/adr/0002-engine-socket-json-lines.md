# 0002: Control the engine over a Unix socket with JSON lines

Status: accepted

## Context

I need a local boundary between the Python conductor and the native engine
that carries validated commands, status and audio tap events. Socket handling
must stay outside the AVFoundation callback. In `engine/src/session.cpp`, the
control loop polls sockets while `engine/src/audio_output.mm` consumes audio
independently.

## Decision

I use a Unix stream socket with newline-delimited JSON and the versioned
command types in `engine/src/protocol.h`. I validate fields before dispatch
and use `hello` to expose the protocol version. I allow a single connected
controller, require a user-owned directory with mode `0700`, and set the socket
to mode `0600`.

I bound input lines to `64 * 1024` bytes, as defined by `kMaxLineBytes`, so a
complete style embedding fits without allowing unbounded input. I retain the
`1024 * 1024` byte output limit in `session.cpp`; a slow reader is disconnected
instead of allowing its queue to grow indefinitely. Oversized input is
rejected and discarded through the next newline. Disconnect invokes the
controller's cleanup callback.

I reject an in-process Python binding because it couples native lifetime and
failure handling to the conductor. I reject TCP because this boundary needs
local filesystem permissions rather than a network listener. I reject a
binary protocol because the current typed commands fit inspectable JSON;
I have not measured a throughput advantage for a binary alternative.

## Consequences

I accept JSON encoding and framing overhead. Stream reads can split or combine
messages, so I frame by newline rather than by read boundaries. Status reports
carry `played_samples`; their receipt time is not the audio clock. The
`100` millisecond status interval in `session.cpp` is a polling policy, not a
measured delivery guarantee.

I keep protocol validation tests and corpus replay separate from live audio
verification. I have not measured transport latency or maximum sustainable
tap throughput, and I make no performance claim for this transport.
