# 0015: Keep the sync target and separate diagnostic scores from acceptance

Status: accepted for measurement corrections; live musical acceptance remains blocked.

## Evidence

The sync target is at least 90 percent of eligible changes within 120 ms,
median absolute detected error strictly below 60 ms, and a paired real-minus-null
95 percent interval above zero. Misses remain in the rate denominator. Nothing
in this investigation establishes that this target is physically impossible.
The model's approximately 200 ms control latency is not such a proof: the
scheduler already sends notes 200 ms ahead, and the model's 40 ms frames are
finer than the 120 ms acceptance window.

The synthetic WAV test in `conductor/tests/test_measure_sync.py` independently
places a C-to-G change at -200, -100, 0, +80, +200, +400 and +700 ms relative to
a nonzero-origin scheduler log. The original scorer lost the -200 ms change:
its search began at -125 ms, although the measurement protocol specifies -250 ms. With the
specified search window, detected offsets are within 30 ms of the injected
values; +700 ms is a miss. Early and late changes outside 120 ms still fail.
The half-bar null scores zero for these synthetic takes.

The five FluidSynth fixture renders, pooled over their recorded spans, score
51/56 changes within 120 ms (91.1 percent), with a 19.7 ms median and three
misses. This is a detector check, not a claim about generated music; individual
charts can still score below the pooled target.

Re-scoring the same 60 archived takes with the corrected window does not make
any preset pass. In table order Bossa, Folk, Strings, Jazz, within-120-ms rates
change from 54.2, 49.8, 54.2, 45.3 percent to 53.2, 49.3, 53.7, 44.8 percent.
The wider search sometimes finds an earlier failed transition instead of a
later apparent hit. These are diagnostic numbers: the recordings predate
ADR 0014 and contain no `dropped_frames` evidence. Their validity cannot be
reconstructed by adding a zero to their sidecars.

On 11 November 2026, four new Ode to Joy probes in shipped onset mode 1,
with the bass pulse and slider sweep, dropped 479, 0, 7 and 172 frames for
Jazz, Folk, Bossa and Strings respectively. Start load averages ranged from
10.65/9.80/14.26 to 11.77/10.08/14.27. The clean Folk take still scored only
6/9 changes within 120 ms, median 47.0 ms, one miss and two late changes
(+205 and +247 ms). Its null rate was zero. That is evidence of a real
musical/timing shortfall in this sample, not proof of the current aggregate
result across five charts and three repeats.

An isolated engine experiment setting upstream `unmask_width` to 127 was
also attempted. The default width 0 leaves inactive pitches unconstrained;
127 would explicitly condition them off. All four experimental takes dropped
frames (290, 469, 916, 1051) at load averages up to 24.48/15.74/15.57.
There is no valid comparison supporting this product change, so it is not
shipped. Altering generated music to fit a detector without such evidence
would not establish the headline claim.

## Decision

- Restore the specified -250 to +500 ms detector search. Keep the 100 ms hold,
  120 ms tolerance, 90 percent floor, null test and miss denominator unchanged.
  Enforce the stated strict median bound, rather than accepting exactly 60 ms.
- Refuse explicitly gapped takes in `score_take`. Before publishing, require
  explicit zero-drop evidence and exactly one cell for every bundled chart,
  preset and repeat 1 through 3. A missing counter is unknown, not zero.
- Add `--diagnostic` for examining incomplete or historical recordings. It
  labels the report and never writes claims. It still refuses known gapped
  audio. A diagnostic numeric pass is not acceptance.
- Publish sync results as well as heard agreement in `claims.json` only from
  the validated corpus. Mark the current archived sync evidence unverified
  and all four presets unaccepted. Preserve identity and tempo claims; this
  investigation does not re-establish those independent measurements.

## Limits and next measurement

The historical and fresh samples do not establish current product acceptance.
The old tables remain reproducible diagnostics, not a clean before/after
product benchmark. No musical engine or scheduler change is promoted here,
and no acceptance threshold is relaxed.

The recorder currently plays each chart once (about 8 to 32 bars), whereas
the measurement protocol asks for 32 bars per take. The new corpus check validates the
gate's chart/preset/repeat coverage and drop counters, not that additional
recording-duration requirement. A release claim must also resolve that
protocol discrepancy; simply looping with the existing beat-keyed timeline
would collapse repeated beats and is not a valid solution.

The next decisive evidence is a complete, duration-compliant set of zero-drop
takes on a quiet Mac. See [sync.md](../benchmarks/sync.md) for the numeric
comparison and commands. Load averages, exact commands, WAVs and sidecars
from this investigation are retained in the job report's evidence directory.

Zero reported drops are necessary, not sufficient evidence of timing integrity:
the count is sampled in status messages, and heard-row analysis runs on the
scheduler thread. A late scheduler tick can send notes late without starving
the audio callback. The fixed 200 ms lookahead also has no measured latency
calibration in the checked-in benchmark. These remain possible contributors;
this investigation does not tune them from one generated take. Final-status
counter coverage and timestamped command deadlines should accompany the next
live measurement.

`evaluation/extraction.py` also uses this change detector: the next AAM
change-time/hit-rate evaluation will use the corrected search window too.
No historical extraction result is silently rewritten or revalidated here.
