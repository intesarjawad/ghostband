import os
import subprocess
from pathlib import Path


def test_report_heading_describes_the_upstream_check(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[2] / "scripts/verify_upstream.sh"
    script = scripts / source.name
    script.write_bytes(source.read_bytes())
    binaries = tmp_path / "bin"
    binaries.mkdir()
    df = binaries / "df"
    df.write_text("#!/bin/sh\nprintf 'Filesystem Blocks Used Available Capacity Mounted\\n'")
    with df.open("a") as stream:
        stream.write("printf 'disk 100 99 1 99%% /\\n'\n")
    df.chmod(0o755)
    finished = subprocess.run(  # noqa: S603
        ["/bin/bash", str(script)],
        env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 1
    report = (tmp_path / "docs/benchmarks/upstream-check.md").read_text()
    assert report.startswith("# Upstream verification\n\nRun:")
    assert "Disk space" in report
