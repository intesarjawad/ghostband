# 0013: Cache the parent revision required by pinned SheetSage2

Status: accepted

## Context

SheetSage2 at `b83c3d79a6d98bcffaf8f9ed112c90aa887887bc` requires
MERT-v2-FullSong at `d8ba1c745e733b3908ce6ad16ebeb17ac7600a42` through
its `base_model_revision`. Its loader resolves that exact revision and checks
the parent code and weight hashes before merging adapters.

SheetSage2 and MERT-v2 weights are licensed CC-BY-NC-4.0, for non-commercial
use only. The respective weight files contain 228,738,564 and 2,529,812,848 bytes.

## Decision

I cache MERT-v2-FullSong at `d8ba1c745e733b3908ce6ad16ebeb17ac7600a42`.
I need the extractor to run exactly as pinned: substituting another parent
can silently change the embeddings. I keep the SheetSage2 configuration and
loader unchanged and verify every cached code, configuration and weight file
against recorded SHA-256 hashes before loading executable model code.

The pinned weight SHA-256 values are:

- SheetSage2: `b235f68091a5f5b644000f2b5acb57d1e70432aca2b34ab1b9cf27236e1f4274`
- MERT-v2: `e6dd2ab187d6dd62b6521cd7d8f932e237acf0c5757745a7232082e28391350d`

## Consequences

I fetch the pinned files explicitly before extraction. During extraction I set
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and `local_files_only=True`.
Missing or corrupt files fail locally with an actionable error.

I decode WAV files with soundfile, mix channels to mono and resample to the
processor's sampling rate before passing a contiguous float32 array to
`transcribe`. This avoids the pinned loader's file and encoded-byte FFmpeg paths.
I use CPU by default and compare MPS charts against CPU before relying on MPS.

I have verified the pinned file metadata and implemented tests without weights.
Full offline transcription, absence of FFmpeg access during inference, fixture
wall times and CPU versus MPS equivalence remain unverified until the weights
are available. The extraction commands and timing definitions are documented
in [the extraction package](../../extract/sheetsage2/README.md).
