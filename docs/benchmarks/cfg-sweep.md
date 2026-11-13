# Offline cfg_notes sweep

Diagnostic only: frame-blocking MRT2 small, onset mode 1, bass pulse, full charts, smoothed A-to-B sweep. Three paired seed rotations (1/2/3) per chart. No live acceptance or public claims are established.

| cfg_notes | Preset | Takes | Within 120 ms | Median error (ms) | Null within 120 ms |
| ---: | --- | ---: | ---: | ---: | ---: |
| 5 | Folk / metal | 15 | 87.1% | 72 | 6.8% |
| 7 | Folk / metal | 15 | 88.1% | 76 | 3.1% |
| 10 | Folk / metal | 15 | 88.1% | 76 | 3.1% |
| 14 | Folk / metal | 15 | 88.1% | 76 | 3.1% |
| 5 | Warm jazz / chiptune | 15 | 81.1% | 64 | 11.5% |
| 7 | Warm jazz / chiptune | 15 | 74.6% | 68 | 7.8% |
| 5 | Bossa nova / synthwave | 15 | 86.6% | 58 | 9.4% |
| 7 | Bossa nova / synthwave | 15 | 84.6% | 67 | 10.4% |
| 5 | String quartet / lo-fi | 15 | 84.1% | 66 | 9.9% |
| 7 | String quartet / lo-fi | 15 | 81.6% | 69 | 6.8% |

Candidate: 7. Identity command exit: 0. Chosen shared cfg_notes after four-preset confirmation: 5.

Identity checks all five charts, three conditions, both slider endpoints and three paired repeats on all four presets. See [the identity report](cfg-identity.md). The other presets receive matched baseline and candidate sync renders.

| Preset | Before (cfg 5) within 120 ms | Tested candidate (cfg 7) within 120 ms |
| --- | ---: | ---: |
| Warm jazz / chiptune | 81.1% | 74.6% |
| Folk / metal | 87.1% | 88.1% |
| Bossa nova / synthwave | 86.6% | 84.6% |
| String quartet / lo-fi | 84.1% | 81.6% |

FluidSynth detector ceiling: 91.1%, 56 eligible changes, 3 misses.
