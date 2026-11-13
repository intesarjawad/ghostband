#pragma once

#include <array>
#include <cstdint>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
#include <string_view>
#include <variant>

namespace ghostband {

inline constexpr int kProtocolVersion = 1;
inline constexpr std::size_t kPromptSlots = 6;
inline constexpr std::size_t kEmbeddingSize = 768;
// Bounds per-client memory while allowing a complete 768-float embedding.
inline constexpr std::size_t kMaxLineBytes = 64 * 1024;

struct Hello {};
struct Load {
  std::string model, resources;
};
struct Play {};
struct Stop {};
struct Prompt {
  int slot;
  std::string text;
  float weight;
};
struct PromptAudio {
  int slot;
  std::string path;
};
struct StyleEmbedding {
  int slot;
  std::array<float, kEmbeddingSize> values;
};
struct Blend {
  std::array<float, kPromptSlots> weights;
};
struct NoteOn {
  int pitch;
};
struct NoteOff {
  int pitch;
};
struct Drumless {
  bool on;
};
struct OnsetMode {
  int mode;
};
struct Params {
  std::optional<float> temperature;
  std::optional<int> topk;
  std::optional<float> cfg_musiccoca, cfg_notes, cfg_drums;
  std::optional<int> buffer_frames;
};
struct ClickAt {
  std::uint64_t sample;
  bool accent;
};
struct RefLoad {
  std::string path;
};
struct RefPlay {
  std::uint64_t at_sample, from_sample;
};
struct RefMix {
  float band_gain, ref_gain;
};
struct RecordStart {
  std::string path;
};
struct RecordStop {};
struct Tap {
  bool on;
};
struct Bench {
  float seconds;
  int latency_probes;
};
struct Quit {};

/// Protocol v1 commands. Parsing validates fields before controller dispatch.
using Command = std::variant<Hello, Load, Play, Stop, Prompt, PromptAudio, StyleEmbedding, Blend,
                             NoteOn, NoteOff, Drumless, OnsetMode, Params, ClickAt, RefLoad,
                             RefPlay, RefMix, RecordStart, RecordStop, Tap, Bench, Quit>;

struct Status {
  std::uint64_t played_samples = 0;
  float ms_per_frame = 0;
  std::uint64_t underruns = 0, latency_samples = 0;
  std::array<int, kPromptSlots> prompt_status{};
  bool recording = false;
  std::uint64_t recorded_samples = 0, tap_dropped = 0;
};

/// Parse one JSON object without the newline; throw on invalid input.
Command parse_command(std::string_view line);
std::string serialize_command(const Command& command);
std::string serialize_status(const Status& status);
std::string hello_reply(std::string_view engine_version);
std::string error_reply(std::string_view message);
/// The one reply a client gets when it connects while another client holds the engine; the
/// engine then closes the connection. held_since is in Unix seconds, holder_pid null if unknown.
std::string busy_reply(std::optional<std::int64_t> holder_pid, std::int64_t held_since);

}  // namespace ghostband
