#include "audio_output.h"

#import <AVFoundation/AVFoundation.h>

#include <magentart/realtime_runner.h>

#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>

namespace ghostband {

void RenderExtensions::render(std::span<float> left, std::span<float> right,
                              std::uint64_t sample) noexcept {
  tap.capture(left, right, sample);
  reference.render(left, right, sample);
  metronome.render(left, right, sample);
}
void RenderExtensions::reset() {
  metronome.reset();
  reference.reset();
  tap.enable(false);
}

OSStatus render_audio(magentart::core::RealtimeRunner &runner, AudioBufferList &output,
                      std::uint32_t frames, std::atomic<std::uint64_t> *played_samples,
                      RenderExtensions *extensions) noexcept [[clang::nonblocking]] {
  if (output.mNumberBuffers != 2) {
    return kAudio_ParamError;
  }
  for (const auto &buffer : {output.mBuffers[0], output.mBuffers[1]}) {
    if (buffer.mNumberChannels != 1 || buffer.mData == nullptr ||
        frames > buffer.mDataByteSize / sizeof(float)) {
      return kAudio_ParamError;
    }
  }
  auto *left = static_cast<float *>(output.mBuffers[0].mData);
  auto *right = static_cast<float *>(output.mBuffers[1].mData);
  runner.read_audio_stereo(left, right, frames, false);
  if (extensions) {
    const auto sample = played_samples ? played_samples->load(std::memory_order_relaxed) : 0;
    extensions->render({left, frames}, {right, frames}, sample);
  }
  static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
  if (played_samples) played_samples->fetch_add(frames, std::memory_order_relaxed);
  return noErr;
}

struct AudioOutput::Impl {
  AVAudioEngine *__strong engine = [[AVAudioEngine alloc] init];
  magentart::core::RealtimeRunner &runner;
  std::atomic<std::uint64_t> played_samples{0};
  explicit Impl(magentart::core::RealtimeRunner &audio_runner) : runner(audio_runner) {}
  ~Impl() {
    // Stop callbacks before stopping inference or releasing the captured runner.
    [engine stop];
    runner.stop();
  }
};

AudioOutput::AudioOutput(magentart::core::RealtimeRunner &runner, RenderExtensions *extensions)
    : impl_(std::make_unique<Impl>(runner)) {
  AVAudioFormat *device_format = [impl_->engine.outputNode outputFormatForBus:0];
  if (device_format.sampleRate <= 0 || device_format.channelCount == 0) {
    throw std::runtime_error("No audio output device available");
  }

  AVAudioFormat *format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:48000 channels:2];
  auto *audio_runner = &runner;
  auto *played_samples = &impl_->played_samples;
  AVAudioSourceNode *source = [[AVAudioSourceNode alloc]
      initWithFormat:format
         renderBlock:^OSStatus(BOOL *is_silence, const AudioTimeStamp *, AVAudioFrameCount frames,
                               AudioBufferList *output) {
           *is_silence = NO;
           return render_audio(*audio_runner, *output, frames, played_samples, extensions);
         }];
  [impl_->engine attachNode:source];
  [impl_->engine connect:source to:impl_->engine.mainMixerNode format:format];
}

AudioOutput::~AudioOutput() = default;

void AudioOutput::start() {
  impl_->played_samples.store(0, std::memory_order_relaxed);
  impl_->runner.start();
  resume();
}

void AudioOutput::pause() { [impl_->engine stop]; }

void AudioOutput::resume() {
  NSError *error = nil;
  if (![impl_->engine startAndReturnError:&error]) {
    impl_->runner.stop();
    throw std::runtime_error("Cannot start audio output: " +
                             std::string(error.localizedDescription.UTF8String));
  }
}

void AudioOutput::stop() {
  [impl_->engine stop];
  impl_->runner.stop();
  impl_->runner.reset_for_playback();
}

std::uint64_t AudioOutput::played_samples() const {
  return impl_->played_samples.load(std::memory_order_relaxed);
}

std::uint64_t play_audio(magentart::core::RealtimeRunner &runner, std::uint32_t seconds) {
  AudioOutput output(runner);

  // The runner primes three model frames; its default capacity holds only one.
  constexpr std::size_t kPrimingFrames = 3;
  runner.set_buffer_size(kPrimingFrames * magentart::core::kFrameSamples);
  output.start();
  std::this_thread::sleep_for(std::chrono::seconds(seconds));
  output.stop();
  return runner.get_metrics().dropped_frames;
}

}  // namespace ghostband
