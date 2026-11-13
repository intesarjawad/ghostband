"""Run and score the blind listening test that decides which bundled presets ship.

Run it from the repository root with the engine started and GHOSTBAND_ENGINE_SOCKET set:

    uv run python scripts/listening_test.py run [--chart CHART] [--seconds N]
    uv run python scripts/listening_test.py score

`run` plays every bundled preset at slider positions 0, 0.25, 0.5, 0.75 and 1 over one chart,
in an order shuffled once and saved to the CSV before the first trial. After each trial it asks
for two ratings from 1 to 5: whether the tune is recognisable and whether the style is
convincing. The terminal shows only the trial number, never the preset or slider position.
Each rating is saved as soon as it is given, so an interrupted test resumes at the first
unrated trial.

`score` reads the CSV, prints each preset's mean rating on both axes and names the presets
whose mean is below 3 on either axis; those are removed from the bundled list. It exits nonzero
while a trial is unrated or a preset still in the bundled list is below 3.
"""

import argparse
import asyncio
import csv
import random
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import fmean

from ghostband import console, first_run
from ghostband.chart import Chart
from ghostband.cli import play_chart
from ghostband.errors import GhostbandError
from ghostband.importing import read_chart
from ghostband.presets import PRESETS
from ghostband.session import Session

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "benchmarks" / "listening.csv"
CHART_PATH = ROOT / "charts" / "greensleeves.chart.json"
SLIDER_POSITIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
RATINGS = range(1, 6)
# A preset ships only when its mean on both axes reaches this rating.
RATING_FLOOR = 3
CSV_FIELDS = ("trial", "preset", "slider", "tune_recognisable", "style_convincing")
QUESTIONS = ("Tune recognisable", "Style convincing")

Play = Callable[["Trial"], None]
Ask = Callable[[str], str]


@dataclass(frozen=True)
class Trial:
    """One preset at one slider position, with its two ratings once given."""

    trial: int
    preset: str
    slider: float
    tune: int | None = None
    style: int | None = None

    @property
    def rated(self) -> bool:
        return self.tune is not None and self.style is not None


def schedule(presets: Sequence[str], rng: random.Random) -> list[Trial]:
    """Every preset at every slider position, numbered in a shuffled order."""
    cells = [(preset, slider) for preset in presets for slider in SLIDER_POSITIONS]
    rng.shuffle(cells)
    return [Trial(index, preset, slider) for index, (preset, slider) in enumerate(cells, 1)]


def _rating(text: str) -> int | None:
    stripped = text.strip()
    if not stripped:
        return None
    value = int(stripped)
    if value not in RATINGS:
        raise ValueError(f"rating {value} is outside 1 to 5")
    return value


def read_trials(path: Path) -> list[Trial]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    try:
        return [
            Trial(
                trial=int(row["trial"]),
                preset=row["preset"],
                slider=float(row["slider"]),
                tune=_rating(row["tune_recognisable"]),
                style=_rating(row["style_convincing"]),
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{path}: {error}; expected columns {', '.join(CSV_FIELDS)}") from error


def write_trials(path: Path, trials: Sequence[Trial]) -> None:
    """Replace the CSV in one step, so a crash mid-write cannot lose earlier ratings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".csv.partial")
    with partial.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_FIELDS)
        for trial in trials:
            writer.writerow(
                [
                    trial.trial,
                    trial.preset,
                    f"{trial.slider:g}",
                    "" if trial.tune is None else trial.tune,
                    "" if trial.style is None else trial.style,
                ]
            )
    partial.replace(path)


def ask_rating(question: str, ask: Ask) -> int:
    """Ask until the answer is a whole number from 1 to 5."""
    while True:
        answer = ask(f"{question} (1 to 5): ").strip()
        if answer.isdigit() and int(answer) in RATINGS:
            return int(answer)


def run(path: Path, presets: Sequence[str], play: Play, ask: Ask, rng: random.Random) -> None:
    """Play and rate every unrated trial, saving after each one."""
    trials = read_trials(path) if path.exists() else schedule(presets, rng)
    write_trials(path, trials)
    for index, trial in enumerate(trials):
        if trial.rated:
            continue
        sys.stdout.write(f"Trial {trial.trial} of {len(trials)}\n")
        play(trial)
        tune, style = (ask_rating(question, ask) for question in QUESTIONS)
        trials[index] = replace(trial, tune=tune, style=style)
        write_trials(path, trials)


@dataclass(frozen=True)
class PresetScore:
    preset: str
    trials: int
    tune: float
    style: float

    @property
    def passed(self) -> bool:
        return self.tune >= RATING_FLOOR and self.style >= RATING_FLOOR


@dataclass(frozen=True)
class Score:
    unrated: tuple[Trial, ...]
    missing: tuple[tuple[str, float], ...]
    presets: tuple[PresetScore, ...]


def score(trials: Sequence[Trial], shipped: Sequence[str]) -> Score:
    """Mean ratings per preset, plus the trials and shipped cells still without a rating."""
    ratings: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for trial in trials:
        if trial.tune is not None and trial.style is not None:
            ratings[trial.preset].append((trial.tune, trial.style))
    presets = tuple(
        PresetScore(
            preset=name,
            trials=len(pairs),
            tune=fmean(tune for tune, _ in pairs),
            style=fmean(style for _, style in pairs),
        )
        for name, pairs in sorted(ratings.items())
    )
    present = {(trial.preset, trial.slider) for trial in trials}
    missing = tuple(
        (preset, slider)
        for preset in shipped
        for slider in SLIDER_POSITIONS
        if (preset, slider) not in present
    )
    unrated = tuple(trial for trial in trials if not trial.rated)
    return Score(unrated=unrated, missing=missing, presets=presets)


def render(result: Score, shipped: Sequence[str]) -> str:
    """The per-preset table and verdicts as Markdown."""
    lines = [
        "| Preset | Trials | Tune recognisable (mean) | Style convincing (mean) | Verdict |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for preset in result.presets:
        if preset.passed:
            verdict = "keep"
        elif preset.preset in shipped:
            verdict = "remove"
        else:
            verdict = "removed"
        lines.append(
            f"| {preset.preset} | {preset.trials} | {preset.tune:.2f} | {preset.style:.2f} "
            f"| {verdict} |"
        )
    lines.extend(f"Unrated: trial {trial.trial}" for trial in result.unrated)
    lines.extend(f"Missing: {preset} at slider {slider:g}" for preset, slider in result.missing)
    return "\n".join(lines) + "\n"


def failed(result: Score, shipped: Sequence[str]) -> bool:
    """Whether the test is incomplete or a preset below the floor still ships."""
    below = any(not preset.passed and preset.preset in shipped for preset in result.presets)
    return bool(result.unrated or result.missing or below)


def play_trial(chart: Chart, trial: Trial, seconds: float | None) -> None:
    """Play one trial through the real engine with the slider held still."""
    session = Session(fake_engine=False)
    session.load_chart(chart)
    preset = [preset.name for preset in session.presets].index(trial.preset)
    # Nothing is recorded; the control path only needs a destination for record commands.
    session.control({"type": "preset", "value": preset}, record_path=Path("take.wav"))
    session.connect()
    session.slew.jump(trial.slider)
    asyncio.run(play_chart(session, seconds, None))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("run", "score"))
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--chart", type=Path, default=CHART_PATH, help="chart every trial plays")
    parser.add_argument(
        "--seconds", type=float, help="length of each trial; by default the whole chart"
    )
    args = parser.parse_args()
    shipped = [preset.name for preset in PRESETS]
    if args.command == "run":
        first_run.configure_environment(first_run.Paths.defaults())
        try:
            chart = read_chart(args.chart)
            run(
                args.csv,
                shipped,
                lambda trial: play_trial(chart, trial, args.seconds),
                input,
                random.SystemRandom(),
            )
        except GhostbandError as error:
            console.fail(error)
            return int(error.code)
    if not args.csv.exists():
        parser.error(f"{args.csv} does not exist; run the test first")
    result = score(read_trials(args.csv), shipped)
    sys.stdout.write(render(result, shipped))
    return 1 if failed(result, shipped) else 0


if __name__ == "__main__":
    sys.exit(main())
