#pragma once

#include <AudioToolbox/AudioToolbox.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <span>

#include "audio_tap.h"
#include "metronome.h"
#include "reference_player.h"

namespace magentart::core {
class RealtimeRunner;
}

namespace ghostband {

struct RenderExtensions {
  Metronome metronome;
  ReferencePlayer reference;
  AudioTap tap;
  void render(std::span<float> left, std::span<float> right, std::uint64_t sample) noexcept;
  /// Clear scheduled work only after callbacks have stopped.
  void reset();
};

/// Fill noninterleaved stereo float buffers without waiting for inference.
OSStatus render_audio(magentart::core::RealtimeRunner& runner, AudioBufferList& output,
                      std::uint32_t frames, std::atomic<std::uint64_t>* played_samples = nullptr,
                      RenderExtensions* extensions = nullptr) noexcept [[clang::nonblocking]];

/// Keep audio callbacks alive while the controller services socket commands.
class AudioOutput {
 public:
  explicit AudioOutput(magentart::core::RealtimeRunner& runner,
                       RenderExtensions* extensions = nullptr);
  ~AudioOutput();
  AudioOutput(const AudioOutput&) = delete;
  AudioOutput& operator=(const AudioOutput&) = delete;
  void start();
  void stop();
  /// Quiesce callbacks without restarting or resetting inference.
  void pause();
  void resume();
  std::uint64_t played_samples() const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

/// Play through the default output device and return the underrun count.
std::uint64_t play_audio(magentart::core::RealtimeRunner& runner, std::uint32_t seconds);

}  // namespace ghostband
