#include "recording.h"

#include <magentart/realtime_runner.h>

#include <algorithm>
#include <array>
#include <stdexcept>

namespace ghostband {
Recording::Recording(magentart::core::RealtimeRunner& runner) : runner_(runner) {}
Recording::~Recording() { runner_.stop_recording(); }

void Recording::start(const std::filesystem::path& path) {
  if (active()) throw std::runtime_error("Stop the current recording before starting another");
  auto candidate = std::make_unique<WavWriter>(path);
  runner_.start_recording();
  writer_ = std::move(candidate);
}

std::size_t Recording::stop() {
  if (!active()) throw std::runtime_error("No recording is active");
  runner_.stop_recording();
  auto writer = std::move(writer_);
  const auto count = runner_.get_recorded_sample_count();
  std::array<float, magentart::core::kFrameSamples> left, right;
  for (std::size_t start = 0; start < count; start += left.size()) {
    const auto size = std::min(left.size(), count - start);
    if (!runner_.get_recorded_audio(left.data(), right.data(), start, size))
      throw std::runtime_error("Cannot copy the upstream recording");
    writer->append(std::span(left).first(size), std::span(right).first(size));
  }
  writer->finish();
  return count;
}
}  // namespace ghostband
