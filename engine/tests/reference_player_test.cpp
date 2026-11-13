#include "../src/reference_player.h"

#include <unistd.h>

#include <array>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>

#include "../src/wav_writer.h"

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
template <typename Function>
void rejects(Function function) {
  bool rejected = false;
  try {
    function();
  } catch (const std::exception&) {
    rejected = true;
  }
  require(rejected, "Invalid reference WAV was accepted");
}
struct Fixture {
  std::filesystem::path directory, path;
  std::array<float, 2000> left{}, right{};
  Fixture() {
    char name[] = "/tmp/bm-reference-XXXXXX";
    const auto* created = ::mkdtemp(name);
    if (!created) throw std::runtime_error("Cannot create reference fixture directory");
    directory = created;
    path = directory / "reference.wav";
    for (std::size_t i = 0; i < left.size(); ++i) {
      left[i] = static_cast<float>(i) / 2048;
      right[i] = -left[i];
    }
    ghostband::WavWriter writer(path);
    writer.append(left, right);
    writer.finish();
  }
  ~Fixture() { std::filesystem::remove_all(directory); }
};
void seek_and_gains(const Fixture& fixture) {
  auto player = std::make_unique<ghostband::ReferencePlayer>();
  player->load(fixture.path);
  require(player->mix(0, 1), "Cannot set reference gain");
  std::array<float, 480> warm_left{}, warm_right{};
  player->render(warm_left, warm_right, 0);
  require(player->schedule(493, 37) && player->schedule(530, 127), "Cannot schedule seeks");
  std::array<float, 101> left{}, right{};
  left.fill(1);
  right.fill(1);
  // Both seeks fall within callbacks rather than at a render boundary.
  player->render(std::span(left).first(29), std::span(right).first(29), 480);
  player->render(std::span(left).subspan(29), std::span(right).subspan(29), 509);
  for (std::size_t i = 0; i < left.size(); ++i) {
    const auto source = i < 50 ? 37 + i - 13 : 127 + i - 50;
    const auto expected = i < 13 ? 0 : fixture.left[source];
    require(left[i] == expected && right[i] == -expected, "Reference seek is not sample-exact");
  }
  require(player->mix(0, 0), "Cannot fade reference");
  std::array<float, 480> fade_left{}, fade_right{};
  player->render(fade_left, fade_right, 581);
  require(std::abs(fade_left[239] - fixture.left[417] * 0.5f) < 0.00001f,
          "Reference gain did not reach its midpoint in five milliseconds");
  require(fade_left.back() == 0 && fade_right.back() == 0,
          "Reference fade did not finish in ten milliseconds");
  player->reset();
  require(player->schedule(0, 0), "Reset kept the previous seek clock");
}
void boundaries_and_replacement(const Fixture& fixture) {
  auto player = std::make_unique<ghostband::ReferencePlayer>();
  rejects([&] { (void)player->schedule(0, 0); });
  player->load(fixture.path);
  require(player->mix(0, 1), "Cannot set reference gain");
  std::array<float, 480> warm_left{}, warm_right{};
  player->render(warm_left, warm_right, 0);
  require(player->schedule(480, 1998), "Cannot schedule EOF seek");
  std::array<float, 8> left{}, right{};
  player->render(left, right, 480);
  require(left[0] == fixture.left[1998] && left[1] == fixture.left[1999] && left[2] == 0,
          "Reference read beyond EOF or cut off its last sample");
  player->load(fixture.path);
  player->render(left, right, 488);
  player->load(fixture.path);
  player->render(left, right, 496);
  require(player->schedule(500, 10), "Cannot schedule late seek");
  player->render(left, right, 504);
  require(left[0] == fixture.left[14], "Late seek did not compensate its sample position");
  require(player->schedule(512, 9999999), "Cannot schedule out-of-file seek");
  player->render(left, right, 512);
  require(left[0] == 0, "Out-of-file seek read audio");
  for (std::uint64_t i = 0; i < 1024; ++i)
    require(player->schedule(1000 + i, 0), "Reference queue filled early");
  require(!player->schedule(2024, 0), "Reference seek queue overflow accepted");
}
void invalid_files(const Fixture& fixture) {
  const auto loaded = ghostband::read_reference_wav(fixture.path);
  require(loaded.left.size() == fixture.left.size() && loaded.left[137] == fixture.left[137] &&
              loaded.right[137] == fixture.right[137],
          "Reference WAV decode differs from source");
  const auto mutate = [&](const char* name, std::streamoff offset, const auto& value) {
    const auto path = fixture.directory / name;
    std::filesystem::copy_file(fixture.path, path);
    std::fstream file(path, std::ios::binary | std::ios::in | std::ios::out);
    file.seekp(offset);
    file.write(reinterpret_cast<const char*>(&value), sizeof(value));
    file.close();
    rejects([&] { (void)ghostband::read_reference_wav(path); });
  };
  mutate("wrong-rate.wav", 24, std::uint32_t{44100});
  mutate("integer.wav", 20, std::uint16_t{1});
  mutate("mono.wav", 22, std::uint16_t{1});
  mutate("bad-chunk.wav", 40, std::uint32_t{0xffffffff});
  mutate("nonfinite.wav", 44, std::uint32_t{0x7fc00000});
  const auto truncated = fixture.directory / "truncated.wav";
  std::filesystem::copy_file(fixture.path, truncated);
  std::filesystem::resize_file(truncated, 100);
  rejects([&] { (void)ghostband::read_reference_wav(truncated); });
}
}  // namespace
int main() {
  try {
    Fixture fixture;
    seek_and_gains(fixture);
    boundaries_and_replacement(fixture);
    invalid_files(fixture);
    std::cout
        << "PASS: sample-exact offline seeks, gain ramps, EOF, replacement and WAV validation\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
