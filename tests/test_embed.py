from pathlib import Path

import pytest

from app.ingestion import embed as embed_module
from app.services.embeddings import section_embedding_key


def test_embed_command_writes_a_report_and_skips_unchanged_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unchanged = section_embedding_key(None, "already stored")
    pages = [
        [
            {"id": "s1", "heading": None, "text": "already stored", "content_sha256": unchanged},
            {"id": "s2", "heading": "Limit", "text": "new text", "content_sha256": None},
        ],
        [],
    ]

    class _Session:
        def __init__(self) -> None:
            self.inserted: list[dict[str, str]] = []

        def __enter__(self) -> "_Session":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def execute(self, statement: object, params: object) -> object:
            sql = str(statement)
            if "INSERT" in sql:
                assert isinstance(params, list)
                self.inserted.extend(params)
                return None
            if "count" in sql:
                return _Scalar(len(self.inserted))
            rows = pages.pop(0)
            return _Mappings(rows)

        def commit(self) -> None:
            return None

        def get_bind(self) -> None:
            return None

    class _Embedder:
        max_seq_length = 256

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] + [0.0] * 383 for _text in texts]

        def truncated(self, texts: list[str]) -> int:
            return 1

    monkeypatch.setattr(embed_module, "create_engine", lambda _url: _Engine())
    monkeypatch.setattr(embed_module, "sessionmaker", lambda **_kwargs: _Session)
    monkeypatch.setattr(
        embed_module,
        "sentence_transformer_embedder",
        lambda *_args, **_kwargs: _Embedder(),
    )
    report = tmp_path / "embedding.json"
    assert embed_module.main(
        ["--model", "test-model", "--revision", "rev", "--batch-size", "2", "--report", str(report)]
    ) == 0
    body = report.read_text(encoding="utf-8")
    assert '"sections_embedded": 1' in body
    assert '"sections_skipped": 1' in body
    assert '"truncated_sections": 1' in body
    assert "test-model@rev" in body


def test_embed_command_rejects_a_small_batch() -> None:
    with pytest.raises(SystemExit):
        embed_module.main(["--batch-size", "0"])


class _Engine:
    def dispose(self) -> None:
        return None


class _Mappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class _Scalar:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value
