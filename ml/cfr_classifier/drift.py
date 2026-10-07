"""How far the later publication window sits from the training window."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

POWER_ITERATIONS = 30


@dataclass(frozen=True)
class PcaShift:
    components: int
    power_iterations: int
    l2_mean_shift: float
    component_shifts: tuple[float, ...]
    explained_variance_ratio: tuple[float, ...]


def pca_mean_shift(
    train: Sequence[Sequence[float]],
    test: Sequence[Sequence[float]],
    *,
    components: int,
) -> PcaShift:
    """Project the test-minus-train mean onto a few train principal directions.

    Directions come from power iteration, not a full decomposition. The iteration
    count is part of the measurement.
    """
    if not train or not test or not train[0]:
        return PcaShift(
            components=0,
            power_iterations=POWER_ITERATIONS,
            l2_mean_shift=0.0,
            component_shifts=(),
            explained_variance_ratio=(),
        )
    train_mean = _mean(train)
    centered = _center(train, train_mean)
    directions = _principal_directions(centered, components)
    delta = [_mean(test)[index] - train_mean[index] for index in range(len(train_mean))]
    return PcaShift(
        components=len(directions),
        power_iterations=POWER_ITERATIONS,
        l2_mean_shift=_l2(delta),
        component_shifts=tuple(_dot(delta, direction) for direction in directions),
        explained_variance_ratio=_explained(centered, directions),
    )


def _principal_directions(rows: list[list[float]], components: int) -> list[list[float]]:
    directions: list[list[float]] = []
    working = rows
    width = len(rows[0])
    count = min(components, width, len(rows))
    for _component in range(count):
        vector = _unit([1.0] * width)
        for _iteration in range(POWER_ITERATIONS):
            vector = _unit(_xtx(working, vector))
        directions.append(vector)
        working = _deflate(working, vector)
    return directions


def _xtx(rows: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    projected = [_dot(row, vector) for row in rows]
    width = len(vector)
    combined = [0.0] * width
    for row, weight in zip(rows, projected, strict=True):
        for index, value in enumerate(row):
            combined[index] += value * weight
    return combined


def _deflate(rows: Sequence[Sequence[float]], direction: Sequence[float]) -> list[list[float]]:
    deflated: list[list[float]] = []
    for row in rows:
        weight = _dot(row, direction)
        deflated.append([value - weight * direction[index] for index, value in enumerate(row)])
    return deflated


def _explained(
    rows: Sequence[Sequence[float]],
    directions: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    total = sum(value * value for row in rows for value in row)
    if total == 0:
        return tuple(0.0 for _direction in directions)
    ratios: list[float] = []
    for direction in directions:
        captured = sum(_dot(row, direction) ** 2 for row in rows)
        ratios.append(captured / total)
    return tuple(ratios)


def _mean(rows: Sequence[Sequence[float]]) -> list[float]:
    width = len(rows[0])
    totals = [0.0] * width
    for row in rows:
        for index, value in enumerate(row):
            totals[index] += value
    count = float(len(rows))
    return [value / count for value in totals]


def _center(rows: Sequence[Sequence[float]], mean: Sequence[float]) -> list[list[float]]:
    return [[value - mean[index] for index, value in enumerate(row)] for row in rows]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(value * other for value, other in zip(left, right, strict=True))


def _l2(values: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def _unit(values: Sequence[float]) -> list[float]:
    norm = _l2(values)
    if norm == 0:
        return list(values)
    return [value / norm for value in values]
