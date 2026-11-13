#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/ghostband-uv}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-/tmp/ghostband-uv-tools}"
export UV_TOOL_BIN_DIR="${UV_TOOL_BIN_DIR:-/tmp/ghostband-uv-bin}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/tmp/ghostband-python}"
export HF_HOME="$PWD/.cache/upstream-0.2/huggingface"
export HF_MODULES_CACHE="$HF_HOME/modules"
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
work="$PWD/.cache/upstream-0.2"
report="$PWD/docs/benchmarks/upstream-check.md"
mkdir -p "$work" "$(dirname "$report")" || exit 1
# The scaffold does not ignore .cache; keep generated downloads out of the index.
printf '*\n' > "$PWD/.cache/.gitignore"
failures=0
printf '# Upstream verification\n\nRun: `%s`\n\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$report"
printf 'Command: `UV_CACHE_DIR=/tmp/ghostband-uv bash scripts/verify_upstream.sh`\n\n' >> "$report"

record() {
    local status="$1" title="$2" evidence="$3" fallback="$4"
    printf '%s: %s\n' "$status" "$title"
    printf '## %s: %s\n\n```text\n' "$status" "$title" >> "$report"
    LC_ALL=C tr -cd '\11\12\15\40-\176' < "$evidence" | tail -n 18 >> "$report"
    printf '\n```\n\n%s\n\n' "$fallback" >> "$report"
    if [[ "$status" == FAIL ]]; then failures=$((failures + 1)); fi
}

probe() {
    local title="$1" fallback="$2" log="$work/probe-$((++probe_number)).log"
    shift 2
    printf 'Running: %s\n' "$title"
    if "$@" > "$log" 2>&1; then
        record PASS "$title" "$log" "$fallback"
    else
        record FAIL "$title" "$log" "$fallback"
    fi
}
probe_number=0

df -h . > "$work/disk.log"
if [[ $(df -Pk . | awk 'NR==2 {print $4}') -lt 29296875 ]]; then
    record FAIL 'Disk space: at least 30 GB free' "$work/disk.log" 'Stopped before downloads. Free disk space before rerunning.'
    exit 1
fi
record PASS 'Disk space: at least 30 GB free' "$work/disk.log" 'Checked before downloads; no model downloads go outside the repo-local .cache.'
probe 'Pinned engine tag and commit' 'See ADR 0010.' bash -c '
    git -C third_party/magenta-realtime rev-parse HEAD
    git -C third_party/magenta-realtime describe --tags --exact-match
    test "$(git -C third_party/magenta-realtime rev-parse HEAD)" = 694a545e4ba0b88bf1150137b129582166d3e07f &&
    test "$(git -C third_party/magenta-realtime describe --tags --exact-match)" = v2.0.3'
probe 'Metal compiler available locally' 'The first CI build remains pending publication. If metal fails there, run xcodebuild -downloadComponent MetalToolchain. See ADR 0010.' xcrun -f metal
probe 'CMake 3.27.9 available without installation' 'No system-wide installation attempted. Native build checks remain blocked; no substitute CMake version is approved. See ADR 0010.' uv tool run --offline --from cmake==3.27.9 cmake --version
cmake_ready=0
if uv tool run --offline --from cmake==3.27.9 cmake --version > "$work/cmake.log" 2>&1; then cmake_ready=1; fi
probe 'Permissive extractor uv lock resolution' 'uv lock --check verifies resolution without changing the existing lockfile.' uv lock --check --project extract/permissive
probe 'SheetSage2 extractor uv lock resolution' 'uv lock --check verifies resolution without changing the existing lockfile.' uv lock --check --project extract/sheetsage2
probe 'Official MRT tools environment' 'Uses the existing locked magenta-rt[mlx]==2.0.3 environment.' uv sync --frozen --project tools/mrt
probe 'Non-interactive mrt model selection' 'NAME=mrt2_small avoids the picker. The download below runs with stdin closed.' uv run --frozen --project tools/mrt mrt models download --help
probe 'Prompt status meanings from pinned source' '0 idle, 1 processing, 2 success, 3 error. Use the wording processing, not fetching. See ADR 0010.' bash -c '
    source_file=third_party/magenta-realtime/core/src/mlx_engine.cpp
    for pair in "0.*idle" "1.*processing" "2.*success" "3.*error"; do
        rg -n "prompt_statuses_\[i\] = $pair" "$source_file" || exit 1
    done'
probe 'SheetSage2 runtime environment' 'Uses Python 3.11 and the existing frozen requirements.' uv sync --frozen --project extract/sheetsage2

python_bin="$PWD/extract/sheetsage2/.venv/bin/python"
export GHOSTBAND_UPSTREAM_WORK="$work"
# Each model probe runs separately so a failed load cannot hide later results.
cat > "$work/models.py" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from huggingface_hub import snapshot_download

WORK = Path(os.environ["GHOSTBAND_UPSTREAM_WORK"])
PINS = {
    "google/magenta-realtime-2": "010aa0dcb0dfd27b24f0ad07b4dad63e8f9521cc",
    "m-a-p/SheetSage2": "b83c3d79a6d98bcffaf8f9ed112c90aa887887bc",
    "m-a-p/MERT-v2-FullSong": "7001f7657276238629e61c7746ce70735e0604c0",
}

def api(path):
    with urllib.request.urlopen("https://huggingface.co/api/models/" + path, timeout=60) as response:
        return json.load(response)

def metadata():
    for repo, revision in PINS.items():
        data = api(repo + "/revision/" + revision)
        print(repo, "sha=" + data["sha"], "gated=" + str(data["gated"]))
        if data["sha"] != revision or data["gated"] is not False:
            raise RuntimeError("Revision or ungated access differs: " + repo)

def sheet_cache():
    for repo in ("m-a-p/SheetSage2", "m-a-p/MERT-v2-FullSong"):
        path = Path(snapshot_download(repo, revision=PINS[repo], token=False,
                    allow_patterns=["*.py", "*.json", "*.safetensors"]))
        tree = api(repo + "/tree/" + PINS[repo] + "?recursive=true")
        for entry in tree:
            target = path / entry["path"]
            if "lfs" in entry and target.is_file():
                digest = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
                if digest != entry["lfs"]["oid"]:
                    target.unlink()
                    raise RuntimeError("Checksum mismatch: " + entry["path"])
        print("Cached and checked pinned files:", repo, PINS[repo], flush=True)
    config = json.loads((path.parent.parent.parent / "models--m-a-p--SheetSage2" /
                         "snapshots" / PINS["m-a-p/SheetSage2"] / "config.json").read_text())
    print("SheetSage2 requests MERT parent revision:", config["base_model_revision"])
    print("Cached MERT revision:", PINS["m-a-p/MERT-v2-FullSong"])

def sheet_run(mode):
    import numpy as np
    from transformers import AutoModel
    model = AutoModel.from_pretrained("m-a-p/SheetSage2", revision=PINS["m-a-p/SheetSage2"],
                                     trust_remote_code=True, local_files_only=True).eval()
    print("SheetSage2 and parent loaded from cache with HF_HUB_OFFLINE=1", flush=True)
    if mode == "array":
        import ctypes
        import torchaudio
        torchaudio.load = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("torchaudio.load called"))
        torchaudio.info = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("torchaudio.info called"))
        # Reject FFmpeg dynamic libraries and subprocesses throughout transcription.
        def audit(event, args):
            if event == "subprocess.Popen" or (event == "ctypes.dlopen" and
                any(part in str(args) for part in ("libavcodec", "libavformat", "libavutil", "libtorchaudio_ffmpeg"))):
                raise RuntimeError("FFmpeg or subprocess access: " + str(args))
        sys.addaudithook(audit)
        for index in range(1, ctypes.CDLL(None)._dyld_image_count()):
            dyld = ctypes.CDLL(None)
            dyld._dyld_get_image_name.restype = ctypes.c_char_p
            image = dyld._dyld_get_image_name(index).decode()
            if any(part in image for part in ("libavcodec", "libavformat", "libavutil")):
                raise RuntimeError("FFmpeg already loaded: " + image)
        result = model.transcribe(np.zeros(30 * 24000, dtype=np.float32), sampling_rate=24000)
        print("30 s array transcription completed without FFmpeg:", sorted(result))

def full_set():
    repo = "google/magenta-realtime-2"
    if api(repo)["sha"] != PINS[repo]:
        raise RuntimeError("mrt CLI has no revision flag; HEAD moved, cannot safely download")
    destination = WORK / "full-model-set"
    for command in (["init"], ["download", "mrt2_small"]):
        subprocess.run(["uv", "run", "--frozen", "--project", "tools/mrt", "mrt", "models",
                        *command, "--download-path", str(destination)], stdin=subprocess.DEVNULL, check=True)
    tree = api(repo + "/tree/" + PINS[repo] + "?recursive=true")
    count = total = 0
    for entry in tree:
        name = entry["path"]
        if entry["type"] != "file" or not name.startswith(("resources/", "models/mrt2_small/")):
            continue
        target = destination / name
        digest = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
        expected = entry.get("lfs", {}).get("oid")
        if expected is None:
            raise RuntimeError("Missing SHA-256 metadata: " + name)
        if digest != expected:
            target.unlink()
            raise RuntimeError("Checksum mismatch: " + name)
        count += 1
        total += target.stat().st_size
    if count == 0:
        raise RuntimeError("No files verified")
    print("Full-set fallback downloaded and SHA-256 verified:", count, "files,", total, "bytes")

{"metadata": metadata, "cache": sheet_cache, "offline": lambda: sheet_run("offline"),
 "array": lambda: sheet_run("array"), "full": full_set}[sys.argv[1]]()
PY
probe 'HF revisions and gated flags for all three repos' 'Anonymous requests to the pinned HF revisions; no sign-in required.' "$python_bin" "$work/models.py" metadata
probe 'Pinned SheetSage2 and MERT cache with SHA-256 verification' 'Downloads remain repo-local and are removed at the end of this run.' "$python_bin" "$work/models.py" cache
probe 'SheetSage2 resolves MERT with HF_HUB_OFFLINE=1' 'No revision change applied. The parent-cache strategy remains undecided. See ADR 0013 for the parent-revision mismatch.' env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$python_bin" "$work/models.py" offline
probe 'SheetSage2 transcribe(array) without FFmpeg libraries' 'If transcription reaches FFmpeg, the fallback is ffmpeg@6 6.1.6 and a subprocess-only DYLD_FALLBACK_LIBRARY_PATH. A parent-cache failure has no documented FFmpeg fallback. See ADR 0013.' env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DYLD_FALLBACK_LIBRARY_PATH= "$python_bin" "$work/models.py" array
probe 'Full default-model fallback download' 'Full resources plus mrt2_small downloaded through the official non-interactive CLI and verified against the pinned revision. No slim-set assumption is used. See ADR 0012.' "$python_bin" "$work/models.py" full

if [[ "$cmake_ready" == 0 ]]; then
    for title in 'hello_mrt2 build and 4 s WAV' 'CMake integration option A then B' 'Default model set: text prompt, 10 s, zero underruns'; do
        record FAIL "$title" "$work/cmake.log" 'Blocked by missing CMake 3.27.9. No system-wide installation or alternate version used. Full-set download is the conservative model fallback, not runtime evidence. See ADR 0010 and ADR 0012.'
    done
else
    # Refuse to claim native coverage on reruns until the documented probes ran.
    printf 'CMake is now available. Run the native probes described in ADR 0010 before accepting native runtime support.\n' > "$work/native.log"
    record FAIL 'Native build and runtime probes require follow-up' "$work/native.log" 'The initial run had no CMake. See ADR 0010; this script does not infer native results from tool availability.'
fi

llvm=/opt/homebrew/opt/llvm/bin/clang++
printf '[[clang::nonblocking]] int render() { return 0; }\nint main() { return render(); }\n' > "$work/rtsan.cpp"
printf '#include <dispatch/dispatch.h>\n[[clang::nonblocking]] int render() { return 0; }\nint main() { int (^block)() = ^{ return render(); }; return block(); }\n' > "$work/rtsan.mm"
probe 'Homebrew RTSan C++ nonblocking function' 'Missing Homebrew clang is recorded without installation. No RTSan claim; see ADR 0011.' "$llvm" -std=c++20 -fsanitize=realtime "$work/rtsan.cpp" -o "$work/rtsan-cpp"
probe 'Homebrew RTSan Objective-C++ block' 'If only ObjC++ fails, isolate render in a sanitized C++ function called by the unsanitized block. With no compiler this fallback cannot be built. See ADR 0011.' "$llvm" -std=c++20 -fblocks -fsanitize=realtime "$work/rtsan.mm" -o "$work/rtsan-objc"
printf 'CI MetalToolchain and 120 s engine RTSan session have not run locally.\n' > "$work/later.log"
record FAIL 'CI Metal build and 120 s live RTSan session' "$work/later.log" 'CI requires the hosted repository; the engine render block is empty in this scaffold. See ADR 0010 and ADR 0011. No GitHub action taken.'

# Keep small source/config evidence and logs, discard model weights after probing.
find "$work" -type f \( -name '*.safetensors' -o -name '*.mlxfn' -o -name '*.tflite' -o -name '*.incomplete' \) -delete
if [[ -d "$HF_HOME/hub" ]]; then
    find "$HF_HOME/hub" -path '*/blobs/*' -type f -size +10M -delete
fi
printf '\nFailures: %s. A documented fallback is not a passing runtime measurement.\n' "$failures" | tee -a "$report"
if [[ "$failures" -gt 0 ]]; then exit 1; fi
