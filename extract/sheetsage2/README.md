# SheetSage2 extraction

SheetSage2 and MERT-v2 weights are **CC-BY-NC-4.0, for non-commercial use only**.
Fetching requires explicit acceptance. No account or paid service is required.
Audio stays local.

I pin SheetSage2 to `b83c3d79a6d98bcffaf8f9ed112c90aa887887bc` and its required
MERT-v2-FullSong parent to `d8ba1c745e733b3908ce6ad16ebeb17ac7600a42`.
The weight files are 228,738,564 and 2,529,812,848 bytes respectively. The file
manifest records SHA-256 checksums for code, configuration, licences and weights.
Fetching and offline loading both verify them.

From the repository root, install the locked environment and fetch once:

```sh
UV_CACHE_DIR=/private/tmp/ghostband-uv-cache uv sync --frozen --project extract/sheetsage2
UV_CACHE_DIR=/private/tmp/ghostband-uv-cache uv run --frozen --project extract/sheetsage2 python extract/sheetsage2/run.py fetch --cache-dir models/sheetsage2 --accept-noncommercial-license
```

Extraction never fetches missing resources. It rejects audio longer than 360
seconds with a reason to use the permissive extractor. WAV decoding uses
soundfile; mono float32 samples are resampled to the processor rate and passed
as an array to `transcribe`. Rendering is disabled and inference uses float32
on CPU by default.

Extract a validated Chart, printed as one JSON object on stdout:

```sh
UV_CACHE_DIR=/private/tmp/ghostband-uv-cache uv run --offline --frozen python -m ghostband.extractors.sheetsage2 fixtures/greensleeves.wav --cache-dir models/sheetsage2 --output-dir /private/tmp/ghostband-sheetsage2/greensleeves
```

The isolated `run.py transcribe` command writes upstream events and MIDI plus
`timing.json`. The conductor converts these to `chart.json`: chord boundaries
snap to the nearest beat, compound eighth-note meters count dotted-quarter
pulses, and the melody voice with more completed notes inside the recording is
selected. Ties select vocal. Missing beats, meter, key or MIDI fail explicitly.
The model writes a pickup as a short opening measure with its own meter. The
chart keeps the meter that follows and counts the pickup from beat 0, so the
first downbeat can fall on a fractional beat. Any other change of meter fails.
Imported rights remain unknown. Golden fixture charts supply reference titles
and never substitute for model output.

Measure every fixture and write `docs/benchmarks/extraction.md`:

```sh
UV_CACHE_DIR=/private/tmp/ghostband-uv-cache uv run --offline --frozen python -m ghostband.extractors.sheetsage2_benchmark --cache-dir models/sheetsage2 --output-dir /private/tmp/ghostband-sheetsage2-benchmark --compare-mps
UV_CACHE_DIR=/private/tmp/ghostband-uv-cache uv run --offline --frozen python -m pytest -m models -k sheetsage2 -q
```

Each device and fixture starts a fresh process. Load includes checksum
verification and model loading; preparation includes decoding and resampling;
inference includes transcription and upstream exports, with MPS synchronized.
Total is their sum and excludes environment startup and chart conversion. The
report records the ratio to recording duration and flags totals above 3x.
MPS comparison requires exact Chart equality and fails after saving differences.
Omit `--compare-mps` for CPU measurements alone.

The measured run is recorded in `docs/benchmarks/extraction.md`. It shared the
machine with other work, so its timings are upper bounds. Tests that load the
weights are marked `models`; ordinary checks use local synthetic model outputs
and fixture audio without loading weights. The marked test rejects network
connections, decoder calls, FFmpeg libraries and child processes during
transcription.
