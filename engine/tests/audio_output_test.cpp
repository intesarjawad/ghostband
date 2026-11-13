#include "../src/audio_output.h"

#include <magentart/realtime_runner.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>

namespace {
constexpr std::size_t kMaxFrames = 4096;
constexpr float kSentinel = 0.75f;

void require(bool condition, const char* message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void test_render() {
  magentart::core::RealtimeRunner runner;
  std::array<float, kMaxFrames + 2> left, right;
  const std::unique_ptr<AudioBufferList, decltype(&std::free)> output{
      static_cast<AudioBufferList*>(
          std::malloc(offsetof(AudioBufferList, mBuffers) + 2 * sizeof(AudioBuffer))),
      &std::free};
  require(output != nullptr, "Cannot allocate test buffer list");
  output->mNumberBuffers = 2;
  output->mBuffers[0] = {1, kMaxFrames * sizeof(float), left.data() + 1};
  output->mBuffers[1] = {1, kMaxFrames * sizeof(float), right.data() + 1};

  std::uint64_t expected_underruns = 0;
  std::atomic<std::uint64_t> played_samples{0};
  std::uint64_t expected_samples = 0;
  for (const std::uint32_t frames : {0, 1, 16, 512, 1920, 4096}) {
    left.fill(kSentinel);
    right.fill(kSentinel);
    require(ghostband::render_audio(runner, *output, frames, &played_samples) == noErr,
            "Valid stereo buffers rejected");
    require(std::all_of(left.begin() + 1, left.begin() + 1 + frames,
                        [](float sample) { return sample == 0; }) &&
                std::all_of(right.begin() + 1, right.begin() + 1 + frames,
                            [](float sample) { return sample == 0; }),
            "Underrun did not zero both output channels");
    require(left.front() == kSentinel && right.front() == kSentinel &&
                left[frames + 1] == kSentinel && right[frames + 1] == kSentinel,
            "Render wrote outside the requested frames");
    expected_underruns += frames > 0;
    expected_samples += frames;
    require(played_samples.load() == expected_samples,
            "Played-sample clock omitted rendered silence or underruns");
    require(runner.get_metrics().dropped_frames == expected_underruns,
            "Underrun count does not match nonblocking reads");
  }

  runner.set_bypass(true);
  require(ghostband::render_audio(runner, *output, 16, &played_samples) == noErr,
          "Bypassed render failed");
  require(runner.get_metrics().dropped_frames == expected_underruns,
          "Bypassed render counted an underrun");
  expected_samples += 16;
  require(played_samples.load() == expected_samples, "Bypass did not advance the clock");

  left.fill(kSentinel);
  right.fill(kSentinel);
  output->mNumberBuffers = 1;
  require(ghostband::render_audio(runner, *output, 16, &played_samples) == kAudio_ParamError,
          "Mono buffer accepted as stereo");
  output->mNumberBuffers = 2;
  output->mBuffers[1].mNumberChannels = 2;
  require(ghostband::render_audio(runner, *output, 16) == kAudio_ParamError,
          "Interleaved buffer accepted");
  output->mBuffers[1].mNumberChannels = 1;
  output->mBuffers[1].mDataByteSize = 15 * sizeof(float);
  require(ghostband::render_audio(runner, *output, 16) == kAudio_ParamError,
          "Short buffer accepted");
  output->mBuffers[1].mDataByteSize = kMaxFrames * sizeof(float);
  output->mBuffers[1].mData = nullptr;
  require(ghostband::render_audio(runner, *output, 16) == kAudio_ParamError,
          "Null buffer accepted");
  require(
      std::all_of(left.begin(), left.end(), [](float sample) { return sample == kSentinel; }) &&
          std::all_of(right.begin(), right.end(), [](float sample) { return sample == kSentinel; }),
      "Invalid buffers changed the output");
  require(runner.get_metrics().dropped_frames == expected_underruns,
          "Invalid buffers reached the runner");
  require(played_samples.load() == expected_samples, "Invalid buffers advanced the clock");
}
}  // namespace

int main() {
  try {
    test_render();
    std::cout << "PASS: stereo rendering, buffer bounds and underrun accounting\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
