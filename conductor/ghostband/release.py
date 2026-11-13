"""Release asset contract shared by the installer and release workflow."""

from importlib.metadata import version

REPOSITORY = "intesarjawad/ghostband"
ENGINE_NAME = "ghostband-engine"
METAL_LIBRARY = "mlx.metallib"
ENGINE_MEMBERS = (ENGINE_NAME, METAL_LIBRARY, "LICENSE")


def release_tag() -> str:
    return "v" + version("ghostband").replace("rc", "-rc.")


def engine_archive_name(tag: str | None = None) -> str:
    return f"ghostband-engine-{tag or release_tag()}-macos-arm64.tar.gz"


def engine_archive_url(tag: str | None = None) -> str:
    tag = tag or release_tag()
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{engine_archive_name(tag)}"


def wheel_url(tag: str) -> str:
    package = tag.removeprefix("v").replace("-rc.", "rc")
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/ghostband-{package}-py3-none-any.whl"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--url", action="store_true")
    parser.add_argument("--wheel-url", action="store_true")
    args = parser.parse_args()
    if args.wheel_url:
        print(wheel_url(args.tag))
    else:
        print(engine_archive_url(args.tag) if args.url else engine_archive_name(args.tag))
