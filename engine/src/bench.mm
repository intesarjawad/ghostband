#include "bench.h"
#include "audio_output.h"

#include <magentart/realtime_runner.h>

#include <chrono>
#include <cmath>
#include <stdexcept>
#include <thread>
#include <vector>

namespace ghostband {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto kFramePeriod = std::chrono::milliseconds(40);
constexpr int kProbePitch = 36;
constexpr int kBenchBufferFrames = 4;

nlohmann::json timing_summary(const std::vector<double>& values) {
  const auto result = percentiles(values);
  return {{"p50_ms", result.p50_ms}, {"p95_ms", result.p95_ms}, {"observations", values.size()}};
}

LatencyProbeResult latency_probe(magentart::core::RealtimeRunner& runner, AudioOutput& output) {
  if (!runner.prefill_silence()) throw std::runtime_error("Cannot prefill silent probe context");
  runner.start_recording();
  output.start();
  std::this_thread::sleep_for(std::chrono::milliseconds(250));
  const auto command_sample = runner.get_recorded_sample_count();
  runner.set_note_on(kProbePitch);
  std::this_thread::sleep_for(std::chrono::seconds(2));
  output.stop();
  runner.stop_recording();
  runner.set_note_off(kProbePitch);
  const auto count = runner.get_recorded_sample_count();
  std::vector<float> left(count), right(count);
  if (!runner.get_recorded_audio(left.data(), right.data(), 0, count))
    throw std::runtime_error("Cannot copy latency probe recording");
  return onset_latency_ms(left, right, command_sample);
}
}  // namespace

nlohmann::json run_bench(magentart::core::RealtimeRunner& runner, double seconds,
                         int latency_probes, const std::filesystem::path& resources) {
  if (!std::isfinite(seconds) || seconds <= 0 || latency_probes < 0)
    throw std::invalid_argument("Bench duration must be positive and probe count nonnegative");
  const auto buffer_size = runner.get_buffer_size();
  const auto drumless = runner.get_drumless();
  const auto onset_mode = runner.get_onset_mode();
  const auto midi_gate = runner.get_midi_gate_enabled();
  if (latency_probes > 0) {
    const auto encoder = resources / "spectrostream/spectrostream_encoder.mlxfn";
    if (!std::filesystem::is_regular_file(encoder) ||
        !runner.load_prefill_model(encoder.c_str(), nullptr))
      throw std::runtime_error("Latency probes require SpectroStream encoder assets under " +
                               resources.string());
  }
  AudioOutput output(runner);
  std::vector<double> frame_ms;
  std::vector<LatencyProbeResult> probes;
  bool prefilled = false;
  const auto restore = [&] {
    output.stop();
    runner.stop_recording();
    for (int pitch = 0; pitch < 128; ++pitch) runner.set_note_off(pitch);
    runner.set_buffer_size(buffer_size);
    runner.set_drumless(drumless);
    runner.set_onset_mode(onset_mode);
    runner.set_midi_gate_enabled(midi_gate);
    if (prefilled) runner.reset_to_factory();
  };
  try {
    runner.set_buffer_size(kBenchBufferFrames * magentart::core::kFrameSamples);
    runner.reset_dropped_frames();
    output.start();
    const auto start = Clock::now();
    while (std::chrono::duration<double>(Clock::now() - start).count() < seconds) {
      std::this_thread::sleep_for(kFramePeriod);
      const auto metrics = runner.get_metrics();
      if (!std::isfinite(metrics.total_ms) || metrics.total_ms < 0)
        throw std::runtime_error("Runner returned an invalid frame timing");
      if (metrics.total_ms > 0) frame_ms.push_back(metrics.total_ms);
    }
    output.stop();
    const auto underruns = runner.get_metrics().dropped_frames;
    runner.set_drumless(true);
    runner.set_onset_mode(0);
    runner.set_midi_gate_enabled(false);
    for (int pitch = 0; pitch < 128; ++pitch) runner.set_note_off(pitch);
    for (int probe = 0; probe < latency_probes; ++probe) {
      prefilled = true;
      probes.push_back(latency_probe(runner, output));
    }
    nlohmann::json result{
        {"ok", true},
        {"seconds", seconds},
        {"buffer_frames", kBenchBufferFrames},
        {"frame_sampling", "40 ms get_metrics snapshots"},
        {"frame_time", timing_summary(frame_ms)},
        {"underruns", underruns},
        {"latency_probes", latency_probes},
        {"probe_pitch", kProbePitch},
        {"latency_clock", "recording samples; callback-quantized command cursor"}};
    result.update(latency_summary(probes));
    restore();
    return result;
  } catch (...) {
    restore();
    throw;
  }
}
}  // namespace ghostband
