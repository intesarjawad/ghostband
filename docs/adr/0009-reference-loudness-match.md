# 0009: Match the reference recording to the band with BS.1770 loudness

Status: accepted; live audio verification pending

## Context

I need the reference recording and the generated band to sit at a comparable
level when a musician switches between them, so the comparison judges the
music and not the volume. Peak level and RMS both ignore how loudness is
perceived. The engine protocol's `ref_mix` command in
`engine/src/protocol.cpp` accepts a `ref_gain` between 0 and 1.

## Decision

I measure integrated loudness as defined by ITU-R BS.1770 in
`conductor/ghostband/loudness.py`: K-weighting filters, 400 ms blocks every
100 ms, an absolute gate at -70 LUFS and a relative gate 10 LU below the
gated mean. The filter coefficients are derived for the sample rate in use, so
44.1 kHz and 48 kHz material measures alike. The reference WAV is measured
once, at its own sample rate, when an import attaches it to the session.

The band is measured from the newest ten seconds of the engine tap. The tap
is mono, so I duplicate it into two channels to make it comparable with a
stereo take. No estimate is produced until one 400 ms block has arrived, and
a gap in tap samples starts a fresh window. When the reference is switched
on, `reference_gain` turns the difference into a linear gain. If either
measurement is missing or silent the gain stays at 1.0.

I cap the gain at 1.0 because the protocol rejects larger values. A reference
quieter than the band therefore plays unboosted, and I accept that rather than
lower the band to match.

I reject continuous gain riding because it would change the reference's
level while a musician is listening.

## Consequences

The gain is set once each time the reference is switched on, from the last ten
seconds of band audio, and does not follow later changes in the band's
loudness. A band that is silent when the reference is enabled leaves the
reference at unity.

Measuring the tap before band gain means the match reflects the model's
output, not the user's band-level control. The tests compare the
measure with `ffmpeg`'s `ebur128` filter on the fixture recordings when
`ffmpeg` is installed. I have not compared live output with an external
meter, and I make no claim about how close the perceived match is.
