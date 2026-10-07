from evals.retrieval_latency import percentile


def test_percentile_interpolates_the_sorted_sample() -> None:
    assert percentile([], 0.5) is None
    assert percentile([10.0, 30.0], 0.5) == 20.0
    assert percentile([5.0, 5.0, 9.0], 0.95) == 8.6
