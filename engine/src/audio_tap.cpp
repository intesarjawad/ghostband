#include "audio_tap.h"

#include <bit>
#include <nlohmann/json.hpp>

namespace ghostband {
void AudioTap::enable(bool on) {
  const auto previous = configuration_.load(std::memory_order_relaxed);
  configuration_.store(((previous + 2) & ~std::uint64_t{1}) | static_cast<std::uint64_t>(on),
                       std::memory_order_release);
  TapBlock discarded;
  while (blocks_.try_pop(discarded)) {
  }
}
void AudioTap::capture(std::span<const float> left, std::span<const float> right,
                       std::uint64_t sample) noexcept {
  const auto configuration = configuration_.load(std::memory_order_acquire);
  if (configuration != active_configuration_) {
    active_configuration_ = configuration;
    filled_ = 0;
    partial_.generation = configuration;
  }
  if (!(configuration & 1)) return;
  for (std::size_t i = 0; i < left.size(); ++i) {
    if (filled_ == 0) partial_.sample = sample + i;
    partial_.pcm[filled_++] = (left[i] + right[i]) * 0.5f;
    if (filled_ == kTapSamples) {
      if (blocks_.push_drop_oldest(partial_)) dropped_.fetch_add(1, std::memory_order_relaxed);
      filled_ = 0;
    }
  }
}
bool AudioTap::read(TapBlock& block) noexcept {
  const auto configuration = configuration_.load(std::memory_order_acquire);
  if (!(configuration & 1)) return false;
  while (blocks_.try_pop(block)) {
    if (block.generation == configuration) return true;
  }
  return false;
}
std::uint64_t AudioTap::dropped() const noexcept {
  return dropped_.load(std::memory_order_relaxed);
}
std::string serialize_tap(const TapBlock& block) {
  constexpr std::string_view kAlphabet =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  static_assert(std::endian::native == std::endian::little);
  const auto bytes = std::as_bytes(std::span(block.pcm));
  std::string encoded;
  encoded.reserve((bytes.size() + 2) / 3 * 4);
  for (std::size_t i = 0; i < bytes.size(); i += 3) {
    const auto first = std::to_integer<unsigned>(bytes[i]);
    const auto second = i + 1 < bytes.size() ? std::to_integer<unsigned>(bytes[i + 1]) : 0;
    const auto third = i + 2 < bytes.size() ? std::to_integer<unsigned>(bytes[i + 2]) : 0;
    encoded += kAlphabet[first >> 2];
    encoded += kAlphabet[((first & 3) << 4) | (second >> 4)];
    encoded += i + 1 < bytes.size() ? kAlphabet[((second & 15) << 2) | (third >> 6)] : '=';
    encoded += i + 2 < bytes.size() ? kAlphabet[third & 63] : '=';
  }
  return nlohmann::json{{"event", "tap"}, {"sample", block.sample}, {"pcm", encoded}}.dump();
}
}  // namespace ghostband
