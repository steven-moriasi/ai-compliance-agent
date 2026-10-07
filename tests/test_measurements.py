import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_measurements_quote_the_report_files() -> None:
    document = (ROOT / "docs" / "MEASUREMENTS.md").read_text(encoding="utf-8")
    corpus = json.loads(
        (ROOT / "evals" / "reports" / "federal_register_corpus.json").read_text(encoding="utf-8")
    )
    sample = json.loads(
        (ROOT / "evals" / "reports" / "federal_register_sample.json").read_text(encoding="utf-8")
    )
    retrieval = json.loads(
        (ROOT / "evals" / "reports" / "keyword_retrieval.json").read_text(encoding="utf-8")
    )
    hardware = json.loads(
        (ROOT / "evals" / "reports" / "hardware.json").read_text(encoding="utf-8")
    )

    assert str(corpus["sections"]) in document
    assert str(corpus["documents_loaded"]) in document
    assert corpus["dataset_version"] in document
    assert str(sample["sections"]) in document
    assert str(retrieval["elapsed_ms"]) in document
    assert hardware["python"] in document
    assert "local_model.json" in document
