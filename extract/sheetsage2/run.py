"""Explicit model fetching and offline array transcription for Ghostband."""

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from sheetsage_cache import fetch
from sheetsage_runtime import transcribe_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("fetch", help="Fetch CC-BY-NC-4.0, non-commercial-only weights")
    download.add_argument("--cache-dir", type=Path, required=True)
    download.add_argument("--accept-noncommercial-license", action="store_true", required=True)
    transcribe = commands.add_parser("transcribe", help="Transcribe a WAV without network access")
    transcribe.add_argument("wav", type=Path)
    transcribe.add_argument("--output-dir", type=Path, required=True)
    transcribe.add_argument("--cache-dir", type=Path, required=True)
    transcribe.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            if args.command == "fetch":
                fetch(args.cache_dir.resolve())
                result: dict[str, object] = {"verified": True}
            else:
                result = transcribe_file(
                    args.wav, args.output_dir, args.cache_dir.resolve(), args.device
                )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
