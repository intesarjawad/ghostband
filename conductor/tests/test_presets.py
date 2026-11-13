from pathlib import Path

import pytest
from ghostband.presets import (
    PRESETS,
    Guidance,
    Preset,
    load_presets,
    load_user_presets,
    preset_directory,
    save_preset,
)


def write_preset(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def test_bundled_presets_are_measured_and_keep_the_engine_pairs() -> None:
    assert [preset.name for preset in PRESETS] == [
        "Warm jazz / chiptune",
        "Folk / metal",
        "Bossa nova / synthwave",
        "String quartet / lo-fi",
    ]
    assert [(preset.a, preset.b) for preset in PRESETS] == [
        (
            "warm jazz piano trio, upright bass, brushed drums",
            "8-bit chiptune, square wave leads, NES drums",
        ),
        (
            "fingerpicked acoustic guitar folk",
            "heavy metal, palm-muted guitars, double kick",
        ),
        (
            "bossa nova, nylon guitar, soft percussion",
            "80s synthwave, analog synths, gated reverb drums",
        ),
        (
            "string quartet, chamber music",
            "lo-fi hip hop, dusty drums, vinyl crackle",
        ),
    ]
    assert all(preset.measured for preset in PRESETS)
    assert [preset.guidance.model_dump() for preset in PRESETS] == [
        {"cfg_musiccoca": 3.0, "cfg_notes": 5.0, "temperature": 1.0}
    ] * 4


def test_default_folder_is_application_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert preset_directory() == tmp_path / "Library/Application Support/ghostband/presets"


def test_missing_user_folder_leaves_the_bundled_presets(tmp_path: Path) -> None:
    assert load_presets(tmp_path / "missing") == (PRESETS, [])


def test_user_presets_append_in_file_order_and_are_unmeasured(tmp_path: Path) -> None:
    write_preset(
        tmp_path,
        "b.toml",
        'name = "Second"\na = "ambient piano"\nb = "drum and bass"\n'
        "\n[guidance]\ntemperature = 0.7\n",
    )
    write_preset(tmp_path, "a.toml", 'name = "First"\na = "a1"\nb = "b1"\n')
    write_preset(tmp_path, "notes.txt", "ignored")
    loaded, problems = load_presets(tmp_path)
    assert problems == []
    assert [preset.name for preset in loaded] == [
        *(preset.name for preset in PRESETS),
        "First",
        "Second",
    ]
    assert all(preset.measured for preset in loaded[: len(PRESETS)])
    assert [preset.measured for preset in loaded[len(PRESETS) :]] == [False, False]
    assert (loaded[-2].a, loaded[-2].b) == ("a1", "b1")
    assert loaded[-1].guidance.model_dump() == {
        "cfg_musiccoca": 3.0,
        "cfg_notes": 5.0,
        "temperature": 0.7,
    }


@pytest.mark.parametrize(
    ("content", "field", "reason"),
    [
        ('a = "x"\nb = "y"\n', "name", "Field required"),
        ('name = "N"\na = "x"\nb = "y"\ntempo = 120\n', "tempo", "Extra inputs are not permitted"),
        (
            'name = "N"\na = "x"\nb = "y"\nmeasured = true\n',
            "measured",
            "Extra inputs are not permitted",
        ),
        ('name = "N"\na = "   "\nb = "y"\n', "a", "must not be blank"),
        ('name = 3\na = "x"\nb = "y"\n', "name", "Input should be a valid string"),
        (
            'name = "N"\na = "x"\nb = "y"\n[guidance]\ntemperature = -1\n',
            "guidance.temperature",
            "greater than or equal to 0",
        ),
        (
            'name = "N"\na = "x"\nb = "y"\n[guidance]\ntopk = 10\n',
            "guidance.topk",
            "Extra inputs are not permitted",
        ),
        (
            'name = "N"\na = "x"\nb = "y"\nguidance = "warm"\n',
            "guidance",
            "Input should be a valid dictionary",
        ),
    ],
)
def test_a_bad_file_is_skipped_and_names_the_field(
    tmp_path: Path, content: str, field: str, reason: str
) -> None:
    path = write_preset(tmp_path, "bad.toml", content)
    write_preset(tmp_path, "good.toml", 'name = "Good"\na = "x"\nb = "y"\n')
    loaded, problems = load_user_presets(tmp_path)
    assert [preset.name for preset in loaded] == ["Good"]
    assert len(problems) == 1
    problem = problems[0]
    assert problem.path == path
    assert problem.field == field
    assert problem.line is None
    assert reason in problem.reason
    assert str(path) in str(problem)
    assert field in str(problem)


def test_invalid_toml_is_skipped_with_its_line(tmp_path: Path) -> None:
    path = write_preset(
        tmp_path, "broken.toml", 'name = "Good"\na = "x"\nb = "y"\n\n[guidance\ntemperature = 1\n'
    )
    write_preset(tmp_path, "good.toml", 'name = "Good"\na = "x"\nb = "y"\n')
    loaded, problems = load_user_presets(tmp_path)
    assert [preset.name for preset in loaded] == ["Good"]
    assert len(problems) == 1
    problem = problems[0]
    assert problem.path == path
    assert problem.line == 5
    assert "invalid TOML" in problem.reason
    assert "line 5" in str(problem)


def test_save_preset_round_trips_slots_and_changed_guidance(tmp_path: Path) -> None:
    saved = save_preset(
        "Late night",
        "ambient piano",
        "drum and bass",
        guidance=Guidance(temperature=0.7),
        directory=tmp_path,
    )
    assert saved == tmp_path / "Late night.toml"
    text = saved.read_text(encoding="utf-8")
    assert "temperature = 0.7" in text
    assert "cfg_musiccoca" not in text
    assert "cfg_notes" not in text
    loaded, problems = load_presets(tmp_path)
    assert problems == []
    assert loaded[-1] == Preset(
        name="Late night",
        a="ambient piano",
        b="drum and bass",
        guidance=Guidance(temperature=0.7),
        measured=False,
    )


def test_save_preset_omits_guidance_at_its_defaults(tmp_path: Path) -> None:
    saved = save_preset("Plain", "a", "b", directory=tmp_path)
    assert "[guidance]" not in saved.read_text(encoding="utf-8")
    loaded, problems = load_presets(tmp_path)
    assert problems == []
    assert loaded[-1].guidance == Guidance()


def test_save_preset_escapes_text_and_keeps_unicode(tmp_path: Path) -> None:
    saved = save_preset(
        'Late "night"',
        'a "b" \\ c 😀',
        "first\nsecond",
        directory=tmp_path,
    )
    loaded, problems = load_presets(tmp_path)
    assert problems == []
    assert saved.is_file()
    assert (loaded[-1].name, loaded[-1].a, loaded[-1].b) == (
        'Late "night"',
        'a "b" \\ c 😀',
        "first\nsecond",
    )


def test_save_preset_keeps_the_file_inside_the_folder(tmp_path: Path) -> None:
    assert save_preset("../../escape", "a", "b", directory=tmp_path) == tmp_path / "escape.toml"
