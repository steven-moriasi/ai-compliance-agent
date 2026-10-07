from evals.retrieval_metrics import mean, mrr_at, ndcg_at, recall_at, relevant
from evals.retrieval_sets import round_robin, section_ref_matches, token_overlap


def test_section_gold_ignores_a_mere_document_match() -> None:
    assert relevant("section-1", "2024-1", {"section-1"}, {"2024-1"})
    assert not relevant("summary", "2024-1", {"section-1"}, {"2024-1"})


def test_document_gold_is_used_when_no_section_was_named() -> None:
    assert relevant("", "2024-1", set(), {"2024-1"})
    assert not relevant("other", "2024-2", set(), {"2024-1"})


def test_negative_items_are_never_relevant() -> None:
    assert not relevant("section-1", "2024-1", set(), set())


def test_recall_and_mrr_use_the_first_hit_inside_k() -> None:
    hits = [False, False, True]
    assert recall_at(hits, 2) == 0.0
    assert recall_at(hits, 3) == 1.0
    assert mrr_at(hits, 10) == 1.0 / 3
    assert mrr_at([False, False], 10) == 0.0


def test_ndcg_is_one_for_a_perfect_prefix() -> None:
    assert ndcg_at([True, True, False], gold_count=2, k=10) == 1.0
    assert ndcg_at([False, True], gold_count=1, k=10) < 1.0
    assert ndcg_at([True], gold_count=0, k=10) == 0.0


def test_mean_of_an_empty_series_is_missing() -> None:
    assert mean([]) is None
    assert mean([1.0, 3.0]) == 2.0


def test_round_robin_is_stable_and_visits_each_part() -> None:
    groups = {"40:52": ["a", "b"], "40:60": ["c"]}
    assert round_robin(groups, 3, 5) == round_robin(groups, 3, 5)
    assert set(round_robin(groups, 3, 5)) == {"a", "b", "c"}


def test_section_numbers_do_not_match_a_longer_neighbor() -> None:
    assert section_ref_matches("§ 60.4", ["§ 60.4"])
    assert section_ref_matches("§ 60.4#2", ["§ 60.4"])
    assert section_ref_matches("§ 60.4~2", ["§ 60.4"])
    assert not section_ref_matches("§ 60.42", ["§ 60.4"])


def test_token_overlap_is_the_share_of_query_tokens_in_the_gold_text() -> None:
    assert token_overlap("final rule approval", ["final rule text"]) == 2 / 3
