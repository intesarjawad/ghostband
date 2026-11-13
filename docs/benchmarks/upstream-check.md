# Historical upstream verification

Run: `2026-11-05T11:12:02Z`

Command: `UV_CACHE_DIR=/tmp/ghostband-uv bash scripts/verify_upstream.sh`

Result: exit 1, 12 PASS and 9 FAIL. This historical result has been superseded by later development; it does not describe current check coverage. The full MRT
download fallback was applied and verified, but native runtime checks and
SheetSage2 transcription remain unverified. Missing toolchains were recorded
without system-wide installation.

| Outstanding check | Evidence and disposition |
| --- | --- |
| CMake, hello WAV, integration A/B | CMake 3.27.9 absent from the offline uv cache; [ADR 0010](../adr/0010-vendor-magenta-realtime.md). Neither integration option selected. |
| Ten-second text-only playback | Native build unavailable; full-set fallback downloaded and hashed, 12 files totaling 1,840,072,891 bytes; [ADR 0012](../adr/0012-default-model-set-fallback.md). No underrun measurement. |
| RTSan C++ and ObjC++ | Homebrew clang absent; [ADR 0011](../adr/0011-realtime-sanitizer-scope.md). The C++ isolation fallback cannot yet be applied. |
| SheetSage2 offline parent and array transcription | Pinned SheetSage2 requests a different MERT revision than the revision this repository caches; [ADR 0013](../adr/0013-sheetsage2-parent-cache.md). The parent-cache strategy remains undecided. FFmpeg fallback does not address this error. |
| First CI Metal build and live RTSan session | The hosted repository and later engine implementation are required. Local Metal lookup passes. |

The sections below are the script's command evidence.

## PASS: Disk space: at least 30 GB free

```text
Filesystem      Size    Used   Avail Capacity iused ifree %iused  Mounted on
/dev/disk3s5   460Gi   335Gi    69Gi    83%    3.6M  725M    0%   /System/Volumes/Data

```

Checked before downloads; no model downloads go outside the repo-local .cache.

## PASS: Pinned engine tag and commit

```text
694a545e4ba0b88bf1150137b129582166d3e07f
v2.0.3

```

See ADR 0010.

## PASS: Metal compiler available locally

```text
/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/metal

```

The first CI build remains pending publication. If metal fails there, run xcodebuild -downloadComponent MetalToolchain. See ADR 0010.

## FAIL: CMake 3.27.9 available without installation

```text
error: No solution found when resolving tool dependencies
  cause: Because cmake was not found in the cache and you require cmake==3.27.9, we can conclude that your requirements are unsatisfiable.

hint: Packages were unavailable because the network was disabled. When the network is disabled, registry packages may only be read from the cache.

```

No system-wide installation attempted. Native build checks remain blocked; no substitute CMake version is approved. See ADR 0010.

## PASS: Permissive extractor uv lock resolution

```text
Using CPython 3.11.18
Resolved 55 packages in 3ms

```

uv lock --check verifies resolution without changing the existing lockfile.

## PASS: SheetSage2 extractor uv lock resolution

```text
Resolved 36 packages in 3ms

```

uv lock --check verifies resolution without changing the existing lockfile.

## PASS: Official MRT tools environment

```text
Checked 104 packages in 9ms

```

Uses the existing locked magenta-rt[mlx]==2.0.3 environment.

## PASS: Non-interactive mrt model selection

```text
Usage: mrt models download [OPTIONS] [NAME]

  Download an exported model by NAME.

  If NAME is omitted an interactive picker lists all available models.

Options:
  --download-path PATH  Root directory for downloaded assets.  [default:
                        ../../../../Documents/Magenta/magenta-rt-v2]
  --source [hf|gcs]     Download source: 'hf' (HuggingFace) or 'gcs' (Google
                        Cloud Storage).  [default: hf]
  --help                Show this message and exit.

```

NAME=mrt2_small avoids the picker. The download below runs with stdin closed.

## PASS: Prompt status meanings from pinned source

```text
2188:      prompt_statuses_[i] = 0; // idle
2117:        prompt_statuses_[i] = 1; // processing
2168:        prompt_statuses_[i] = 1; // processing
2115:        prompt_statuses_[i] = 2; // success
2122:          prompt_statuses_[i] = 2; // success
2146:          prompt_statuses_[i] = 2; // success
2164:        prompt_statuses_[i] = 2; // success
2181:        prompt_statuses_[i] = 2; // success
2126:          prompt_statuses_[i] = 3; // error
2173:          prompt_statuses_[i] = 3; // error

```

0 idle, 1 processing, 2 success, 3 error. Use the wording processing, not fetching. See ADR 0010.

## PASS: SheetSage2 runtime environment

```text
Checked 35 packages in 3ms

```

Uses Python 3.11 and the existing frozen requirements.

## PASS: HF revisions and gated flags for all three repos

```text
google/magenta-realtime-2 sha=010aa0dcb0dfd27b24f0ad07b4dad63e8f9521cc gated=False
m-a-p/SheetSage2 sha=b83c3d79a6d98bcffaf8f9ed112c90aa887887bc gated=False
m-a-p/MERT-v2-FullSong sha=7001f7657276238629e61c7746ce70735e0604c0 gated=False

```

Anonymous requests to the pinned HF revisions; no sign-in required.

## PASS: Pinned SheetSage2 and MERT cache with SHA-256 verification

```text

Fetching 34 files:   0%|          | 0/34 [00:00<?, ?it/s]
Fetching 34 files:  24%|       | 8/34 [00:00<00:02, 12.53it/s]
Fetching 34 files:  47%|     | 16/34 [00:01<00:01, 13.50it/s]
Fetching 34 files:  71%|   | 24/34 [00:01<00:00, 15.65it/s]
Fetching 34 files:  94%|| 32/34 [00:01<00:00, 17.67it/s]
Fetching 34 files: 100%|| 34/34 [00:10<00:00,  3.19it/s]
Cached and checked pinned files: m-a-p/SheetSage2 b83c3d79a6d98bcffaf8f9ed112c90aa887887bc

Fetching 7 files:   0%|          | 0/7 [00:00<?, ?it/s]
Fetching 7 files:  14%|        | 1/7 [00:00<00:01,  4.47it/s]
Fetching 7 files: 100%|| 7/7 [01:33<00:00, 13.34s/it]
Fetching 7 files: 100%|| 7/7 [01:33<00:00, 13.34s/it]
Cached and checked pinned files: m-a-p/MERT-v2-FullSong 7001f7657276238629e61c7746ce70735e0604c0
SheetSage2 requests MERT parent revision: d8ba1c745e733b3908ce6ad16ebeb17ac7600a42
Cached MERT revision: 7001f7657276238629e61c7746ce70735e0604c0

```

Downloads remain repo-local and are removed at the end of this run.

## FAIL: SheetSage2 resolves MERT with HF_HUB_OFFLINE=1

```text
  File "./.cache/upstream-0.2/models.py", line 101, in <module>
    {"metadata": metadata, "cache": sheet_cache, "offline": lambda: sheet_run("offline"),
  File "./.cache/upstream-0.2/models.py", line 101, in <lambda>
    {"metadata": metadata, "cache": sheet_cache, "offline": lambda: sheet_run("offline"),
                                                                    ^^^^^^^^^^^^^^^^^^^^
  File "./.cache/upstream-0.2/models.py", line 50, in sheet_run
    model = AutoModel.from_pretrained("m-a-p/SheetSage2", revision=PINS["m-a-p/SheetSage2"],
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./extract/sheetsage2/.venv/lib/python3.11/site-packages/transformers/models/auto/auto_factory.py", line 559, in from_pretrained
    return model_class.from_pretrained(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./.cache/upstream-0.2/huggingface/modules/transformers_modules/m-a-p/SheetSage2/b83c3d79a6d98bcffaf8f9ed112c90aa887887bc/modeling_sheetsage2.py", line 248, in from_pretrained
    resolved = cached_file(parent, filename, **parent_hub_args)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./extract/sheetsage2/.venv/lib/python3.11/site-packages/transformers/utils/hub.py", line 446, in cached_file
    raise EnvironmentError(
OSError: We couldn't connect to 'https://huggingface.co' to load this file, couldn't find it in the cached files and it looks like m-a-p/MERT-v2-FullSong is not the path to a directory containing a file named configuration_mert2.py.
Checkout your internet connection or see how to run the library in offline mode at 'https://huggingface.co/docs/transformers/installation#offline-mode'.

```

No revision change applied. The parent-cache strategy remains undecided. See ADR 0013 for the parent-revision mismatch.

## FAIL: SheetSage2 transcribe(array) without FFmpeg libraries

```text
  File "./.cache/upstream-0.2/models.py", line 101, in <module>
    {"metadata": metadata, "cache": sheet_cache, "offline": lambda: sheet_run("offline"),
  File "./.cache/upstream-0.2/models.py", line 102, in <lambda>
    "array": lambda: sheet_run("array"), "full": full_set}[sys.argv[1]]()
                     ^^^^^^^^^^^^^^^^^^
  File "./.cache/upstream-0.2/models.py", line 50, in sheet_run
    model = AutoModel.from_pretrained("m-a-p/SheetSage2", revision=PINS["m-a-p/SheetSage2"],
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./extract/sheetsage2/.venv/lib/python3.11/site-packages/transformers/models/auto/auto_factory.py", line 559, in from_pretrained
    return model_class.from_pretrained(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./.cache/upstream-0.2/huggingface/modules/transformers_modules/m-a-p/SheetSage2/b83c3d79a6d98bcffaf8f9ed112c90aa887887bc/modeling_sheetsage2.py", line 248, in from_pretrained
    resolved = cached_file(parent, filename, **parent_hub_args)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "./extract/sheetsage2/.venv/lib/python3.11/site-packages/transformers/utils/hub.py", line 446, in cached_file
    raise EnvironmentError(
OSError: We couldn't connect to 'https://huggingface.co' to load this file, couldn't find it in the cached files and it looks like m-a-p/MERT-v2-FullSong is not the path to a directory containing a file named configuration_mert2.py.
Checkout your internet connection or see how to run the library in offline mode at 'https://huggingface.co/docs/transformers/installation#offline-mode'.

```

If transcription reaches FFmpeg, the fallback is ffmpeg@6 6.1.6 and a subprocess-only DYLD_FALLBACK_LIBRARY_PATH. A parent-cache failure has no documented FFmpeg fallback. See ADR 0013.

## PASS: Full default-model fallback download

```text
  Downloading spm.model 
  Downloading text_encoder.tflite 

 Downloading resources/spectrostream 
  Downloading decoder.safetensors 
  Downloading encoder.safetensors 
  Downloading quantizer.safetensors 
  Downloading spectrostream_encoder.mlxfn 

 Init complete.

 Downloading model mrt2_small from HuggingFace  ./.cache/upstream-0.2/full-model-set/models/mrt2_small
  Downloading mrt2_small.mlxfn 
Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
  Downloading mrt2_small_state.safetensors 

 Model 'mrt2_small' downloaded.
Full-set fallback downloaded and SHA-256 verified: 12 files, 1840072891 bytes

```

Full resources plus mrt2_small downloaded through the official non-interactive CLI and verified against the pinned revision. No slim-set assumption is used. See ADR 0012.

## FAIL: hello_mrt2 build and 4 s WAV

```text
error: No solution found when resolving tool dependencies
  cause: Because cmake was not found in the cache and you require cmake==3.27.9, we can conclude that your requirements are unsatisfiable.

hint: Packages were unavailable because the network was disabled. When the network is disabled, registry packages may only be read from the cache.

```

Blocked by missing CMake 3.27.9. No system-wide installation or alternate version used. Full-set download is the conservative model fallback, not runtime evidence. See ADR 0010 and ADR 0012.

## FAIL: CMake integration option A then B

```text
error: No solution found when resolving tool dependencies
  cause: Because cmake was not found in the cache and you require cmake==3.27.9, we can conclude that your requirements are unsatisfiable.

hint: Packages were unavailable because the network was disabled. When the network is disabled, registry packages may only be read from the cache.

```

Blocked by missing CMake 3.27.9. No system-wide installation or alternate version used. Full-set download is the conservative model fallback, not runtime evidence. See ADR 0010 and ADR 0012.

## FAIL: Default model set: text prompt, 10 s, zero underruns

```text
error: No solution found when resolving tool dependencies
  cause: Because cmake was not found in the cache and you require cmake==3.27.9, we can conclude that your requirements are unsatisfiable.

hint: Packages were unavailable because the network was disabled. When the network is disabled, registry packages may only be read from the cache.

```

Blocked by missing CMake 3.27.9. No system-wide installation or alternate version used. Full-set download is the conservative model fallback, not runtime evidence. See ADR 0010 and ADR 0012.

## FAIL: Homebrew RTSan C++ nonblocking function

```text
scripts/verify_upstream.sh: line 36: /opt/homebrew/opt/llvm/bin/clang++: No such file or directory

```

Missing Homebrew clang is recorded without installation. No RTSan claim; see ADR 0011.

## FAIL: Homebrew RTSan Objective-C++ block

```text
scripts/verify_upstream.sh: line 36: /opt/homebrew/opt/llvm/bin/clang++: No such file or directory

```

If only ObjC++ fails, isolate render in a sanitized C++ function called by the unsanitized block. With no compiler this fallback cannot be built. See ADR 0011.

## FAIL: CI Metal build and 120 s live RTSan session

```text
CI MetalToolchain and 120 s engine RTSan session require the later engine/CI tasks; neither ran locally.

```

CI requires the hosted repository; this historical run predates the engine implementation. See ADR 0010 and ADR 0011. No GitHub action taken.


Failures: 9. A documented fallback is not a passing runtime measurement.
