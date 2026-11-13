# 0004: Default to the permissive extractor and offer SheetSage2 only on a measured gain

Status: proposed; the measurement has not run and the choice is not yet confirmed

## Context

Two extractors turn a recording into a chart. The permissive extractor's
models and code allow any use. SheetSage2 and MERT-v2 weights are licensed
CC-BY-NC-4.0, for non-commercial use only, and need a large separate download
([ADR 0013](0013-sheetsage2-parent-cache.md)). A default that carries a
non-commercial licence and a multi-gigabyte download needs a measured reason.

## Decision

The permissive extractor is the default. SheetSage2 is offered during setup
only when it is measurably better, and the measure is chord majmin on AAM, the
share of reference chord time covered by the same major or minor triad.
Both extractors run on the same AAM tracks through
`ghostband.evaluation.extraction`. For each track both extracted, the
difference is SheetSage2's score minus the permissive extractor's. The offer is
enabled only if the 95% percentile bootstrap interval of that per-track
difference lies entirely above zero. A mean above zero with an interval that
reaches zero does not enable it.
`uv run python -m scripts.measure_sheetsage2_gain` computes the table below
from `extraction_tracks.csv` and writes the difference, its interval and the
number of paired tracks to `docs/benchmarks/claims.json` as `sheetsage2_gain`,
or `null` when no track has both scores.

I reject offering SheetSage2 on a higher mean alone: AAM is artificial audio
and a small gain with a wide interval is within what resampling tracks
produces. I reject dropping the offer without measuring, because it may be
clearly better on chords.

## Measurement

Not measured. The table stays empty and the offer stays off until the script
has run on the fetched AAM tracks, and this record is updated with its output.

| Extractor | Paired tracks | AAM chord majmin (95% interval) |
| --- | ---: | --- |
| permissive | not measured | not measured |
| sheetsage2 | not measured | not measured |
| sheetsage2 minus permissive | not measured | not measured |

Offer: not enabled. No track has a chord score from both extractors.

## Consequences

When the verdict line above reads `Offer: enabled.`, it quotes the difference
and its interval from `sheetsage2_gain`. The test in
`conductor/tests/test_sheetsage2_offer.py` fails if the line disagrees with
`claims.json`. The measurement says nothing about recorded music, since AAM
audio is rendered from instrument samples. Even an enabled offer is opt-in,
with the non-commercial licence stated before the download.
