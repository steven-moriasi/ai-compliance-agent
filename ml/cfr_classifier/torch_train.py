"""PyTorch heads on the same TF-IDF rows. Weights stay under data/models and out of git."""

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal


def fit_and_score(
    train_x: Sequence[Sequence[float]],
    train_y: Sequence[Sequence[int]],
    test_x: Sequence[Sequence[float]],
    *,
    kind: Literal["linear", "mlp"],
    epochs: int,
    seed: int,
    hidden: int,
    learning_rate: float,
    weight_decay: float,
    artifact_path: Path | None,
) -> list[list[float]]:
    """Train on the earlier window and return a probability for every test row and label.

    Positive labels are rare, so the loss up-weights them, capped so one rare part
    cannot dominate the step. Dropout is off when the scores are taken.
    """
    import torch
    from safetensors.torch import save_file
    from torch import nn

    torch.manual_seed(seed)
    features = torch.tensor(train_x, dtype=torch.float32)
    targets = torch.tensor(train_y, dtype=torch.float32)
    label_count = int(targets.shape[1])
    feature_count = int(features.shape[1])
    if kind == "linear":
        model = nn.Linear(feature_count, label_count)
    else:
        model = nn.Sequential(
            nn.Linear(feature_count, hidden),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, label_count),
        )
    positives = targets.sum(dim=0).clamp(min=1)
    negatives = targets.shape[0] - positives
    pos_weight = (negatives / positives).clamp(max=20)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    model.train()
    for _epoch in range(epochs):
        optimizer.zero_grad()
        loss = loss_fn(model(features), targets)
        loss.backward()
        optimizer.step()
    model.eval()
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        tensors = {
            key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()
        }
        save_file(tensors, str(artifact_path))
    held_out = torch.tensor(test_x, dtype=torch.float32)
    with torch.no_grad():
        probabilities = torch.sigmoid(model(held_out))
    scores = [[float(value) for value in row] for row in probabilities.tolist()]
    if any(not math.isfinite(value) for row in scores for value in row):
        raise RuntimeError("classifier scores were not finite")
    return scores
