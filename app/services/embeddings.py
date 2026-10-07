"""Optional section embeddings for hybrid retrieval.

Keyword search does not import this model path. A hybrid or embedding query needs
either an injected embedder or the optional retrieval extra plus a built index.
"""

import hashlib
import importlib.util
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class RetrievalUnavailable(Exception):
    """Embedding retrieval was requested without a model or an index."""


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one vector per input text, in the same order."""


def section_embedding_text(heading: str | None, text: str) -> str:
    return " ".join(part for part in (heading, text) if part)


def section_embedding_key(heading: str | None, text: str) -> str:
    body = section_embedding_text(heading, text)
    return hashlib.sha256(body.encode()).hexdigest()


def embedding_model_id(model_name: str, revision: str) -> str:
    """Keep the revision in the stored id so two weight snapshots do not share rows."""
    return f"{model_name}@{revision}"


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = float(sum(first * second for first, second in zip(left, right, strict=True)))
    left_norm = float(sum(value * value for value in left) ** 0.5)
    right_norm = float(sum(value * value for value in right) ** 0.5)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class SentenceTransformerEmbedder:
    """Adapter around a loaded model. Tests can pass a fake with the same methods."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        encoded = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in encoded]

    @property
    def max_seq_length(self) -> int:
        return int(self._model.max_seq_length)

    def truncated(self, texts: Sequence[str]) -> int:
        limit = self.max_seq_length
        tokenized = self._model.tokenizer(
            list(texts),
            add_special_tokens=True,
            truncation=True,
            max_length=limit + 1,
            padding=False,
        )
        if not isinstance(tokenized, Mapping):
            raise RetrievalUnavailable("tokenizer did not return token ids")
        ids = tokenized.get("input_ids")
        if not isinstance(ids, Sequence):
            raise RetrievalUnavailable("tokenizer did not return token ids")
        return sum(len(row) > limit for row in ids)


def sentence_transformer_embedder(
    model_name: str,
    revision: str | None = None,
) -> SentenceTransformerEmbedder:
    """Load weights from safetensors at a pinned revision. Callers record that revision."""
    if importlib.util.find_spec("sentence_transformers") is None:
        raise RetrievalUnavailable(
            "Install the retrieval extra before using embedding or hybrid search"
        )
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(
        model_name,
        revision=revision,
        model_kwargs={"use_safetensors": True},
    )
    return SentenceTransformerEmbedder(model)
