#include <magentart/realtime_runner.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

#include "../src/bench.h"
#include "../src/recording.h"

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
template <typename Function>
void rejects(Function function) {
  bool rejected = false;
  try {
    function();
  } catch (const std::exception&) {
    rejected = true;
  }
  require(rejected, "Invalid operation succeeded");
}
struct TemporaryDirectory {
  std::array<char, 40> name{};
  std::filesystem::path path;
  TemporaryDirectory() {
    std::strcpy(name.data(), "/tmp/ghostband-recording-XXXXXX");
    const auto* created = ::mkdtemp(name.data());
    if (!created) throw std::runtime_error("Cannot create temporary test directory");
    path = created;
  }
  ~TemporaryDirectory() { std::filesystem::remove_all(path); }
};
std::vector<char> read_file(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
template <typename T>
T value_at(const std::vector<char>& data, std::size_t offset) {
  require(offset + sizeof(T) <= data.size(), "WAV data is truncated");
  T result;
  std::memcpy(&result, data.data() + offset, sizeof(T));
  return result;
}
void wav_samples(const TemporaryDirectory& directory) {
  const auto path = directory.path / "stereo.wav";
  ghostband::WavWriter writer(path);
  const std::array<float, 3> left{0.25f, -0.5f, 0.75f}, right{-0.25f, 0.5f, -0.75f};
  writer.append(left, right);
  writer.append(std::span(left).first(1), std::span(right).first(1));
  writer.finish();
  const auto bytes = read_file(path);
  require(bytes.size() == 44 + 4 * 8, "Wrong WAV length");
  require(std::string(bytes.data(), 4) == "RIFF" &&
              std::string(bytes.data() + 8, 8) == "WAVEfmt " &&
              std::string(bytes.data() + 36, 4) == "data",
          "Wrong WAV chunk identifiers");
  require(
      value_at<std::uint32_t>(bytes, 4) == bytes.size() - 8 &&
          value_at<std::uint32_t>(bytes, 16) == 16 && value_at<std::uint16_t>(bytes, 20) == 3 &&
          value_at<std::uint16_t>(bytes, 22) == 2 && value_at<std::uint32_t>(bytes, 24) == 48000 &&
          value_at<std::uint32_t>(bytes, 28) == 384000 && value_at<std::uint16_t>(bytes, 32) == 8 &&
          value_at<std::uint16_t>(bytes, 34) == 32 && value_at<std::uint32_t>(bytes, 40) == 32,
      "Wrong stereo float WAV metadata");
  for (std::size_t i = 0; i < 4; ++i) {
    const auto source = i == 3 ? 0 : i;
    require(value_at<float>(bytes, 44 + i * 8) == left[source] &&
                value_at<float>(bytes, 48 + i * 8) == right[source],
            "Stereo samples changed or channels swapped");
  }
  const auto chunked_path = directory.path / "chunked.wav";
  ghostband::WavWriter chunked(chunked_path);
  std::vector<float> long_left(2053, 0.25f), long_right(2053, -0.5f);
  chunked.append(long_left, long_right);
  chunked.finish();
  const auto chunks = read_file(chunked_path);
  require(chunks.size() == 44 + 2053 * 8 && value_at<float>(chunks, 44 + 2052 * 8) == 0.25f &&
              value_at<float>(chunks, 48 + 2052 * 8) == -0.5f,
          "Chunked write lost the final partial block or changed channels");
  ghostband::WavWriter invalid(directory.path / "invalid.wav");
  rejects([&] { invalid.append(left, std::span(right).first(1)); });
  const std::array<float, 1> nan{std::numeric_limits<float>::quiet_NaN()};
  rejects([&] { invalid.append(nan, nan); });
  invalid.finish();
  require(read_file(directory.path / "invalid.wav").size() == 44,
          "Rejected samples changed the WAV");
  rejects([&] { ghostband::WavWriter missing(directory.path / "missing/out.wav"); });
}
void recording(const TemporaryDirectory& directory) {
  magentart::core::RealtimeRunner runner;
  ghostband::Recording take(runner);
  rejects([&] { ghostband::run_bench(runner, 0, 0, directory.path); });
  rejects([&] { ghostband::run_bench(runner, NAN, 0, directory.path); });
  rejects([&] { ghostband::run_bench(runner, 1, -1, directory.path); });
  bool missing_encoder = false;
  try {
    ghostband::run_bench(runner, 1, 1, directory.path);
  } catch (const std::runtime_error& error) {
    missing_encoder =
        std::string(error.what()).find("SpectroStream encoder assets") != std::string::npos;
  }
  require(missing_encoder, "Missing encoder did not fail before opening audio output");
  rejects([&] { take.stop(); });
  rejects([&] { take.start(directory.path / "missing/out.wav"); });
  require(!take.active(), "Failed file open armed the recorder");
  const auto path = directory.path / "twenty-seconds.wav";
  take.start(path);
  rejects([&] { take.start(directory.path / "duplicate.wav"); });
  std::array<float, 1920> left, right;
  for (int frame = 0; frame < 500; ++frame)
    runner.read_audio_stereo(left.data(), right.data(), left.size(), false);
  require(take.active() && runner.get_recorded_sample_count() == 960000,
          "Recorder did not capture exactly twenty seconds");
  require(take.stop() == 960000 && !take.active(), "Stop lost recorded samples");
  runner.read_audio_stereo(left.data(), right.data(), left.size(), false);
  require(runner.get_recorded_sample_count() == 960000, "Recording continued after stop");
  const auto bytes = read_file(path);
  require(bytes.size() == 44 + 960000 * 8 && value_at<std::uint32_t>(bytes, 40) == 960000 * 8,
          "Twenty-second WAV header or duration is wrong");
  take.start(directory.path / "partial.wav");
  runner.read_audio_stereo(left.data(), right.data(), 137, false);
  require(take.stop() == 137 && read_file(directory.path / "partial.wav").size() == 44 + 137 * 8,
          "Recorder lost a partial model frame");
  take.start(directory.path / "empty.wav");
  require(take.stop() == 0 && read_file(directory.path / "empty.wav").size() == 44,
          "Empty recording is not a valid WAV");
  take.start(directory.path / "cap.wav");
  require(runner.get_recorded_sample_count() == 0, "New recording retained the previous take");
  for (int frame = 0; frame < 7501; ++frame)
    runner.read_audio_stereo(left.data(), right.data(), left.size(), false);
  require(take.stop() == 5 * 60 * 48000, "Upstream five-minute cap changed");
}
void timing_and_onsets() {
  const std::array<double, 5> values{50, 10, 40, 20, 30};
  const auto summary = ghostband::percentiles(values);
  require(summary.p50_ms == 30 && summary.p95_ms == 50, "Wrong nearest-rank percentiles");
  require(ghostband::percentiles(std::array<double, 1>{7}).p95_ms == 7,
          "Singleton percentile changed");
  std::array<double, 100> hundred;
  for (std::size_t i = 0; i < hundred.size(); ++i) hundred[i] = static_cast<double>(i + 1);
  const auto large_summary = ghostband::percentiles(hundred);
  require(large_summary.p50_ms == 50 && large_summary.p95_ms == 95,
          "p95 used the maximum instead of its rank");
  rejects([] { ghostband::percentiles({}); });
  rejects([] { ghostband::percentiles(std::array<double, 1>{-1}); });
  rejects([] { ghostband::percentiles(std::array<double, 1>{NAN}); });
  constexpr std::size_t command = 12000, onset = command + 9600;
  std::vector<float> left(48000), right(48000);
  // Opposite phase must not cancel in the stereo power detector.
  for (std::size_t i = onset; i < left.size(); ++i) {
    left[i] = 0.1f * std::sin(static_cast<float>(i - onset) * 0.02f);
    right[i] = -left[i];
  }
  const auto successful = ghostband::onset_latency_ms(left, right, command);
  const auto latency = std::get<double>(successful);
  require(latency >= 200 && latency <= 205, "Known onset latency differs by more than 5 ms");
  left[command - 1] = 0.5;
  const auto noisy = ghostband::onset_latency_ms(left, right, command);
  require(std::get<ghostband::LatencyProbeFailure>(noisy) ==
              ghostband::LatencyProbeFailure::NoisyBaseline,
          "Noisy baseline with a valid onset did not exclude the probe");
  left[command - 1] = 0;
  right.back() = std::numeric_limits<float>::infinity();
  const auto non_finite = ghostband::onset_latency_ms(left, right, command);
  require(std::get<ghostband::LatencyProbeFailure>(non_finite) ==
              ghostband::LatencyProbeFailure::NonFiniteSamples,
          "Non-finite audio after a valid onset was accepted");
  std::fill(left.begin(), left.end(), 0);
  std::fill(right.begin(), right.end(), 0);
  const auto missing = ghostband::onset_latency_ms(left, right, command);
  require(std::get<ghostband::LatencyProbeFailure>(missing) ==
              ghostband::LatencyProbeFailure::MissingOnset,
          "Silent probe did not report a missing onset");
  left[onset] = 1;
  require(ghostband::onset_latency_ms(left, right, command) == missing,
          "Isolated spike was accepted as a sustained onset");
  left[command - 1] = 0.5;
  require(ghostband::onset_latency_ms(left, right, command) == noisy,
          "Noisy baseline did not exclude the probe");

  const std::array<ghostband::LatencyProbeResult, 2> half{noisy, successful};
  const auto half_summary = ghostband::latency_summary(half);
  require(half_summary.at("latency").at("observations") == 1 &&
              half_summary.at("latency").at("p50_ms") == latency &&
              half_summary.at("latency").at("p95_ms") == latency &&
              half_summary.at("latency_successful") == 1 && half_summary.at("latency_failed") == 1,
          "Noisy baseline aborted aggregation or contaminated successful timings");
  require(half_summary.at("latency_results").at(0) == nlohmann::json{{"probe", 1},
                                                                     {"status", "excluded"},
                                                                     {"reason", "noisy_baseline"},
                                                                     {"latency_ms", nullptr}} &&
              half_summary.at("latency_results").at(1) == nlohmann::json{{"probe", 2},
                                                                         {"status", "success"},
                                                                         {"reason", nullptr},
                                                                         {"latency_ms", latency}},
          "Per-probe status lost the exclusion reason or fabricated a latency");
  const std::array<ghostband::LatencyProbeResult, 4> probes{noisy, non_finite, missing, successful};
  const auto failed_summary = ghostband::latency_summary(probes);
  require(failed_summary.at("latency").is_null() && failed_summary.at("latency_successful") == 1 &&
              failed_summary.at("latency_failed") == 3 &&
              failed_summary.at("latency_failure_counts") ==
                  nlohmann::json{
                      {"noisy_baseline", 1}, {"non_finite_samples", 1}, {"missing_onset", 1}} &&
              failed_summary.at("latency_results").size() == probes.size(),
          "Fewer than half successful probes must report null latency and failure counts");
  const std::array<ghostband::LatencyProbeResult, 3> odd{noisy, successful, successful};
  require(!ghostband::latency_summary(odd).at("latency").is_null() &&
              ghostband::latency_summary(std::span(probes).first(3)).at("latency").is_null() &&
              ghostband::latency_summary(std::span(probes).last(3)).at("latency").is_null(),
          "Odd probe counts did not require at least half to succeed");
  const auto empty = ghostband::latency_summary({});
  require(empty.at("latency").is_null() && empty.at("latency_successful") == 0 &&
              empty.at("latency_failed") == 0 && empty.at("latency_results").empty(),
          "Zero probes fabricated a latency or failure");
  rejects([&] { ghostband::onset_latency_ms(left, right, 0); });
  rejects([&] { ghostband::onset_latency_ms(left, right, left.size()); });
  rejects([&] { ghostband::onset_latency_ms(left, std::span(right).first(1), command); });
  left[command - 1] = NAN;
  require(ghostband::onset_latency_ms(left, right, command) == non_finite,
          "Non-finite baseline did not exclude the probe");
}
}  // namespace
int main() {
  try {
    TemporaryDirectory directory;
    wav_samples(directory);
    recording(directory);
    timing_and_onsets();
    std::cout << "PASS: WAV metadata and stereo samples; twenty-second recording; lifecycle and "
                 "five-minute cap; percentiles; onset, silence and invalid probes; "
                 "probe exclusions, failure counts and success threshold\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
