#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
root=$PWD
preview=false
output="$root/fixtures"
if [[ ${1:-} == --preview ]]; then
    [[ $# -ge 3 ]] || { echo 'Usage: render_fixtures.sh --preview DIR CHART...' >&2; exit 2; }
    preview=true
    output=$2
    shift 2
fi
if [[ $# -eq 0 ]]; then
    set -- "$root"/charts/*.chart.json
fi

synth=${FLUIDSYNTH:-fluidsynth}
command -v "$synth" >/dev/null || { echo 'Install FluidSynth 2.6.2 to render fixtures.' >&2; exit 1; }
version=$("$synth" --version)
[[ ${version%%$'\n'*} == 'FluidSynth runtime version 2.6.2' ]] || {
    echo 'Fixture rendering requires FluidSynth 2.6.2.' >&2
    exit 1
}

cache=${TMPDIR:-/tmp}/ghostband-fixtures-cache
mkdir -p "$cache" "$output"
package=fluid-soundfont-gm_3.1-6_all.deb
sha256=9965fbcc6acee17d6f72b685d28c7f968ec64eba14645445b476d5c6cc2eee4c
if [[ ! -f "$cache/$package" ]]; then
    curl --fail --location --retry 3 \
        "https://deb.debian.org/debian/pool/main/f/fluid-soundfont/$package" \
        --output "$cache/$package.download"
    echo "$sha256  $cache/$package.download" | shasum -a 256 --check
    mv "$cache/$package.download" "$cache/$package"
fi
echo "$sha256  $cache/$package" | shasum -a 256 --check
work=$(mktemp -d "${TMPDIR:-/tmp}/ghostband-render.XXXXXX")
trap 'rm -rf "$work"' EXIT
ar -p "$cache/$package" data.tar.xz | tar -xJf - -C "$work" \
    ./usr/share/sounds/sf2/FluidR3_GM.sf2 ./usr/share/doc/fluid-soundfont-gm/copyright
soundfont="$work/usr/share/sounds/sf2/FluidR3_GM.sf2"
if [[ $preview == false ]]; then
    cp "$work/usr/share/doc/fluid-soundfont-gm/copyright" "$output/FluidR3-LICENSE.txt"
fi

for chart in "$@"; do
    name=$(basename "$chart" .chart.json)
    uv run ghostband export-midi "$chart" --output "$work/$name.mid"
    # Disable effects and parallel synthesis to keep PCM repeatable.
    "$synth" -ni -q -f /dev/null -R 0 -C 0 -g 0.6 -r 44100 -T wav -O s16 \
        -o synth.cpu-cores=1 -o player.timing-source=sample -F "$work/$name.stereo.wav" \
        "$soundfont" "$work/$name.mid"
    uv run python - "$work/$name.stereo.wav" "$work/$name.wav" <<'PY'
import sys

import numpy as np
import soundfile as sf

frames = 1_323_000
audio, rate = sf.read(sys.argv[1], dtype="int16", always_2d=True)
mono = np.rint(audio.astype(np.float64).mean(axis=1)).astype(np.int16)
pcm = np.zeros(frames, dtype=np.int16)
count = min(frames, len(mono))
pcm[:count] = mono[:count]
sf.write(sys.argv[2], pcm, rate, subtype="PCM_16", format="WAV")
PY
    if [[ $preview == true ]]; then
        ffmpeg -hide_banner -loglevel error -y -i "$work/$name.wav" \
            -map_metadata -1 -c:a libmp3lame -b:a 128k -flags:a +bitexact \
            -write_xing 0 -id3v2_version 0 "$output/$name.mp3"
    else
        cp "$work/$name.mid" "$output/$name.mid"
        cp "$work/$name.wav" "$output/$name.wav"
        cp "$chart" "$output/$name.golden.json"
    fi
    echo "Rendered $name"
done
