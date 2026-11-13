#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>
#include <string_view>

#include "../src/protocol.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
  ghostband::Command command;
  try {
    command = ghostband::parse_command(std::string_view(reinterpret_cast<const char*>(data), size));
  } catch (const nlohmann::json::exception&) {
    return 0;
  } catch (const std::invalid_argument&) {
    return 0;
  }
  // Only rejection at the input boundary is expected; accepted commands must round-trip.
  const auto wire = ghostband::serialize_command(command);
  if (ghostband::serialize_command(ghostband::parse_command(wire)) != wire) std::abort();
  return 0;
}
