"""Blend smoothing at the conductor's 20 Hz control rate."""

from collections.abc import Callable
from math import exp, isfinite

from ghostband.engine_client import Command

TICK_S = 0.050
TIME_CONSTANT_S = 0.300
SEND_THRESHOLD = 0.005
# The exponential alone needs 1.59 s to reach 0.005 after a full-scale step.
# Settling below 0.01 at 1.40 s bounds completion without changing the time constant.
SETTLE_THRESHOLD = 0.010


class Slew:
    """Smooth a slider target and send accumulated changes, including its endpoint."""

    def __init__(self, send: Callable[[Command], None], initial: float = 0.0) -> None:
        self._validate(initial)
        self.send = send
        self.current = initial
        self.target = initial
        self._sent = initial
        self._last_tick_s: float | None = None

    @staticmethod
    def _validate(value: float) -> None:
        if not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Style position must be finite and between 0 and 1")

    def set_target(self, target: float) -> None:
        self._validate(target)
        self.target = target

    def jump(self, target: float) -> None:
        """Move to a position at once, so a measured take starts at that slider end."""
        self._validate(target)
        self.target = self.current = self._sent = target
        self.send({"cmd": "blend", "weights": [1 - target, target, 0, 0, 0, 0]})

    def tick(self, now_s: float) -> float:
        """Use an injected monotonic clock, coalescing calls faster than 20 Hz."""
        if not isfinite(now_s) or (self._last_tick_s is not None and now_s < self._last_tick_s):
            raise ValueError("Slew clock must be finite and monotonic")
        if self._last_tick_s is None:
            self._last_tick_s = now_s
            return self.current
        elapsed_s = now_s - self._last_tick_s
        if elapsed_s < TICK_S - 1e-12:
            return self.current
        self._last_tick_s = now_s
        self.current = self.target + (self.current - self.target) * exp(
            -elapsed_s / TIME_CONSTANT_S
        )
        if abs(self.current - self.target) < SETTLE_THRESHOLD:
            self.current = self.target
        changed = abs(self.current - self._sent) > SEND_THRESHOLD
        settled = self.current == self.target and self.current != self._sent
        if changed or settled:
            self.send({"cmd": "blend", "weights": [1 - self.current, self.current, 0, 0, 0, 0]})
            self._sent = self.current
        return self.current
