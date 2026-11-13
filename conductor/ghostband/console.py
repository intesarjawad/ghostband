"""Plain terminal rendering, independent of colour and terminal capabilities."""

import shutil
import textwrap

import click

from ghostband.errors import MESSAGES, ErrorCode, GhostbandError


def write(key: str | ErrorCode, detail: str = "", *, error: bool = False) -> None:
    """Wrap both contract lines to the available terminal width."""
    entry = MESSAGES[key]
    width = max(20, shutil.get_terminal_size(fallback=(80, 24)).columns)
    for prefix, template in (("ghostband: ", entry.what), ("  next: ", entry.next)):
        lines = textwrap.wrap(
            template.format(detail=detail),
            width=width,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
            # A path or URL split across lines can no longer be copied or clicked.
            break_long_words=False,
            break_on_hyphens=False,
        )
        click.echo("\n".join(lines), err=error)


def fail(error: GhostbandError) -> None:
    write(error.code, error.detail, error=True)


def write_schema(encoded: str) -> None:
    """Keep structured schema output directly consumable by JSON tools."""
    click.echo(encoded, nl=False)
