#include "bench.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

#include "wav_writer.h"

namespace ghostband {
Percentiles percentiles(std::span<const double> values) {
  if (values.empty()) throw std::invalid_argument("No timing observations collected");
  std::vector<double> sorted(values.begin(), values.end());
  for (const auto value : sorted) {
    if (!std::isfinite(value) || value < 0)
      throw std::invalid_argument("Timing observations must be finite and nonnegative");
  }
  std::sort(sorted.begin(), sorted.end());
  const auto rank = [&](double quantile) {
    return sorted[static_cast<std::size_t>(std::ceil(quantile * sorted.size())) - 1];
  };
  return {rank(0.50), rank(0.95)};
}

LatencyProbeResult onset_latency_ms(std::span<const float> left, std::span<const float> right,
                                    std::size_t command_sample) {
  // Require 10 ms above a 5 ms RMS threshold to reject isolated spikes at -60 dBFS.
  constexpr std::size_t kWindowSamples = kRecordingSampleRateHz / 200;
  constexpr std::size_t kBaselineSamples = kRecordingSampleRateHz / 10;
  constexpr double kOnsetPower = 0.001 * 0.001;
  if (left.size() != right.size() || command_sample < kBaselineSamples ||
      command_sample > left.size() || left.size() - command_sample < kWindowSamples)
    throw std::invalid_argument("Latency probe needs a 100 ms baseline and post-command audio");
  // Validate the entire capture so an early onset cannot hide invalid trailing audio.
  for (std::size_t i = 0; i < left.size(); ++i) {
    if (!std::isfinite(left[i]) || !std::isfinite(right[i]))
      return LatencyProbeFailure::NonFiniteSamples;
  }
  const auto power = [&](std::size_t i) {
    return (static_cast<double>(left[i]) * left[i] + static_cast<double>(right[i]) * right[i]) / 2;
  };
  for (std::size_t i = command_sample - kBaselineSamples; i < command_sample; ++i) {
    if (power(i) >= kOnsetPower) return LatencyProbeFailure::NoisyBaseline;
  }
  double window_power = 0;
  std::size_t above_threshold = 0, onset_end = 0;
  for (std::size_t i = command_sample; i < left.size(); ++i) {
    window_power += power(i);
    if (i >= command_sample + kWindowSamples) window_power -= power(i - kWindowSamples);
    if (i + 1 >= command_sample + kWindowSamples && window_power / kWindowSamples >= kOnsetPower) {
      if (above_threshold == 0) onset_end = i + 1;
      if (++above_threshold >= 2 * kWindowSamples)
        return static_cast<double>(onset_end - command_sample) * 1000 / kRecordingSampleRateHz;
    } else {
      above_threshold = 0;
    }
  }
  return LatencyProbeFailure::MissingOnset;
}

nlohmann::json latency_summary(std::span<const LatencyProbeResult> probes) {
  std::vector<double> successful_ms;
  auto results = nlohmann::json::array();
  nlohmann::json failures{{"noisy_baseline", 0}, {"non_finite_samples", 0}, {"missing_onset", 0}};
  for (const auto& probe : probes) {
    if (const auto* latency_ms = std::get_if<double>(&probe)) {
      successful_ms.push_back(*latency_ms);
      results.push_back({{"probe", results.size() + 1},
                         {"status", "success"},
                         {"reason", nullptr},
                         {"latency_ms", *latency_ms}});
    } else {
      const auto reason = [](LatencyProbeFailure failure) {
        switch (failure) {
          case LatencyProbeFailure::NoisyBaseline:
            return "noisy_baseline";
          case LatencyProbeFailure::NonFiniteSamples:
            return "non_finite_samples";
          case LatencyProbeFailure::MissingOnset:
            return "missing_onset";
        }
        throw std::logic_error("Unknown latency probe failure");
      }(std::get<LatencyProbeFailure>(probe));
      failures[reason] = failures.at(reason).get<std::size_t>() + 1;
      results.push_back({{"probe", results.size() + 1},
                         {"status", "excluded"},
                         {"reason", reason},
                         {"latency_ms", nullptr}});
    }
  }
  nlohmann::json latency = nullptr;
  if (!successful_ms.empty() && successful_ms.size() >= (probes.size() + 1) / 2) {
    const auto summary = percentiles(successful_ms);
    latency = {{"p50_ms", summary.p50_ms},
               {"p95_ms", summary.p95_ms},
               {"observations", successful_ms.size()}};
  }
  return {{"latency", latency},
          {"latency_successful", successful_ms.size()},
          {"latency_failed", probes.size() - successful_ms.size()},
          {"latency_failure_counts", failures},
          {"latency_results", results}};
}
}  // namespace ghostband
