#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <span>

namespace ghostband {
constexpr std::uint32_t kRecordingSampleRateHz = 48000;

/// Stream 48 kHz stereo IEEE-float WAV data on the controller thread.
class WavWriter {
 public:
  explicit WavWriter(const std::filesystem::path& path);
  void append(std::span<const float> left, std::span<const float> right);
  void finish();

 private:
  void header();
  std::ofstream out_;
  std::uint32_t samples_ = 0;
};
}  // namespace ghostband
