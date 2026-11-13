# Contributing

Read the [engineering standards](docs/engineering-standards.md) before changing code. Use the versions in the project configuration and commit updated lockfiles with dependency changes.

Run `make setup` to install dependencies, then `make check` for formatting, lint, strict type checking and tests. `npm --prefix ui run build` builds the interface; `make demo` starts it with a stand-in engine and no live audio. Model downloads are excluded from ordinary checks.

Keep changes focused. Explain the problem, resulting behaviour and validation in each pull request. Include screenshots for interface changes and deterministic tests for changed behaviour. Follow semantic versioning and record user-visible changes in CHANGELOG.md.

The tracked pre-commit configuration uses the locked tools. Do not replace an externally managed `core.hooksPath`.

Report security issues through GitHub private vulnerability reporting as described in [SECURITY.md](SECURITY.md). Follow the [code of conduct](CODE_OF_CONDUCT.md).

## Native build

On Apple Silicon macOS, install full Xcode with its Metal toolchain and initialize the submodule with `git submodule update --init --recursive`. From the repository root:

```sh
uv run --with cmake==3.27.9 cmake -S engine -B engine/build
uv run --with cmake==3.27.9 cmake --build engine/build -j 2
```

The native engine also needs model resources for live playback; see [the architecture](docs/architecture.md).
