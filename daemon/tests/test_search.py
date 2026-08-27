from pathlib import Path

import pytest

from physical_context.database import Database
from physical_context.embeddings import EMBEDDING_DIMENSIONS, EmbeddingInputType
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository
from physical_context.search import (
    CaptureSearch,
    MatchSource,
    to_fts_match_query,
)

# Captures sit on mutually orthogonal basis axes, so any two differ by a cosine
# distance of exactly 1.0. That lets "near the query vector" and "shares
# vocabulary with the query" be varied independently, and puts every unrelated
# pair on a known side of the 0.6 floor.
KEYWORD_AXIS = 0
SEMANTIC_AXIS = 1
UNRELATED_AXIS = 2


def vector(axis: int) -> list[float]:
    values = [0.0] * EMBEDDING_DIMENSIONS
    values[axis] = 1.0
    return values


def make_repository(tmp_path: Path) -> tuple[Database, CaptureRepository]:
    database = Database(tmp_path / "physical_context.db")
    database.migrate()
    return database, CaptureRepository(database)


def seed(
    repository: CaptureRepository,
    capture_id: str,
    caption: str | None,
    *,
    embedding: list[float] | None = None,
    created_at: str = "2026-08-26T12:00:00Z",
    state: CaptureState = CaptureState.READY,
) -> None:
    repository.insert(
        Capture(
            id=capture_id,
            client_capture_id=f"client-{capture_id}",
            created_at=created_at,
            device_ts=1_777_000_000,
            image_path=f"/tmp/{capture_id}.jpg",
            state=CaptureState.PENDING,
        )
    )
    if state == CaptureState.PENDING:
        return

    repository.transition_state(capture_id, CaptureState.CAPTIONING)
    repository.write_search_indexes(capture_id, caption=caption, tags=(), embedding=embedding)
    repository.transition_state(capture_id, CaptureState.READY)


class StubEmbeddingProvider:
    def __init__(self, query_vector: list[float]) -> None:
        self.query_vector = query_vector
        self.requests: list[tuple[str, str]] = []

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        self.requests.append((text, input_type))
        return tuple(self.query_vector)


class FailingEmbeddingProvider:
    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        raise RuntimeError("embedding provider unavailable")


def seed_two_axis_corpus(repository: CaptureRepository) -> None:
    """Three captures: one reachable only by keyword, one only by vector, one by both."""
    seed(
        repository,
        "keyword-only",
        "Red resistor connected near header J4",
        embedding=vector(KEYWORD_AXIS),
        created_at="2026-08-26T10:00:00Z",
    )
    seed(
        repository,
        "semantic-only",
        "A soldering iron rests on the bench",
        embedding=vector(SEMANTIC_AXIS),
        created_at="2026-08-26T11:00:00Z",
    )
    seed(
        repository,
        "both-arms",
        "Header J4 soldered onto the board",
        embedding=vector(SEMANTIC_AXIS),
        created_at="2026-08-26T12:00:00Z",
    )


def make_search(
    repository: CaptureRepository,
    *,
    query_vector: list[float] | None = None,
    max_semantic_distance: float = 0.6,
) -> CaptureSearch:
    return CaptureSearch(
        repository,
        StubEmbeddingProvider(query_vector or vector(SEMANTIC_AXIS)),
        max_semantic_distance=max_semantic_distance,
    )


def test_search_merges_keyword_and_semantic_arms_into_one_list(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = make_search(repository).search("J4 header")

    matched = {result.capture_id: result.matched_by for result in response.results}
    assert matched == {
        "both-arms": MatchSource.BOTH,
        "keyword-only": MatchSource.KEYWORD,
        "semantic-only": MatchSource.SEMANTIC,
    }
    assert response.is_no_match is False
    assert response.note is None


def test_a_capture_found_by_both_arms_outranks_single_arm_matches(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = make_search(repository).search("J4 header")

    assert response.results[0].capture_id == "both-arms"
    assert response.results[0].matched_by == MatchSource.BOTH
    assert response.results[0].score > response.results[1].score


def test_query_is_embedded_as_a_query_not_a_document(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)
    provider = StubEmbeddingProvider(vector(SEMANTIC_AXIS))

    CaptureSearch(repository, provider).search("J4 header")

    assert provider.requests == [("J4 header", "query")]


def test_captures_without_embeddings_still_match_by_keyword(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed(repository, "unembedded", "Header J4 has a cold solder joint", embedding=None)

    response = make_search(repository).search("J4 header")

    assert [result.capture_id for result in response.results] == ["unembedded"]
    assert response.results[0].matched_by == MatchSource.KEYWORD


def test_default_limit_is_five(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    for index in range(8):
        seed(
            repository,
            f"capture-{index}",
            f"Header J4 inspected on pass {index}",
            embedding=vector(SEMANTIC_AXIS),
            created_at=f"2026-08-26T1{index}:00:00Z",
        )

    response = make_search(repository).search("J4 header")

    assert len(response.results) == 5


def test_explicit_limit_is_honoured(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = make_search(repository).search("J4 header", limit=2)

    assert len(response.results) == 2


def test_limit_below_one_is_rejected(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)

    with pytest.raises(ValueError, match="at least 1"):
        make_search(repository).search("J4 header", limit=0)


def test_no_match_returns_an_explicit_response(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = make_search(repository, query_vector=vector(UNRELATED_AXIS)).search("xylophone")

    assert response.is_no_match is True
    assert response.results == ()
    assert response.note is not None
    assert "xylophone" in response.note


def test_low_confidence_semantic_hits_are_cut_by_the_distance_floor(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    # The query vector is orthogonal to every stored caption and shares no
    # vocabulary with any of them, so only the floor can exclude it.
    strict = make_search(repository, query_vector=vector(UNRELATED_AXIS), max_semantic_distance=0.6)
    permissive = make_search(
        repository, query_vector=vector(UNRELATED_AXIS), max_semantic_distance=2.0
    )

    assert strict.search("xylophone").is_no_match is True
    assert permissive.search("xylophone").is_no_match is False


def test_blank_query_returns_no_match_without_touching_the_provider(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)
    provider = StubEmbeddingProvider(vector(SEMANTIC_AXIS))

    response = CaptureSearch(repository, provider).search("   ")

    assert response.is_no_match is True
    assert provider.requests == []


def test_natural_language_punctuation_does_not_break_the_fts_parser(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = make_search(repository).search('what\'s the "J4" header? (urgent) AND OR NEAR *')

    assert "both-arms" in {result.capture_id for result in response.results}


def test_query_embedding_failure_degrades_to_keyword_only(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = CaptureSearch(repository, FailingEmbeddingProvider()).search("J4 header")

    assert {result.capture_id for result in response.results} == {"both-arms", "keyword-only"}
    assert all(result.matched_by == MatchSource.KEYWORD for result in response.results)
    assert response.note is not None
    assert "keyword" in response.note


def test_no_match_note_explains_a_degraded_semantic_arm(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed_two_axis_corpus(repository)

    response = CaptureSearch(repository, FailingEmbeddingProvider()).search("xylophone")

    assert response.is_no_match is True
    assert response.note is not None
    assert "xylophone" in response.note
    assert "keyword" in response.note


def test_captures_that_are_not_ready_never_surface(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed(repository, "still-pending", None, state=CaptureState.PENDING)
    seed(repository, "no-caption", None, embedding=None)

    response = make_search(repository).search("J4 header")

    assert response.is_no_match is True


def test_five_results_stay_within_the_response_token_budget(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    long_summary = "Header J4 " + "with a long observed detail about the board " * 12
    for index in range(5):
        seed(
            repository,
            f"capture-{index}",
            f"{long_summary}\nDetails: {'more text ' * 50}",
            embedding=vector(SEMANTIC_AXIS),
            created_at=f"2026-08-26T1{index}:00:00Z",
        )

    response = make_search(repository).search("J4 header")

    rendered = "\n".join(
        f"{result.capture_id} {result.short_id} {result.created_at} "
        f"{result.matched_by} {result.score} {result.snippet}"
        for result in response.results
    )
    assert len(response.results) == 5
    assert len(rendered) / 4 < 1500


def test_snippet_uses_the_summary_line_and_is_bounded(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed(
        repository,
        "capture-1",
        "Header J4 summary line\nDetails: a much longer body that should not appear",
        embedding=vector(SEMANTIC_AXIS),
    )

    response = make_search(repository).search("J4 header")

    assert response.results[0].snippet == "Header J4 summary line"


def test_results_carry_a_short_id_matching_the_ingest_response(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    seed(repository, "abcdef0123456789", "Header J4 on the board", embedding=vector(SEMANTIC_AXIS))

    response = make_search(repository).search("J4 header")

    assert response.results[0].short_id == "abcdef01"


def test_fts_query_builder_strips_syntax_and_short_tokens() -> None:
    assert to_fts_match_query('what\'s the "J4" header?') == '"what" OR "the" OR "J4" OR "header"'
    assert to_fts_match_query("a b c") is None
    assert to_fts_match_query("!!!") is None
    assert to_fts_match_query("") is None
