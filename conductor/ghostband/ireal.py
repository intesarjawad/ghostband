"""Read and write iReal Pro chord charts as portable charts.

iReal stores a chord chart in a percent-encoded link and scrambles every full
50-character block by swapping character pairs. The swap is its own inverse,
so one routine serves both directions. Harmony, meter and section letters
survive the round trip; melody, tempo detail and section names do not exist in
the format.
"""

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, unquote

from ghostband.chart import Chart, ChordEvent, Section
from ghostband.chords import Chord, ChordQuality, parse_chord
from ghostband.errors import ErrorCode, GhostbandError

SONG_PREFIX = "1r34LbKcu7"
SCHEMES = ("irealb://", "irealbook://")
BLOCK_SIZE = 50
DEFAULT_TEMPO_BPM = 120
DEFAULT_STYLE = "Medium Swing"
DOCUMENT_SUFFIXES = frozenset({".html", ".htm", ".txt"})
MAX_DOCUMENT_BYTES = 1 << 20
REFERENCE_PATTERN = re.compile(r"ireal(?:b|book)://[^\s\"'<>]+", re.IGNORECASE)
FIELD_PATTERN = re.compile(r"=+")
TIME_PATTERN = re.compile(r"T(\d+)")
PARENTHESIS_PATTERN = re.compile(r"\([^)]*\)")
CHORD_PATTERN = re.compile(r"([A-G])([#b]?)([^/]*)(?:/([A-G][#b]?))?")
REPEAT_ONE = "\x01"
REPEAT_TWO = "\x02"
CHORD_STARTS = frozenset("ABCDEFGW")
CHORD_CHARS = frozenset("ABCDEFGWM#b+-^hijmofsuadlnt0123456789/()")
QUALITY_PREFIXES = (
    ("^", "maj7"),
    ("h", "m7b5"),
    ("ø", "m7b5"),
    ("dim", "dim"),
    ("o", "dim"),
    ("+", "aug"),
    ("aug", "aug"),
    ("sus2", "sus2"),
    ("7sus", "7sus4"),
    ("sus", "sus4"),
    ("maj", "maj7"),
    ("M", "maj7"),
    ("alt", "7"),
    ("-", "m"),
    ("m", "m"),
)
QUALITY_TO_IREAL: Mapping[ChordQuality, str] = MappingProxyType(
    {
        ChordQuality.MAJOR: "",
        ChordQuality.MINOR: "-",
        ChordQuality.DOMINANT_SEVENTH: "7",
        ChordQuality.MAJOR_SEVENTH: "^7",
        ChordQuality.MINOR_SEVENTH: "-7",
        ChordQuality.DIMINISHED: "o",
        ChordQuality.DIMINISHED_SEVENTH: "o7",
        ChordQuality.HALF_DIMINISHED_SEVENTH: "h7",
        ChordQuality.AUGMENTED: "+",
        ChordQuality.SUSPENDED_SECOND: "sus2",
        ChordQuality.SUSPENDED_FOURTH: "sus",
        ChordQuality.SUSPENDED_SEVENTH: "7sus",
    }
)


@dataclass(frozen=True)
class _Song:
    title: str
    style: str
    key: str
    tempo_bpm: int
    chart: str


@dataclass(frozen=True)
class _Bar:
    section: str
    cells: tuple[str, ...]


def style_text(style: str) -> str:
    """Turn an iReal style name into the slot A style prompt."""
    lowered = style.strip().casefold()
    return f"{lowered} jazz" if "swing" in lowered else style


def is_ireal_source(source: str | Path) -> bool:
    """Recognise a link directly or inside a small exported document."""
    if isinstance(source, str):
        return REFERENCE_PATTERN.search(source) is not None
    if source.suffix.lower() not in DOCUMENT_SUFFIXES:
        return False
    try:
        if source.stat().st_size > MAX_DOCUMENT_BYTES:
            return False
        return REFERENCE_PATTERN.search(_read_document(source)) is not None
    except (OSError, GhostbandError):
        return False


def read_ireal(source: str | Path) -> Chart:
    """Read the first song in a link or exported document as a portable chart."""
    try:
        text = _read_document(source) if isinstance(source, Path) else source
        reference = REFERENCE_PATTERN.search(text)
        if reference is None:
            raise ValueError("no iReal Pro link found")
        songs = _songs(html.unescape(reference.group(0)))
        if not songs:
            raise ValueError("the iReal Pro link contains no song")
        return _to_chart(_parse_song(songs[0]))
    except (ValueError, IndexError) as error:
        raise GhostbandError(ErrorCode.INVALID_INPUT, str(error)) from error


def chart_to_ireal(chart: Chart) -> str:
    """Encode harmony and section letters as a single-song iReal link."""
    beats_per_bar = chart.beats_per_bar
    bar_count = ceil(chart.duration_beats / beats_per_bar)
    last_marker = ""
    bars: list[str] = []
    for index in range(bar_count):
        start = index * beats_per_bar
        stop = start + beats_per_bar
        chord_starts = [
            _ireal_symbol(chord.symbol)
            for chord in chart.chords
            if start <= chord.start_beat < stop
        ]
        section = next(
            (item for item in chart.sections if item.start_beat <= start < item.end_beat),
            chart.sections[-1],
        )
        marker = _section_marker(section.name)
        if marker != last_marker:
            bars.append(f"*{marker}")
            last_marker = marker
        if chord_starts:
            bars.append(" ".join(chord_starts))
    numerator, denominator = chart.time_signature
    plain = f"T{numerator}{denominator}" + "|".join(bars) + "Z"
    song = "=".join(
        (
            quote(chart.title, safe=""),
            quote("Unknown", safe=""),
            quote(DEFAULT_STYLE, safe=""),
            quote(_ireal_key(chart.key), safe=""),
            "",
            quote(SONG_PREFIX + _unscramble(plain), safe=""),
            "",
            str(round(chart.tempo_bpm)),
            "0",
        )
    )
    return "irealb://" + song


def _read_document(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise GhostbandError(ErrorCode.IO_FAILED, str(error)) from error


def _songs(reference: str) -> list[str]:
    decoded = unquote(reference)
    for scheme in SCHEMES:
        if decoded.casefold().startswith(scheme):
            decoded = decoded[len(scheme) :]
            break
    return [part for part in decoded.split("===") if SONG_PREFIX in part]


def _parse_song(song: str) -> _Song:
    fields = [field for field in FIELD_PATTERN.split(song) if field]
    chart_index = next(
        (index for index, field in enumerate(fields) if field.startswith(SONG_PREFIX)), None
    )
    if chart_index is None or chart_index < 4:
        raise ValueError("the iReal Pro song is missing its title, style, key or chart")
    tempo = next(
        (int(field) for field in fields[chart_index + 1 :] if field.isdigit() and int(field) > 0),
        DEFAULT_TEMPO_BPM,
    )
    return _Song(
        title=fields[0],
        style=fields[2],
        key=fields[3],
        tempo_bpm=tempo,
        chart=fields[chart_index][len(SONG_PREFIX) :],
    )


def _unscramble(text: str) -> str:
    pieces: list[str] = []
    while len(text) > BLOCK_SIZE:
        block, text = text[:BLOCK_SIZE], text[BLOCK_SIZE:]
        pieces.append(block if len(text) < 2 else _swap_block(block))
    pieces.append(text)
    return "".join(pieces)


def _swap_block(block: str) -> str:
    characters = list(block)
    for left, right in ((index, BLOCK_SIZE - 1 - index) for index in (*range(5), *range(10, 24))):
        characters[left], characters[right] = characters[right], characters[left]
    return "".join(characters)


class _ChartReader:
    """Turn the iReal measure stream into bars, expanding repeats as it goes."""

    def __init__(self) -> None:
        self.bars: list[_Bar] = []
        self.cells: list[str] = []
        self.sections = ["A"]
        self.time_signature = (4, 4)
        self.last_symbol: str | None = None
        self.last_root: str | None = None
        self.bracket_start: int | None = None
        self.brace_start: int | None = None
        self.ending_head: int | None = None
        self.has_ending = False
        self.segnos: list[int] = []
        self.codas: list[int] = []

    def read(self, text: str) -> tuple[tuple[int, int], list[_Bar]]:
        index = 0
        while index < len(text):
            index = self._step(text, index)
        return self.time_signature, self._finish()

    def _step(self, text: str, index: int) -> int:
        handlers = (
            self._read_layout,
            self._read_barline,
            self._read_repeat,
            self._read_cell,
        )
        for handler in handlers:
            next_index = handler(text, index)
            if next_index is not None:
                return next_index
        return index + 1

    def _read_layout(self, text: str, index: int) -> int | None:
        character = text[index]
        if text.startswith("XyQ", index):
            return index + 3
        if character == "<":
            end = text.find(">", index)
            return len(text) if end == -1 else end + 1
        if character == "*" and index + 1 < len(text):
            self._flush()
            self.sections.append(text[index + 1])
            return index + 2
        if (time_match := TIME_PATTERN.match(text, index)) is not None:
            digits = time_match.group(1)
            if len(digits) < 2:
                raise ValueError("iReal time signature needs a numerator and denominator")
            self.time_signature = (int(digits[:-1]), int(digits[-1]))
            return index + len(digits) + 1
        if character == "Y" or character in " ,\n\t":
            return index + 1
        return None

    def _read_barline(self, text: str, index: int) -> int | None:
        character = text[index]
        if text.startswith("LZ", index):
            self._flush()
            return index + (3 if text.startswith("LZ|", index) else 2)
        if text.startswith("Kcl", index):
            self._flush()
            self.cells.append(REPEAT_ONE)
            return index + 3
        if character == "K":
            self._flush()
            return index + 1
        if text.startswith("cl", index):
            self.cells.append(REPEAT_ONE)
            return index + 2
        if character == "|":
            self._flush()
            return index + 1
        return None

    def _read_repeat(self, text: str, index: int) -> int | None:
        character = text[index]
        consumed = index + 1
        if character == "[":
            self._flush()
            self.bracket_start = len(self.bars)
        elif character == "]":
            self._flush()
            if self.bracket_start is not None:
                self.bars.extend(self.bars[self.bracket_start :])
                self.bracket_start = None
        elif character == "{":
            self._flush()
            self.brace_start, self.ending_head, self.has_ending = len(self.bars), None, False
        elif character == "}":
            self._flush()
            if self.brace_start is not None and not self.has_ending:
                self.bars.extend(self.bars[self.brace_start :])
                self.brace_start, self.ending_head = None, None
        elif character == "N" and index + 1 < len(text) and text[index + 1].isdigit():
            self._read_ending(text[index + 1])
            consumed = index + 2
        elif character == "Z":
            self._flush()
            consumed = len(text)
        else:
            consumed = -1
        return None if consumed < 0 else consumed

    def _read_cell(self, text: str, index: int) -> int | None:
        character = text[index]
        consumed = index + 1
        if character == "x":
            self.cells.append(REPEAT_ONE)
        elif character == "r":
            self.cells.append(REPEAT_TWO)
        elif character == "n":
            self.cells.append("N.C.")
            self.last_symbol, self.last_root = "N.C.", None
        elif character == "p":
            if self.last_symbol is not None:
                self.cells.append(self.last_symbol)
        elif character in "SQ":
            marker = self.segnos if character == "S" else self.codas
            marker.append(len(self.bars) + (1 if self.cells else 0))
        elif character in "Uf" or (
            character in "sl" and text[index + 1 : index + 2] in CHORD_STARTS
        ):
            pass
        elif character in CHORD_STARTS:
            consumed = self._read_chord(text, index)
        else:
            consumed = -1
        return None if consumed < 0 else consumed

    def _read_chord(self, text: str, index: int) -> int:
        start = index
        index += 1
        while index < len(text) and text[index] in CHORD_CHARS:
            index += 1
        symbol = _read_chord(text[start:index], self.last_root)
        if symbol is not None:
            self.cells.append(symbol)
            self.last_symbol, self.last_root = symbol, symbol.split("/")[0]
        return index

    def _read_ending(self, digit: str) -> None:
        if self.brace_start is None:
            return
        if digit == "1":
            self.ending_head, self.has_ending = len(self.bars), True
        elif self.ending_head is not None:
            self.bars.extend(self.bars[self.brace_start : self.ending_head])

    def _flush(self) -> None:
        if self.cells:
            self.bars.append(_Bar(self.sections[-1], tuple(self.cells)))
            self.cells.clear()

    def _finish(self) -> list[_Bar]:
        if len(self.codas) == 1:
            self.codas.clear()
        if len(self.codas) == 2:
            first, second = self.codas
            segue = self.segnos[0] if self.segnos else 0
            if not 0 <= segue <= first <= second <= len(self.bars):
                raise ValueError("iReal coda markers are out of order")
            self.bars = [
                *self.bars[:second],
                *self.bars[segue:first],
                *self.bars[second:],
            ]
        if not self.bars:
            raise ValueError("the iReal Pro chart has no measures")
        return _expand_bars(self.bars)


def _parse_chart(text: str) -> tuple[tuple[int, int], list[_Bar]]:
    return _ChartReader().read(text)


def _expand_bars(bars: list[_Bar]) -> list[_Bar]:
    expanded: list[_Bar] = []
    for bar in bars:
        if bar.cells == (REPEAT_ONE,):
            source = expanded[-1].cells if expanded else ()
            expanded.append(_Bar(bar.section, source))
        elif bar.cells == (REPEAT_TWO,):
            sources = expanded[-2:] if len(expanded) >= 2 else expanded[-1:]
            expanded.extend(_Bar(bar.section, source.cells) for source in sources)
        else:
            expanded.append(bar)
    return expanded


def _read_chord(raw: str, last_root: str | None) -> str | None:
    cleaned = PARENTHESIS_PATTERN.sub("", raw).replace("f", "")
    if not cleaned:
        return None
    if cleaned.startswith("W"):
        previous = CHORD_PATTERN.match(last_root or "C")
        root = f"{previous.group(1)}{previous.group(2)}" if previous else "C"
        cleaned = root + cleaned[1:]
    match = CHORD_PATTERN.fullmatch(cleaned)
    if match is None:
        return None
    root, accidental, suffix, bass = match.groups()
    symbol = f"{root}{accidental}{_canonical_quality(suffix)}"
    return f"{symbol}/{bass}" if bass else symbol


def _canonical_quality(suffix: str) -> str:
    quality = next((value for prefix, value in QUALITY_PREFIXES if suffix.startswith(prefix)), "")
    if suffix.startswith(("o", "dim")) and "7" in suffix:
        return "dim7"
    if suffix.startswith(("-", "m")) and "b5" in suffix:
        return "m7b5"
    extended = any(tension in suffix for tension in ("7", "9", "11", "13"))
    if quality == "m" and extended:
        return "m7"
    plain = quality == "" and "add" not in suffix and "6" not in suffix
    return "7" if plain and extended else quality


def _beats_per_bar(time_signature: tuple[int, int]) -> int:
    numerator, denominator = time_signature
    if denominator == 8 and numerator % 3 == 0:
        return numerator // 3
    return numerator


def _to_chart(song: _Song) -> Chart:
    time_signature, bars = _parse_chart(_unscramble(song.chart))
    beats_per_bar = _beats_per_bar(time_signature)
    chords: list[ChordEvent] = []
    previous: str | None = None
    for index, bar in enumerate(bars):
        start = index * beats_per_bar
        symbols = bar.cells if bar.cells else ((previous,) if previous else ())
        cell = beats_per_bar / len(symbols) if symbols else 0
        for offset, symbol in enumerate(symbols):
            chords.append(
                ChordEvent(
                    start_beat=start + offset * cell,
                    end_beat=start + (offset + 1) * cell,
                    symbol=symbol,
                )
            )
        previous = symbols[-1] if symbols else None

    sections: list[Section] = []
    for index, bar in enumerate(bars):
        end = (index + 1) * beats_per_bar
        if sections and sections[-1].name == bar.section:
            sections[-1] = sections[-1].model_copy(update={"end_beat": end})
        else:
            sections.append(
                Section(name=bar.section, start_beat=index * beats_per_bar, end_beat=end)
            )
    merged: list[ChordEvent] = []
    for chord in chords:
        if merged and merged[-1].symbol == chord.symbol and merged[-1].end_beat == chord.start_beat:
            merged[-1] = merged[-1].model_copy(update={"end_beat": chord.end_beat})
        else:
            merged.append(chord)
    data: dict[str, object] = {
        "schema_version": 1,
        "title": song.title,
        "source": "iReal Pro",
        "rights": "unknown",
        "extractor": "manual",
        "tempo_bpm": song.tempo_bpm,
        "beat_times_s": [],
        "time_signature": list(time_signature),
        "key": _canonical_key(song.key),
        "sections": [section.model_dump() for section in sections],
        "chords": [chord.model_dump() for chord in merged],
        "melody": [],
        "duration_beats": len(bars) * beats_per_bar,
    }
    return Chart.from_import(data)


def _canonical_key(key: str) -> str:
    stripped = key.strip()
    if not stripped or stripped == "n":
        return "unknown"
    return stripped[:-1] + "m" if stripped.endswith("-") else stripped


def _ireal_key(key: str) -> str:
    root = key[:-1]
    return (
        f"{root}-"
        if key.endswith("m")
        and root in {"A", "Bb", "B", "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab"}
        else key
    )


def _section_marker(name: str) -> str:
    return name[:1].upper() if name[:1].isalnum() else "A"


def _ireal_symbol(symbol: str) -> str:
    try:
        chord = parse_chord(symbol)
    except ValueError:
        return symbol
    if not isinstance(chord, Chord):
        return "n"
    bass = f"/{chord.bass}" if chord.bass is not None else ""
    return f"{chord.root}{QUALITY_TO_IREAL[chord.quality]}{bass}"
