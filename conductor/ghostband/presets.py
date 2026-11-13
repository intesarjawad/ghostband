"""Bundled style presets and validated presets from the user's preset folder."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Text = Annotated[str, Field(min_length=1)]

_UNSAFE_FILENAME = re.compile(r"[/\\:\x00-\x1f\x7f]+")


class PresetError(ValueError):
    """A preset file that cannot be used, naming the file, field and reason."""

    def __init__(self, path: Path, field: str, reason: str, line: int | None = None) -> None:
        self.path = path
        self.field = field
        self.reason = reason
        self.line = line
        if line is not None:
            location = f"{path}: line {line}"
        elif field:
            location = f"{path}: {field}"
        else:
            location = str(path)
        super().__init__(f"{location}: {reason}")


@dataclass(frozen=True)
class PresetProblem:
    """A preset file skipped while loading, at the line or field that failed."""

    path: Path
    reason: str
    line: int | None = None
    field: str = ""

    def __str__(self) -> str:
        if self.line is not None:
            location = f"{self.path}: line {self.line}"
        elif self.field:
            location = f"{self.path}: {self.field}"
        else:
            location = str(self.path)
        return f"{location}: {self.reason}"


class Guidance(BaseModel):
    """Generation settings; omitted values keep the engine's defaults."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    cfg_musiccoca: Annotated[float, Field(ge=0)] = 3.0
    cfg_notes: Annotated[float, Field(ge=0)] = 5.0
    temperature: Annotated[float, Field(ge=0)] = 1.0


class _PresetFile(BaseModel):
    """The fields a user-written TOML file may set."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: Text
    a: Text
    b: Text
    guidance: Guidance = Field(default_factory=Guidance)

    @field_validator("name", "a", "b")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Preset(_PresetFile):
    """A style pair and its guidance; `measured` marks presets with measurements."""

    measured: bool = True


PRESETS: tuple[Preset, ...] = (
    Preset(
        name="Warm jazz / chiptune",
        a="warm jazz piano trio, upright bass, brushed drums",
        b="8-bit chiptune, square wave leads, NES drums",
    ),
    Preset(
        name="Folk / metal",
        a="fingerpicked acoustic guitar folk",
        b="heavy metal, palm-muted guitars, double kick",
    ),
    Preset(
        name="Bossa nova / synthwave",
        a="bossa nova, nylon guitar, soft percussion",
        b="80s synthwave, analog synths, gated reverb drums",
    ),
    Preset(
        name="String quartet / lo-fi",
        a="string quartet, chamber music",
        b="lo-fi hip hop, dusty drums, vinyl crackle",
    ),
)


def preset_directory() -> Path:
    """The preset folder inside the app's Application Support directory."""
    return Path.home() / "Library/Application Support/ghostband/presets"


def load_presets(
    directory: Path | None = None,
) -> tuple[tuple[Preset, ...], list[PresetProblem]]:
    """Return usable presets and the files skipped while reading them."""
    user, problems = load_user_presets(directory or preset_directory())
    return PRESETS + user, problems


def load_user_presets(
    directory: Path,
) -> tuple[tuple[Preset, ...], list[PresetProblem]]:
    """Read every TOML file in a folder, in file-name order, skipping broken ones."""
    if not directory.is_dir():
        return (), []
    presets: list[Preset] = []
    problems: list[PresetProblem] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            presets.append(_read_preset(path))
        except PresetError as error:
            problems.append(PresetProblem(error.path, error.reason, error.line, error.field))
    return tuple(presets), problems


def save_preset(
    name: str,
    a: str,
    b: str,
    *,
    guidance: Guidance | None = None,
    directory: Path | None = None,
) -> Path:
    """Write a preset file with its slots and only the guidance values that changed."""
    folder = directory or preset_directory()
    user = _PresetFile.model_validate(
        {"name": name, "a": a, "b": b, "guidance": (guidance or Guidance()).model_dump()}
    )
    lines = [
        f"name = {_toml_string(user.name)}",
        f"a = {_toml_string(user.a)}",
        f"b = {_toml_string(user.b)}",
    ]
    defaults = Guidance()
    changed = {
        key: value
        for key, value in user.guidance.model_dump().items()
        if value != getattr(defaults, key)
    }
    if changed:
        lines.extend(["", "[guidance]"])
        lines.extend(f"{key} = {value!r}" for key, value in changed.items())
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_file_stem(user.name)}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _file_stem(name: str) -> str:
    """Keep a name usable as one file in the preset folder."""
    stem = _UNSAFE_FILENAME.sub("-", name).strip(" .-")
    if not stem:
        raise ValueError(f"preset name {name!r} must contain a usable file name")
    return stem


def _toml_string(value: str) -> str:
    """Escape text as a TOML basic string, leaving non-ASCII text readable."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    escaped = "".join(
        f"\\u{ord(char):04X}" if ord(char) < 0x20 or ord(char) == 0x7F else char for char in escaped
    )
    return f'"{escaped}"'


def _read_preset(path: Path) -> Preset:
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
    except (OSError, UnicodeDecodeError) as error:
        raise PresetError(path, "", str(error)) from error
    except tomllib.TOMLDecodeError as error:
        raise PresetError(path, "", f"invalid TOML: {error}", _decode_line(error)) from error
    try:
        user = _PresetFile.model_validate(data)
    except ValidationError as error:
        _raise_field_error(path, error)
    return Preset.model_validate({**user.model_dump(), "measured": False})


def _decode_line(error: tomllib.TOMLDecodeError) -> int | None:
    """The line a decode error points at, from the offset or the message."""
    position = getattr(error, "pos", None)
    document = getattr(error, "doc", None)
    if isinstance(position, int) and isinstance(document, str):
        return document.count("\n", 0, position) + 1
    match = re.search(r"at line (\d+)", str(error))
    return int(match.group(1)) if match else None


def _raise_field_error(path: Path, error: ValidationError) -> NoReturn:
    problem = error.errors()[0]
    field = ".".join(str(part) for part in problem["loc"]) or "file"
    raise PresetError(path, field, problem["msg"]) from error
