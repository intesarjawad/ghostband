#pragma once

#include <filesystem>
#include <functional>
#include <memory>
#include <stop_token>

#include "protocol.h"

namespace ghostband {

struct SessionController {
  std::function<std::optional<std::string>(const Command&)> apply;
  std::function<std::optional<Status>()> status;
  std::function<void()> disconnect;
  std::function<std::optional<std::string>()> event;
};

/// Serve one client at a time. All controller callbacks run on the calling thread.
/// The socket is mode 0600 in a private directory and removed on shutdown.
void serve_session(const std::filesystem::path& socket_path, const SessionController& controller,
                   std::stop_token stop = {});

/// Own the runner and audio output on the controller thread, loading assets on demand.
class EngineSession {
 public:
  EngineSession();
  ~EngineSession();
  EngineSession(const EngineSession&) = delete;
  EngineSession& operator=(const EngineSession&) = delete;
  std::optional<std::string> apply(const Command& command);
  std::optional<Status> status() const;
  std::optional<std::string> event();
  void stop();

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace ghostband
