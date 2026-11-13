#pragma once

#include <array>
#include <cstdint>
#include <span>

#include "spsc_queue.h"

namespace ghostband {
class Metronome {
 public:
  Metronome();
  void reset();
  /// Schedule in nondecreasing sample order; return false when the queue is full.
  bool schedule(std::uint64_t sample, bool accent);
  void render(std::span<float> left, std::span<float> right, std::uint64_t sample) noexcept;

 private:
  struct Click {
    std::uint64_t sample;
    bool accent;
  };
  // A short decaying pulse, 10 ms at the engine's 48 kHz sample rate.
  static constexpr std::size_t kClickSamples = 480;
  std::array<float, kClickSamples> normal_{}, accent_{};
  std::array<float, kClickSamples> tail_{};
  SpscQueue<Click, 1024> clicks_;
  Click pending_{};
  bool has_pending_ = false;
  std::uint64_t last_scheduled_ = 0;
  std::size_t tail_position_ = 0;
};
}  // namespace ghostband
