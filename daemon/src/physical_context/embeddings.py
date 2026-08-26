import math
from collections.abc import Sequence
from typing import Literal, Protocol

EMBEDDING_DIMENSIONS = 512
EmbeddingInputType = Literal["document", "query"]


class EmbeddingProvider(Protocol):
    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]: ...


class EmbeddingProviderError(RuntimeError):
    pass


class EmbeddingValidationError(ValueError):
    pass


class UnavailableEmbeddingProvider:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        raise EmbeddingProviderError(self.reason)


def validate_embedding(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != EMBEDDING_DIMENSIONS:
        raise EmbeddingValidationError(
            f"Embedding must contain {EMBEDDING_DIMENSIONS} values, got {len(values)}"
        )

    embedding = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in embedding):
        raise EmbeddingValidationError("Embedding values must be finite")
    return embedding
