#pragma once

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <span>
#include <vector>

#include "spsc_queue.h"

namespace ghostband {
struct ReferenceAudio {
  std::vector<float> left, right;
};
ReferenceAudio read_reference_wav(const std::filesystem::path& path);

class ReferencePlayer {
 public:
  void reset();
  /// Load and retain immutable audio until the render thread acknowledges its replacement.
  void load(const std::filesystem::path& path);
  bool schedule(std::uint64_t at_sample, std::uint64_t from_sample);
  bool mix(float band_gain, float ref_gain);
  void render(std::span<float> left, std::span<float> right, std::uint64_t sample) noexcept;

 private:
  struct Control {
    const ReferenceAudio* audio = nullptr;
    std::uint64_t generation = 0;
    float band_gain = 1, ref_gain = 0;
  };
  struct Seek {
    std::uint64_t at_sample, from_sample;
  };
  struct OwnedAudio {
    std::uint64_t generation;
    std::unique_ptr<ReferenceAudio> audio;
  };
  struct Gain {
    float current, target, step = 0;
    std::size_t remaining = 0;
    void set(float value) noexcept;
    float next() noexcept;
  };
  SpscQueue<Control, 1024> controls_;
  SpscQueue<Seek, 1024> seeks_;
  std::vector<OwnedAudio> owned_;
  std::uint64_t next_generation_ = 1, last_scheduled_ = 0;
  std::atomic<std::uint64_t> acknowledged_{0};
  const ReferenceAudio* audio_ = nullptr;
  Seek pending_{};
  bool has_pending_ = false, playing_ = false;
  std::uint64_t position_ = 0;
  Gain band_{1, 1}, reference_{0, 0};
};
}  // namespace ghostband
