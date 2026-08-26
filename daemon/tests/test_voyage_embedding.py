from types import SimpleNamespace

import pytest

from physical_context.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProviderError,
    EmbeddingValidationError,
)
from physical_context.voyage_embedding import VoyageEmbeddingProvider


class FakeClient:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings
        self.request: dict[str, object] | None = None

    def embed(self, texts: list[str], **kwargs: object) -> object:
        self.request = {"texts": texts, **kwargs}
        return SimpleNamespace(embeddings=self.embeddings)


def make_vector(value: float = 0.5, size: int = EMBEDDING_DIMENSIONS) -> list[float]:
    return [value] * size


def test_provider_requests_the_configured_model_at_the_schema_dimension() -> None:
    client = FakeClient([make_vector()])
    provider = VoyageEmbeddingProvider(api_key="test-key", model="test-model", client=client)

    result = provider.embed("A person sits in front of closed curtains.", input_type="document")

    assert result == tuple(make_vector())
    assert client.request == {
        "texts": ["A person sits in front of closed curtains."],
        "model": "test-model",
        "input_type": "document",
        "truncation": True,
        "output_dimension": EMBEDDING_DIMENSIONS,
    }


def test_provider_passes_query_input_type_through() -> None:
    client = FakeClient([make_vector()])
    provider = VoyageEmbeddingProvider(api_key="test-key", model="test-model", client=client)

    provider.embed("soldering iron", input_type="query")

    assert client.request is not None
    assert client.request["input_type"] == "query"


def test_provider_rejects_blank_text_before_calling_the_api() -> None:
    client = FakeClient([make_vector()])
    provider = VoyageEmbeddingProvider(api_key="test-key", model="test-model", client=client)

    with pytest.raises(EmbeddingProviderError, match="must not be blank"):
        provider.embed("   ", input_type="document")

    assert client.request is None


def test_provider_rejects_unexpected_embedding_count() -> None:
    provider = VoyageEmbeddingProvider(
        api_key="test-key",
        model="test-model",
        client=FakeClient([make_vector(), make_vector()]),
    )

    with pytest.raises(EmbeddingProviderError, match="unexpected embedding count"):
        provider.embed("caption", input_type="document")


def test_provider_rejects_a_vector_that_does_not_match_the_schema() -> None:
    provider = VoyageEmbeddingProvider(
        api_key="test-key",
        model="test-model",
        client=FakeClient([make_vector(size=256)]),
    )

    with pytest.raises(EmbeddingValidationError, match="512"):
        provider.embed("caption", input_type="document")


def test_provider_rejects_non_finite_values() -> None:
    provider = VoyageEmbeddingProvider(
        api_key="test-key",
        model="test-model",
        client=FakeClient([[float("nan")] + make_vector(size=EMBEDDING_DIMENSIONS - 1)]),
    )

    with pytest.raises(EmbeddingValidationError, match="finite"):
        provider.embed("caption", input_type="document")
