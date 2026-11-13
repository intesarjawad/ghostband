import ast
from pathlib import Path

import pytest
from ghostband.errors import ErrorCode, GhostbandError


@pytest.mark.parametrize(
    ("code", "message", "fix"),
    [
        (ErrorCode.INVALID_INPUT, "Invalid input: sample.wav", "Run ghostband --help."),
        (
            ErrorCode.ENGINE_OFFLINE,
            "Engine offline: sample.wav",
            "Run ghostband setup, then ghostband doctor, which names anything still missing.",
        ),
        (
            ErrorCode.DECODE_FAILED,
            "Could not decode the file: sample.wav",
            "Run ghostband import with a complete WAV or MP3 file.",
        ),
        (
            ErrorCode.EXTRACT_FAILED,
            "Extraction failed: sample.wav",
            "Run ghostband doctor to check the extractor, or import a MIDI file or chart of the "
            "song instead.",
        ),
        (
            ErrorCode.IO_FAILED,
            "Could not read or write the file: sample.wav",
            "Run ghostband --help, then retry with a readable input and writable output path.",
        ),
        (
            ErrorCode.UNAVAILABLE,
            "Unavailable: sample.wav",
            "Run ghostband doctor to check the local installation.",
        ),
        (
            ErrorCode.ENGINE_FAILED,
            "Engine failed: sample.wav",
            "Run ghostband doctor, then try again; ghostband starts a new engine.",
        ),
        (
            ErrorCode.SILENT,
            "Rejected sample.wav: the file is silent",
            "Run ghostband import with an audible recording.",
        ),
        (
            ErrorCode.TOO_SHORT,
            "Rejected sample.wav: minimum duration is 10 s",
            "Run ghostband import with a recording at least 10 s long.",
        ),
        (
            ErrorCode.TOO_LARGE,
            "Rejected sample.wav: upload exceeds 200 MB",
            "Run ghostband import with a file no larger than 200 MB.",
        ),
        (
            ErrorCode.FORMAT_MISMATCH,
            "Rejected sample.wav: extension and content disagree",
            "Run ghostband import with a WAV, MP3, FLAC, AIFF, M4A, MIDI or chart JSON "
            "matching its extension.",
        ),
        (
            ErrorCode.ENGINE_BUSY,
            "Engine busy: sample.wav",
            "Press Ctrl+C where that program runs, then try again; the engine serves one "
            "program at a time.",
        ),
    ],
)
def test_registry_has_actionable_messages(code: ErrorCode, message: str, fix: str) -> None:
    error = GhostbandError(code, "sample.wav")
    assert error.message == message
    assert error.fix == fix
    event = error.event()
    assert event["event"] == "error"
    assert event["message"] == message
    assert event["fix"] == fix


def test_registry_covers_each_exit_code() -> None:
    assert {int(code) for code in ErrorCode} == {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}


@pytest.mark.parametrize("module", ["cli", "console", "importing", "session"])
def test_boundary_raises_use_registry(module: str) -> None:
    source = Path(__file__).resolve().parents[1] / "ghostband" / f"{module}.py"
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            assert isinstance(node.exc, ast.Call), (source.name, node.lineno)
            assert isinstance(node.exc.func, ast.Name), (source.name, node.lineno)
            assert node.exc.func.id == "GhostbandError", (source.name, node.lineno)
            assert isinstance(node.exc.args[0], ast.Attribute), (source.name, node.lineno)
            assert isinstance(node.exc.args[0].value, ast.Name), (source.name, node.lineno)
            assert node.exc.args[0].value.id == "ErrorCode", (source.name, node.lineno)
