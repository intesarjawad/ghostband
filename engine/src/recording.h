#pragma once

#include <cstddef>
#include <filesystem>
#include <memory>

#include "wav_writer.h"

namespace magentart::core {
class RealtimeRunner;
}
namespace ghostband {
/// The caller must quiesce audio callbacks before stop or destruction.
class Recording {
 public:
  explicit Recording(magentart::core::RealtimeRunner& runner);
  ~Recording();
  void start(const std::filesystem::path& path);
  std::size_t stop();
  bool active() const { return writer_ != nullptr; }

 private:
  magentart::core::RealtimeRunner& runner_;
  std::unique_ptr<WavWriter> writer_;
};
}  // namespace ghostband
