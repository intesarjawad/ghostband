#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <iostream>
#include <memory>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <vector>

#include "../src/audio_output.h"

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void capture() {
  auto tap = std::make_unique<ghostband::AudioTap>();
  std::array<float, 137> left{}, right{};
  left.fill(0.75f);
  right.fill(0.25f);
  ghostband::TapBlock block;
  tap->capture(left, right, 0);
  require(!tap->read(block), "Disabled tap emitted a block");
  tap->enable(true);
  std::uint64_t sample = 123;
  constexpr std::size_t kBlocks = 25;
  const auto end = sample + kBlocks * ghostband::kTapSamples;
  while (sample < end) {
    const auto count = std::min<std::size_t>(left.size(), end - sample);
    tap->capture(std::span(left).first(count), std::span(right).first(count), sample);
    sample += count;
  }
  require(tap->dropped() == 5, "Tap does not hold exactly two seconds or count dropped blocks");
  for (std::size_t i = 5; i < kBlocks; ++i) {
    require(tap->read(block), "Tap lost a retained block");
    require(block.sample == 123 + i * ghostband::kTapSamples, "Tap sample index is wrong");
    require(
        std::all_of(block.pcm.begin(), block.pcm.end(), [](float value) { return value == 0.5f; }),
        "Tap is not the mono channel mean");
  }
  require(!tap->read(block), "Tap retained too much audio");
  tap->capture(left, right, sample);
  tap->enable(false);
  tap->enable(true);
  std::array<float, ghostband::kTapSamples> full{};
  tap->capture(full, full, 900000);
  require(tap->read(block) && block.sample == 900000, "Toggle kept a partial or stale block");
}
void encoding() {
  ghostband::TapBlock block{.sample = 1920};
  block.pcm[0] = 1;
  block.pcm[1] = -0.5f;
  const auto event = nlohmann::json::parse(ghostband::serialize_tap(block));
  require(event.at("event") == "tap" && event.at("sample") == 1920, "Tap event fields changed");
  const auto encoded = event.at("pcm").get<std::string>();
  require(encoded.size() == 25600, "Tap base64 does not contain 4800 float32 samples");
  constexpr std::string_view kAlphabet =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::vector<unsigned char> bytes;
  for (std::size_t i = 0; i < encoded.size(); i += 4) {
    std::uint32_t value = 0;
    for (std::size_t j = 0; j < 4; ++j)
      value = (value << 6) | static_cast<std::uint32_t>(kAlphabet.find(encoded[i + j]));
    bytes.push_back(static_cast<unsigned char>(value >> 16));
    bytes.push_back(static_cast<unsigned char>(value >> 8));
    bytes.push_back(static_cast<unsigned char>(value));
  }
  const auto expected = std::as_bytes(std::span(block.pcm));
  require(bytes.size() == expected.size(), "Decoded tap length differs");
  for (std::size_t i = 0; i < bytes.size(); ++i)
    require(bytes[i] == std::to_integer<unsigned char>(expected[i]), "Tap PCM byte order is wrong");
}
void mix_order() {
  auto extensions = std::make_unique<ghostband::RenderExtensions>();
  extensions->tap.enable(true);
  require(extensions->reference.mix(0, 0), "Cannot mute band");
  require(extensions->metronome.schedule(1000, true), "Cannot schedule click");
  std::array<float, ghostband::kTapSamples> left{}, right{};
  left.fill(0.75f);
  right.fill(0.25f);
  extensions->render(left, right, 0);
  require(left[479] == 0 && right[479] == 0 && left[1000] == 0.25f && right[1000] == 0.25f,
          "Gains or clicks applied in the wrong order");
  ghostband::TapBlock block;
  require(extensions->tap.read(block), "Render extensions omitted tap capture");
  require(
      std::all_of(block.pcm.begin(), block.pcm.end(), [](float value) { return value == 0.5f; }),
      "Tap contains band gain or click audio");
  require(std::abs(left[239] - 0.375f) < 0.00001f && left[0] < 0.75f && left[0] > 0.74f,
          "Band gain does not ramp over ten milliseconds");
}
}  // namespace
int main() {
  try {
    capture();
    encoding();
    mix_order();
    std::cout
        << "PASS: tap sample indices, two-second drop-oldest ring, PCM encoding and mix order\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
