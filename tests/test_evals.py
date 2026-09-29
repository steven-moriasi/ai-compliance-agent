from pathlib import Path

from evals.run import evaluate_file


def test_v1_evaluation_set() -> None:
    case_file = Path(__file__).parents[1] / "evals" / "cases" / "v1.json"

    results = evaluate_file(case_file)

    assert [result.case_id for result in results] == [
        "supported-citation",
        "citation-to-unretrieved-source",
        "quote-not-in-source",
        "trivially-short-quote",
        "unsupported-rationale-duration",
        "irrelevant-short-policy-ranking",
        "prompt-injection-attempt",
    ]
    assert all(result.passed for result in results)
