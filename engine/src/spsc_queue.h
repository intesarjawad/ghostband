#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace ghostband {

/// Preallocated SPSC transport. Only push_drop_oldest may advance an unread item.
template <typename T, std::size_t Capacity, std::size_t Limit = Capacity>
class SpscQueue {
  static_assert(Capacity > 0 && (Capacity & (Capacity - 1)) == 0);
  static_assert(Limit > 0 && Limit <= Capacity);
  static_assert(std::is_trivially_copyable_v<T>);
  static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
  static constexpr std::size_t kWords =
      (sizeof(T) + sizeof(std::uint64_t) - 1) / sizeof(std::uint64_t);
  struct Slot {
    std::atomic<std::uint64_t> sequence{0};
    // Atomic words let a slow reader safely reject a concurrently overwritten item.
    std::array<std::atomic<std::uint64_t>, kWords> words{};
  };

 public:
  bool try_push(const T& value) noexcept {
    const auto write = write_.load(std::memory_order_relaxed);
    if (write - read_.load(std::memory_order_acquire) >= Limit) return false;
    publish(value, write);
    return true;
  }

  /// Always publish; return true exactly when an unread item was discarded.
  bool push_drop_oldest(const T& value) noexcept {
    const auto write = write_.load(std::memory_order_relaxed);
    auto read = read_.load(std::memory_order_acquire);
    const bool dropped = write - read >= Limit &&
                         read_.compare_exchange_strong(read, read + 1, std::memory_order_acq_rel);
    publish(value, write);
    return dropped;
  }

  bool try_pop(T& value) noexcept {
    auto read = read_.load(std::memory_order_acquire);
    if (read >= write_.load(std::memory_order_acquire)) return false;
    const auto& slot = slots_[read & (Capacity - 1)];
    if (slot.sequence.load(std::memory_order_acquire) != read + 1) return false;
    std::array<std::uint64_t, kWords> words{};
    for (std::size_t i = 0; i < kWords; ++i)
      words[i] = slot.words[i].load(std::memory_order_acquire);
    if (slot.sequence.load(std::memory_order_acquire) != read + 1 ||
        !read_.compare_exchange_strong(read, read + 1, std::memory_order_acq_rel))
      return false;
    std::memcpy(&value, words.data(), sizeof(T));
    return true;
  }

 private:
  void publish(const T& value, std::uint64_t write) noexcept {
    auto& slot = slots_[write & (Capacity - 1)];
    std::array<std::uint64_t, kWords> words{};
    std::memcpy(words.data(), &value, sizeof(T));
    slot.sequence.store(0, std::memory_order_release);
    for (std::size_t i = 0; i < kWords; ++i)
      slot.words[i].store(words[i], std::memory_order_release);
    slot.sequence.store(write + 1, std::memory_order_release);
    write_.store(write + 1, std::memory_order_release);
  }
  std::array<Slot, Capacity> slots_{};
  alignas(64) std::atomic<std::uint64_t> write_{0};
  alignas(64) std::atomic<std::uint64_t> read_{0};
};
}  // namespace ghostband
