# 0003: Count the chart in beats and schedule from an injected clock

Status: accepted

## Context

I need one chart format that every importer can write and the scheduler can
play. Importers disagree about time: MIDI stores ticks and microseconds per
quarter note, iReal Pro charts carry one tempo number, and the
extractors report recording seconds. In `conductor/ghostband/chart.py`, a
musician changing tempo or looping a section must not require rewriting the
chart's chords and melody.

## Decision

I store every musical interval in `Chart` as a half-open range of
musician-counted beats: sections, chords and melody use `start_beat` and
`end_beat`, and `duration_beats` closes the chart. `tempo_bpm` is the only
link to seconds for the band. `beat_times_s` holds recording timestamps and
belongs to reference playback alone. The validators require contiguous
sections from beat 0, sorted non-overlapping chords, and events that end
within `duration_beats`. `beats_per_bar` counts dotted quarters in compound
eighth-note meters, so 6/8 counts two beats.

`Scheduler` in `conductor/ghostband/scheduler.py` converts beats to wall time
from `tempo_bpm`, a tempo factor and a lookahead interval. It takes the current
time as an argument instead of reading a clock, and rejects a clock that goes
backwards. It emits note commands once their audible time falls inside the
lookahead horizon, releasing before attacking at the same beat, and flushes
every held pitch on stop. A tempo change begins at the next whole beat beyond
the horizon, because commands already sent to the engine cannot be recalled.
Seeking or changing a loop restarts with a fresh pre-roll.

I reject seconds as the stored unit because a tempo change would move every
event. I reject a scheduler that reads the system clock because it could not
be tested deterministically and would tie scheduling to when the conductor
happens to run rather than to the engine's reported audio position
([ADR 0001](0001-project-stack.md)).

## Consequences

I accept a conversion step on import: MIDI ticks and extractor seconds must
be turned into beats, and MIDI tempo is rounded to three decimals. Irregular
tempo in a source recording is not represented in the band's timing; the
recording's own beat times stay available for the reference.

A late tick catches up in musical order rather than skipping events, and a
tempo edit is audible later than the control moved by up to the lookahead plus
the rest of the current beat. I have not measured the lookahead needed for
reliable playback with the live engine; the caller chooses it.
