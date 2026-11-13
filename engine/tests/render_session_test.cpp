#include <magentart/realtime_runner.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <future>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <vector>

#include "../src/audio_output.h"
#include "../src/protocol.h"
#include "../src/wav_writer.h"

namespace {
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
constexpr std::size_t kSampleRateHz = 48000, kCallbackSamples = 512;
constexpr std::size_t kSessionSamples = 30 * kSampleRateHz;
constexpr float kModelSample = 0.125f;

void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

struct Fixture {
  std::filesystem::path directory, path;
  Fixture() {
    char name[] = "/tmp/bm-render-session-XXXXXX";
    require(::mkdtemp(name) != nullptr, "Cannot create render session fixtures");
    directory = name;
    path = directory / "reference.wav";
    // Match a 100-frame stereo float32 export: 1,536,044 bytes.
    const std::vector<float> audio(100 * 1920, 0.25f);
    ghostband::WavWriter writer(path);
    writer.append(audio, audio);
    writer.finish();
  }
  ~Fixture() { std::filesystem::remove_all(directory); }
};

struct CallbackObservation {
  std::uint64_t sample;
  std::string_view command;
  Clock::duration elapsed;
  std::uint64_t underruns;
  std::size_t available;
  float frame_ms;
  int prompt_status;
};

struct OfflineOutput {
  std::array<float, kCallbackSamples> left{}, right{};
  std::vector<CallbackObservation> observations;
  magentart::core::RealtimeRunner* runner;
  std::atomic<std::uint64_t> played_samples{0};
  const std::unique_ptr<AudioBufferList, decltype(&std::free)> buffers{
      static_cast<AudioBufferList*>(
          std::malloc(offsetof(AudioBufferList, mBuffers) + 2 * sizeof(AudioBuffer))),
      &std::free};
  explicit OfflineOutput(magentart::core::RealtimeRunner* model = nullptr) : runner(model) {
    require(buffers != nullptr, "Cannot allocate offline output buffers");
    buffers->mNumberBuffers = 2;
    buffers->mBuffers[0] = {1, sizeof(left), left.data()};
    buffers->mBuffers[1] = {1, sizeof(right), right.data()};
    observations.reserve(kSessionSamples / kCallbackSamples + 1);
  }
  void render(ghostband::RenderExtensions& extensions, std::uint64_t sample,
              std::string_view command, std::size_t count = kCallbackSamples) {
    left.fill(kModelSample);
    right.fill(kModelSample);
    const auto metrics = runner ? runner->get_metrics() : magentart::core::EngineMetrics{};
    const auto started = Clock::now();
    OSStatus status = noErr;
    if (runner)
      status = ghostband::render_audio(*runner, *buffers, static_cast<std::uint32_t>(count),
                                       &played_samples, &extensions);
    else
      extensions.render(std::span(left).first(count), std::span(right).first(count), sample);
    const auto elapsed = Clock::now() - started;
    const auto underruns = runner ? runner->get_metrics().dropped_frames : 0;
    observations.push_back({sample, command, elapsed, underruns - metrics.dropped_frames,
                            metrics.buffer_available, metrics.total_ms,
                            runner ? runner->get_prompt_status(0) : 0});
    require(status == noErr, "Offline audio callback failed");
    require(std::all_of(left.begin(), left.begin() + count,
                        [](float value) { return std::isfinite(value); }),
            "Render produced non-finite output");
  }
  void report(std::string_view scenario) const {
    const auto deadline = std::chrono::duration<double>(static_cast<double>(kCallbackSamples) /
                                                        static_cast<double>(kSampleRateHz));
    std::size_t stalls = 0;
    for (const auto& observation : observations) {
      if (observation.underruns)
        std::cout << "Model underrun: sample=" << observation.sample
                  << " after_command=" << observation.command
                  << " available_before=" << observation.available
                  << " frame_ms=" << observation.frame_ms
                  << " prompt_status=" << observation.prompt_status << '\n';
      if (observation.elapsed <= deadline) continue;
      ++stalls;
      std::cout << "Callback deadline missed: scenario=" << scenario
                << " sample=" << observation.sample << " command=" << observation.command
                << " elapsed_ms="
                << std::chrono::duration<double, std::milli>(observation.elapsed).count() << '\n';
    }
    const auto longest = std::max_element(
        observations.begin(), observations.end(),
        [](const auto& left, const auto& right) { return left.elapsed < right.elapsed; });
    std::cout << "Offline callbacks: scenario=" << scenario << " callbacks=" << observations.size()
              << " stalls=" << stalls
              << " max_ms=" << std::chrono::duration<double, std::milli>(longest->elapsed).count()
              << '\n';
    require(stalls == 0, "Offline render exceeded the 512-sample callback deadline");
  }
};

void protocol_sequence(const Fixture& fixture, magentart::core::RealtimeRunner* runner) {
  auto extensions = std::make_unique<ghostband::RenderExtensions>();
  OfflineOutput output(runner);
  const auto load_started = Clock::now();
  extensions->reference.load(fixture.path);
  std::cout << "Pre-play ref_load_ms="
            << std::chrono::duration<double, std::milli>(Clock::now() - load_started).count()
            << '\n';
  if (runner) {
    std::vector<std::string> prompts(ghostband::kPromptSlots);
    std::vector<float> weights(ghostband::kPromptSlots);
    prompts[0] = "a jazz piano trio";
    weights[0] = 1;
    runner->set_text_prompts(prompts, weights);
    runner->set_blend_weights(weights.data(), weights.size());
    for (const int pitch : {60, 64, 67}) runner->set_note_on(pitch);
  }
  extensions->tap.enable(true);
  require(extensions->reference.mix(1, 0), "Cannot set initial mix");
  require(extensions->reference.schedule(kSampleRateHz, 0), "Cannot schedule initial reference");
  for (std::uint64_t beat = 0; beat < 60; ++beat)
    require(extensions->metronome.schedule(kSampleRateHz + beat * kSampleRateHz / 2, beat % 4 == 0),
            "Cannot schedule protocol clicks");
  std::size_t next_toggle = 5 * kSampleRateHz, next_status = ghostband::kTapSamples;
  std::size_t blocks = 0;
  std::string_view command = "play";
  ghostband::TapBlock tap;
  if (runner) {
    runner->start();
    const auto metrics = runner->get_metrics();
    std::cout << "Post-prime available=" << metrics.buffer_available
              << " underruns=" << metrics.dropped_frames << '\n';
  }
  const auto started = Clock::now();
  for (std::size_t sample = 0; sample < kSessionSamples; sample += kCallbackSamples) {
    if (runner)
      std::this_thread::sleep_until(
          started + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(
                        static_cast<double>(sample) / kSampleRateHz)));
    const auto count = std::min(kCallbackSamples, kSessionSamples - sample);
    output.render(*extensions, sample, command, count);
    if (sample + count < next_status) continue;
    next_status += ghostband::kTapSamples;
    while (extensions->tap.read(tap)) {
      require(tap.sample == blocks * ghostband::kTapSamples, "Tap clock skipped samples");
      require(runner || std::all_of(tap.pcm.begin(), tap.pcm.end(),
                                    [](float value) { return value == kModelSample; }),
              "Tap captured mixed audio instead of model audio");
      require(!ghostband::serialize_tap(tap).empty(), "Cannot encode tap event");
      ++blocks;
    }
    if (sample + count < next_toggle) continue;
    const bool reference_on = (next_toggle / (5 * kSampleRateHz)) % 2 == 1;
    require(extensions->reference.mix(reference_on ? 0 : 1, reference_on ? 1 : 0),
            "Cannot alternate reference gain");
    require(extensions->reference.schedule(sample + count + ghostband::kTapSamples, kSampleRateHz),
            "Cannot schedule protocol seek");
    next_toggle += 5 * kSampleRateHz;
    command = "ref_mix/ref_play";
    if (runner) std::cout << "Command: sample=" << sample + count << " name=" << command << '\n';
  }
  if (runner) runner->stop();
  require(blocks == kSessionSamples / ghostband::kTapSamples, "Tap lost a complete block");
  require(extensions->tap.dropped() == 0, "Tap dropped a block during protocol replay");
  output.report(runner ? "model-backed 30-second protocol sequence"
                       : "synthetic 30-second protocol sequence");
  require(!runner || runner->get_metrics().dropped_frames == 0,
          "Model inference starved during offline protocol replay");
}

void blocked_reference_load(const Fixture& fixture) {
  auto extensions = std::make_unique<ghostband::RenderExtensions>();
  extensions->reference.load(fixture.path);
  require(extensions->reference.mix(0, 1) && extensions->reference.schedule(0, 0),
          "Cannot prepare reference playback");
  OfflineOutput output;
  output.render(*extensions, 0, "ref_play");
  const auto fifo = fixture.directory / "delayed.wav";
  require(::mkfifo(fifo.c_str(), 0600) == 0, "Cannot create delayed WAV input");
  std::promise<void> writer_ready, release_writer;
  auto released = release_writer.get_future();
  // Opening the FIFO pairs with the loader's open; no bytes arrive until explicitly released.
  auto writer = std::async(std::launch::async, [&] {
    std::ofstream stream(fifo, std::ios::binary);
    writer_ready.set_value();
    released.wait();
    stream.write("bad!", 4);
  });
  std::thread::id loader_thread;
  std::exception_ptr load_error;
  auto loader = std::async(std::launch::async, [&] {
    loader_thread = std::this_thread::get_id();
    try {
      extensions->reference.load(fifo);
    } catch (...) {
      load_error = std::current_exception();
    }
  });
  writer_ready.get_future().wait();
  const auto load_pending = loader.wait_for(0s) == std::future_status::timeout;
  std::exception_ptr render_error;
  try {
    for (std::size_t sample = kCallbackSamples; sample < kSampleRateHz; sample += kCallbackSamples)
      output.render(*extensions, sample, "ref_load blocked on file input");
  } catch (...) {
    render_error = std::current_exception();
  }
  const auto still_pending = loader.wait_for(0s) == std::future_status::timeout;
  release_writer.set_value();
  writer.get();
  loader.get();
  if (render_error) std::rethrow_exception(render_error);
  require(load_pending && still_pending, "Delayed WAV input did not hold reference loading");
  require(loader_thread != std::this_thread::get_id(), "Reference decoded on render thread");
  require(load_error != nullptr, "Malformed delayed WAV was accepted");
  require(output.left.front() == 0.25f && output.right.front() == 0.25f,
          "Blocked or failed load disturbed the current reference");
  output.report("reference load blocked on file input");
}
}  // namespace

int main(int argc, char** argv) {
  try {
    require(argc == 1 || (argc == 3 && std::string_view(argv[1]) == "--model-dir"),
            "Usage: render_session_test [--model-dir PATH]");
    std::unique_ptr<magentart::core::RealtimeRunner> runner;
    if (argc == 3) {
      const auto directory = std::filesystem::path(argv[2]);
      const auto resources = directory / "resources";
      const auto model = directory / "models/mrt2_small/mrt2_small.mlxfn";
      require(std::filesystem::is_directory(resources) && std::filesystem::is_regular_file(model),
              "Model assets missing: provide resources/ and models/mrt2_small/ under --model-dir");
      runner = std::make_unique<magentart::core::RealtimeRunner>();
      runner->set_buffer_size(3 * magentart::core::kFrameSamples);
      require(runner->init_assets(resources.c_str()) &&
                  runner->load_musiccoca_model(resources.c_str(), "musiccoca") &&
                  runner->load_model(model.c_str()),
              "Cannot load model or MusicCoCa assets");
      runner->stop();
      runner->reset_for_playback();
    }
    Fixture fixture;
    protocol_sequence(fixture, runner.get());
    blocked_reference_load(fixture);
    std::cout << "PASS: offline protocol replay and rendering during blocked reference input\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
