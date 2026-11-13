# 0001: Separate the engine, conductor and browser interface

Status: accepted

## Context

I need native audio playback and model inference alongside musical scheduling,
file import and an interactive interface. In `engine/src/audio_output.mm`, the
AVFoundation callback consumes `magentart::core::RealtimeRunner` audio and
advances `played_samples`. Scheduling must follow that audio clock rather than
browser timers or the arrival time of socket messages.

I also need separate extraction environments: the permissive extractor and
SheetSage2 have different locked dependencies, and the latter carries
non-commercial terms. I cannot treat them as interchangeable installations.

## Decision

I separate the native engine, Python conductor and browser interface. I keep
C++ and Objective-C++ responsible for inference, audio output and the sample
clock. I keep chart interpretation, scheduling and extraction orchestration in
the uv-managed Python conductor, and use the TypeScript interface as a view
and control surface. Extraction runs in separate subprocess environments.

I reject putting Python scheduling or browser callbacks in the audio render
path because their execution must not determine when audio is consumed. I
reject rewriting the upstream runner because it already supplies audio ring
buffers, inference scheduling, prompt blending and MIDI gating. I reject a
shared extractor environment because dependency and licence boundaries need
to remain explicit.

## Consequences

I accept process lifecycle management and explicit protocol boundaries in
exchange for keeping audio execution separate from application logic. The
engine reports sample positions; the conductor must schedule against those
positions. I retain separate extractor lockfiles and keep extraction outside
the audio callback.

I have not measured the latency cost of the process split against an in-process
binding. This decision rests on clock ownership and dependency isolation, not
a measured speed advantage. [ADR 0002](0002-engine-socket-json-lines.md) records
the engine transport.
