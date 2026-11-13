from typing import TYPE_CHECKING

import pytest
from ghostband.style import Slew

if TYPE_CHECKING:
    from ghostband.engine_client import Command


@pytest.mark.parametrize(("initial", "target"), [(0, 1), (1, 0), (0.2, 0.8), (0.8, 0.2)])
def test_slew_settles_and_transmits_target_in_under_one_and_a_half_seconds(
    initial: float, target: float
) -> None:
    commands: list[Command] = []
    slew = Slew(commands.append, initial)
    slew.tick(10)
    slew.set_target(target)
    values = [slew.tick(10 + tick * 0.05) for tick in range(1, 30)]
    assert abs(values[-1] - target) <= 0.005
    assert values[-1] == target
    assert commands[-1] == {"cmd": "blend", "weights": [1 - target, target, 0, 0, 0, 0]}
    assert all(min(initial, target) <= value <= max(initial, target) for value in values)
    assert values == sorted(values, reverse=initial > target)


def test_slew_coalesces_fast_inputs_and_accumulates_small_changes() -> None:
    commands: list[Command] = []
    slew = Slew(commands.append)
    slew.tick(0)
    for tick in range(1, 50):
        slew.set_target(tick / 100)
        slew.tick(tick / 1000)
    assert not commands
    slew.tick(0.05)
    assert len(commands) == 1
    slew.set_target(0.5)
    for tick in range(2, 60):
        slew.tick(tick * 0.05)
    assert slew.current == 0.5
    assert commands[-1]["weights"] == [0.5, 0.5, 0, 0, 0, 0]
    previous_count = len(commands)
    slew.tick(10)
    assert len(commands) == previous_count


def test_jump_sends_the_endpoint_without_gliding() -> None:
    commands: list[Command] = []
    slew = Slew(commands.append)
    slew.jump(1)
    assert (slew.current, slew.target) == (1, 1)
    assert commands == [{"cmd": "blend", "weights": [0, 1, 0, 0, 0, 0]}]
    slew.tick(0)
    slew.tick(1)
    assert len(commands) == 1
    with pytest.raises(ValueError, match="position"):
        slew.jump(2)


def test_slew_reverses_smoothly_and_uses_elapsed_time() -> None:
    slew = Slew(lambda _: None)
    slew.tick(0)
    slew.set_target(1)
    assert slew.tick(0.3) == pytest.approx(0.6321205588)
    slew.set_target(0)
    assert slew.tick(0.6) == pytest.approx(0.2325441579)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_slider_values_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="position"):
        Slew(lambda _: None, value)
    with pytest.raises(ValueError, match="position"):
        Slew(lambda _: None).set_target(value)


def test_slew_rejects_invalid_clock() -> None:
    slew = Slew(lambda _: None)
    slew.tick(1)
    for now in [0, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="clock"):
            slew.tick(now)
