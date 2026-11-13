#include "wav_writer.h"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace ghostband {
namespace {
constexpr std::uint16_t kChannels = 2;
constexpr std::uint16_t kBlockBytes = kChannels * sizeof(float);
constexpr std::uint32_t kRiffHeaderBytes = 36;
constexpr std::uint32_t kMaxSamples =
    (std::numeric_limits<std::uint32_t>::max() - kRiffHeaderBytes) / kBlockBytes;

template <typename T>
void write_value(std::ostream& out, T value) {
  static_assert(std::endian::native == std::endian::little);
  out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}
}  // namespace

WavWriter::WavWriter(const std::filesystem::path& path) {
  static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
  out_.open(path, std::ios::binary | std::ios::trunc);
  if (!out_) throw std::runtime_error("Cannot create WAV output: " + path.string());
  out_.exceptions(std::ios::failbit | std::ios::badbit);
  header();
}

void WavWriter::header() {
  out_.seekp(0);
  out_.write("RIFF", 4);
  write_value(out_, kRiffHeaderBytes + samples_ * kBlockBytes);
  out_.write("WAVEfmt ", 8);
  write_value(out_, std::uint32_t{16});
  write_value(out_, std::uint16_t{3});  // WAV format 3 is IEEE float.
  write_value(out_, kChannels);
  write_value(out_, kRecordingSampleRateHz);
  write_value(out_, kRecordingSampleRateHz * kBlockBytes);
  write_value(out_, kBlockBytes);
  write_value(out_, std::uint16_t{32});
  out_.write("data", 4);
  write_value(out_, samples_ * kBlockBytes);
}

void WavWriter::append(std::span<const float> left, std::span<const float> right) {
  if (left.size() != right.size() || left.size() > kMaxSamples - samples_)
    throw std::invalid_argument("Stereo WAV channel sizes differ or exceed the RIFF limit");
  for (std::size_t i = 0; i < left.size(); ++i) {
    if (!std::isfinite(left[i]) || !std::isfinite(right[i]))
      throw std::invalid_argument("WAV samples must be finite");
  }
  constexpr std::size_t kChunkSamples = 1920;
  std::array<float, kChunkSamples * kChannels> interleaved;
  for (std::size_t start = 0; start < left.size(); start += kChunkSamples) {
    const auto count = std::min(kChunkSamples, left.size() - start);
    for (std::size_t i = 0; i < count; ++i) {
      interleaved[2 * i] = left[start + i];
      interleaved[2 * i + 1] = right[start + i];
    }
    out_.write(reinterpret_cast<const char*>(interleaved.data()), count * kBlockBytes);
  }
  samples_ += static_cast<std::uint32_t>(left.size());
}

void WavWriter::finish() {
  header();
  out_.close();
}
}  // namespace ghostband
