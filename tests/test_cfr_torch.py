from pathlib import Path
from typing import Literal

import pytest

from ml.cfr_classifier.torch_train import fit_and_score


@pytest.mark.models
def test_linear_and_mlp_heads_separate_two_classes(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    train_x = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
    train_y = [[1, 0], [1, 0], [0, 1], [0, 1]]
    test_x = [[1.0, 0.0], [0.0, 1.0]]

    _expect_separated(tmp_path, train_x, train_y, test_x, "linear")
    _expect_separated(tmp_path, train_x, train_y, test_x, "mlp")


def _expect_separated(
    tmp_path: Path,
    train_x: list[list[float]],
    train_y: list[list[int]],
    test_x: list[list[float]],
    kind: Literal["linear", "mlp"],
) -> None:
    artifact = tmp_path / f"{kind}.safetensors"
    scores = fit_and_score(
        train_x,
        train_y,
        test_x,
        kind=kind,
        epochs=60,
        seed=7,
        hidden=8,
        learning_rate=0.05,
        weight_decay=0.0,
        artifact_path=artifact,
    )
    assert scores[0][0] > scores[0][1]
    assert scores[1][1] > scores[1][0]
    assert artifact.is_file()
