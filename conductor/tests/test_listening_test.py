"""The blind listening test schedules, resumes and scores without playing any audio."""

import random
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.listening_test import (
    SLIDER_POSITIONS,
    Trial,
    ask_rating,
    failed,
    read_trials,
    render,
    run,
    schedule,
    score,
    write_trials,
)

PRESETS = ("Jazz to chiptune", "Folk to metal")


def seeded(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - a repeatable shuffle, not a secret


def answers(*values: str) -> Iterator[str]:
    yield from values


def test_the_schedule_covers_every_preset_and_slider_once_in_shuffled_order() -> None:
    trials = schedule(PRESETS, seeded(7))
    cells = [(trial.preset, trial.slider) for trial in trials]
    assert sorted(cells) == sorted((p, s) for p in PRESETS for s in SLIDER_POSITIONS)
    assert [trial.trial for trial in trials] == list(range(1, len(cells) + 1))
    in_order = [(p, s) for p in PRESETS for s in SLIDER_POSITIONS]
    assert cells != in_order
    assert not any(trial.rated for trial in trials)


def test_a_rating_is_asked_again_until_it_is_one_to_five() -> None:
    replies = answers("", "0", "6", "three", "2.5", " 4 ")
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        return next(replies)

    assert ask_rating("Style convincing", ask) == 4
    assert len(asked) == 6
    assert asked[0] == "Style convincing (1 to 5): "


def test_run_hides_the_trial_and_saves_each_rating(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "listening.csv"
    played: list[Trial] = []
    replies = answers(*["4", "3"] * len(SLIDER_POSITIONS) * len(PRESETS))

    def play(trial: Trial) -> None:
        played.append(trial)
        # Every earlier trial is already on disk when the next one plays.
        assert sum(saved.rated for saved in read_trials(path)) == len(played) - 1

    run(path, PRESETS, play, lambda _question: next(replies), seeded(1))
    trials = read_trials(path)
    assert played == [Trial(trial.trial, trial.preset, trial.slider) for trial in trials]
    assert all((trial.tune, trial.style) == (4, 3) for trial in trials)
    shown = capsys.readouterr().out
    assert "Trial 1 of 10" in shown
    assert not any(preset in shown for preset in PRESETS)


def test_an_interrupted_run_resumes_at_the_first_unrated_trial(tmp_path: Path) -> None:
    path = tmp_path / "listening.csv"
    trials = schedule(PRESETS, seeded(3))
    trials[:4] = [Trial(t.trial, t.preset, t.slider, 5, 5) for t in trials[:4]]
    write_trials(path, trials)
    played: list[int] = []
    replies = answers(*["2"] * 12)

    def play(trial: Trial) -> None:
        played.append(trial.trial)

    run(path, PRESETS, play, lambda _question: next(replies), seeded(99))
    assert played == [trial.trial for trial in trials[4:]]
    saved = read_trials(path)
    assert [(t.preset, t.slider) for t in saved] == [(t.preset, t.slider) for t in trials]
    assert [t.tune for t in saved] == [5] * 4 + [2] * 6


def test_a_csv_rating_outside_one_to_five_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "listening.csv"
    path.write_text(
        "trial,preset,slider,tune_recognisable,style_convincing\n1,Folk to metal,0,7,3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="outside 1 to 5"):
        read_trials(path)


def rated(preset: str, tune: int, style: int) -> list[Trial]:
    return [
        Trial(index, preset, slider, tune, style)
        for index, slider in enumerate(SLIDER_POSITIONS, 1)
    ]


def test_a_preset_below_three_on_either_axis_is_to_be_removed() -> None:
    trials = [*rated("Jazz to chiptune", 4, 3), *rated("Folk to metal", 5, 2)]
    result = score(trials, PRESETS)
    assert [(p.preset, p.tune, p.style, p.passed) for p in result.presets] == [
        ("Folk to metal", 5.0, 2.0, False),
        ("Jazz to chiptune", 4.0, 3.0, True),
    ]
    assert failed(result, PRESETS)
    report = render(result, PRESETS)
    assert "| Folk to metal | 5 | 5.00 | 2.00 | remove |" in report
    assert "| Jazz to chiptune | 5 | 4.00 | 3.00 | keep |" in report
    # Once it is taken out of the bundled list, the same ratings pass the gate.
    assert not failed(result, PRESETS[:1])
    assert "| Folk to metal | 5 | 5.00 | 2.00 | removed |" in render(result, PRESETS[:1])


def test_the_score_fails_while_a_trial_is_unrated_or_missing() -> None:
    trials = [*rated("Jazz to chiptune", 4, 4), *rated("Folk to metal", 4, 4)[:-1]]
    trials.append(Trial(10, "Folk to metal", 1.0))
    unrated = score(trials, PRESETS)
    assert [trial.trial for trial in unrated.unrated] == [10]
    assert failed(unrated, PRESETS)
    assert "Unrated: trial 10" in render(unrated, PRESETS)
    missing = score(trials[:-1], PRESETS)
    assert missing.missing == (("Folk to metal", 1.0),)
    assert "Missing: Folk to metal at slider 1" in render(missing, PRESETS)
    assert not failed(score(rated("Jazz to chiptune", 3, 3), PRESETS[:1]), PRESETS[:1])
