import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from physical_context.embeddings import EmbeddingProvider, EmbeddingProviderError
from physical_context.models import Capture
from physical_context.repository import CaptureRepository

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 5
MAX_SEMANTIC_DISTANCE = 0.6
SNIPPET_MAX_CHARS = 240

# Reciprocal rank fusion constant. bm25 relevance and cosine distance are not
# comparable scales, so the two arms are merged on rank alone. The conventional
# value of 60 damps the difference between adjacent ranks enough that a result
# found by both arms outranks one that merely tops a single arm.
RRF_K = 60

# Each arm is over-fetched so that fusion has room to promote captures that
# rank moderately in both arms over captures that rank first in only one.
CANDIDATE_MULTIPLIER = 4

# FTS5 reads punctuation as query syntax, so a natural-language question would
# otherwise raise a syntax error rather than search. Only word characters
# survive, which makes operator injection impossible by construction.
_FTS_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")

_KEYWORD_ONLY_NOTE = "Semantic search was unavailable, so these are keyword matches only."

# One arm's ranked output: capture id paired with that arm's own relevance
# figure, which fusion deliberately discards in favour of position.
Hits = tuple[tuple[str, float], ...]


class MatchSource(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class SearchResult:
    capture_id: str
    short_id: str
    created_at: str
    snippet: str
    tags: tuple[str, ...]
    matched_by: MatchSource
    score: float


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    results: tuple[SearchResult, ...] = ()
    note: str | None = None

    @property
    def is_no_match(self) -> bool:
        return not self.results


class CaptureSearch:
    def __init__(
        self,
        repository: CaptureRepository,
        embedding_provider: EmbeddingProvider,
        *,
        max_semantic_distance: float = MAX_SEMANTIC_DISTANCE,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.max_semantic_distance = max_semantic_distance

    def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> SearchResponse:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        normalized_query = query.strip()
        if not normalized_query:
            return SearchResponse(query=query, note="No query was supplied.")

        candidate_limit = limit * CANDIDATE_MULTIPLIER
        keyword_hits = self._search_keyword(normalized_query, candidate_limit)
        semantic_hits, semantic_note = self._search_semantic(normalized_query, candidate_limit)

        ranked = _fuse(keyword_hits, semantic_hits)
        captures = self.repository.list_by_ids(tuple(ranked))
        results = _build_results(ranked, captures, limit)

        if not results:
            return SearchResponse(
                query=normalized_query,
                note=_no_match_note(normalized_query, semantic_note),
            )
        return SearchResponse(query=normalized_query, results=results, note=semantic_note)

    def _search_keyword(self, query: str, limit: int) -> Hits:
        match_query = to_fts_match_query(query)
        if match_query is None:
            return ()
        return self.repository.search_keyword(match_query, limit=limit)

    def _search_semantic(self, query: str, limit: int) -> tuple[Hits, str | None]:
        try:
            embedding = self.embedding_provider.embed(query, input_type="query")
        except EmbeddingProviderError as error:
            # A provider that is switched off or unconfigured is a steady state,
            # not an incident: every query would otherwise log a traceback.
            logger.warning("query_embedding_unavailable reason=%s", error)
            return (), _KEYWORD_ONLY_NOTE
        except Exception:
            logger.exception("query_embedding_failed")
            return (), _KEYWORD_ONLY_NOTE

        return (
            self.repository.search_semantic(
                embedding,
                limit=limit,
                max_distance=self.max_semantic_distance,
            ),
            None,
        )


def to_fts_match_query(text: str) -> str | None:
    """Reduce free text to a quoted OR-query that FTS5 can parse.

    Terms are OR-ed rather than AND-ed because bm25 already ranks captures
    matching more terms higher, and requiring every term would drop the
    partial matches this is meant to surface.
    """
    tokens = [token for token in _FTS_TOKEN_PATTERN.findall(text) if len(token) > 1]
    if not tokens:
        return None
    return " OR ".join(f'"{token}"' for token in tokens)


def _fuse(
    keyword_hits: Hits,
    semantic_hits: Hits,
) -> dict[str, tuple[float, frozenset[MatchSource]]]:
    scores: dict[str, float] = {}
    sources: dict[str, set[MatchSource]] = {}

    for hits, source in (
        (keyword_hits, MatchSource.KEYWORD),
        (semantic_hits, MatchSource.SEMANTIC),
    ):
        for rank, (capture_id, _) in enumerate(hits, start=1):
            scores[capture_id] = scores.get(capture_id, 0.0) + 1.0 / (RRF_K + rank)
            sources.setdefault(capture_id, set()).add(source)

    return {
        capture_id: (score, frozenset(sources[capture_id])) for capture_id, score in scores.items()
    }


def _build_results(
    ranked: dict[str, tuple[float, frozenset[MatchSource]]],
    captures: dict[str, Capture],
    limit: int,
) -> tuple[SearchResult, ...]:
    scored: list[tuple[float, str, Capture, frozenset[MatchSource]]] = []
    for capture_id, (score, matched_sources) in ranked.items():
        capture = captures.get(capture_id)
        if capture is None or capture.caption is None:
            continue
        scored.append((score, capture.created_at, capture, matched_sources))

    # Stable sorts applied least-significant first: ties on fused score fall
    # back to recency, and ties on both fall back to id so ordering is total.
    scored.sort(key=lambda item: item[2].id)
    scored.sort(key=lambda item: item[1], reverse=True)
    scored.sort(key=lambda item: item[0], reverse=True)

    return tuple(
        SearchResult(
            capture_id=capture.id,
            short_id=capture.id[:8],
            created_at=capture.created_at,
            snippet=_snippet(capture.caption or ""),
            tags=capture.tags,
            matched_by=_match_source(matched_sources),
            score=round(score, 6),
        )
        for score, _, capture, matched_sources in scored[:limit]
    )


def _match_source(sources: frozenset[MatchSource]) -> MatchSource:
    if len(sources) > 1:
        return MatchSource.BOTH
    return next(iter(sources))


def _snippet(caption: str) -> str:
    summary = caption.split("\n", 1)[0].strip()
    if len(summary) <= SNIPPET_MAX_CHARS:
        return summary
    return summary[: SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def _no_match_note(query: str, semantic_note: str | None) -> str:
    note = f"No captures matched {query!r}."
    if semantic_note is not None:
        return f"{note} {semantic_note}"
    return note
