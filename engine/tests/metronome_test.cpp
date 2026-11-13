#include "../src/metronome.h"

#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
std::vector<float> render(ghostband::Metronome& click, std::size_t block) {
  std::vector<float> left(2400), right(2400);
  for (std::size_t start = 0; start < left.size(); start += block) {
    const auto count = std::min(block, left.size() - start);
    click.render(std::span(left).subspan(start, count), std::span(right).subspan(start, count),
                 start);
  }
  require(left == right, "Click channels differ");
  return left;
}
void positions() {
  ghostband::Metronome whole, split;
  for (auto* click : {&whole, &split}) {
    require(
        click->schedule(17, false) && click->schedule(512, true) && click->schedule(1600, false),
        "Click scheduling failed");
  }
  const auto expected = render(whole, 2400);
  require(expected == render(split, 127), "Click tails changed across callback boundaries");
  std::vector<std::size_t> starts;
  bool silent = true;
  for (std::size_t i = 0; i < expected.size(); ++i) {
    if (expected[i] != 0 && silent) starts.push_back(i);
    silent = expected[i] == 0;
  }
  require(starts == std::vector<std::size_t>{17, 512, 1600}, "Clicks are not sample-exact");
  require(expected[17] == 0.25f && expected[512] == 0.25f && expected[1600] == 0.25f,
          "Click onset amplitude changed");
  require(expected[18] != expected[513], "Accent has the same waveform as a normal click");
  split.reset();
  require(split.schedule(0, false), "Reset kept the previous scheduling clock");
  require(render(split, 128)[0] == 0.25f, "Reset did not clear pending work");
}
void overflow_and_late() {
  ghostband::Metronome click;
  for (std::uint64_t i = 0; i < 1024; ++i) require(click.schedule(i, false), "Queue filled early");
  require(!click.schedule(1024, false), "Queue overflow was accepted");
  click.reset();
  require(click.schedule(0, false), "Late click not queued");
  std::array<float, 16> left{}, right{};
  click.render(left, right, 1000);
  require(std::all_of(left.begin(), left.end(), [](float value) { return value == 0; }),
          "Expired click moved to the current callback");
}
}  // namespace
int main() {
  try {
    positions();
    overflow_and_late();
    std::cout << "PASS: exact click positions, accents, split buffers, overflow and reset\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
