# Design direction: the roll

The Pages demo (`ui/demo.html`) is one object: the chart punched as a player-piano roll and read at a brass tracker bar while the recorded take plays. Everything on the page is that roll, its key, its measurements, or how to run the band that made it.

## What it takes from

Conlon Nancarrow's Studies for Player Piano (1948 to 1992): the music exists only as holes in a moving strip, position across the paper is pitch, length along it is time, and one fixed bar reads it. Two rules come with it. The geometry is exact: a hole's row is a pitch and its length is a duration, the strip moves at the take's own tempo past one fixed line. Nothing on the paper is decorative: every hole, pencil mark and margin note is data.

## The mapping

| On the roll | Is | From |
| --- | --- | --- |
| Chains of round holes | each chord tone, in the octave from G3 | `charts/oh-susanna.chart.json` chords |
| One slot per beat, low rows | the bass, struck on every beat (onset mode 1) | chord bass |
| Pencil outlines | the tune: the band hears it, but it is the part you can take (M mutes it) | chart melody |
| Light through a hole | flares white as it crosses the bar, as bright as the take is loud in the hole's register (bass slots by 30 to 180 Hz, chord holes by 180 to 2000 Hz), and trails off behind the bar over a length that grows with that loudness | the take's register loudness |
| One hole in the leader | the pilot hole, punched where the leader rests over the bar, so the lamp shows before the roll moves | the bar position |
| Glow in the paper round each lit hole | the take's treble, 2000 to 12000 Hz, which no hole sounds; it widens the glow but never leaves the holes | the take's register loudness |
| Margin, top | each chord change, and, printed as the change crosses the bar, when the band really changed (+7 ms late, &minus;78 ms early, missed) | sync detector |
| Margin, foot | the chord heard on each counted beat, ringed in red where it is not the chart's chord | heard-chord detector |

Heard beats are counted on the chart's own grid, from the bar line after the half-beat pickup, so no counted beat straddles a change. In this take the detector agrees on 62 of 63; the one ring is beat 23.5, where the chart asks for D and the detector heard G.

At 8.5 and 24.5 the chart holds D7 for one beat and D for two before G. D7 to D reduces to one triad, so the scorer does not count it as a change; the page marks that D with a dagger and a footnote.

## Signature moment

Light through punched holes. A lamp sits behind the paper at the brass bar; light passes only through the holes crossing it, and each hole on the bar lights the vellum around it in a halo about six hole pitches wide that bleeds into the fibre grain, never as a column. The bar's slot is narrower than the gap between holes in a chain, so each row lights one hole at a time: a solid white core, its halo reddening only at its outer edge, the way a bright source halates on film, and behind the bar an afterglow that fades smoothly like phosphor over a length set by the take's loudness (about 10 px on a phone, so it never runs under the readout). Under reduced motion there is no afterglow, only the holes on the bar. The brass bar's two lips cross the paper either side of the slot, over the leader too, each a dark brass edge with a bright highlight along its inner side. Pressing play takes the room down to near black (`#0b0806`) over 700 ms: the case and brass go dark and the paper becomes a lightbox: lit from behind in a high key, hottest along the bar and falling off along the roll, with a fibre grain 2 to 4 px long and no larger mottling, so the holes still to come read as black silhouettes and everything printed or pencilled on it reads dark, as on a lightbox (the heard row in `--ink`). As each change crosses the bar the margin prints its offset, which glows and cools. Pause mid-take and the room comes back to about 75 percent, warm rather than grey, with the holes at the bar as hot cores and the bloom cut to a quarter; at rest the leader's pilot hole sits between the lips with a hot core and a small amber halo. The paper winds onto a take-up spool on a desk and comes off a full supply roll on a phone, both drawn as cylinders whose radius follows how much paper is wound, with a matte sheen and no holes showing on the wound paper, the take-up spool a shaded cylinder with a sheen across its middle and a brass rim light on its far edge, stopping at the case edges; the transport starts at 166 px, clear of the spool and its flanges. On a phone only every other step of a chain is punched, so chains read as 4 px holes with gaps, and a lit hole's core is 6 px.

## Material and tokens

Tokens live in `ui/src/demo.css` `:root`; the shader repeats the four material colours as constants.

| Token | Value | Use |
| --- | --- | --- |
| `--case` | `#120d09` | the piano case, every dark surface |
| `--dark` | `#0b0806` | the case with the room lights down |
| `--lit` | `#f3dfb6` | margin print in the dark room |
| `--paper` | `#ebe0c5` | the roll and every cut length of it |
| `--ink` | `#26190e` | printed type on paper |
| `--graphite` | `#4e4a45` | pencil, secondary print |
| `--red` / `--red-on-case` | `#a8231b` / `#e0675a` | the editor's red pencil, on paper and on the case |
| `--brass` | `#c9a25a` | tracker bar, flanges, eyelet, focus ring, scrubber |
| `--muted` / `--dim` | `#cbbd9c` / `#a99c7e` | text on the case (10:1 and 7:1 on `--case`) |

Type: Big Shoulders Stencil Display 800 for the headline, the play button and the name on the case, nothing else; Spectral for print, section headings and pencil notes; Courier Prime for anything the machine reports (readout, command, times, offsets) and for the printed labels heading Measured and Run it; only How it plays has a set heading. All three are self-hosted under the SIL Open Font Licence (`ui/public/fonts/OFL.txt`); no font request leaves the page.

## Motion

- Play from rest: the leader glides to the first beat over 650 ms (ease-in-out) while the take starts, then the paper is locked to the audio clock: the beat comes from `audio.currentTime` through the scheduler's beat log, extrapolated between clock updates.
- The lamp warms up over 900 ms once the shader is ready; the room dims or lifts over about 700 ms.
- Reduced motion: no glide, no warm-up; the roll is redrawn once per bar and holds still between, with the same lit holes and readout. Arrow keys step one bar.
- No WebGL2: at rest the paper is a still of the roll (`ui/public/takes/roll.webp`, `roll-upright.webp`) under the same live ink, readout and controls. Once the take plays the still gives way to plain paper with a brass bar, the holes drawn in the ink layer so they travel with the take, and the room stays lit, since there is no lamp to dim it for. On a phone that paper starts flush under the nav, since there is no drawn supply roll above it, and the print fades out over the 24 px above the case panel rather than being cut at its edge.

## Layout

- 760 px and wider: the roll runs across the screen, bar at 24 percent of the width (300 to 400 px), 120 px per beat, take-up spool at the left, the leader tab hooked toward it at rest; a 540 px leader leaves about 430 px of punched roll in view at 1440. The bar runs 24 px past each paper edge into a bracket.
- Narrower: the roll stands upright like the piano feeds it, supply roll at the top, 64 px per beat. At rest the leader tab hangs to just above the case and the bar crosses it at the pilot hole, centred on the tab's axis above the eyelet. The paper stops 16 px above the readout: an opaque panel of case, 88 px tall, holds the readout, timecode and scrubber in every state, so nothing reads over paper or light. The readout has no box of its own on either layout.
- One play control at a time: the leader's button before the roll moves, the transport after. The transport button is 44 px, the scrubber a 4 px brass track in a 44 px target, the timecode 15 px.
- Below the machine, How it plays is a length of the roll cut with torn ends (bars 11 to 16, or 12 to 13 on a phone), with a dark band of case behind the bar between the brass lips, where only the holes on the bar show, as white cores in an amber halo about two hole pitches wide. Five notes sit in the margin straight above or below what they describe, each joined to it by a pencil line ending in a point. Narrower than 700 px, each mark carries its note's number in an ink disc and the notes follow below as a numbered key.
- Measured runs full width: the figures in plain prose, then the whole take in torn lengths of 16 beats, each torn differently. The holes and pencil there are context at 35 percent; a length punched exactly like an earlier one shows only its margin and names the bars it repeats. Each change is a 2 px tick in `--ink` off a zero line, up when early and down when late, to scale at 0.2 px per ms but never shorter than 6 px, against hairlines at 120 ms either side; offsets print in Courier Prime 12 px in `--ink`, centred on their ticks on rows 14 px apart, a row further out where one is taken, clear of each other and of the torn ends; chord names that would run together drop to a second baseline on a phone; a missed change is a red cross marked "missed". The plot is only as tall as its ticks and labels. On a phone each length keeps only the margin (chords and ticks), so the section stays under 1,200 px. One column of footnotes closes it: the tick key, the dagger, the method. Both sections are drawn from the same chart, holes and take as the machine.
- The page ends on the roll's tail: a length with a torn start whose last chains, at the machine's scale and torn where no chain is cut to a stub, run out to blank paper, then a pointed tab mirroring the leader's with a brass-ringed hole near its tip, pointing right on a desk and down on a phone. Run it is printed on the tail as a label, the commands in Courier, each on one line on a desk, with a Copy button, and the licence and credits as a small imprint. Nothing follows the tab.
- Torn edges are uneven in both spacing (1.5 to 9.5 percent) and depth (1 to 10 px), seeded per piece, so no run of teeth repeats.
- The canvas never holds text. Chord names, pencil, margin and the leader are DOM (SVG and HTML) laid over it, so they stay selectable; a visually hidden table repeats every margin figure for screen readers.

## Numbers and where they come from

`scripts/export_pages_take.py` writes `ui/public/takes/oh-susanna-t1-o1-r2.json` and `.mp3` from the recorded take, scoring it with `scripts/measure_sync.py`'s detector unchanged:

```sh
uv run python scripts/export_pages_take.py TAKES/oh-susanna-t1-o1-r2.take.json \
  --out ui/public/takes --pool TAKES/*-t1-o1-r*.take.json
```

It gives the counted changes, timing errors, misses, heard-chord agreement, pooled scores, source sidecars and file hashes in [the exported data](../../ui/public/takes/oh-susanna-t1-o1-r2.json). Tempo, key, metre and title come from the chart file. The page writes no number of its own.

The shipped take and pool were regenerated from the original archived WAVs. Their capture-time recording mode, dropped-frame counts and engine/model identity were not retained. The exporter preserves those unknowns and labels these scores diagnostic. Its default path still requires live mode, zero dropped frames and matching capture provenance; `--diagnostic` also refuses known dropped frames, fake recordings and mismatched provenance. The original WAV hashes identify the scoring inputs; the separate audio hash identifies the encoded MP3 served by the page.

To replay the shipped export, set `RECORDINGS` to the directory containing the original WAVs. Reconstruct portable sidecars from the embedded source set, verifying each input before scoring:

```sh
export RECORDINGS=/path/to/archived/wavs
export STAGED="$(mktemp -d)"
uv run python - <<'PY'
import json
import os
from pathlib import Path
from ghostband.setup_models import sha256_file

data = json.loads(Path("ui/public/takes/oh-susanna-t1-o1-r2.json").read_text())
staged = Path(os.environ["STAGED"])
for source in data["pool"]["sources"]:
    sidecar = source["sidecar"]
    recording = (Path(os.environ["RECORDINGS"]) / sidecar["recording"]).resolve()
    assert sha256_file(recording) == source["provenance"]["recording_sha256"]
    assert sha256_file(Path(sidecar["chart"])) == source["provenance"]["chart_sha256"]
    (staged / source["name"]).write_text(json.dumps(sidecar, separators=(",", ":")))
    (staged / sidecar["recording"]).symlink_to(recording)
PY
uv run python scripts/export_pages_take.py "$STAGED/oh-susanna-t1-o1-r2.take.json" \
  --out ui/public/takes --diagnostic --pool "$STAGED"/*.take.json
```

## Regenerating the stills

With `npm --prefix ui run dev` running, from `~/.local/bin/playwright-cli`:

- README hero `docs/screenshots/roll.jpg`: 1440 x 900, press Play, capture at 0:16.
- Social card `ui/public/social.png`: 1280 x 640 at `demo.html?t=16.1`, the header line set to the headline, readout and transport hidden.
- No-WebGL stills: 1440 x 900 and 390 x 844 at rest, ink and leader hidden, canvas only, encoded with `cwebp -q 82`.

`?t=` opens the roll paused at that second of the take, so any moment can be linked.

## What must not change

- No ornamental hole, mark or glow: everything on the paper maps to the chart, the take or the machine (the pilot hole, the bar).
- Light comes through the holes. The paper only glows faintly from behind, falling off from the bar; it never lights evenly as a whole (that is a lamp under the sheet, a different product's look).
- The audio never autoplays; the room dims only while the take plays.
- The canvas never carries the only copy of a word or number.
