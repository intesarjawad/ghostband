"""The protocol reference follows the parser, and `--check` refuses a stale document."""

from pathlib import Path

import pytest

from scripts import gen_protocol_doc
from scripts.gen_protocol_doc import commands, constants, render, section, update

ROOT = Path(__file__).resolve().parents[2]

HEADER = """\
inline constexpr int kProtocolVersion = 2;
inline constexpr std::size_t kPromptSlots = 6;
inline constexpr std::size_t kMaxLineBytes = 8 * 1024;
"""

SOURCE = """\
Command parse_command(std::string_view line) {
  if (cmd == "ping") return Ping{};
  if (cmd == "load") {
    auto name = text(object.at("name"));
    if (name != "a")
      throw std::invalid_argument("name must be a");
    return Load{name, path(object.at("folder"))};
  }
  if (cmd == "prompt")
    return Prompt{slot(), text(object.at("text")), number(object.at("weight"), 0, 1)};
  if (cmd == "params") {
    Params params;
    if (object.contains("temperature")) params.temperature = number(object.at("temperature"));
    if (object.contains("topk")) params.topk = 8;
    return params;
  }
  if (cmd == "record") {
    const auto action = text(object.at("action"));
    if (action == "start") return RecordStart{path(object.at("path"))};
    if (action == "stop") return RecordStop{};
    throw std::invalid_argument("Record action must be start or stop");
  }
  throw std::invalid_argument("Unknown command: " + cmd);
}

std::string serialize_status(const Status& value) {
  return json{{"event", "status"},
              {"played_samples", value.played_samples}}
      .dump();
}

std::string hello_reply(std::string_view engine_version) {
  return json{{"ok", true},
              {"protocol", kProtocolVersion},
              {"version", engine_version},
              {"upstream", "v2.0.3"}}
      .dump();
}

std::string error_reply(std::string_view message) {
  return json{{"ok", false}, {"error", message}}.dump();
}

std::string busy_reply(std::optional<std::int64_t> holder_pid, std::int64_t held_since) {
  return json{{"ok", false},
              {"holder_pid", holder_pid ? json(*holder_pid) : json(nullptr)},
              {"held_since", held_since}}
      .dump();
}
"""

EXPECTED = """\
This section is generated from `engine/src/protocol.cpp` by
`scripts/gen_protocol_doc.py`; edit the parser, not the table.

The engine speaks protocol version 2. Each control message is one JSON object
on a single line, at most 8192 bytes, and `cmd` names the command.

| Command | Fields |
| --- | --- |
| `ping` | none |
| `load` | `name`, `folder` |
| `prompt` | `slot`, `text`, `weight` |
| `params` | `temperature`?, `topk`? |
| `record` | `action`, `path`? |

Fields are the JSON keys the parser reads before dispatch; a trailing `?` marks a
key that some valid commands omit.

The engine answers `hello` with `ok`, `protocol`, `version` and `upstream`, and failures
carry `ok` and `error`.

The engine serves one client at a time. A client that connects while another holds it
gets one reply with `ok`, `holder_pid` and `held_since`, naming the holder's process
and the Unix time it connected, and is then disconnected.

Status events carry `event` and `played_samples`."""


def repository(tmp_path: Path, document: str) -> Path:
    engine = tmp_path / "engine" / "src"
    engine.mkdir(parents=True)
    (engine / "protocol.h").write_text(HEADER, encoding="utf-8")
    (engine / "protocol.cpp").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text(document, encoding="utf-8")
    return tmp_path


def test_numeric_constants_multiply_their_factors() -> None:
    assert constants(HEADER) == {"kProtocolVersion": 2, "kPromptSlots": 6, "kMaxLineBytes": 8192}


def test_each_command_branch_becomes_one_command_in_source_order() -> None:
    found = [
        (command.name, [(field.name, field.optional) for field in command.fields])
        for command in commands(SOURCE)
    ]
    assert found == [
        ("ping", []),
        ("load", [("name", False), ("folder", False)]),
        ("prompt", [("slot", False), ("text", False), ("weight", False)]),
        ("params", [("temperature", True), ("topk", True)]),
        ("record", [("action", False), ("path", True)]),
    ]


def test_the_rendered_section_lists_commands_fields_and_replies() -> None:
    assert render(HEADER, SOURCE) == EXPECTED


def test_update_replaces_only_the_marked_section() -> None:
    document = (
        f"# Architecture\n\n{gen_protocol_doc.START}\nold text\n{gen_protocol_doc.END}\n\nEnd.\n"
    )
    block = section(HEADER, SOURCE)
    assert update(document, block) == f"# Architecture\n\n{block}\n\nEnd.\n"


@pytest.mark.parametrize(
    "document",
    [
        "# Architecture\n",
        f"{gen_protocol_doc.START}\nold\n{gen_protocol_doc.END}\n{gen_protocol_doc.START}\n",
        f"{gen_protocol_doc.END}\nold\n{gen_protocol_doc.START}\n",
    ],
)
def test_update_requires_one_marker_pair_in_order(document: str) -> None:
    with pytest.raises(ValueError, match="protocol-doc marker"):
        update(document, section(HEADER, SOURCE))


def test_check_accepts_the_committed_document() -> None:
    assert gen_protocol_doc.main(["--check", "--root", str(ROOT)]) == 0


def test_check_reports_a_stale_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = f"# Architecture\n\n{gen_protocol_doc.START}\nold text\n{gen_protocol_doc.END}\n"
    root = repository(tmp_path, document)
    assert gen_protocol_doc.main(["--check", "--root", str(root)]) == 1
    assert "stale" in capsys.readouterr().err
    assert (root / "docs" / "architecture.md").read_text(encoding="utf-8") == document


def test_a_write_leaves_the_document_current(tmp_path: Path) -> None:
    document = f"# Architecture\n\n{gen_protocol_doc.START}\n{gen_protocol_doc.END}\n\nEnd.\n"
    root = repository(tmp_path, document)
    assert gen_protocol_doc.main(["--root", str(root)]) == 0
    assert (root / "docs" / "architecture.md").read_text(encoding="utf-8") == (
        f"# Architecture\n\n{section(HEADER, SOURCE)}\n\nEnd.\n"
    )
    assert gen_protocol_doc.main(["--check", "--root", str(root)]) == 0


def test_a_document_without_markers_fails_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path, "# Architecture\n")
    assert gen_protocol_doc.main(["--root", str(root)]) == 1
    assert "marker" in capsys.readouterr().err
    assert (root / "docs" / "architecture.md").read_text(encoding="utf-8") == "# Architecture\n"
