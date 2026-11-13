# 0010: Vendor the pinned Magenta RealTime engine

Status: accepted; live runtime verification pending

## Context

I need the upstream real-time runner without maintaining a copy of its model,
ring-buffer and inference implementation. The existing submodule pins
`magenta/magenta-realtime` at tag `v2.0.3`, commit
`694a545e4ba0b88bf1150137b129582166d3e07f`.

## Decision

I retain the recursive git submodule at `third_party/magenta-realtime` and
leave its sources unchanged. In `engine/CMakeLists.txt`, I add the upstream
root with `EXCLUDE_FROM_ALL` and link `magentart::core`. I reject adding only
the core directory and recreating its dependency bootstrap because the root
integration already builds the standalone engine without building example
applications. I reject copying upstream sources or following a moving branch
because I need a reviewable dependency revision and explicit upgrades.

I keep CMake at `3.27.9`; the engine configuration rejects versions at or
above `3.28`. I preserve upstream's MLX `v0.31.1` pin. The parent FetchContent
declaration applies `engine/cmake/PatchMlxMetal.cmake` to fetched MLX build
scripts so Metal compiler and linker discovery work without modifying the
submodule. I retain SDK selection, deployment settings and kernel build
dependencies. Unexpected command forms fail configuration rather than silently
changing the patch's scope. [ADR 0012](0012-default-model-set-fallback.md)
contains the existing Metal discovery details.

## Consequences

I accept recursive checkout and upstream dependency configuration, including
fetches for host application headers that the standalone engine does not use.
A dependency upgrade requires reviewing the pin and the build adapter together.
I have not measured build time or binary size against a copied-source or
core-only integration.

I retain the recorded native build result: `Built target ghostband-engine`,
with the copied Metal library and executable both passing signature
verification. This establishes build integration, not working inference.
The recorded offline run failed at model import with
`[MLXEngine] Failed to load model: unknown exception`; the independent Metal
probe reported `Metal devices: 0` and `Default Metal device: none`.
I have no acceptance WAV or live underrun measurement from that environment.
The normal check target does not build or run the model-backed engine.
