"""Reciprocal rank fusion: deterministic, rank-only, independent of score scale."""

import pytest

from navox.knowledge.rrf import RRF_K, reciprocal_rank_fusion


def test_fusion_prefers_the_key_ranked_well_by_both_retrievers() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])
    assert [key for key, _ in fused] == ["a", "b", "c", "d"]


def test_fusion_uses_only_rank_positions() -> None:
    """One retriever's first place beats two middling places, per k=60."""
    fused = dict(reciprocal_rank_fusion([["top"], ["x", "y", "z"]]))
    assert fused["top"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["x"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["x"] < fused["top"] + 1e-9


def test_repeated_keys_inside_one_ranking_are_counted_once() -> None:
    fused = dict(reciprocal_rank_fusion([["a", "a", "a"], ["b"]]))
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 1))


def test_ties_break_deterministically_on_the_key() -> None:
    first = reciprocal_rank_fusion([["b"], ["a"]])
    second = reciprocal_rank_fusion([["a"], ["b"]])
    assert [key for key, _ in first] == ["a", "b"]
    assert [key for key, _ in second] == ["a", "b"]


def test_single_retriever_order_is_preserved() -> None:
    ranking = ["z", "y", "x"]
    assert [key for key, _ in reciprocal_rank_fusion([ranking])] == ranking


def test_limit_and_validation() -> None:
    assert [key for key, _ in reciprocal_rank_fusion([["a", "b", "c"]], limit=2)] == ["a", "b"]
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], k=0)
