#include "metronome.h"

#include <cmath>
#include <numbers>
#include <stdexcept>

namespace ghostband {
Metronome::Metronome() {
  constexpr float kNormalHz = 1000, kAccentHz = 1500, kAmplitude = 0.25f;
  for (std::size_t i = 0; i < kClickSamples; ++i) {
    const auto time = static_cast<float>(i) / 48000;
    const auto envelope = kAmplitude * (1 - static_cast<float>(i) / kClickSamples);
    normal_[i] = envelope * std::cos(2 * std::numbers::pi_v<float> * kNormalHz * time);
    accent_[i] = envelope * std::cos(2 * std::numbers::pi_v<float> * kAccentHz * time);
  }
}
void Metronome::reset() {
  Click discarded;
  while (clicks_.try_pop(discarded)) {
  }
  has_pending_ = false;
  last_scheduled_ = 0;
  tail_.fill(0);
  tail_position_ = 0;
}
bool Metronome::schedule(std::uint64_t sample, bool accent) {
  if (sample < last_scheduled_)
    throw std::invalid_argument("Clicks must be scheduled in sample order");
  if (!clicks_.try_push({sample, accent})) return false;
  last_scheduled_ = sample;
  return true;
}
void Metronome::render(std::span<float> left, std::span<float> right,
                       std::uint64_t sample) noexcept {
  std::size_t budget = 1024;
  for (std::size_t i = 0; i < left.size(); ++i) {
    if (!has_pending_) has_pending_ = clicks_.try_pop(pending_);
    while (budget && has_pending_ && pending_.sample <= sample + i) {
      --budget;
      const auto age = sample + i - pending_.sample;
      const auto& pulse = pending_.accent ? accent_ : normal_;
      for (std::size_t offset = 0; age + offset < kClickSamples; ++offset)
        tail_[(tail_position_ + offset) % kClickSamples] += pulse[age + offset];
      has_pending_ = clicks_.try_pop(pending_);
    }
    left[i] += tail_[tail_position_];
    right[i] += tail_[tail_position_];
    tail_[tail_position_] = 0;
    tail_position_ = (tail_position_ + 1) % kClickSamples;
  }
}
}  // namespace ghostband
