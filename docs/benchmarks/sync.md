# Sync acceptance: unverified live corpus, all archived numeric results fail

Ghostband is experimental. No preset passes sync acceptance yet. Engine timings
are unpublished; see [the engine report](engine.md).

The threshold remains **at least 90% within 120 ms, median below 60 ms,
and a paired-null 95% interval above zero**. No preset is accepted.
The following corrected tables use the original 60 recordings from 10 November
2026, not new product output. Their dropped-frame counts are unknown; the
validated gate refuses them. See [ADR 0015](../adr/0015-sync-measurement-validity.md).

The scorer now searches from -250 ms as specified, rather than -125 ms.
Finding an earlier failed transition can displace a later apparent hit.
Acceptance thresholds are unchanged. Synthetic signed offsets are recovered
within 30 ms; offsets outside 120 ms fail and a +700 ms change is a miss.
The five FluidSynth renders score 51/56 changes within 120 ms (91.1%) with a
19.7 ms pooled median and three misses; this checks the detector, not the band.

Fresh Ode to Joy probes in shipped mode 1 produced dropped-frame counts
479 / 0 / 7 / 172 in preset-index order (Jazz / Folk / Bossa / Strings).
Only Folk was valid: 6/9 within 120 ms, median 47.0 ms, 1 miss, null 0%,
heard agreement 24/31. This single take is diagnostic, not the full gate.
The first Jazz reproduction also dropped 65 frames and is excluded.

Reproduce the historical diagnostic without updating claims:

```sh
uv run python scripts/measure_sync.py "$TAKES/sync" --diagnostic --report sync-diagnostic.md
```

For acceptance, omit `--diagnostic`. The command requires all 60 cells and
explicit zero dropped frames; unknown, incomplete or duplicate data exits 2
before writing reports or claims. Known gapped takes are refused in both modes.
The 32-bar duration requirement remains a separate protocol limitation noted
in ADR 0015. Deterministic checks:

```sh
uv run python -m pytest conductor/tests/test_measure_sync.py conductor/tests/test_chord_change.py -q
```

Diagnostic only: corpus completeness and zero dropped frames are not verified. The table verdicts compare numeric thresholds only; they are not acceptance. No claims were published.

| Chart | Preset | Takes | Changes | Skipped (shared / N.C. / outside) | Misses | Median error (ms) | Within 60 ms | Within 120 ms | Null within 120 ms |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| amazing-grace | Bossa nova / synthwave | 3 | 27 | 15 / 0 / 0 | 0 | 23 | 74.1% | 88.9% | 0.0% |
| amazing-grace | Folk / metal | 3 | 27 | 15 / 0 / 0 | 1 | 23 | 66.7% | 85.2% | 4.2% |
| amazing-grace | String quartet / lo-fi | 3 | 27 | 15 / 0 / 0 | 2 | 44 | 55.6% | 77.8% | 0.0% |
| amazing-grace | Warm jazz / chiptune | 3 | 27 | 15 / 0 / 0 | 4 | 60 | 44.4% | 59.3% | 8.3% |
| greensleeves | Bossa nova / synthwave | 3 | 42 | 3 / 6 / 0 | 6 | 80 | 31.0% | 54.8% | 28.2% |
| greensleeves | Folk / metal | 3 | 42 | 3 / 6 / 0 | 10 | 56 | 40.5% | 52.4% | 23.1% |
| greensleeves | String quartet / lo-fi | 3 | 42 | 3 / 6 / 0 | 6 | 66 | 42.9% | 59.5% | 15.4% |
| greensleeves | Warm jazz / chiptune | 3 | 42 | 3 / 6 / 0 | 9 | 79 | 26.2% | 52.4% | 5.1% |
| londonderry-air | Bossa nova / synthwave | 3 | 45 | 3 / 0 / 0 | 10 | 43 | 46.7% | 57.8% | 11.9% |
| londonderry-air | Folk / metal | 3 | 45 | 3 / 0 / 0 | 7 | 46 | 57.8% | 68.9% | 9.5% |
| londonderry-air | String quartet / lo-fi | 3 | 45 | 3 / 0 / 0 | 19 | 60 | 28.9% | 46.7% | 14.3% |
| londonderry-air | Warm jazz / chiptune | 3 | 45 | 3 / 0 / 0 | 16 | 56 | 33.3% | 53.3% | 14.3% |
| ode-to-joy | Bossa nova / synthwave | 3 | 27 | 0 / 0 / 0 | 14 | 205 | 11.1% | 11.1% | 28.0% |
| ode-to-joy | Folk / metal | 3 | 27 | 0 / 0 / 0 | 18 | 307 | 3.7% | 3.7% | 8.3% |
| ode-to-joy | String quartet / lo-fi | 3 | 27 | 0 / 0 / 0 | 11 | 277 | 7.4% | 11.1% | 12.5% |
| ode-to-joy | Warm jazz / chiptune | 3 | 27 | 0 / 0 / 0 | 11 | 95 | 18.5% | 37.0% | 29.2% |
| oh-susanna | Bossa nova / synthwave | 3 | 60 | 54 / 0 / 0 | 19 | 41 | 38.3% | 51.7% | 15.8% |
| oh-susanna | Folk / metal | 3 | 60 | 54 / 0 / 0 | 26 | 49 | 35.0% | 36.7% | 10.5% |
| oh-susanna | String quartet / lo-fi | 3 | 60 | 54 / 0 / 0 | 12 | 31 | 48.3% | 63.3% | 10.5% |
| oh-susanna | Warm jazz / chiptune | 3 | 60 | 54 / 0 / 0 | 29 | 89 | 18.3% | 30.0% | 8.8% |

| Preset | Takes | Median error (ms) | Within 60 ms | Within 120 ms | Null within 120 ms | Real minus null (95% interval) | Heard agreement (95% interval) | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| Bossa nova / synthwave | 15 | 54 | 39.8% | 53.2% | 17.1% | 35.9% (17.1% to 54.5%) | 54.8% (44.6% to 65.6%) | fail |
| Folk / metal | 15 | 47 | 41.3% | 49.3% | 11.8% | 38.2% (22.2% to 54.6%) | 49.6% (36.7% to 63.0%) | fail |
| String quartet / lo-fi | 15 | 59 | 38.3% | 53.7% | 11.3% | 41.1% (26.2% to 56.4%) | 61.8% (50.1% to 74.2%) | fail |
| Warm jazz / chiptune | 15 | 73 | 26.9% | 44.8% | 11.8% | 33.3% (22.3% to 45.1%) | 40.7% (31.3% to 52.0%) | fail |
