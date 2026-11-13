#include "../src/protocol.h"

#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <system_error>
#include <thread>
#include <vector>

#include "../src/audio_tap.h"
#include "../src/session.h"

namespace {
using namespace std::chrono_literals;
using nlohmann::json;

void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

void round_trips() {
  std::vector<json> commands = {
      {{"cmd", "hello"}},
      {{"cmd", "load"}, {"model", "mrt2_small"}, {"resources", "/tmp/resources"}},
      {{"cmd", "load"}, {"model", "mrt2_base"}, {"resources", "/tmp/resources"}},
      {{"cmd", "play"}},
      {{"cmd", "stop"}},
      {{"cmd", "prompt"}, {"slot", 5}, {"text", "piano\ntrio"}, {"weight", 0.5}},
      {{"cmd", "prompt_audio"}, {"slot", 0}, {"path", "/tmp/prompt.wav"}},
      {{"cmd", "style_embedding"}, {"slot", 2}, {"values", std::array<float, 768>{}}},
      {{"cmd", "blend"}, {"weights", {0.25, 0.25, 0.0, 0.0, 0.0, 0.5}}},
      {{"cmd", "noteon"}, {"pitch", 127}},
      {{"cmd", "noteoff"}, {"pitch", 0}},
      {{"cmd", "drumless"}, {"on", true}},
      {{"cmd", "onset_mode"}, {"mode", 1}},
      {{"cmd", "params"}},
      {{"cmd", "params"},
       {"temperature", 1.0},
       {"topk", 100},
       {"cfg_musiccoca", 3.0},
       {"cfg_notes", 5.0},
       {"cfg_drums", 1.0},
       {"buffer_frames", 3}},
      {{"cmd", "click_at"}, {"sample", 1234567890123ULL}, {"accent", false}},
      {{"cmd", "ref_load"}, {"path", "/tmp/ref.wav"}},
      {{"cmd", "ref_play"}, {"at_sample", 48000}, {"from_sample", 96000}},
      {{"cmd", "ref_mix"}, {"band_gain", 0.5}, {"ref_gain", 1.0}},
      {{"cmd", "record"}, {"action", "start"}, {"path", "/tmp/take.wav"}},
      {{"cmd", "record"}, {"action", "stop"}},
      {{"cmd", "tap"}, {"on", false}},
      {{"cmd", "bench"}, {"seconds", 60}, {"latency_probes", 50}},
      {{"cmd", "quit"}},
  };
  for (const auto& wire : commands) {
    const auto command = ghostband::parse_command(wire.dump());
    const auto encoded = ghostband::serialize_command(command);
    require(json::parse(encoded) == wire, "Command changed during round trip");
    require(ghostband::serialize_command(ghostband::parse_command(encoded)) == encoded,
            "Command serialization is unstable");
  }
  ghostband::Status status{.played_samples = 48000,
                           .ms_per_frame = 12.5f,
                           .underruns = 2,
                           .latency_samples = 5760,
                           .prompt_status = {0, 1, 2, 3, 0, 0},
                           .recording = true,
                           .recorded_samples = 24000,
                           .tap_dropped = 4};
  const auto event = json::parse(ghostband::serialize_status(status));
  require(event == json({{"event", "status"},
                         {"played_samples", 48000},
                         {"ms_per_frame", 12.5},
                         {"underruns", 2},
                         {"latency_samples", 5760},
                         {"prompt_status", {0, 1, 2, 3, 0, 0}},
                         {"recording", true},
                         {"recorded_samples", 24000},
                         {"tap_dropped", 4}}),
          "Status fields do not match protocol v1");
}

void invalid_commands() {
  for (const auto* input : {"",
                            "{",
                            "[]",
                            "null",
                            "{}",
                            R"({"cmd":4})",
                            R"({"cmd":"unknown"})",
                            R"({"cmd":"noteon","pitch":128})",
                            R"({"cmd":"noteoff","pitch":-1})",
                            R"({"cmd":"noteon","pitch":60.5})",
                            R"({"cmd":"noteon","pitch":true})",
                            R"({"cmd":"noteon","pitch":18446744073709551615})",
                            R"({"cmd":"prompt","slot":6,"text":"piano","weight":1})",
                            R"({"cmd":"prompt","slot":0,"text":null,"weight":1})",
                            R"({"cmd":"prompt_audio","slot":0,"path":""})",
                            R"({"cmd":"style_embedding","slot":0,"values":[1]})",
                            R"({"cmd":"blend","weights":[1,0,0,0,0,-1]})",
                            R"({"cmd":"blend","weights":[1,0,0,0,0]})",
                            R"({"cmd":"drumless","on":1})",
                            R"({"cmd":"onset_mode","mode":2})",
                            R"({"cmd":"params","temperature":1e100})",
                            R"({"cmd":"params","topk":0})",
                            R"({"cmd":"params","buffer_frames":0})",
                            R"({"cmd":"params","buffer_frames":4294967296})",
                            R"({"cmd":"click_at","sample":-1,"accent":true})",
                            R"({"cmd":"ref_play","at_sample":0.5,"from_sample":0})",
                            R"({"cmd":"ref_mix","band_gain":1.01,"ref_gain":0})",
                            R"({"cmd":"record","action":"start"})",
                            R"({"cmd":"record","action":"pause"})",
                            R"({"cmd":"bench","seconds":0,"latency_probes":0})",
                            R"({"cmd":"load","model":"other","resources":"/tmp"})",
                            R"({"cmd":"load","model":"mrt2_small","resources":"\u0000"})"}) {
    bool rejected = false;
    try {
      (void)ghostband::parse_command(input);
    } catch (const std::exception&) {
      rejected = true;
    }
    require(rejected, input);
  }
}

struct Client {
  int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  ~Client() {
    if (fd >= 0) ::close(fd);
  }
  void connect(const sockaddr_un& address) const {
    const auto deadline = std::chrono::steady_clock::now() + 3s;
    while (::connect(fd, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
      require(std::chrono::steady_clock::now() < deadline, "Server did not listen");
      std::this_thread::sleep_for(1ms);
    }
    const timeval timeout{3, 0};
    require(::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0,
            "Cannot set client timeout");
  }
  void send(std::string_view data) const {
    while (!data.empty()) {
      const auto count = ::send(fd, data.data(), data.size(), MSG_NOSIGNAL);
      require(count > 0, "Client send failed");
      data.remove_prefix(static_cast<std::size_t>(count));
    }
  }
  json line() const {
    std::string data;
    for (;;) {
      char byte;
      require(::recv(fd, &byte, 1, 0) == 1, "Client read timed out or disconnected");
      if (byte == '\n') return json::parse(data);
      data += byte;
    }
  }
};

void socket_session() {
  char directory[] = "/tmp/bm-protocol-XXXXXX";
  require(::mkdtemp(directory) != nullptr, "Cannot create socket test directory");
  const auto path = std::filesystem::path(directory) / "engine.sock";
  std::exception_ptr server_error;
  bool playing = false;
  auto tap = std::make_unique<ghostband::AudioTap>();
  std::array<float, ghostband::kTapSamples> model_audio{};
  std::size_t ticks = 0;
  std::atomic<std::size_t> disconnects{0};
  const auto controller_thread = std::this_thread::get_id();
  std::thread::id server_thread;
  ghostband::SessionController controller{
      .apply = [&](const ghostband::Command& command) -> std::optional<std::string> {
        require(std::this_thread::get_id() == server_thread &&
                    std::this_thread::get_id() != controller_thread,
                "Command left the controller thread");
        if (std::holds_alternative<ghostband::Hello>(command))
          return ghostband::hello_reply("0.1.0");
        if (const auto* value = std::get_if<ghostband::Tap>(&command)) tap->enable(value->on);
        if (std::holds_alternative<ghostband::Play>(command)) playing = true;
        if (std::holds_alternative<ghostband::Stop>(command)) playing = false;
        if (std::holds_alternative<ghostband::Load>(command))
          throw std::runtime_error("missing assets");
        return std::nullopt;
      },
      .status = [&]() -> std::optional<ghostband::Status> {
        if (!playing) return std::nullopt;
        tap->capture(model_audio, model_audio, ticks * ghostband::kTapSamples);
        return ghostband::Status{.played_samples = ++ticks * ghostband::kTapSamples,
                                 .tap_dropped = tap->dropped()};
      },
      .disconnect =
          [&] {
            playing = false;
            tap->enable(false);
            ++disconnects;
          },
      .event = [&]() -> std::optional<std::string> {
        ghostband::TapBlock block;
        if (!tap->read(block)) return std::nullopt;
        return ghostband::serialize_tap(block);
      }};
  {
    std::jthread server([&](std::stop_token stop) {
      server_thread = std::this_thread::get_id();
      try {
        ghostband::serve_session(path, controller, stop);
      } catch (...) {
        server_error = std::current_exception();
      }
    });
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, path.c_str());
    const auto connected_at = std::time(nullptr);
    Client client;
    client.connect(address);
    struct stat info{};
    require(::stat(path.c_str(), &info) == 0 && (info.st_mode & 0777) == 0600,
            "Socket permissions are not 0600");
    client.send("{\"cmd\":\"hel");
    client.send("lo\"}\n{bad}\n{\"cmd\":\"hello\"}\n");
    require(client.line().at("protocol") == 1, "Fragmented hello failed");
    require(client.line().at("ok") == false, "Malformed line was accepted");
    require(client.line().at("upstream") == "v2.0.3", "Coalesced hello failed");
    {
      Client second;
      second.connect(address);
      const auto busy = second.line();
      require(busy.at("ok") == false && busy.at("holder_pid") == ::getpid() &&
                  busy.at("held_since").get<std::int64_t>() >= connected_at,
              "Second client was not told which process holds the engine since when");
      char byte;
      require(::recv(second.fd, &byte, 1, 0) == 0, "Second simultaneous client was accepted");
    }
    bool occupied = false;
    try {
      ghostband::serve_session(path, controller);
    } catch (const std::system_error&) {
      occupied = true;
    }
    require(occupied && std::filesystem::exists(path), "Listener replaced an occupied socket");
    client.send(std::string(ghostband::kMaxLineBytes + 1, 'x') + "\n{\"cmd\":\"hello\"}\n");
    require(client.line().at("ok") == false, "Oversized line was accepted");
    require(client.line().at("protocol") == 1, "Framing did not recover after oversized input");
    client.send("{\"cmd\":\"load\",\"model\":\"mrt2_small\",\"resources\":\"/missing\"}\n");
    require(client.line().at("error") == "missing assets", "Controller error lost");
    client.send("{\"cmd\":\"play\"}\n");
    const auto started = std::chrono::steady_clock::now();
    require(client.line().at("played_samples") == 4800, "First status tick missing");
    require(client.line().at("played_samples") == 9600, "Second status tick missing");
    require(std::chrono::steady_clock::now() - started >= 100ms, "Status timer ran too fast");
    client.send("{\"cmd\":\"tap\",\"on\":true}\n");
    const auto tap_status = client.line();
    require(tap_status.at("event") == "status", "Tap replaced the status event");
    const auto tap_event = client.line();
    require(tap_event.at("event") == "tap" &&
                tap_event.at("sample").get<std::uint64_t>() + ghostband::kTapSamples ==
                    tap_status.at("played_samples").get<std::uint64_t>() &&
                tap_event.at("pcm").get<std::string>().size() == 25600,
            "Socket tap event lost its sample clock or PCM payload");
    client.send("{\"cmd\":\"stop\"}\n{\"cmd\":\"hello\"}\n");
    auto reply = client.line();
    while (reply.contains("event")) reply = client.line();
    require(reply.at("ok") == true, "Fire-and-forget commands emitted a reply");
    client.send("{\"cmd\":\"hello\"}\n");
    require(::shutdown(client.fd, SHUT_WR) == 0, "Cannot half-close client");
    require(client.line().at("protocol") == 1, "Half-close lost the pending reply");
    char byte;
    require(::recv(client.fd, &byte, 1, 0) == 0, "Half-closed client was not disconnected");
    const auto completed = disconnects.load();
    {
      Client early_close;
      early_close.connect(address);
    }
    const auto disconnect_deadline = std::chrono::steady_clock::now() + 3s;
    while (disconnects.load() == completed) {
      require(std::chrono::steady_clock::now() < disconnect_deadline,
              "Early-close peer was not disconnected");
      std::this_thread::sleep_for(1ms);
    }
    Client reconnected;
    reconnected.connect(address);
    reconnected.send("{\"cmd\":\"hello\"}\n{\"cmd\":\"quit\"}\n");
    require(reconnected.line().at("protocol") == 1, "Reconnect failed or quit lost a reply");
    server.join();
  }
  if (server_error) std::rethrow_exception(server_error);
  require(!std::filesystem::exists(path), "Socket was not removed on quit");
  std::filesystem::remove(directory);
}
void stalled_reader_keeps_connection() {
  char directory[] = "/tmp/bm-protocol-XXXXXX";
  require(::mkdtemp(directory) != nullptr, "Cannot create socket test directory");
  const auto path = std::filesystem::path(directory) / "engine.sock";
  std::exception_ptr server_error;
  bool playing = false;
  std::atomic<std::size_t> disconnects{0};
  // An endless supply of tap-sized events, as a tap ring that never empties would give.
  const std::string tap_line(25600, 'x');
  ghostband::SessionController controller{
      .apply = [&](const ghostband::Command& command) -> std::optional<std::string> {
        if (std::holds_alternative<ghostband::Hello>(command))
          return ghostband::hello_reply("0.1.0");
        if (std::holds_alternative<ghostband::Play>(command)) playing = true;
        return std::nullopt;
      },
      .status = [&]() -> std::optional<ghostband::Status> {
        if (!playing) return std::nullopt;
        return ghostband::Status{};
      },
      .disconnect = [&] { ++disconnects; },
      .event = [&]() -> std::optional<std::string> {
        if (!playing) return std::nullopt;
        return R"({"event":"tap","pcm":")" + tap_line + R"("})";
      }};
  {
    std::jthread server([&](std::stop_token stop) {
      try {
        ghostband::serve_session(path, controller, stop);
      } catch (...) {
        server_error = std::current_exception();
      }
    });
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, path.c_str());
    Client client;
    client.connect(address);
    client.send("{\"cmd\":\"play\"}\n");
    // Long enough for the uncapped event backlog to pass the output limit several times.
    std::this_thread::sleep_for(1s);
    require(disconnects.load() == 0, "A reader that stalled lost its connection");
    client.send("{\"cmd\":\"hello\"}\n{\"cmd\":\"quit\"}\n");
    auto reply = client.line();
    while (reply.contains("event")) reply = client.line();
    require(reply.at("protocol") == 1, "A reader that stalled lost its reply");
    server.join();
  }
  if (server_error) std::rethrow_exception(server_error);
  std::filesystem::remove(directory);
}
}  // namespace

int main() {
  try {
    round_trips();
    invalid_commands();
    socket_session();
    stalled_reader_keeps_connection();
    std::cout << "PASS: all protocol v1 commands, validation, socket framing and status ticks\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
