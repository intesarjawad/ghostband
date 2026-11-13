#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <span>
#include <string>

#include "spsc_queue.h"

namespace ghostband {
inline constexpr std::size_t kTapSamples = 4800;
struct TapBlock {
  std::uint64_t sample = 0, generation = 0;
  std::array<float, kTapSamples> pcm{};
};

class AudioTap {
 public:
  void enable(bool on);
  void capture(std::span<const float> left, std::span<const float> right,
               std::uint64_t sample) noexcept;
  bool read(TapBlock& block) noexcept;
  std::uint64_t dropped() const noexcept;

 private:
  // 20 live blocks give two seconds; 32 physical slots keep indexing a power of two.
  SpscQueue<TapBlock, 32, 20> blocks_;
  std::atomic<std::uint64_t> configuration_{0}, dropped_{0};
  std::uint64_t active_configuration_ = 0;
  TapBlock partial_{};
  std::size_t filled_ = 0;
};

/// Encode IEEE float32 little-endian PCM on the controller thread.
std::string serialize_tap(const TapBlock& block);
}  // namespace ghostband
