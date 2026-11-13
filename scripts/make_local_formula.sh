#!/usr/bin/env bash
# Build the engine tarball and the wheel, then write a copy of the Homebrew formula that
# installs them from local files. Homebrew only reads formulae inside a tap, and the release
# assets do not exist before a public release, so this is how the formula is tried first.
#
#   scripts/make_local_formula.sh [--out DIR] [--engine PATH] [--wheel PATH]
#
# --engine and --wheel use an existing engine binary or wheel instead of building one.
# Then, on the account that owns Homebrew:
#   brew tap-new --no-git intesarjawad/local-test
#   cp DIR/ghostband.rb "$(brew --repository intesarjawad/local-test)/Formula/"
#   brew install intesarjawad/local-test/ghostband && brew test ghostband
#   brew untap intesarjawad/local-test
set -euo pipefail

cd "$(dirname "$0")/.."
out=/Users/Shared/ghostband-test
engine=''
wheel=''
while (($#)); do
    case "$1" in
        --out | --engine | --wheel)
            (($# >= 2)) || { echo "$1 needs a value." >&2; exit 2; }
            case "$1" in
                --out) out="$2" ;;
                --engine) engine="$2" ;;
                --wheel) wheel="$2" ;;
            esac
            shift 2
            ;;
        *)
            echo "Usage: $0 [--out DIR] [--engine PATH] [--wheel PATH]" >&2
            exit 2
            ;;
    esac
done

version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
[[ -n "$version" ]] || { echo 'No version found in pyproject.toml.' >&2; exit 1; }
mkdir -p "$out"
out="$(cd "$out" && pwd)"

if [[ -z "$engine" ]]; then
    uv run --with cmake==3.27.9 cmake -S engine -B engine/build
    uv run --with cmake==3.27.9 cmake --build engine/build --target ghostband-engine -j 2
    engine=engine/build/ghostband-engine
fi
[[ -f "$engine" ]] || { echo "$engine is not a file." >&2; exit 1; }

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
cp "$engine" "$staging/ghostband-engine"
chmod 755 "$staging/ghostband-engine"
cp "$(dirname "$engine")/mlx.metallib" "$staging/mlx.metallib"
archive="$(uv run python scripts/package_engine.py "v$version" --build "$staging" --out "$out")"
tarball="$(basename "$archive")"

if [[ -n "$wheel" ]]; then
    [[ -f "$wheel" ]] || { echo "$wheel is not a file." >&2; exit 1; }
    cp "$wheel" "$out/"
    wheel="$out/$(basename "$wheel")"
else
    npm --prefix ui ci --ignore-scripts
    npm --prefix ui run build
    mkdir -p conductor/ghostband/extract_envs
    for name in permissive sheetsage2; do
        rm -rf "conductor/ghostband/extract_envs/$name"
        cp -R "extract/$name" "conductor/ghostband/extract_envs/$name"
        rm -rf "conductor/ghostband/extract_envs/$name/.venv" "conductor/ghostband/extract_envs/$name/__pycache__"
    done
    uv build --wheel --out-dir "$out"
    wheel="$out/ghostband-$version-py3-none-any.whl"
fi
[[ -f "$wheel" ]] || { echo "$wheel is not a file." >&2; exit 1; }

checksum="$(shasum -a 256 "$out/$tarball" | cut -d ' ' -f 1)"
sed -E \
    -e "s|^  url \".*\"\$|  url \"file://$out/$tarball\"|" \
    -e "s|^  sha256 \".*\"\$|  sha256 \"$checksum\"|" \
    -e "s|^    wheel = \".*\"\$|    wheel = \"file://$wheel\"|" \
    -e "s|tool run |tool run --find-links $out |g" \
    packaging/homebrew/ghostband.rb >"$out/ghostband.rb"
grep -q "^  url \"file://$out/$tarball\"\$" "$out/ghostband.rb" \
    || { echo 'The formula has no url line to replace.' >&2; exit 1; }
grep -q "^  sha256 \"$checksum\"\$" "$out/ghostband.rb" \
    || { echo 'The formula has no sha256 line to replace.' >&2; exit 1; }
grep -q -- "--find-links $out" "$out/ghostband.rb" \
    || { echo 'The formula launcher has no uv tool run line to extend.' >&2; exit 1; }
grep -q "^    wheel = \"file://$wheel\"\$" "$out/ghostband.rb" \
    || { echo 'The formula has no wheel line to replace.' >&2; exit 1; }
echo "Wrote $out/ghostband.rb"
