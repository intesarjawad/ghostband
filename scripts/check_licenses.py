"""Reject non-commercial dependency licences in an installed extractor project."""

import argparse
import asyncio
import re
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path
from shutil import which

NONCOMMERCIAL = re.compile(r"\bCC[- ]BY(?:[- ]SA)?[- ]NC\b|\bNC\b|non[- ]?commercial", re.I)
LICENSE_HEADING = re.compile(
    r"^(?:SPDX-License-Identifier:\s*)?(?:Apache|MIT|(?:[234]-Clause )?BSD|ISC|MPL|GPL|LGPL|AGPL|"
    r"GNU (?:Lesser |Affero )?General Public|Mozilla Public|Python Software Foundation|"
    r"PSF|Unlicense|The Unlicense|Zlib|Artistic|Creative Commons|CC[- ]|CC0)\b",
    re.I,
)
HEADING_LINES = 10  # Licence titles can follow copyright and blank lines.


def identifies_license(value: str) -> bool:
    return bool(NONCOMMERCIAL.search(value) or LICENSE_HEADING.search(value.strip()))


def check_distribution(distribution: Distribution) -> str | None:
    """Inspect licence metadata and classifiers, failing closed when none is present."""
    metadata = distribution.metadata
    name = metadata.get("Name", "unnamed package")
    expression = metadata.get("License-Expression", "").strip()
    meaningful = (
        [expression]
        if expression.upper() not in {"", "UNKNOWN", "NOASSERTION", "NONE", "N/A"}
        else []
    )
    meaningful.extend(
        value
        for value in metadata.get_all("Classifier", [])
        if value.startswith("License ::") and value not in {"License :: OSI Approved"}
    )
    if not meaningful:
        first_line = metadata.get("License", "").split("\n", 1)[0]
        if identifies_license(first_line):
            meaningful.append(first_line)
    if not meaningful:
        for filename in metadata.get_all("License-File", []):
            contents = distribution.read_text(filename)
            if contents is not None:
                meaningful.extend(
                    line
                    for line in contents.splitlines()[:HEADING_LINES]
                    if LICENSE_HEADING.search(line.strip()) or NONCOMMERCIAL.match(line.strip())
                )
    if not meaningful:
        return f"{name}: missing licence metadata"
    if any(NONCOMMERCIAL.search(value) for value in meaningful):
        return f"{name}: non-commercial licence"
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.installed:
        packages = list(distributions())
        if not packages:
            parser.exit(1, "No installed package metadata found\n")
        failures = [failure for package in packages if (failure := check_distribution(package))]
        if failures:
            print("\n".join(sorted(failures)), file=sys.stderr)
            return 1
        print(f"Checked {len(packages)} installed packages: no non-commercial licences")
        return 0
    if args.project is None:
        parser.error("--project is required")
    executable = which("uv")
    if executable is None:
        parser.exit(1, "Install uv before checking an extractor project\n")
    process = await asyncio.create_subprocess_exec(
        executable,
        "run",
        "--offline",
        "--frozen",
        "--project",
        str(args.project.resolve()),
        "python",
        str(Path(__file__).resolve()),
        "--installed",
    )
    return await process.wait()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
