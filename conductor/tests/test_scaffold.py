from importlib.metadata import distribution


def test_install_registers_ghostband_command() -> None:
    package = distribution("ghostband")
    commands = {entry.name: entry.value for entry in package.entry_points}
    assert commands["ghostband"] == "ghostband.cli:main"
