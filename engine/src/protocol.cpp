#include "protocol.h"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <type_traits>

namespace ghostband {
namespace {
using nlohmann::json;

std::string text(const json& value) {
  if (!value.is_string()) throw std::invalid_argument("Expected a string");
  return value.get<std::string>();
}

std::string path(const json& value) {
  auto result = text(value);
  if (result.empty() || result.find('\0') != std::string::npos)
    throw std::invalid_argument("Path must be nonempty without NUL bytes");
  return result;
}

std::uint64_t integer(const json& value, std::uint64_t maximum) {
  if (!value.is_number_integer() ||
      (!value.is_number_unsigned() && value.get<std::int64_t>() < 0) ||
      value.get<std::uint64_t>() > maximum)
    throw std::invalid_argument("Integer outside the permitted range");
  return value.get<std::uint64_t>();
}

int bounded_int(const json& value, int minimum, int maximum) {
  const auto result = static_cast<int>(integer(value, maximum));
  if (result < minimum) throw std::invalid_argument("Integer below the permitted range");
  return result;
}

float number(const json& value, float minimum = -std::numeric_limits<float>::max(),
             float maximum = std::numeric_limits<float>::max()) {
  if (!value.is_number()) throw std::invalid_argument("Expected a number");
  const auto result = value.get<double>();
  if (!std::isfinite(result) || result < minimum || result > maximum)
    throw std::invalid_argument("Number outside the permitted range");
  return static_cast<float>(result);
}

bool boolean(const json& value) {
  if (!value.is_boolean()) throw std::invalid_argument("Expected a boolean");
  return value.get<bool>();
}

template <std::size_t N>
std::array<float, N> numbers(const json& value, float minimum, float maximum) {
  if (!value.is_array() || value.size() != N) throw std::invalid_argument("Incorrect array length");
  std::array<float, N> result;
  for (std::size_t i = 0; i < N; ++i) result[i] = number(value.at(i), minimum, maximum);
  return result;
}
}  // namespace

Command parse_command(std::string_view line) {
  if (line.size() > kMaxLineBytes) throw std::invalid_argument("Protocol line is too long");
  const auto object = json::parse(line);
  if (!object.is_object()) throw std::invalid_argument("Expected a JSON object");
  const auto cmd = text(object.at("cmd"));
  const auto slot = [&] { return bounded_int(object.at("slot"), 0, kPromptSlots - 1); };
  const auto sample = [&](const char* key) {
    return integer(object.at(key), std::numeric_limits<std::uint64_t>::max());
  };
  if (cmd == "hello") return Hello{};
  if (cmd == "load") {
    auto model = text(object.at("model"));
    if (model != "mrt2_small" && model != "mrt2_base")
      throw std::invalid_argument("Model must be mrt2_small or mrt2_base");
    return Load{model, path(object.at("resources"))};
  }
  if (cmd == "play") return Play{};
  if (cmd == "stop") return Stop{};
  if (cmd == "prompt")
    return Prompt{slot(), text(object.at("text")), number(object.at("weight"), 0, 1)};
  if (cmd == "prompt_audio") return PromptAudio{slot(), path(object.at("path"))};
  if (cmd == "style_embedding")
    return StyleEmbedding{
        slot(), numbers<kEmbeddingSize>(object.at("values"), -std::numeric_limits<float>::max(),
                                        std::numeric_limits<float>::max())};
  if (cmd == "blend") return Blend{numbers<kPromptSlots>(object.at("weights"), 0, 1)};
  if (cmd == "noteon") return NoteOn{bounded_int(object.at("pitch"), 0, 127)};
  if (cmd == "noteoff") return NoteOff{bounded_int(object.at("pitch"), 0, 127)};
  if (cmd == "drumless") return Drumless{boolean(object.at("on"))};
  if (cmd == "onset_mode") return OnsetMode{bounded_int(object.at("mode"), 0, 1)};
  if (cmd == "params") {
    Params params;
    if (object.contains("temperature")) params.temperature = number(object.at("temperature"), 0);
    if (object.contains("topk"))
      params.topk = bounded_int(object.at("topk"), 1, std::numeric_limits<int>::max());
    if (object.contains("cfg_musiccoca"))
      params.cfg_musiccoca = number(object.at("cfg_musiccoca"), 0);
    if (object.contains("cfg_notes")) params.cfg_notes = number(object.at("cfg_notes"), 0);
    if (object.contains("cfg_drums")) params.cfg_drums = number(object.at("cfg_drums"), 0);
    // Multiplication by a 1,920-sample model frame must fit the upstream setter.
    if (object.contains("buffer_frames"))
      params.buffer_frames =
          bounded_int(object.at("buffer_frames"), 1, std::numeric_limits<int>::max() / 1920);
    return params;
  }
  if (cmd == "click_at") return ClickAt{sample("sample"), boolean(object.at("accent"))};
  if (cmd == "ref_load") return RefLoad{path(object.at("path"))};
  if (cmd == "ref_play") return RefPlay{sample("at_sample"), sample("from_sample")};
  if (cmd == "ref_mix")
    return RefMix{number(object.at("band_gain"), 0, 1), number(object.at("ref_gain"), 0, 1)};
  if (cmd == "record") {
    const auto action = text(object.at("action"));
    if (action == "start") return RecordStart{path(object.at("path"))};
    if (action == "stop") return RecordStop{};
    throw std::invalid_argument("Record action must be start or stop");
  }
  if (cmd == "tap") return Tap{boolean(object.at("on"))};
  if (cmd == "bench") {
    const auto seconds = number(object.at("seconds"), 0);
    if (seconds <= 0) throw std::invalid_argument("Bench seconds must be positive");
    return Bench{seconds,
                 bounded_int(object.at("latency_probes"), 0, std::numeric_limits<int>::max())};
  }
  if (cmd == "quit") return Quit{};
  throw std::invalid_argument("Unknown command: " + cmd);
}

std::string serialize_command(const Command& command) {
  const auto result = std::visit(
      [](const auto& value) -> json {
        using T = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<T, Hello>)
          return {{"cmd", "hello"}};
        else if constexpr (std::is_same_v<T, Load>)
          return {{"cmd", "load"}, {"model", value.model}, {"resources", value.resources}};
        else if constexpr (std::is_same_v<T, Play>)
          return {{"cmd", "play"}};
        else if constexpr (std::is_same_v<T, Stop>)
          return {{"cmd", "stop"}};
        else if constexpr (std::is_same_v<T, Prompt>)
          return {{"cmd", "prompt"},
                  {"slot", value.slot},
                  {"text", value.text},
                  {"weight", value.weight}};
        else if constexpr (std::is_same_v<T, PromptAudio>)
          return {{"cmd", "prompt_audio"}, {"slot", value.slot}, {"path", value.path}};
        else if constexpr (std::is_same_v<T, StyleEmbedding>)
          return {{"cmd", "style_embedding"}, {"slot", value.slot}, {"values", value.values}};
        else if constexpr (std::is_same_v<T, Blend>)
          return {{"cmd", "blend"}, {"weights", value.weights}};
        else if constexpr (std::is_same_v<T, NoteOn>)
          return {{"cmd", "noteon"}, {"pitch", value.pitch}};
        else if constexpr (std::is_same_v<T, NoteOff>)
          return {{"cmd", "noteoff"}, {"pitch", value.pitch}};
        else if constexpr (std::is_same_v<T, Drumless>)
          return {{"cmd", "drumless"}, {"on", value.on}};
        else if constexpr (std::is_same_v<T, OnsetMode>)
          return {{"cmd", "onset_mode"}, {"mode", value.mode}};
        else if constexpr (std::is_same_v<T, Params>) {
          json object{{"cmd", "params"}};
          if (value.temperature) object["temperature"] = *value.temperature;
          if (value.topk) object["topk"] = *value.topk;
          if (value.cfg_musiccoca) object["cfg_musiccoca"] = *value.cfg_musiccoca;
          if (value.cfg_notes) object["cfg_notes"] = *value.cfg_notes;
          if (value.cfg_drums) object["cfg_drums"] = *value.cfg_drums;
          if (value.buffer_frames) object["buffer_frames"] = *value.buffer_frames;
          return object;
        } else if constexpr (std::is_same_v<T, ClickAt>)
          return {{"cmd", "click_at"}, {"sample", value.sample}, {"accent", value.accent}};
        else if constexpr (std::is_same_v<T, RefLoad>)
          return {{"cmd", "ref_load"}, {"path", value.path}};
        else if constexpr (std::is_same_v<T, RefPlay>)
          return {{"cmd", "ref_play"},
                  {"at_sample", value.at_sample},
                  {"from_sample", value.from_sample}};
        else if constexpr (std::is_same_v<T, RefMix>)
          return {{"cmd", "ref_mix"}, {"band_gain", value.band_gain}, {"ref_gain", value.ref_gain}};
        else if constexpr (std::is_same_v<T, RecordStart>)
          return {{"cmd", "record"}, {"action", "start"}, {"path", value.path}};
        else if constexpr (std::is_same_v<T, RecordStop>)
          return {{"cmd", "record"}, {"action", "stop"}};
        else if constexpr (std::is_same_v<T, Tap>)
          return {{"cmd", "tap"}, {"on", value.on}};
        else if constexpr (std::is_same_v<T, Bench>)
          return {{"cmd", "bench"},
                  {"seconds", value.seconds},
                  {"latency_probes", value.latency_probes}};
        else {
          static_assert(std::is_same_v<T, Quit>);
          return {{"cmd", "quit"}};
        }
      },
      command);
  return result.dump();
}

std::string serialize_status(const Status& value) {
  return json{{"event", "status"},
              {"played_samples", value.played_samples},
              {"ms_per_frame", value.ms_per_frame},
              {"underruns", value.underruns},
              {"latency_samples", value.latency_samples},
              {"prompt_status", value.prompt_status},
              {"recording", value.recording},
              {"recorded_samples", value.recorded_samples},
              {"tap_dropped", value.tap_dropped}}
      .dump();
}

std::string hello_reply(std::string_view engine_version) {
  return json{{"ok", true},
              {"protocol", kProtocolVersion},
              {"version", engine_version},
              {"upstream", "v2.0.3"}}
      .dump();
}

std::string error_reply(std::string_view message) {
  return json{{"ok", false}, {"error", message}}.dump();
}

std::string busy_reply(std::optional<std::int64_t> holder_pid, std::int64_t held_since) {
  return json{{"ok", false},
              {"error", "Engine is serving another client"},
              {"holder_pid", holder_pid ? json(*holder_pid) : json(nullptr)},
              {"held_since", held_since}}
      .dump();
}
}  // namespace ghostband
