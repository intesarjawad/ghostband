#include "session.h"
#include "audio_output.h"
#include "bench.h"
#include "recording.h"

#import <AVFoundation/AVFoundation.h>

#include <magentart/realtime_runner.h>

#include <cmath>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace ghostband {
namespace {
namespace core = magentart::core;
constexpr std::size_t kPrimingFrames = 3;

void audio_prompt(core::RealtimeRunner& runner, const PromptAudio& command) {
  NSError* error = nil;
  NSString* path = [NSString stringWithUTF8String:command.path.c_str()];
  AVAudioFile* file = [[AVAudioFile alloc] initForReading:[NSURL fileURLWithPath:path]
                                                    error:&error];
  if (!file) throw std::runtime_error("Cannot read prompt WAV: " + command.path);
  const auto format = file.processingFormat;
  const auto source = file.fileFormat;
  if (source.sampleRate != 16000 || source.channelCount != 1 ||
      source.commonFormat != AVAudioPCMFormatFloat32 || file.length <= 0 ||
      file.length > std::numeric_limits<AVAudioFrameCount>::max())
    throw std::runtime_error("Audio prompt must be a nonempty 16 kHz mono float WAV");
  AVAudioPCMBuffer* buffer =
      [[AVAudioPCMBuffer alloc] initWithPCMFormat:format
                                    frameCapacity:static_cast<AVAudioFrameCount>(file.length)];
  if (!buffer || ![file readIntoBuffer:buffer error:&error] || buffer.frameLength != file.length)
    throw std::runtime_error("Cannot read complete audio prompt: " + command.path);
  for (AVAudioFrameCount i = 0; i < buffer.frameLength; ++i) {
    if (!std::isfinite(buffer.floatChannelData[0][i]))
      throw std::runtime_error("Audio prompt contains non-finite samples");
  }
  runner.set_audio_prompt_samples(command.slot, command.path, buffer.floatChannelData[0],
                                  buffer.frameLength);
}
}  // namespace

struct EngineSession::Impl {
  std::unique_ptr<core::RealtimeRunner> runner;
  std::unique_ptr<Recording> recording;
  std::unique_ptr<RenderExtensions> extensions;
  std::unique_ptr<AudioOutput> output;
  std::vector<std::string> prompts = std::vector<std::string>(kPromptSlots);
  std::vector<float> weights = std::vector<float>(kPromptSlots);
  bool playing = false;
  std::filesystem::path resources;

  void load(const Load& command) {
    if (playing || (recording && recording->active()))
      throw std::runtime_error("Stop playback and recording before loading a model");
    const std::filesystem::path resources(command.resources);
    const auto model =
        resources.parent_path() / "models" / command.model / (command.model + ".mlxfn");
    if (!std::filesystem::is_directory(resources) || !std::filesystem::is_regular_file(model))
      throw std::runtime_error("Model assets missing; resources must sit beside models/" +
                               command.model);
    auto candidate = std::make_unique<core::RealtimeRunner>();
    candidate->set_buffer_size(kPrimingFrames * core::kFrameSamples);
    if (!candidate->init_assets(resources.c_str()) ||
        !candidate->load_musiccoca_model(resources.c_str(), "musiccoca") ||
        !candidate->load_model(model.c_str()))
      throw std::runtime_error("Cannot load model or MusicCoCa assets from " + command.resources);
    candidate->stop();
    candidate->reset_for_playback();
    output.reset();
    recording.reset();
    runner = std::move(candidate);
    recording = std::make_unique<Recording>(*runner);
    extensions = std::make_unique<RenderExtensions>();
    this->resources = resources;
    prompts.assign(kPromptSlots, "");
    weights.assign(kPromptSlots, 0);
  }

  template <typename T>
  void control(const T& value) {
    if constexpr (std::is_same_v<T, Play>) {
      if (!playing) {
        if (!output) output = std::make_unique<AudioOutput>(*runner, extensions.get());
        output->start();
        playing = true;
      }
    } else if constexpr (std::is_same_v<T, Prompt>) {
      prompts[value.slot] = value.text;
      weights[value.slot] = value.weight;
      runner->set_text_prompts(prompts, weights);
      runner->set_blend_weights(weights.data(), weights.size());
    } else if constexpr (std::is_same_v<T, PromptAudio>)
      audio_prompt(*runner, value);
    else if constexpr (std::is_same_v<T, StyleEmbedding>)
      runner->set_audio_embedding(value.slot, value.values.data());
    else if constexpr (std::is_same_v<T, Blend>) {
      weights.assign(value.weights.begin(), value.weights.end());
      runner->set_blend_weights(value.weights.data(), value.weights.size());
      runner->reblend_musiccoca_tokens(value.weights.data(), value.weights.size());
    } else if constexpr (std::is_same_v<T, NoteOn>)
      runner->set_note_on(value.pitch);
    else if constexpr (std::is_same_v<T, NoteOff>)
      runner->set_note_off(value.pitch);
    else if constexpr (std::is_same_v<T, Drumless>)
      runner->set_drumless(value.on);
    else if constexpr (std::is_same_v<T, OnsetMode>) {
      if (playing) throw std::runtime_error("Stop playback before changing onset_mode");
      runner->set_onset_mode(value.mode);
    } else if constexpr (std::is_same_v<T, Params>) {
      if (value.temperature) runner->set_temperature(*value.temperature);
      if (value.topk) runner->set_top_k(*value.topk);
      if (value.cfg_musiccoca) runner->set_cfg_musiccoca(*value.cfg_musiccoca);
      if (value.cfg_notes) runner->set_cfg_notes(*value.cfg_notes);
      if (value.cfg_drums) runner->set_cfg_drums(*value.cfg_drums);
      if (value.buffer_frames) runner->set_buffer_size(*value.buffer_frames * core::kFrameSamples);
    } else if constexpr (std::is_same_v<T, RecordStart>) {
      recording->start(value.path);
    } else if constexpr (std::is_same_v<T, ClickAt>) {
      if (!extensions->metronome.schedule(value.sample, value.accent))
        throw std::runtime_error("Metronome queue is full");
    } else if constexpr (std::is_same_v<T, RefLoad>) {
      extensions->reference.load(value.path);
    } else if constexpr (std::is_same_v<T, RefPlay>) {
      if (!extensions->reference.schedule(value.at_sample, value.from_sample))
        throw std::runtime_error("Reference seek queue is full");
    } else if constexpr (std::is_same_v<T, RefMix>) {
      if (!extensions->reference.mix(value.band_gain, value.ref_gain))
        throw std::runtime_error("Reference gain queue is full");
    } else if constexpr (std::is_same_v<T, Tap>) {
      extensions->tap.enable(value.on);
    }
  }
};

EngineSession::EngineSession() : impl_(std::make_unique<Impl>()) {}
EngineSession::~EngineSession() = default;

std::optional<std::string> EngineSession::apply(const Command& command) {
  if (std::holds_alternative<Hello>(command)) return hello_reply(GHOSTBAND_VERSION);
  if (const auto* load = std::get_if<Load>(&command)) {
    impl_->load(*load);
    return nlohmann::json{{"ok", true}}.dump();
  }
  if (std::holds_alternative<Stop>(command) || std::holds_alternative<Quit>(command)) {
    stop();
    return std::nullopt;
  }
  if (!impl_->runner) throw std::runtime_error("Load a model before sending this command");
  if (const auto* bench = std::get_if<Bench>(&command)) {
    if (impl_->playing || impl_->recording->active())
      throw std::runtime_error("Stop playback and recording before benchmarking");
    return run_bench(*impl_->runner, bench->seconds, bench->latency_probes, impl_->resources)
        .dump();
  }
  if (std::holds_alternative<RecordStop>(command)) {
    if (!impl_->recording->active()) throw std::runtime_error("No recording is active");
    const bool was_playing = impl_->playing;
    if (was_playing) {
      impl_->output->pause();
      impl_->playing = false;
    }
    const auto resume = [&] {
      if (was_playing) {
        impl_->output->resume();
        impl_->playing = true;
      }
    };
    std::size_t count;
    try {
      count = impl_->recording->stop();
    } catch (...) {
      resume();
      throw;
    }
    resume();
    return nlohmann::json{{"ok", true}, {"recorded_samples", count}}.dump();
  }
  std::visit([&](const auto& value) { impl_->control(value); }, command);
  if (std::holds_alternative<RecordStart>(command) || std::holds_alternative<RefLoad>(command))
    return nlohmann::json{{"ok", true}}.dump();
  return std::nullopt;
}

std::optional<Status> EngineSession::status() const {
  if (!impl_->playing) return std::nullopt;
  const auto& runner = *impl_->runner;
  const auto metrics = runner.get_metrics();
  Status result{.played_samples = impl_->output->played_samples(),
                .ms_per_frame = metrics.total_ms,
                .underruns = metrics.dropped_frames,
                .latency_samples = runner.get_latency_samples()};
  for (std::size_t slot = 0; slot < kPromptSlots; ++slot)
    result.prompt_status[slot] = runner.get_prompt_status(slot);
  result.tap_dropped = impl_->extensions->tap.dropped();
  result.recording = impl_->recording->active();
  result.recorded_samples = runner.get_recorded_sample_count();
  return result;
}

std::optional<std::string> EngineSession::event() {
  if (!impl_->playing) return std::nullopt;
  TapBlock block;
  if (!impl_->extensions->tap.read(block)) return std::nullopt;
  return serialize_tap(block);
}

void EngineSession::stop() {
  if (!impl_->runner) return;
  for (int pitch = 0; pitch < 128; ++pitch) impl_->runner->set_note_off(pitch);
  if (impl_->output)
    impl_->output->stop();
  else {
    impl_->runner->stop();
    impl_->runner->reset_for_playback();
  }
  impl_->extensions->reset();
  impl_->playing = false;
}
}  // namespace ghostband
