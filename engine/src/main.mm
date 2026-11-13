#include "audio_output.h"
#include "bench.h"
#include "protocol.h"
#include "session.h"
#include "wav_writer.h"

#include <magentart/mlx_engine.h>
#include <magentart/realtime_runner.h>

#include <array>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

namespace {
namespace core = magentart::core;
namespace fs = std::filesystem;

constexpr std::uint32_t kSampleRateHz = 48000;
constexpr std::uint16_t kChannels = core::kNumChannels;
constexpr std::uint16_t kBlockBytes = kChannels * sizeof(float);
constexpr std::uint32_t kRiffHeaderBytes = 36;
constexpr auto kPromptPollInterval = std::chrono::milliseconds(10);
constexpr std::string_view kUsage =
    "Usage: ghostband-engine --offline [--frames N] [--prompt TEXT] "
    "[--out PATH] [--commands PATH] [--model NAME] [--model-dir PATH]\n"
    "       ghostband-engine --play [--seconds N] [--prompt TEXT] "
    "[--model NAME] [--model-dir PATH]\n"
    "       ghostband-engine --bench N [--latency-probes N] "
    "[--prompt TEXT] [--model NAME] [--model-dir PATH]\n"
    "       ghostband-engine [--socket PATH]\n"
    "Model directory: --model-dir, then GHOSTBAND_MODEL_DIR, then "
    "~/Documents/Magenta/magenta-rt-v2.\n";

struct Options {
  enum class Mode { Unset, Offline, Play, Bench, Server } mode = Mode::Unset;
  std::uint32_t frames = 100;
  std::uint32_t seconds = 30;
  int latency_probes = 0;
  std::string prompt = "a jazz piano trio";
  std::string model = "mrt2_small";
  fs::path out = "out.wav";
  fs::path commands;
  fs::path model_dir;
  fs::path socket_path;
};

Options parse_options(int argc, char **argv) {
  Options options;
  bool has_offline_options = false;
  bool has_play_options = false;
  bool has_bench_options = false;
  for (int i = 1; i < argc; ++i) {
    const std::string_view arg = argv[i];
    if (arg == "--socket") {
      if (options.mode != Options::Mode::Unset && options.mode != Options::Mode::Server)
        throw std::runtime_error("--socket requires server mode");
      options.mode = Options::Mode::Server;
      if (++i == argc || std::string_view(argv[i]).empty())
        throw std::runtime_error("Missing value for --socket");
      options.socket_path = argv[i];
      continue;
    }
    if (arg == "--bench") {
      if (options.mode != Options::Mode::Unset && options.mode != Options::Mode::Bench)
        throw std::runtime_error("Select only one engine mode");
      options.mode = Options::Mode::Bench;
      if (++i == argc || std::string_view(argv[i]).empty())
        throw std::runtime_error("Missing value for --bench");
      const std::string_view value = argv[i];
      const auto [end, error] =
          std::from_chars(value.data(), value.data() + value.size(), options.seconds);
      if (error != std::errc{} || end != value.data() + value.size() || options.seconds == 0)
        throw std::runtime_error("--bench must be a positive integer");
      continue;
    }
    if (arg == "--offline" || arg == "--play") {
      const auto mode = arg == "--offline" ? Options::Mode::Offline : Options::Mode::Play;
      if (options.mode != Options::Mode::Unset && options.mode != mode) {
        throw std::runtime_error("Select only one of --offline and --play");
      }
      options.mode = mode;
      continue;
    }
    if (arg != "--frames" && arg != "--prompt" && arg != "--out" && arg != "--commands" &&
        arg != "--model" && arg != "--model-dir" && arg != "--seconds" &&
        arg != "--latency-probes") {
      throw std::runtime_error("Unknown option: " + std::string(arg));
    }
    if (++i == argc || std::string_view(argv[i]).empty()) {
      throw std::runtime_error("Missing value for " + std::string(arg));
    }
    const std::string_view value = argv[i];
    if (arg == "--frames") {
      has_offline_options = true;
      const auto [end, error] =
          std::from_chars(value.data(), value.data() + value.size(), options.frames);
      constexpr auto kMaxFrames = (std::numeric_limits<std::uint32_t>::max() - kRiffHeaderBytes) /
                                  (core::kFrameSamples * kBlockBytes);
      if (error != std::errc{} || end != value.data() + value.size() || options.frames == 0 ||
          options.frames > kMaxFrames) {
        throw std::runtime_error("--frames must be a positive integer fitting a RIFF WAV");
      }
    } else if (arg == "--seconds") {
      has_play_options = true;
      const auto [end, error] =
          std::from_chars(value.data(), value.data() + value.size(), options.seconds);
      if (error != std::errc{} || end != value.data() + value.size() || options.seconds == 0) {
        throw std::runtime_error("--seconds must be a positive integer");
      }
    } else if (arg == "--latency-probes") {
      has_bench_options = true;
      const auto [end, error] =
          std::from_chars(value.data(), value.data() + value.size(), options.latency_probes);
      if (error != std::errc{} || end != value.data() + value.size() || options.latency_probes < 0)
        throw std::runtime_error("--latency-probes must be a nonnegative integer");
    } else if (arg == "--prompt") {
      options.prompt = value;
    } else if (arg == "--out") {
      has_offline_options = true;
      options.out = value;
    } else if (arg == "--commands") {
      has_offline_options = true;
      options.commands = value;
    } else if (arg == "--model") {
      options.model = value;
    } else {
      options.model_dir = value;
    }
  }
  if (options.model != "mrt2_small" && options.model != "mrt2_base") {
    throw std::runtime_error("--model must be mrt2_small or mrt2_base");
  }
  if (options.mode == Options::Mode::Unset) {
    if (argc == 1)
      options.mode = Options::Mode::Server;
    else
      throw std::runtime_error("Select --offline to render a WAV or --play for audio output");
  }
  if (options.mode == Options::Mode::Server) {
    if (argc != 1 && (argc != 3 || std::string_view(argv[1]) != "--socket"))
      throw std::runtime_error("Server mode accepts only --socket PATH");
    if (options.socket_path.empty()) {
      const char *temporary = std::getenv("TMPDIR");
      options.socket_path =
          fs::path(temporary && *temporary ? temporary : "/tmp") / "ghostband/engine.sock";
    }
    return options;
  }
  if (has_bench_options && options.mode != Options::Mode::Bench)
    throw std::runtime_error("--latency-probes requires --bench");
  if (options.mode == Options::Mode::Bench && (has_offline_options || has_play_options))
    throw std::runtime_error("--frames and --out require --offline; --seconds requires --play");
  if ((options.mode == Options::Mode::Play && has_offline_options) ||
      (options.mode == Options::Mode::Offline && has_play_options)) {
    throw std::runtime_error("--frames and --out require --offline; --seconds requires --play");
  }
  if (options.model_dir.empty()) {
    if (const char *model_dir = std::getenv("GHOSTBAND_MODEL_DIR"); model_dir && *model_dir) {
      options.model_dir = model_dir;
    } else if (const char *home = std::getenv("HOME"); home && *home) {
      options.model_dir = fs::path(home) / "Documents/Magenta/magenta-rt-v2";
    } else {
      throw std::runtime_error("Set GHOSTBAND_MODEL_DIR or pass --model-dir");
    }
  }
  return options;
}

template <typename Engine>
void prepare_engine(Engine &engine, const Options &options) {
  const auto resources = options.model_dir / "resources";
  const auto model = options.model_dir / "models" / options.model / (options.model + ".mlxfn");
  if (!fs::is_directory(resources) || !fs::is_regular_file(model)) {
    throw std::runtime_error("Model assets missing under " + options.model_dir.string() +
                             "; pass --model-dir with full resources and models/" + options.model);
  }
  if (!engine.init_assets(resources.c_str())) {
    throw std::runtime_error("Cannot initialize assets from " + resources.string());
  }
  if (!engine.load_model(model.c_str())) {
    throw std::runtime_error("Cannot load model " + model.string());
  }
  engine.set_temperature(1.0f);
  engine.set_top_k(100);
  engine.set_cfg_musiccoca(3.0f);
  engine.set_cfg_notes(5.0f);
  engine.set_cfg_drums(1.0f);
  engine.set_text_prompt(options.prompt);
  while (engine.get_text_encoder_status() == 1 || engine.get_quantizer_status() == 1) {
    std::this_thread::sleep_for(kPromptPollInterval);
  }
  if (engine.get_text_encoder_status() != 2 || engine.get_quantizer_status() != 2 ||
      engine.get_prompt_status(0) != 2) {
    throw std::runtime_error("Text prompt encoding failed; check the MusicCoCa assets");
  }
}

struct TimedCommand {
  std::uint32_t frame;
  ghostband::Command command;
};

std::vector<TimedCommand> read_timeline(const fs::path &path, int &seed_rotation) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("Cannot read command timeline: " + path.string());
  nlohmann::json document;
  try {
    input >> document;
  } catch (const std::exception &error) {
    throw std::runtime_error("Invalid command timeline: " + std::string(error.what()));
  }
  if (!document.is_object() || !document.contains("commands") ||
      !document.at("commands").is_array()) {
    throw std::runtime_error("Command timeline needs a commands array");
  }
  std::vector<TimedCommand> timeline;
  seed_rotation = document.value("seed_rotation", 0);
  if (seed_rotation < 0) throw std::runtime_error("seed_rotation must be nonnegative");
  for (const auto &item : document.at("commands")) {
    if (!item.is_object() || !item.contains("frame") || !item.contains("command")) {
      throw std::runtime_error("Command timeline entry needs frame and command");
    }
    const auto frame = item.at("frame").get<std::uint32_t>();
    auto command = ghostband::parse_command(item.at("command").dump());
    const bool setup = std::holds_alternative<ghostband::OnsetMode>(command) ||
                       std::holds_alternative<ghostband::Params>(command) ||
                       std::holds_alternative<ghostband::Prompt>(command);
    if (setup && frame != 0)
      throw std::runtime_error("Offline onset_mode, params and prompt require frame 0");
    if (!setup && !std::holds_alternative<ghostband::NoteOn>(command) &&
        !std::holds_alternative<ghostband::NoteOff>(command) &&
        !std::holds_alternative<ghostband::Blend>(command))
      throw std::runtime_error("Unsupported offline command");
    if (const auto *params = std::get_if<ghostband::Params>(&command);
        params && params->buffer_frames)
      throw std::runtime_error("Offline params cannot set buffer_frames");
    if (!timeline.empty() && frame < timeline.back().frame)
      throw std::runtime_error("Command timeline is not sorted by frame");
    timeline.push_back({frame, std::move(command)});
  }
  return timeline;
}

void render_offline(const Options &options) {
  if (options.out.has_parent_path() && !fs::is_directory(options.out.parent_path())) {
    throw std::runtime_error("Output directory does not exist: " +
                             options.out.parent_path().string());
  }
  int seed_rotation = 0;
  const auto timeline = options.commands.empty() ? std::vector<TimedCommand>{}
                                                 : read_timeline(options.commands, seed_rotation);
  if (!timeline.empty() && timeline.back().frame >= options.frames)
    throw std::runtime_error("Command timeline continues past the last frame");
  core::MLXEngine engine;
  prepare_engine(engine, options);
  std::vector<std::string> prompts(ghostband::kPromptSlots);
  std::vector<float> weights(ghostband::kPromptSlots);
  prompts[0] = options.prompt;
  weights[0] = 1.0f;
  bool has_prompts = false;
  for (const auto &item : timeline) {
    if (const auto *prompt = std::get_if<ghostband::Prompt>(&item.command)) {
      prompts[prompt->slot] = prompt->text;
      weights[prompt->slot] = prompt->weight;
      has_prompts = true;
    }
  }
  if (has_prompts) {
    engine.set_text_prompts(prompts, weights);
    while (engine.get_text_encoder_status() == 1 || engine.get_quantizer_status() == 1)
      std::this_thread::sleep_for(kPromptPollInterval);
    for (std::size_t slot = 0; slot < prompts.size(); ++slot)
      if (!prompts[slot].empty() && engine.get_prompt_status(static_cast<int>(slot)) != 2)
        throw std::runtime_error("Offline text prompt encoding failed");
  }
  engine.set_seed_rotation(seed_rotation);
  engine.reset_state();

  ghostband::WavWriter out(options.out);
  std::array<float, core::kFrameSamples> left, right;
  std::size_t cursor = 0;
  for (std::uint32_t frame = 0; frame < options.frames; ++frame) {
    while (cursor < timeline.size() && timeline[cursor].frame == frame) {
      if (const auto *note = std::get_if<ghostband::NoteOn>(&timeline[cursor].command)) {
        engine.set_note_on(note->pitch);
      } else if (const auto *note = std::get_if<ghostband::NoteOff>(&timeline[cursor].command)) {
        engine.set_note_off(note->pitch);
      } else if (const auto *onset = std::get_if<ghostband::OnsetMode>(&timeline[cursor].command)) {
        engine.set_onset_mode(onset->mode);
      } else if (const auto *params = std::get_if<ghostband::Params>(&timeline[cursor].command)) {
        if (params->temperature) engine.set_temperature(*params->temperature);
        if (params->topk) engine.set_top_k(*params->topk);
        if (params->cfg_musiccoca) engine.set_cfg_musiccoca(*params->cfg_musiccoca);
        if (params->cfg_notes) engine.set_cfg_notes(*params->cfg_notes);
        if (params->cfg_drums) engine.set_cfg_drums(*params->cfg_drums);
      } else if (const auto *blend = std::get_if<ghostband::Blend>(&timeline[cursor].command)) {
        if (!engine.reblend_musiccoca_tokens(blend->weights.data(), blend->weights.size()))
          throw std::runtime_error("Offline prompt blend failed");
      }
      ++cursor;
    }
    if (cursor < timeline.size() && timeline[cursor].frame < frame) {
      throw std::runtime_error("Command timeline is not sorted by frame");
    }
    if (!engine.generate_frame(left.data(), right.data())) {
      throw std::runtime_error("Audio generation failed at frame " + std::to_string(frame));
    }
    out.append(left, right);
  }
  if (cursor != timeline.size()) {
    throw std::runtime_error("Command timeline continues past the last frame");
  }
  out.finish();
  const double seconds = static_cast<double>(options.frames) * core::kFrameSamples / kSampleRateHz;
  std::cout << "Wrote " << options.out.string() << " (" << seconds << " s, " << kSampleRateHz
            << " Hz, " << kChannels << " channels)\n";
}
}  // namespace

int main(int argc, char **argv) {
  if (argc == 2 && std::string_view(argv[1]) == "--version") {
    std::cout << "ghostband-engine " << GHOSTBAND_VERSION << '\n';
    return 0;
  }
  if (argc == 2 && std::string_view(argv[1]) == "--help") {
    std::cout << kUsage;
    return 0;
  }
  try {
    const auto options = parse_options(argc, argv);
    @autoreleasepool {
      if (options.mode == Options::Mode::Server) {
        ghostband::EngineSession session;
        ghostband::serve_session(
            options.socket_path,
            {.apply = [&](const ghostband::Command &command) { return session.apply(command); },
             .status = [&] { return session.status(); },
             .disconnect = [&] { session.stop(); },
             .event = [&] { return session.event(); }});
      } else if (options.mode == Options::Mode::Offline) {
        render_offline(options);
      } else {
        core::RealtimeRunner runner;
        prepare_engine(runner, options);
        const float weight = 1.0f;
        runner.set_blend_weights(&weight, 1);
        if (options.mode == Options::Mode::Bench) {
          runner.stop();
          std::cout << ghostband::run_bench(runner, options.seconds, options.latency_probes,
                                            options.model_dir / "resources")
                           .dump()
                    << '\n';
        } else {
          const auto underruns = ghostband::play_audio(runner, options.seconds);
          std::cout << "underruns: " << underruns << '\n';
        }
      }
    }
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "ghostband-engine: " << error.what() << '\n' << kUsage;
    return 1;
  }
}
