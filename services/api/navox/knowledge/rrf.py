"""Reciprocal rank fusion shared by every retriever.

Retrievers return ordered keys. Independent score scales are never summed:
only ranks contribute. Deterministic ties break on the key so results are
stable regardless of database ordering.
"""

from __future__ import annotations

from collections.abc import Sequence

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = RRF_K, limit: int | None = None
) -> list[tuple[str, float]]:
    """Fuse ordered retriever rankings into one deterministic ranking.

    A key that appears twice inside the same retriever list is credited once per
    rank position, so a retriever cannot inflate itself by repeating a key.
    """
    if k < 1:
        raise ValueError("RRF k must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for position, key in enumerate(ranking, start=1):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:limit] if limit is not None else ordered
