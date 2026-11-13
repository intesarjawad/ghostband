#include "reference_player.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstring>
#include <fstream>
#include <stdexcept>

namespace ghostband {
namespace {
constexpr std::uint32_t kSampleRateHz = 48000;
constexpr std::size_t kGainRampSamples = kSampleRateHz / 100;
constexpr std::size_t kStereoFrameBytes = 2 * sizeof(float);

template <typename T>
T read_value(std::istream& input) {
  static_assert(std::endian::native == std::endian::little);
  T value{};
  input.read(reinterpret_cast<char*>(&value), sizeof(value));
  return value;
}
std::array<char, 4> tag(std::istream& input) {
  std::array<char, 4> value{};
  input.read(value.data(), value.size());
  return value;
}
bool is_tag(const std::array<char, 4>& value, const char* expected) {
  return std::memcmp(value.data(), expected, value.size()) == 0;
}
void validate_format(std::istream& input, std::uint32_t size) {
  if (size < 16) throw std::runtime_error("Truncated reference WAV format");
  const auto format = read_value<std::uint16_t>(input);
  const auto channels = read_value<std::uint16_t>(input);
  const auto rate = read_value<std::uint32_t>(input);
  const auto bytes_per_second = read_value<std::uint32_t>(input);
  const auto block_bytes = read_value<std::uint16_t>(input);
  const auto bits = read_value<std::uint16_t>(input);
  bool floating = format == 3;
  if (format == 0xfffe && size >= 40) {
    const auto extra = read_value<std::uint16_t>(input);
    const auto valid_bits = read_value<std::uint16_t>(input);
    (void)read_value<std::uint32_t>(input);  // Channel mask does not change sample interleaving.
    std::array<unsigned char, 16> guid{};
    input.read(reinterpret_cast<char*>(guid.data()), guid.size());
    constexpr std::array<unsigned char, 16> kFloatGuid{3,   0, 0, 0,   0, 0,  16,  0,
                                                       128, 0, 0, 170, 0, 56, 155, 113};
    floating = extra >= 22 && valid_bits == 32 && guid == kFloatGuid;
  }
  if (!floating || channels != 2 || rate != kSampleRateHz || bits != 32 ||
      block_bytes != kStereoFrameBytes || bytes_per_second != kSampleRateHz * kStereoFrameBytes)
    throw std::runtime_error("Reference must be a 48 kHz stereo float32 WAV");
}
}  // namespace

ReferenceAudio read_reference_wav(const std::filesystem::path& path) {
  std::ifstream input(path, std::ios::binary);
  if (!input) throw std::runtime_error("Cannot read reference WAV: " + path.string());
  input.exceptions(std::ios::failbit | std::ios::badbit);
  if (!is_tag(tag(input), "RIFF")) throw std::runtime_error("Reference must be a RIFF WAV");
  const auto riff_bytes = read_value<std::uint32_t>(input);
  if (!is_tag(tag(input), "WAVE")) throw std::runtime_error("Reference must be a WAV");
  const auto end = std::uint64_t{riff_bytes} + 8;
  if (end > std::filesystem::file_size(path)) throw std::runtime_error("Truncated reference WAV");
  bool format_found = false;
  std::uint64_t data_offset = 0;
  std::uint32_t data_bytes = 0;
  while (static_cast<std::uint64_t>(input.tellg()) + 8 <= end) {
    const auto chunk = tag(input);
    const auto size = read_value<std::uint32_t>(input);
    const auto start = static_cast<std::uint64_t>(input.tellg());
    const auto next = start + size + (size & 1);
    if (next > end) throw std::runtime_error("Reference WAV chunk exceeds RIFF size");
    if (is_tag(chunk, "fmt ")) {
      validate_format(input, size);
      format_found = true;
    } else if (is_tag(chunk, "data")) {
      data_offset = start;
      data_bytes = size;
    }
    input.seekg(static_cast<std::streamoff>(next));
  }
  if (!format_found || !data_offset || !data_bytes || data_bytes % kStereoFrameBytes != 0)
    throw std::runtime_error("Reference WAV needs a format and complete stereo frames");
  ReferenceAudio result;
  result.left.resize(data_bytes / kStereoFrameBytes);
  result.right.resize(result.left.size());
  input.seekg(static_cast<std::streamoff>(data_offset));
  for (std::size_t i = 0; i < result.left.size(); ++i) {
    result.left[i] = read_value<float>(input);
    result.right[i] = read_value<float>(input);
    if (!std::isfinite(result.left[i]) || !std::isfinite(result.right[i]))
      throw std::runtime_error("Reference WAV contains non-finite samples");
  }
  return result;
}

void ReferencePlayer::load(const std::filesystem::path& path) {
  auto audio = std::make_unique<ReferenceAudio>(read_reference_wav(path));
  const auto acknowledged = acknowledged_.load(std::memory_order_acquire);
  std::erase_if(owned_,
                [acknowledged](const auto& item) { return item.generation < acknowledged; });
  owned_.push_back({next_generation_, std::move(audio)});
  if (!controls_.try_push({owned_.back().audio.get(), next_generation_})) {
    owned_.pop_back();
    throw std::runtime_error("Reference command queue is full");
  }
  ++next_generation_;
}
void ReferencePlayer::reset() {
  Seek discarded;
  while (seeks_.try_pop(discarded)) {
  }
  has_pending_ = playing_ = false;
  last_scheduled_ = position_ = 0;
}
bool ReferencePlayer::schedule(std::uint64_t at_sample, std::uint64_t from_sample) {
  if (owned_.empty()) throw std::runtime_error("Load reference audio before ref_play");
  if (at_sample < last_scheduled_)
    throw std::invalid_argument("Reference seeks must be scheduled in sample order");
  if (!seeks_.try_push({at_sample, from_sample})) return false;
  last_scheduled_ = at_sample;
  return true;
}
bool ReferencePlayer::mix(float band_gain, float ref_gain) {
  return controls_.try_push({nullptr, 0, band_gain, ref_gain});
}
void ReferencePlayer::Gain::set(float value) noexcept {
  target = value;
  remaining = kGainRampSamples;
  step = (target - current) / static_cast<float>(remaining);
}
float ReferencePlayer::Gain::next() noexcept {
  if (remaining) {
    current += step;
    if (--remaining == 0) current = target;
  }
  return current;
}
void ReferencePlayer::render(std::span<float> left, std::span<float> right,
                             std::uint64_t sample) noexcept {
  Control control;
  // Bound callback work even if the controller continually refills the queue.
  for (std::size_t i = 0; i < 1024 && controls_.try_pop(control); ++i) {
    if (control.audio) {
      audio_ = control.audio;
      acknowledged_.store(control.generation, std::memory_order_release);
    } else {
      band_.set(control.band_gain);
      reference_.set(control.ref_gain);
    }
  }
  std::size_t budget = 1024;
  for (std::size_t i = 0; i < left.size(); ++i) {
    if (!has_pending_) has_pending_ = seeks_.try_pop(pending_);
    while (budget && has_pending_ && pending_.at_sample <= sample + i) {
      --budget;
      const auto late = sample + i - pending_.at_sample;
      position_ = pending_.from_sample;
      playing_ =
          audio_ && position_ < audio_->left.size() && late < audio_->left.size() - position_;
      if (playing_) position_ += late;
      has_pending_ = seeks_.try_pop(pending_);
    }
    const auto band = band_.next(), reference = reference_.next();
    left[i] *= band;
    right[i] *= band;
    if (playing_ && audio_ && position_ < audio_->left.size()) {
      left[i] += audio_->left[position_] * reference;
      right[i] += audio_->right[position_] * reference;
      ++position_;
    }
  }
}
}  // namespace ghostband
