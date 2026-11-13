"""Stable messages shared by the terminal and local protocol."""

from dataclasses import dataclass
from enum import IntEnum


class ErrorCode(IntEnum):
    INVALID_INPUT = 2
    ENGINE_OFFLINE = 3
    DECODE_FAILED = 4
    EXTRACT_FAILED = 5
    IO_FAILED = 6
    UNAVAILABLE = 7
    ENGINE_FAILED = 8
    SILENT = 9
    TOO_SHORT = 10
    TOO_LARGE = 11
    FORMAT_MISMATCH = 12
    ENGINE_BUSY = 13
    INVALID_CHART = 14


@dataclass(frozen=True)
class Message:
    what: str
    next: str


MESSAGES = {
    ErrorCode.INVALID_CHART: Message(
        "Invalid chart: {detail}",
        "Fix the listed fields in the chart JSON, then import it again.",
    ),
    ErrorCode.INVALID_INPUT: Message("Invalid input: {detail}", "Run ghostband --help."),
    ErrorCode.ENGINE_OFFLINE: Message(
        "Engine offline: {detail}",
        "Run ghostband setup, then ghostband doctor, which names anything still missing.",
    ),
    ErrorCode.DECODE_FAILED: Message(
        "Could not decode the file: {detail}",
        "Run ghostband import with a complete WAV or MP3 file.",
    ),
    ErrorCode.EXTRACT_FAILED: Message(
        "Extraction failed: {detail}",
        "Run ghostband doctor to check the extractor, or import a MIDI file or chart of the "
        "song instead.",
    ),
    ErrorCode.IO_FAILED: Message(
        "Could not read or write the file: {detail}",
        "Run ghostband --help, then retry with a readable input and writable output path.",
    ),
    ErrorCode.UNAVAILABLE: Message(
        "Unavailable: {detail}", "Run ghostband doctor to check the local installation."
    ),
    ErrorCode.ENGINE_FAILED: Message(
        "Engine failed: {detail}",
        "Run ghostband doctor, then try again; ghostband starts a new engine.",
    ),
    ErrorCode.SILENT: Message(
        "Rejected {detail}: the file is silent", "Run ghostband import with an audible recording."
    ),
    ErrorCode.TOO_SHORT: Message(
        "Rejected {detail}: minimum duration is 10 s",
        "Run ghostband import with a recording at least 10 s long.",
    ),
    ErrorCode.TOO_LARGE: Message(
        "Rejected {detail}: upload exceeds 200 MB",
        "Run ghostband import with a file no larger than 200 MB.",
    ),
    ErrorCode.FORMAT_MISMATCH: Message(
        "Rejected {detail}: extension and content disagree",
        "Run ghostband import with a WAV, MP3, FLAC, AIFF, M4A, MIDI or chart JSON "
        "matching its extension.",
    ),
    ErrorCode.ENGINE_BUSY: Message(
        "Engine busy: {detail}",
        "Press Ctrl+C where that program runs, then try again; the engine serves one "
        "program at a time.",
    ),
    "wrote": Message("Wrote {detail}", "Run ghostband --help for more commands."),
    "playing": Message("Playing {detail}", "Press Ctrl+C to stop."),
    "stopped": Message("Stopped {detail}", "Run ghostband play to play another chart."),
    "serving": Message("Listening at {detail}", "Open this URL to connect locally."),
    "doctor": Message(
        "Local check: {detail}",
        "Run ghostband to start the band, or ghostband --fake-engine to start without audio.",
    ),
    "running": Message(
        "Benchmarking {detail}", "Wait for the figures; the engine is generating audio."
    ),
    "measured": Message(
        "Measured {detail}", "Run ghostband bench --model mrt2_base to check the larger model."
    ),
    "settings": Message(
        "Live band, mrt2_base: {detail}",
        "Run ghostband bench --model mrt2_base after any engine update.",
    ),
    "progress": Message("Import {detail}", "Wait for the chart path before playing."),
    "long": Message(
        "Recording exceeds 360 s; using permissive extraction",
        "Run ghostband import --extractor permissive to select it explicitly.",
    ),
    "skipped-preset": Message(
        "Skipped preset {detail}",
        "Fix the named line or field in that file, then run ghostband doctor again.",
    ),
    "installing": Message(
        "Installing the engine and models in {detail}",
        "Wait for setup to finish before starting ghostband.",
    ),
    "installed": Message(
        "Setup complete in {detail}",
        "Run ghostband doctor to confirm the install, then ghostband to start the band.",
    ),
    "declined": Message(
        "Setup installed nothing: {detail} was declined",
        "Run ghostband setup without {detail}, or add --yes to accept it.",
    ),
    "fallback": Message(
        "SheetSage2 is not installed; using permissive extraction",
        "Run ghostband import --extractor permissive to select it explicitly.",
    ),
}


class GhostbandError(Exception):
    """An actionable failure with a stable process exit code."""

    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        entry = MESSAGES[code]
        self.message = entry.what.format(detail=detail)
        self.fix = entry.next.format(detail=detail)
        super().__init__(self.message)

    def event(self) -> dict[str, object]:
        return {
            "event": "error",
            "code": self.code.name.lower(),
            "message": self.message,
            "fix": self.fix,
        }
