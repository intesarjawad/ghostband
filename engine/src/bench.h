#pragma once

#include <cstddef>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <span>
#include <variant>

namespace magentart::core {
class RealtimeRunner;
}
namespace ghostband {
struct Percentiles {
  double p50_ms, p95_ms;
};
/// Nearest-rank percentiles of finite, nonnegative observations.
Percentiles percentiles(std::span<const double> values);

enum class LatencyProbeFailure { NoisyBaseline, NonFiniteSamples, MissingOnset };
using LatencyProbeResult = std::variant<double, LatencyProbeFailure>;

/// Return latency in ms or an exclusion reason for unusable audio.
/// command_sample is the recording cursor immediately before sending note-on.
LatencyProbeResult onset_latency_ms(std::span<const float> left, std::span<const float> right,
                                    std::size_t command_sample);

/// Include timings only when at least half of the requested probes succeed.
nlohmann::json latency_summary(std::span<const LatencyProbeResult> probes);

/// Run on a loaded, stopped runner. Uses four buffered frames and leaves it stopped.
/// Frame observations are 40 ms snapshots because upstream exposes no frame sequence ID.
nlohmann::json run_bench(magentart::core::RealtimeRunner& runner, double seconds,
                         int latency_probes, const std::filesystem::path& resources);
}  // namespace ghostband
