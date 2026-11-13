#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

#include "../src/protocol.h"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t*, std::size_t);

int main(int argc, char** argv) {
  try {
    if (argc != 2) throw std::runtime_error("Usage: protocol_corpus_test CORPUS");
    std::size_t count = 0;
    for (const auto& entry : std::filesystem::directory_iterator(argv[1])) {
      if (!entry.is_regular_file()) continue;
      std::ifstream stream(entry.path(), std::ios::binary);
      if (!stream) throw std::runtime_error("Cannot read corpus seed: " + entry.path().string());
      const std::string input{std::istreambuf_iterator<char>(stream), {}};
      LLVMFuzzerTestOneInput(reinterpret_cast<const std::uint8_t*>(input.data()), input.size());
      ++count;
    }
    if (count == 0) throw std::runtime_error("Protocol corpus is empty");
    for (const auto size :
         {std::size_t{0}, ghostband::kMaxLineBytes, ghostband::kMaxLineBytes + 1}) {
      const std::string input(size, '\0');
      LLVMFuzzerTestOneInput(reinterpret_cast<const std::uint8_t*>(input.data()), input.size());
    }
    std::cout << "PASS: " << count << " protocol seeds and line-size boundaries\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
