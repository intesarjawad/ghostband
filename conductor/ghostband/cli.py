"""Local chart import, playback, export and machine checks."""

import asyncio
import json
import secrets
import signal
import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from time import monotonic
from types import FrameType

import click
from aiohttp import web

from ghostband import bench as engine_bench
from ghostband import console, first_run, setup_models
from ghostband import doctor as doctor_checks
from ghostband.chart import Chart
from ghostband.errors import ErrorCode, GhostbandError
from ghostband.evaluation import live as live_report
from ghostband.evaluation.provenance import TakeProvenance
from ghostband.export import (
    chart_to_midi,
    export_bundle,
    share_safe,
)
from ghostband.importing import import_tune, read_chart
from ghostband.ireal import chart_to_ireal, is_ireal_source, read_ireal
from ghostband.presets import load_presets
from ghostband.render import OfflineEngine, render_chart
from ghostband.server import create_app
from ghostband.session import SHIPPED_ONSET_MODE, TEMPO_MAX, TEMPO_MIN, Session

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)
# Shells report a process ended by signal N as status 128 + N.
SIGNAL_STATUS_BASE = 128


def end_echoed_interrupt(received: Sequence[signal.Signals]) -> None:
    """Start the next line below the ^C the terminal echoes without a newline."""
    if signal.SIGINT in received and sys.stdout.isatty():
        click.echo()


@contextmanager
def signals_noted(received: list[signal.Signals]) -> Generator[None]:
    """Note Ctrl+C and SIGTERM in received, so a take ends cleanly instead of unwinding.

    A second one pressed while the take is saved and the engine released is ignored.
    """

    def note(number: int, _frame: FrameType | None) -> None:
        received.append(signal.Signals(number))

    previous = {number: signal.signal(number, note) for number in STOP_SIGNALS}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class RegistryGroup(click.Group):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.ClickException as error:
            console.write(ErrorCode.INVALID_INPUT, error.format_message(), error=True)
            ctx.exit(int(ErrorCode.INVALID_INPUT))

    def invoke(self, ctx: click.Context) -> object:
        received = signal.SIGINT

        def interrupt(number: int, frame: FrameType | None) -> None:
            nonlocal received
            received = signal.Signals(number)
            signal.default_int_handler(number, frame)

        # SIGTERM unwinds like Ctrl+C, so an engine this process started is stopped too.
        previous = signal.signal(signal.SIGTERM, interrupt)
        try:
            return super().invoke(ctx)
        except KeyboardInterrupt:
            end_echoed_interrupt([received])
            ctx.exit(SIGNAL_STATUS_BASE + received)
        except GhostbandError as error:
            console.fail(error)
            ctx.exit(int(error.code))
        except click.ClickException as error:
            console.write(ErrorCode.INVALID_INPUT, error.format_message(), error=True)
            ctx.exit(int(ErrorCode.INVALID_INPUT))
        except OSError as error:
            console.write(ErrorCode.IO_FAILED, str(error), error=True)
            ctx.exit(int(ErrorCode.IO_FAILED))
        except ValueError as error:
            console.write(ErrorCode.INVALID_INPUT, str(error), error=True)
            ctx.exit(int(ErrorCode.INVALID_INPUT))
        finally:
            signal.signal(signal.SIGTERM, previous)


@click.group(cls=RegistryGroup, invoke_without_command=True)
@click.version_option(package_name="ghostband", prog_name="ghostband")
@click.option("--fake-engine", is_flag=True, help="Run without models or an audio device.")
@click.option("--dev-origin", default=None, help="Allow one explicit development origin.")
@click.pass_context
def main(ctx: click.Context, fake_engine: bool, dev_origin: str | None) -> None:
    """Import a tune, play a chart, or start the local server."""
    ctx.obj = fake_engine
    paths = first_run.Paths.defaults()
    first_run.configure_environment(paths)
    if ctx.invoked_subcommand is None:
        token = secrets.token_urlsafe(32)
        app = create_app(
            Session(fake_engine=fake_engine),
            token=token,
            dev_origin=dev_origin,
            auto_setup=not fake_engine,
        )
        console.write("serving", f"http://127.0.0.1:7777/?t={token}")
        web.run_app(app, host="127.0.0.1", port=7777, print=None, access_log=None)


@main.command("import")
@click.argument("input_path", type=str)
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--extractor", type=click.Choice(["permissive", "sheetsage2"]), default="permissive")
@click.option("--time", "show_time", is_flag=True, help="Print import stages and elapsed seconds.")
def import_command(input_path: str, output: Path | None, extractor: str, show_time: bool) -> None:
    """Import audio, MIDI, a chart or an iReal Pro link without network access."""
    started = monotonic()

    def progress(stage: str, pct: int) -> None:
        if show_time:
            console.write("progress", f"{stage} {pct}% {monotonic() - started:.3f} s")

    path = Path(input_path)
    if is_ireal_source(path) or is_ireal_source(input_path):
        chart = read_ireal(path if path.exists() else input_path)
    else:
        chart = asyncio.run(
            import_tune(path, extractor=extractor, progress=progress, notice=console.write)
        )
    if output is None and not path.exists():
        raise GhostbandError(ErrorCode.INVALID_INPUT, "choose --output when importing a link")
    destination = output or path.with_suffix(".chart.json")
    if path.exists() and destination.resolve() == path.resolve():
        raise GhostbandError(
            ErrorCode.INVALID_INPUT, "choose --output to avoid replacing the input"
        )
    destination.write_text(chart.model_dump_json(indent=2) + "\n", encoding="utf-8")
    console.write("wrote", str(destination))


async def play_chart(
    session: Session,
    seconds: float | None,
    record: Path | None,
    *,
    sweep: bool = False,
    stop: Sequence[signal.Signals] = (),
) -> tuple[float, list[tuple[float, float]]]:
    """Run the transport, then return the play clock and beat log a take needs.

    The take also ends, like at its last bar, once `stop` holds a signal.
    """
    play_time = 0.0
    beat_log: list[tuple[float, float]] = []
    try:
        if record is not None:
            session.record("start", record)
        session.play()
        play_time = session.play_time_s
        started = monotonic()
        while session.scheduler is not None and session.scheduler.running and not stop:
            if seconds is not None and monotonic() - started >= seconds:
                break
            if sweep and session.chart is not None and session.chart.duration_beats > 0:
                position = session.scheduler.audible_beat(session.now())
                fraction = min(1.0, max(0.0, position / session.chart.duration_beats))
                session.control(
                    {"type": "slider", "value": fraction},
                    record_path=record or Path("take.wav"),
                )
            session.tick()
            await asyncio.sleep(0.01)
    finally:
        beat_log = session.beat_log()
        if play_time == 0.0:
            play_time = session.play_time_s
        session.close()
    return play_time, beat_log


def write_take_sidecar(  # noqa: PLR0913
    recording: Path,
    *,
    chart_path: Path,
    preset: str,
    run: int,
    play_time_s: float,
    beat_log: list[tuple[float, float]],
    condition: str | None = None,
    slider: int | None = None,
    tempo_factor: float | None = None,
    onset_mode: int | None = None,
    dropped_frames: int = 0,
    fake_engine: bool = False,
) -> Path:
    """Write the sidecar a recording is scored from, named beside its WAV."""
    data: dict[str, object] = {
        "chart": str(chart_path),
        "preset": preset,
        "run": run,
        "recording": recording.name,
        "play_time_s": play_time_s,
        "beat_audible": [[beat, time] for beat, time in beat_log],
        "dropped_frames": dropped_frames,
        "mode": "fake" if fake_engine else "live",
        "provenance": TakeProvenance.capture(
            chart_path, recording, fake_engine=fake_engine
        ).model_dump(),
    }
    if condition is not None:
        data["condition"] = condition
        data["slider"] = slider
    if tempo_factor is not None:
        data["tempo_factor"] = tempo_factor
        data["onset_mode"] = onset_mode
    sidecar = recording.with_suffix(".take.json")
    sidecar.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return sidecar


@main.command()
@click.argument("chart_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--preset",
    type=click.IntRange(min=0),
    default=0,
    help="Style pair the band plays, numbered from 0 in the order the app lists them.",
)
@click.option(
    "--seconds",
    type=click.FloatRange(min=0, min_open=True),
    help="Stop after this long; by default the band plays to the end of the chart.",
)
@click.option(
    "--record",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Save what the band plays to this WAV, with a .take.json sidecar beside it.",
)
@click.option(
    "--take-run",
    type=click.IntRange(min=1),
    default=1,
    help="Repeat number written to the sidecar; the band sounds the same.",
)
@click.option(
    "--take-condition",
    type=click.Choice(["chords_melody", "chords", "none"]),
    help="Use with --take-slider: guide the band with the whole chart, its chords only, "
    "or nothing.",
)
@click.option(
    "--take-slider",
    type=click.IntRange(0, 1),
    help="Use with --take-condition: hold the style slider at style A (0) or B (1).",
)
@click.option(
    "--take-tempo-factor",
    type=click.FloatRange(min=0, min_open=True),
    help="Play the chart at this multiple of its tempo, such as 0.8 for 80 percent.",
)
@click.option(
    "--take-onset-mode",
    type=click.IntRange(0, 1),
    help="Use with --take-tempo-factor: 0 lets the model place note starts; 1 strikes "
    "the bass on every beat.",
)
@click.option("--slider-sweep", is_flag=True, help="Sweep the style slider 0 to 1 over the chart.")
@click.option(
    "--fake-engine",
    is_flag=True,
    help="Use a stand-in engine with no models or audio device; nothing is heard.",
)
def play(  # noqa: PLR0913, PLR0917
    chart_path: Path,
    preset: int,
    seconds: float | None,
    record: Path | None,
    take_run: int,
    take_condition: str | None,
    take_slider: int | None,
    take_tempo_factor: float | None,
    take_onset_mode: int | None,
    slider_sweep: bool,
    fake_engine: bool,
) -> None:
    """Play CHART_PATH, optionally saving a WAV recording and its take sidecar."""
    if seconds is not None and not isfinite(seconds):
        raise GhostbandError(ErrorCode.INVALID_INPUT, "seconds must be finite and positive")
    if (take_condition is None) != (take_slider is None):
        raise GhostbandError(
            ErrorCode.INVALID_INPUT, "take-condition and take-slider must be given together"
        )
    if take_onset_mode is not None and take_tempo_factor is None:
        raise GhostbandError(ErrorCode.INVALID_INPUT, "take-onset-mode requires take-tempo-factor")
    if take_tempo_factor is not None and take_onset_mode is None:
        take_onset_mode = SHIPPED_ONSET_MODE
    # A tempo take measures any offered factor, whatever an earlier measurement passed.
    session = Session(
        fake_engine=fake_engine or bool(click.get_current_context().obj),
        tempo_range=(TEMPO_MIN, TEMPO_MAX) if take_tempo_factor is not None else None,
        onset_mode=SHIPPED_ONSET_MODE if take_onset_mode is None else take_onset_mode,
    )
    if preset >= len(session.presets):
        last = len(session.presets) - 1
        raise GhostbandError(ErrorCode.INVALID_INPUT, f"preset must be an integer from 0 to {last}")
    chart = read_chart(chart_path)
    if take_condition in {"chords", "none"}:
        chart = chart.model_copy(update={"melody": []})
    if take_condition == "none":
        chart = chart.model_copy(update={"chords": []})
    session.load_chart(chart)
    record_path = record or Path("take.wav")
    play_time = 0.0
    beat_log: list[tuple[float, float]] = []
    received: list[signal.Signals] = []
    try:
        session.control({"type": "preset", "value": preset}, record_path=record_path)
        if take_slider is not None:
            session.connect()
            session.slew.jump(float(take_slider))
        if take_tempo_factor is not None:
            session.control(
                {"type": "tempo_factor", "value": take_tempo_factor}, record_path=record_path
            )
        with signals_noted(received):
            console.write("playing", str(chart_path))
            play_time, beat_log = asyncio.run(
                play_chart(session, seconds, record, sweep=slider_sweep, stop=received)
            )
    finally:
        session.close()
    end_echoed_interrupt(received)
    console.write("stopped", str(chart_path))
    if record:
        console.write("wrote", str(record))
        sidecar = write_take_sidecar(
            record,
            chart_path=chart_path,
            preset=session.presets[preset].name,
            run=take_run,
            play_time_s=play_time,
            beat_log=beat_log,
            condition=take_condition,
            slider=take_slider,
            tempo_factor=take_tempo_factor,
            onset_mode=take_onset_mode,
            dropped_frames=session.dropped_frames,
            fake_engine=session.fake_engine,
        )
        console.write("wrote", str(sidecar))
    if received:
        click.get_current_context().exit(SIGNAL_STATUS_BASE + received[0])


@main.command()
@click.argument("chart_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--model", type=click.Choice(engine_bench.MODELS), default="mrt2_small")
@click.option("--preset", type=click.IntRange(min=0), default=0)
@click.option(
    "--output", "--out", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path)
)
@click.option("--tempo", type=click.FloatRange(min=0, min_open=True), default=1.0)
def render(chart_path: Path, model: str, preset: int, output: Path, tempo: float) -> None:
    """Render CHART_PATH offline, without real-time playback."""
    paths = first_run.Paths.defaults()
    presets = load_presets()[0]
    if preset >= len(presets):
        last = len(presets) - 1
        raise GhostbandError(ErrorCode.INVALID_INPUT, f"preset must be an integer from 0 to {last}")
    if not paths.engine_binary.is_file():
        raise GhostbandError(
            ErrorCode.UNAVAILABLE,
            f"engine not found at {paths.engine_binary}; run ghostband setup",
        )
    engine = OfflineEngine(
        paths.engine_binary,
        paths.models,
        presets[preset].a,
        model=model,
        preset=presets[preset].name,
        prompt_b=presets[preset].b,
        guidance=presets[preset].guidance,
    )
    render_chart(read_chart(chart_path), output, engine, tempo_factor=tempo)
    console.write("wrote", str(output))
    console.write("wrote", str(output.with_name(output.name + ".json")))


@main.command("export-midi")
@click.argument("chart_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
def export_midi(chart_path: Path, output: Path) -> None:
    """Export harmony and melody to a MIDI file."""
    output.write_bytes(chart_to_midi(read_chart(chart_path)))
    console.write("wrote", str(output))


@main.command("export")
@click.argument("chart_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--ireal", "ireal_link", is_flag=True, help="Write an iReal Pro link.")
@click.option(
    "--bundle",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write chart JSON, split MIDI parts and ABC into a folder.",
)
@click.option("--share", is_flag=True, help="Drop melody unless the chart's rights allow sharing.")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def export_command(
    chart_path: Path, ireal_link: bool, bundle: Path | None, share: bool, output: Path | None
) -> None:
    """Export a chart to an interchangeable chord-chart format."""
    chart = read_chart(chart_path)
    if bundle is not None:
        for path in export_bundle(chart, bundle, share=share):
            console.write("wrote", str(path))
        return
    if output is None:
        raise GhostbandError(ErrorCode.INVALID_INPUT, "choose --output or --bundle")
    if share:
        output.write_text(share_safe(chart).model_dump_json(indent=2) + "\n", encoding="utf-8")
    elif ireal_link:
        output.write_text(chart_to_ireal(chart) + "\n", encoding="utf-8")
    else:
        raise GhostbandError(
            ErrorCode.INVALID_INPUT, "choose a format such as --ireal, --share or --bundle"
        )
    console.write("wrote", str(output))


@main.command()
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path))
def schema(output: Path | None) -> None:
    """Write the portable chart JSON schema."""
    encoded = json.dumps(Chart.model_json_schema(), indent=2) + "\n"
    if output:
        output.write_text(encoded, encoding="utf-8")
        console.write("wrote", str(output))
    else:
        console.write_schema(encoded)


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check local prerequisites without opening an audio device."""
    paths = first_run.Paths.defaults()
    for problem in doctor_checks.preset_problems():
        console.write("skipped-preset", str(problem))
    problems, summary = doctor_checks.check(paths, fake_engine=bool(ctx.obj))
    if problems:
        raise GhostbandError(ErrorCode.ENGINE_OFFLINE, "; ".join(problems))
    console.write("doctor", summary)


def confirm_or_stop(prompt: str, option: str) -> None:
    """Ask before a licence or a large download; a refusal leaves the install untouched."""
    try:
        accepted = click.confirm(prompt)
    except click.Abort:
        # End of input or Ctrl+C at the prompt is a refusal; end the prompt's line first.
        click.echo()
        accepted = False
    if not accepted:
        console.write("declined", option, error=True)
        # The status click gives any aborted prompt; declining is a choice, not a registry error.
        click.get_current_context().exit(1)


@main.command()
@click.option(
    "--sheetsage2",
    is_flag=True,
    help="Also fetch SheetSage2, which is CC-BY-NC-4.0 and non-commercial only.",
)
@click.option(
    "--with-base",
    is_flag=True,
    help="Also download mrt2_base, about 2.8 GB; run ghostband bench before using it live.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Accept the SheetSage2 licence and the mrt2_base download non-interactively.",
)
def setup(sheetsage2: bool, with_base: bool, assume_yes: bool) -> None:
    """Install the engine, models and local extraction environment."""
    paths = first_run.Paths.defaults()
    if sheetsage2 and not assume_yes:
        confirm_or_stop(
            "SheetSage2 weights are CC-BY-NC-4.0, for non-commercial use only. Continue?",
            "--sheetsage2",
        )
    if with_base and not assume_yes:
        confirm_or_stop("mrt2_base is a download of about 2.8 GB. Continue?", "--with-base")
    try:
        console.write("installing", str(paths.data))
        first_run.install(paths, sheetsage2=sheetsage2, base=with_base)
    except (OSError, ValueError, RuntimeError) as error:
        raise GhostbandError(ErrorCode.UNAVAILABLE, str(error)) from error
    first_run.configure_environment(paths)
    console.write("installed", str(paths.data))


@main.command()
@click.option("--model", type=click.Choice(engine_bench.MODELS), default="mrt2_small")
@click.option(
    "--seconds",
    type=click.FloatRange(min=0, min_open=True),
    default=engine_bench.LIVE_MIN_SECONDS,
    show_default=True,
    help="How long to generate audio; live use needs at least the default.",
)
@click.option(
    "--report",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Markdown report to rewrite; defaults to docs/benchmarks/engine.md in a checkout.",
)
def bench(model: str, seconds: float, report: Path | None) -> None:
    """Time frame generation on the real engine and record whether MODEL can play live."""
    if not isfinite(seconds):
        raise GhostbandError(ErrorCode.INVALID_INPUT, "seconds must be finite and positive")
    paths = first_run.Paths.defaults()
    problems = setup_models.inspect_models(
        paths.models, setup_models.model_files(model), verify_hashes=False
    )
    if problems or not paths.engine_binary.is_file():
        suffix = " --with-base" if model == engine_bench.BASE_MODEL else ""
        raise GhostbandError(
            ErrorCode.UNAVAILABLE,
            f"engine or {model} missing; run ghostband setup{suffix}",
        )
    console.write("running", f"{model} for {seconds:g} s")
    try:
        result = engine_bench.run_engine(paths.engine_binary, paths.resources, model, seconds)
    except (OSError, ValueError, RuntimeError) as error:
        raise GhostbandError(ErrorCode.ENGINE_FAILED, str(error)) from error
    console.write("measured", f"{model}: {result.summary()}")
    console.write("wrote", str(engine_bench.save(result)))
    destination = report or engine_bench.default_report()
    if destination is not None:
        engine_bench.write_report(destination)
        console.write("wrote", str(destination))
    if model == engine_bench.BASE_MODEL:
        console.write("settings", engine_bench.base_model_status().message)


@main.group("eval")
def evaluate() -> None:
    """Write evaluation reports from measurements already taken."""


@evaluate.command("live")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Report to write; defaults to docs/benchmarks/live.md in a checkout.",
)
@click.option(
    "--claims",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Claims file; defaults to docs/benchmarks/claims.json.",
)
@click.option(
    "--sync",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Sync report from the sync measurement; defaults to docs/benchmarks/sync.md.",
)
@click.option(
    "--extraction",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Extraction summary CSV; defaults to docs/benchmarks/extraction.csv.",
)
@click.option(
    "--bench-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Folder of saved engine benchmark runs; defaults to the app's bench folder.",
)
def live(
    output: Path | None,
    claims: Path | None,
    sync: Path | None,
    extraction: Path | None,
    bench_dir: Path | None,
) -> None:
    """Write live.md from the sync, tempo, melody and engine measurements on disk."""
    folder = live_report.default_directory()
    options = {
        "--output": (output, live_report.REPORT_NAME),
        "--claims": (claims, live_report.CLAIMS_NAME),
        "--sync": (sync, live_report.SYNC_NAME),
        "--extraction": (extraction, live_report.EXTRACTION_NAME),
    }
    paths = {
        flag: given or (folder / name if folder is not None else None)
        for flag, (given, name) in options.items()
    }
    unset = [flag for flag, path in paths.items() if path is None]
    if unset:
        raise GhostbandError(
            ErrorCode.INVALID_INPUT, f"choose {', '.join(unset)} outside a checkout"
        )
    destination, claims_path, sync_path, extraction_path = (
        path for path in paths.values() if path is not None
    )
    live_report.write_report(
        destination, claims_path, sync_path, extraction_path, bench_directory=bench_dir
    )
    console.write("wrote", str(destination))
