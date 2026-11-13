"""Keep the engine protocol reference in docs/architecture.md in step with the parser.

Run `scripts/gen_protocol_doc.py` after changing `engine/src/protocol.cpp` or
`engine/src/protocol.h`; it rewrites the marked section of the architecture document.
`--check` compares without writing and exits nonzero when the section is stale.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- protocol-doc:start -->"
END = "<!-- protocol-doc:end -->"

CONSTANT = re.compile(r"inline constexpr \S+ (?P<name>\w+) = (?P<value>[^;]+);")
COMMAND = re.compile(r'if \(cmd == "(?P<name>[a-z_]+)"\)')
FIELD = re.compile(
    r'\.at\("(?P<read>[a-z_]+)"\)'
    r'|\.contains\("(?P<optional>[a-z_]+)"\)'
    r'|sample\("(?P<sample>[a-z_]+)"\)'
    r"|\bslot\(\)"
)
STATUS = re.compile(r"std::string serialize_status\(.*?json\{(?P<body>.*?)\}\s*\.dump", re.S)
HELLO = re.compile(r"std::string hello_reply\(.*?json\{(?P<body>.*?)\}\s*\.dump", re.S)
ERROR = re.compile(r"std::string error_reply\(.*?json\{(?P<body>.*?)\}\s*\.dump", re.S)
BUSY = re.compile(r"std::string busy_reply\(.*?json\{(?P<body>.*?)\}\s*\.dump", re.S)
KEY = re.compile(r'\{"(?P<key>\w+)"')


@dataclass(frozen=True)
class Field:
    name: str
    optional: bool


@dataclass(frozen=True)
class Command:
    name: str
    fields: tuple[Field, ...]


def constants(header: str) -> dict[str, int]:
    """Numeric protocol constants; a value like `64 * 1024` is multiplied out."""
    found: dict[str, int] = {}
    for match in CONSTANT.finditer(header):
        product = 1
        for part in match["value"].split("*"):
            product *= int(part)
        found[match["name"]] = product
    return found


def fields(body: str) -> tuple[Field, ...]:
    """Keys the parser reads in one command branch, in source order.

    A key is optional when the parser guards it with `contains` or reads it in a
    conditional statement, as `record` does for `path` when the action is `start`.
    """
    order: list[str] = []
    conditional: set[str] = set()
    for match in FIELD.finditer(body):
        name = match["read"] or match["optional"] or match["sample"] or "slot"
        line_start = body.rfind("\n", 0, match.start()) + 1
        if match["optional"] is not None or "if (" in body[line_start : match.start()]:
            conditional.add(name)
        if name not in order:
            order.append(name)
    return tuple(Field(name, name in conditional) for name in order)


def commands(source: str) -> list[Command]:
    """Command branches of `parse_command`, before the final unknown-command throw."""
    end = source.index("Unknown command")
    matches = list(COMMAND.finditer(source, 0, end))
    found: list[Command] = []
    for index, match in enumerate(matches):
        stop = matches[index + 1].start() if index + 1 < len(matches) else end
        found.append(Command(match["name"], fields(source[match.end() : stop])))
    return found


def reply_keys(pattern: re.Pattern[str], source: str) -> list[str]:
    found = pattern.search(source)
    if found is None:
        raise ValueError(f"no reply function matches {pattern.pattern}")
    return [match["key"] for match in KEY.finditer(found["body"])]


def listed(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names[:-1]) + f" and `{names[-1]}`"


def render(header: str, source: str) -> str:
    """The generated section body, from the protocol header and parser."""
    values = constants(header)
    version = values["kProtocolVersion"]
    limit = values["kMaxLineBytes"]
    lines = [
        "This section is generated from `engine/src/protocol.cpp` by",
        "`scripts/gen_protocol_doc.py`; edit the parser, not the table.",
        "",
        f"The engine speaks protocol version {version}. Each control message is one JSON object",
        f"on a single line, at most {limit} bytes, and `cmd` names the command.",
        "",
        "| Command | Fields |",
        "| --- | --- |",
    ]
    for command in commands(source):
        names = [f"`{field.name}`" + ("?" if field.optional else "") for field in command.fields]
        lines.append(f"| `{command.name}` | {', '.join(names) or 'none'} |")
    lines += [
        "",
        "Fields are the JSON keys the parser reads before dispatch; a trailing `?` marks a",
        "key that some valid commands omit.",
        "",
        f"The engine answers `hello` with {listed(reply_keys(HELLO, source))}, and failures",
        f"carry {listed(reply_keys(ERROR, source))}.",
        "",
        "The engine serves one client at a time. A client that connects while another holds it",
        f"gets one reply with {listed(reply_keys(BUSY, source))}, naming the holder's process",
        "and the Unix time it connected, and is then disconnected.",
        "",
        f"Status events carry {listed(reply_keys(STATUS, source))}.",
    ]
    return "\n".join(lines)


def section(header: str, source: str) -> str:
    return f"{START}\n\n{render(header, source)}\n\n{END}"


def update(document: str, block: str) -> str:
    """Replace the marked section, keeping the rest of the document byte for byte."""
    if document.count(START) != 1 or document.count(END) != 1:
        raise ValueError("docs/architecture.md needs exactly one protocol-doc marker pair")
    start, end = document.index(START), document.index(END)
    if end < start:
        raise ValueError("docs/architecture.md has the protocol-doc markers in the wrong order")
    return document[:start] + block + document[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the section is stale")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to read")
    args = parser.parse_args(argv)
    engine = args.root / "engine" / "src"
    header = (engine / "protocol.h").read_text(encoding="utf-8")
    source = (engine / "protocol.cpp").read_text(encoding="utf-8")
    document_path = args.root / "docs" / "architecture.md"
    document = document_path.read_text(encoding="utf-8")
    try:
        updated = update(document, section(header, source))
    except ValueError as error:
        sys.stderr.write(f"gen_protocol_doc: {error}\n")
        return 1
    if updated == document:
        return 0
    if args.check:
        sys.stderr.write(
            "gen_protocol_doc: docs/architecture.md is stale; run scripts/gen_protocol_doc.py\n"
        )
        return 1
    document_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
