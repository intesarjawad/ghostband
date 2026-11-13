"""Build the release engine archive from the installer's shared asset contract."""

import argparse
import os
import tarfile
from pathlib import Path

from ghostband.release import ENGINE_MEMBERS, REPOSITORY, engine_archive_name


def package_engine(build: Path, license_path: Path, output: Path, tag: str) -> Path:
    sources = {name: license_path if name == "LICENSE" else build / name for name in ENGINE_MEMBERS}
    for path in sources.values():
        if not path.is_file():
            raise FileNotFoundError(f"release requires {path}")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / engine_archive_name(tag)
    with tarfile.open(archive, "w:gz") as bundle:
        for name, path in sources.items():
            bundle.add(path, arcname=name)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--build", type=Path, default=Path("engine/build"))
    parser.add_argument("--license", type=Path, default=Path("LICENSE"))
    parser.add_argument("--out", type=Path, default=Path())
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY", REPOSITORY) != REPOSITORY:
        parser.error(f"release repository must match the installer contract: {REPOSITORY}")
    print(package_engine(args.build, args.license, args.out, args.tag))


if __name__ == "__main__":
    main()
