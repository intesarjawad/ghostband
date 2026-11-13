# 0012: Use the full MRT model set until the slim set is proven

Status: conservative fallback; runtime verification blocked. The first-run
defaults and their timing live in "First run" below.

## Context

The original verification sought to establish whether text-only playback works without
`resources/musiccoca/music_encoder.tflite`,
`resources/spectrostream/spectrostream_encoder.mlxfn` and
`resources/spectrostream/encoder.safetensors` in a directory filled per file.
The original verification lacked a working native toolchain, so it obtained no ten-second
playback or zero-underrun result. See [upstream-check.md](../benchmarks/upstream-check.md).

## Decision

Apply the conservative download fallback: full resources plus
`mrt2_small`, through the official `magenta-rt[mlx]==2.0.3` CLI. Its positional
`NAME` accepts `mrt2_small`; no menu-driving stdin is needed. Run both
`mrt models init --download-path <repo-local-cache>` and
`mrt models download mrt2_small --download-path <repo-local-cache>` with
stdin closed. Do not write into the user's Documents directory.

The CLI has no revision flag and downloads the current HF head. The script
first requires head to equal the pinned revision
`010aa0dcb0dfd27b24f0ad07b4dad63e8f9521cc`, then verifies every downloaded
resource and small-model file against that revision's LFS SHA-256 metadata.
A moved head or checksum mismatch is a failure, not permission to change pins.

## Consequences

The verification report records 12 files totaling 1,840,072,891 bytes, all
verified against the pinned LFS hashes.
The original verification removed the large weights after checking. A later build restored the full
set through the same official CLI and verified all 12 files again against
the pinned revision's SHA-256 metadata, totaling 1,840,072,891 bytes. They
remain in ignored `.cache/upstream-0.2/full-model-set` for the next build.
Use `GHOSTBAND_MODEL_DIR="$PWD/.cache/upstream-0.2/full-model-set"` or the
offline CLI's `--model-dir` option to select that root, which contains
`resources/` and `models/mrt2_small/`. Without an override the CLI uses the
upstream `~/Documents/Magenta/magenta-rt-v2` location. This development CLI
does not implement first-run setup.

A later build built and signs the native engine successfully, but model
import in this execution environment fails with an unknown exception.
An independent Metal probe exposes zero devices and no default device.
No offline WAV or new slim-set evidence was obtained. Later setup work must
use the full set until a real slim-set run proves text encoding and ten
seconds of nonblocking playback with zero underruns. A successful download
or native build alone cannot satisfy that runtime check.

## Local Metal workaround for the native build

I verified that `xcrun -f metal` and `xcrun -f metallib` resolve to
working binaries in the downloaded cryptex `Metal.xctoolchain`. A probe
kernel compiled directly. SDK-qualified compiler invocation still fails;
completing Xcode first launch requires administrator access.

Resolve the compiler and linker independently, using `find_program`, then
unqualified `xcrun -f`, with `xcrun -sdk macosx -f` as the lookup fallback.
`GHOSTBAND_METAL_COMPILER` and `GHOSTBAND_METAL_LINKER` are overridable CMake
FILEPATH cache entries. An explicit nonexistent path fails configuration.
After a toolchain update, clear stale cached paths with CMake's
`-U 'GHOSTBAND_METAL_*'` option to rediscover the mounted tools.

Use the resolved tools for MLX's Metal version detection, kernel compilation,
library linking and preamble generation. A FetchContent patch adapts only
the fetched MLX build scripts under `engine/build/_deps`; it does not modify
the pinned upstream submodule. Full Xcode and a functioning Metal Toolchain
remain the user requirements. SDK version detection, target macOS version,
Metal flags, required kernels and post-build signing remain unchanged.
No GPU check is bypassed and no CPU fallback is introduced.

The restricted local environment also denies writes to the compiler's
default module cache. Setting `CLANG_MODULE_CACHE_PATH` did not redirect
Metal's cache. For this build only, `/tmp/ghostband-metal` wraps the compiler
reported by `xcrun -f metal`, adding
`-fmodules-cache-path=/tmp/ghostband-metal-module-cache`. Configure with
`-DGHOSTBAND_METAL_COMPILER=/tmp/ghostband-metal` to use it. The wrapper is a
local build artifact, not a shipped requirement; normal builds use the
resolved binary directly. The native build succeeded and both the library and
executable passed
`codesign --verify --verbose=2`. The offline run failed at model import; a
Metal probe reported zero devices and no default device. See
[ADR 0010](0010-vendor-magenta-realtime.md) for the recorded output.

The section above covers the default-model fallback alone. The broader
first-run defaults and previews are recorded under "First run" below.

## First run: what the harness times

`conductor/ghostband/first_run.py` installs the extractor environments, the
engine and the weights in that order, so the interface can open and take a
dropped track before the weights arrive. `scripts/cold_start.py` drives one
first run on a clean account and prints one stage per checkpoint:

| Stage | Marked when | Budget |
| --- | --- | --- |
| install | the route's install command exits; the local wheel is installed by setup, so its row is printed but not timed | 900 s |
| ready-preview | the page and its first state are served | 5 s |
| chart | the dropped track's chart is back | 120 s |
| ready-live | the conductor reports an online engine | 1800 s |
| playing | the transport's first clock arrives | 5 s |

The dropped track is the first mix of the AAM lock, fetched before the run, so
no stage contains a download the harness did not time itself. A second drop
after the install repeats the last two stages as `chart-2` and `playing-2`, with
no budget, to show what a warm account gets. `--route` chooses the install
command and the launched program, and `--budget NAME=SECONDS` overrides one
budget for a slower machine.

There is no browser in this harness: `ready-preview` is a served page plus a
first state, the moment the interface can show its take. The preview is meant
to play while the weights arrive, so the report also carries
`preview_before_download`, which compares that moment with the first moment
`first_run.py`'s progress file reaches `ready`. Download bytes come from that
file and the seconds from the harness's own sampling, so the rate is the one
that file reports over the window this run observed. A stale progress file left
by an earlier setup is ignored rather than credited to the new run.

## Consequences

Stage numbers belong to one machine and one network: the harness writes them
where `--report` points and commits nothing. The budgets above are targets, not
measurements; a run that misses one fails with the stage named. The offline
check stays committed as `conductor/ghostband/offline_check.json` because it is
the evidence that a released wheel stays on loopback, and it is rerun in the
same account as the timed run.


Setup also installs the Beat This `final0` checkpoint, pinned by size and SHA-256 in
`setup_models.py`, first in the default set: audio import finds beats with it, the
extractor runs offline and never fetches it itself, and it is small enough to arrive
before the model files.
