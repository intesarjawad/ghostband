.PHONY: setup check format lint pyright test copy demo tsan asan-ubsan llvm-check fuzz-build fuzz-smoke fuzz rtsan takes gate fixtures fixtures-worst

CMAKE = uv run --with cmake==3.27.9 cmake
LLVM_BIN ?= /opt/homebrew/opt/llvm/bin
FUZZ_SECONDS = 600
RTSAN_SECONDS ?= 120
RTSAN_TRACE ?= engine/build-rtsan/session.jsonl
MODEL_DIR ?= $(HOME)/Documents/Magenta/magenta-rt-v2

setup:
	uv sync --frozen
	npm --prefix ui ci
	cd ui && npx playwright install chromium

check: format lint pyright test copy tsan

format:
	uv run ruff format --check .
	npm --prefix ui run format:check

lint:
	uv run ruff check .
	npm --prefix ui run lint

pyright:
	uv run pyright
	npm --prefix ui run typecheck

test:
	uv run python -m pytest -m "not models" -q
	npm --prefix ui test
	npm --prefix ui run e2e

# The FluidR3 licence must stay verbatim, author address included; third_party is upstream code.
copy:
	bash scripts/check_copy.sh
	uv run python scripts/check_readme_numbers.py
	! git grep -nIE '@(gmail|outlook|icloud)\.' -- . ':!fixtures/FluidR3-LICENSE.txt' ':!third_party'

GATE_TAKES ?= takes
GATE_MODEL ?= mrt2_base
GATE_CLAIMS ?= docs/benchmarks/claims.json

# Real-time recording on the installed models; an interrupted run resumes at the first missing take.
takes:
	caffeinate -dims uv run python scripts/record_takes.py all $(GATE_TAKES)

gate: takes
	uv run python -m pytest -m models -q
	uv run ghostband bench --model $(GATE_MODEL) --report docs/benchmarks/engine.md
	uv run python scripts/measure_sync.py $(GATE_TAKES)/sync --report docs/benchmarks/sync.md --claims $(GATE_CLAIMS)
	uv run python scripts/measure_identity.py $(GATE_TAKES)/identity --report docs/benchmarks/identity.md --claims $(GATE_CLAIMS)
	uv run python scripts/measure_tempo.py $(GATE_TAKES)/tempo --report docs/benchmarks/tempo.md --csv docs/benchmarks/tempo.csv --claims $(GATE_CLAIMS)
	uv run python -m ghostband.evaluation.extraction --output-dir docs/benchmarks
	$(MAKE) rtsan

demo:
	uv run ghostband --fake-engine

tsan:
	uv run --with cmake==3.27.9 cmake -S engine -B engine/build-tsan -DGHOSTBAND_TSAN_ONLY=ON -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++ -DCMAKE_OBJCXX_COMPILER=/usr/bin/clang++
	uv run --with cmake==3.27.9 cmake --build engine/build-tsan --target spsc_stress_test -j 2
	uv run --with cmake==3.27.9 ctest --test-dir engine/build-tsan --output-on-failure

asan-ubsan:
	$(CMAKE) -S engine -B engine/build-asan -DGHOSTBAND_SANITIZE=address,undefined
	$(CMAKE) --build engine/build-asan --target protocol_test protocol_corpus_test metronome_test reference_player_test audio_tap_test render_session_test -j 2
	UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 ASAN_OPTIONS=abort_on_error=1 uv run --with cmake==3.27.9 ctest --test-dir engine/build-asan --output-on-failure -R '^(protocol|protocol_corpus|metronome|reference_player|audio_tap|render_session)$$'

llvm-check:
	"$(LLVM_BIN)/clang++" --version | rg 'clang version 23\.1\.5( |$$)'

fuzz-build: llvm-check
	$(CMAKE) -S engine -B engine/build-fuzz -DGHOSTBAND_FUZZ_ONLY=ON -DCMAKE_C_COMPILER="$(LLVM_BIN)/clang" -DCMAKE_CXX_COMPILER="$(LLVM_BIN)/clang++" -DCMAKE_OBJCXX_COMPILER="$(LLVM_BIN)/clang++"
	$(CMAKE) --build engine/build-fuzz --target protocol_fuzz -j 2
	mkdir -p engine/build-fuzz/corpus engine/build-fuzz/artifacts
	cp engine/fuzz/corpus/* engine/build-fuzz/corpus/

fuzz-smoke: FUZZ_SECONDS = 60
fuzz-smoke: fuzz

fuzz: fuzz-build
	UBSAN_OPTIONS=halt_on_error=1 ASAN_OPTIONS=abort_on_error=1 ./engine/build-fuzz/protocol_fuzz engine/build-fuzz/corpus -dict=engine/fuzz/protocol.dict -max_len=65537 -max_total_time=$(FUZZ_SECONDS) -timeout=10 -artifact_prefix=engine/build-fuzz/artifacts/

rtsan: llvm-check
	$(CMAKE) -S engine -B engine/build-rtsan -DGHOSTBAND_SANITIZE=realtime -DCMAKE_C_COMPILER="$(LLVM_BIN)/clang" -DCMAKE_CXX_COMPILER="$(LLVM_BIN)/clang++" -DCMAKE_OBJCXX_COMPILER="$(LLVM_BIN)/clang++"
	$(CMAKE) --build engine/build-rtsan --target ghostband-engine -j 2
	bash scripts/run_rtsan.sh engine/build-rtsan/ghostband-engine "$(MODEL_DIR)/resources" "$(RTSAN_SECONDS)" "$(RTSAN_TRACE)"

fixtures:
	bash scripts/render_fixtures.sh

fixtures-worst:
	uv run python scripts/make_worst_case.py
