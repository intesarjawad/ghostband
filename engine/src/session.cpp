#include "session.h"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <optional>
#include <stdexcept>
#include <system_error>
#include <utility>

namespace ghostband {
namespace {
using Clock = std::chrono::steady_clock;
// Bounds the replies and status lines held for a client that has stopped reading.
constexpr std::size_t kMaxOutputBytes = 1024 * 1024;
// About one second of encoded tap blocks. Past it, blocks wait in the tap ring, which drops
// and counts the oldest, so a reader that stalls loses audio for the heard row, not the take.
constexpr std::size_t kEventBacklogBytes = 256 * 1024;
constexpr auto kStatusInterval = std::chrono::milliseconds(100);

void check(int result, const char* operation) {
  if (result < 0) throw std::system_error(errno, std::generic_category(), operation);
}

std::optional<std::int64_t> peer_pid(int fd) {
  pid_t pid = 0;
  socklen_t size = sizeof(pid);
  if (::getsockopt(fd, SOL_LOCAL, LOCAL_PEERPID, &pid, &size) != 0) return std::nullopt;
  return pid;
}

class Socket {
 public:
  explicit Socket(int fd = -1) : fd_(fd) {}
  ~Socket() { reset(); }
  Socket(const Socket&) = delete;
  Socket& operator=(const Socket&) = delete;
  int get() const { return fd_; }
  int release() { return std::exchange(fd_, -1); }
  void reset(int fd = -1) {
    if (fd_ >= 0) ::close(fd_);
    fd_ = fd;
  }
  void nonblocking() const {
    const int flags = ::fcntl(fd_, F_GETFL);
    check(flags, "Read socket flags");
    check(::fcntl(fd_, F_SETFL, flags | O_NONBLOCK), "Set nonblocking socket");
    check(::fcntl(fd_, F_SETFD, FD_CLOEXEC), "Set close-on-exec socket");
  }

 private:
  int fd_;
};

struct Listener {
  Socket socket{::socket(AF_UNIX, SOCK_STREAM, 0)};
  std::filesystem::path bound_path;
  ~Listener() {
    if (!bound_path.empty()) ::unlink(bound_path.c_str());
  }

  explicit Listener(const std::filesystem::path& path) {
    check(socket.get(), "Create listener");
    socket.nonblocking();
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if (path.string().size() >= sizeof(address.sun_path))
      throw std::invalid_argument("Unix socket path is too long");
    const auto parent = path.parent_path();
    if (::mkdir(parent.c_str(), 0700) != 0 && errno != EEXIST)
      check(-1, "Create private socket directory");
    struct stat info{};
    check(::lstat(parent.c_str(), &info), "Inspect socket directory");
    if (!S_ISDIR(info.st_mode) || info.st_uid != ::getuid() || (info.st_mode & 077) != 0)
      throw std::runtime_error("Socket directory must be owned by this user with mode 0700");
    std::memcpy(address.sun_path, path.c_str(), path.string().size() + 1);
    check(::bind(socket.get(), reinterpret_cast<sockaddr*>(&address), sizeof(address)),
          "Bind socket; remove a stale socket only after checking its engine has stopped");
    bound_path = path;
    // The parent is private before bind, so peers cannot race this chmod.
    if (::chmod(path.c_str(), 0600) != 0 || ::listen(socket.get(), 1) != 0) {
      const auto error = errno;
      ::unlink(path.c_str());
      throw std::system_error(error, std::generic_category(), "Prepare listener");
    }
  }
};
}  // namespace

void serve_session(const std::filesystem::path& socket_path, const SessionController& controller,
                   std::stop_token stop) {
  Listener listener(socket_path);
  Socket client;
  std::optional<std::int64_t> holder_pid;
  std::int64_t held_since = 0;
  std::string input, output;
  bool discarding = false, eof = false, quitting = false;
  auto next_status = Clock::now() + kStatusInterval;
  const auto disconnect = [&] {
    controller.disconnect();
    client.reset();
    input.clear();
    output.clear();
    discarding = eof = false;
  };
  const auto queue = [&](std::string line) {
    if (output.size() + line.size() + 1 > kMaxOutputBytes) {
      disconnect();
      return;
    }
    output += line;
    output += '\n';
  };
  try {
    while (!stop.stop_requested()) {
      if (quitting && output.empty()) break;
      const auto remaining =
          std::chrono::ceil<std::chrono::milliseconds>(next_status - Clock::now());
      std::array<pollfd, 2> sockets{
          {{listener.socket.get(), POLLIN, 0},
           {client.get(),
            static_cast<short>((!eof && !quitting ? POLLIN : 0) | (!output.empty() ? POLLOUT : 0)),
            0}}};
      const auto result = ::poll(sockets.data(), sockets.size(),
                                 static_cast<int>(std::max(remaining.count(), std::int64_t{0})));
      if (result < 0 && errno == EINTR) continue;
      check(result, "Poll sockets");
      if (sockets[0].revents & POLLIN) {
        Socket incoming(::accept(listener.socket.get(), nullptr, nullptr));
        if (incoming.get() < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
          check(-1, "Accept client");
        if (incoming.get() >= 0 && client.get() < 0) {
          client.reset(incoming.release());
          client.nonblocking();
          holder_pid = peer_pid(client.get());
          held_since = std::time(nullptr);
          next_status = Clock::now() + kStatusInterval;
        } else if (incoming.get() >= 0) {
          // One client at a time; naming the holder tells the newcomer what to stop.
          const auto reply = busy_reply(holder_pid, held_since) + '\n';
          // A short reply fits an empty socket buffer, and a newcomer that left needs none.
          (void)::send(incoming.get(), reply.data(), reply.size(), MSG_NOSIGNAL);
        }
      }
      if (client.get() >= 0 && (sockets[1].revents & (POLLIN | POLLHUP)) && !eof && !quitting) {
        std::array<char, 4096> buffer;
        const auto count = ::recv(client.get(), buffer.data(), buffer.size(), 0);
        if (count == 0)
          eof = true;
        else if (count < 0) {
          if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) disconnect();
        } else {
          for (std::ptrdiff_t i = 0; i < count && client.get() >= 0 && !quitting; ++i) {
            const char byte = buffer[static_cast<std::size_t>(i)];
            if (byte == '\n') {
              if (!discarding) {
                try {
                  const auto command = parse_command(input);
                  if (auto reply = controller.apply(command)) queue(*reply);
                  quitting = std::holds_alternative<Quit>(command);
                } catch (const std::exception& error) {
                  queue(error_reply(error.what()));
                }
              }
              input.clear();
              discarding = false;
            } else if (!discarding) {
              if (input.size() == kMaxLineBytes) {
                input.clear();
                discarding = true;
                queue(error_reply("Protocol line is too long"));
              } else
                input += byte;
            }
          }
        }
      }
      if (Clock::now() >= next_status) {
        next_status = Clock::now() + kStatusInterval;
        if (client.get() >= 0 && !eof && !quitting) {
          if (auto status = controller.status()) queue(serialize_status(*status));
          if (controller.event) {
            for (std::size_t i = 0;
                 i < 20 && client.get() >= 0 && output.size() < kEventBacklogBytes; ++i) {
              auto event = controller.event();
              if (!event) break;
              queue(*event);
            }
          }
        }
      }
      if (client.get() >= 0 && !output.empty()) {
        const auto count = ::send(client.get(), output.data(), output.size(), MSG_NOSIGNAL);
        if (count > 0)
          output.erase(0, static_cast<std::size_t>(count));
        else if (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
          disconnect();
      }
      if (client.get() >= 0 &&
          ((eof && output.empty()) || (sockets[1].revents & (POLLERR | POLLNVAL))))
        disconnect();
    }
  } catch (...) {
    controller.disconnect();
    throw;
  }
  controller.disconnect();
}
}  // namespace ghostband
