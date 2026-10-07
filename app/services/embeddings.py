"""Optional section embeddings for hybrid retrieval.

Keyword search does not import this model path. A hybrid or embedding query needs
either an injected embedder or the optional retrieval extra plus a built index.
"""

import hashlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.models import Policy


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


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = float(sum(first * second for first, second in zip(left, right, strict=True)))
    left_norm = float(sum(value * value for value in left) ** 0.5)
    right_norm = float(sum(value * value for value in right) ** 0.5)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def write_embedding_index(
    path: Path,
    model_name: str,
    vectors: Mapping[str, Sequence[float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model_name,
        "sections": {key: [float(value) for value in vector] for key, vector in vectors.items()},
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def read_embedding_index(path: Path) -> tuple[str, dict[str, tuple[float, ...]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model_name = payload["model"]
    if not isinstance(model_name, str):
        raise RetrievalUnavailable("embedding index is missing a model name")
    raw_sections = payload["sections"]
    if not isinstance(raw_sections, dict):
        raise RetrievalUnavailable("embedding index is missing section vectors")
    sections: dict[str, tuple[float, ...]] = {}
    for key, vector in raw_sections.items():
        if not isinstance(key, str) or not isinstance(vector, list):
            raise RetrievalUnavailable("embedding index has a malformed section vector")
        sections[key] = tuple(float(value) for value in vector)
    return model_name, sections


def build_embedding_index(
    session: Session,
    embedder: Embedder,
    path: Path,
    model_name: str,
) -> int:
    """Embed every stored section. Query-time filters still decide which ones are eligible."""
    policies = session.scalars(select(Policy).options(selectinload(Policy.sections)))
    texts: list[str] = []
    keys: list[str] = []
    for policy in policies:
        for section in policy.sections:
            body = section_embedding_text(section.heading, section.text)
            keys.append(section_embedding_key(section.heading, section.text))
            texts.append(body)
    if not texts:
        write_embedding_index(path, model_name, {})
        return 0
    vectors = embedder.embed(texts)
    if len(vectors) != len(keys):
        raise RetrievalUnavailable("embedder returned a different number of vectors than texts")
    write_embedding_index(path, model_name, dict(zip(keys, vectors, strict=True)))
    return len(keys)


def sentence_transformer_embedder(model_name: str) -> Embedder:
    if importlib.util.find_spec("sentence_transformers") is None:
        raise RetrievalUnavailable(
            "Install the retrieval extra before using embedding or hybrid search"
        )
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    model = SentenceTransformer(model_name)

    class _SentenceTransformerEmbedder:
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            encoded = model.encode(list(texts), normalize_embeddings=True)
            return [[float(value) for value in vector] for vector in encoded]

    return _SentenceTransformerEmbedder()
