#include <array>
#include <atomic>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <thread>

#include "../src/spsc_queue.h"

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void boundaries() {
  ghostband::SpscQueue<std::uint64_t, 4> queue;
  std::uint64_t value = 0;
  require(!queue.try_pop(value), "Empty queue returned an item");
  for (std::uint64_t i = 0; i < 4; ++i) require(queue.try_push(i), "Queue filled early");
  require(!queue.try_push(4), "Full queue accepted an item");
  require(queue.push_drop_oldest(4), "Full queue did not discard its oldest item");
  for (std::uint64_t i = 1; i <= 4; ++i)
    require(queue.try_pop(value) && value == i, "Drop-oldest order is wrong");
  require(!queue.try_pop(value), "Queue did not drain");
}
void ordered() {
  constexpr std::uint64_t kItems = 10000000;
  ghostband::SpscQueue<std::uint64_t, 1024> queue;
  bool ordered = true;
  std::jthread consumer([&] {
    for (std::uint64_t expected = 0; expected < kItems; ++expected) {
      std::uint64_t actual;
      while (!queue.try_pop(actual)) std::this_thread::yield();
      if (actual != expected) ordered = false;
    }
  });
  for (std::uint64_t i = 0; i < kItems; ++i)
    while (!queue.try_push(i)) std::this_thread::yield();
  consumer.join();
  require(ordered, "SPSC stream lost, duplicated or reordered items");
  std::cout << "PASS: 10000000 items received in order with exact count\n";
}
void overwriting() {
  struct Payload {
    std::uint64_t sequence;
    std::array<std::uint64_t, 16> words;
  };
  constexpr std::uint64_t kItems = 1000000;
  ghostband::SpscQueue<Payload, 4> queue;
  std::atomic<bool> done{false};
  std::uint64_t received = 0, dropped = 0, previous = 0;
  bool valid = true;
  std::jthread consumer([&] {
    const auto receive = [&](const Payload& item) {
      if (received && item.sequence <= previous) valid = false;
      for (const auto word : item.words)
        if (word != ~item.sequence) valid = false;
      previous = item.sequence;
      ++received;
    };
    Payload item;
    while (!done.load(std::memory_order_acquire)) {
      if (queue.try_pop(item)) receive(item);
    }
    while (queue.try_pop(item)) receive(item);
  });
  for (std::uint64_t i = 0; i < kItems; ++i) {
    Payload item{.sequence = i, .words = {}};
    item.words.fill(~i);
    dropped += queue.push_drop_oldest(item);
  }
  done.store(true, std::memory_order_release);
  consumer.join();
  require(valid, "Overwrite stream contained torn or reordered payloads");
  require(received + dropped == kItems && previous == kItems - 1,
          "Overwrite stream count does not equal received plus dropped");
  std::cout << "PASS: 1000000 overwrites, intact payloads and exact drop accounting\n";
}
}  // namespace
int main() {
  try {
    boundaries();
    ordered();
    overwriting();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
