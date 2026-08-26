from typing import Protocol

import voyageai

from physical_context.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingInputType,
    EmbeddingProviderError,
    validate_embedding,
)


class _EmbeddingResponse(Protocol):
    embeddings: list[list[float]]


class _VoyageClient(Protocol):
    def embed(
        self,
        texts: list[str],
        *,
        model: str,
        input_type: str,
        truncation: bool,
        output_dimension: int,
    ) -> _EmbeddingResponse: ...


class VoyageEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: _VoyageClient | None = None,
    ) -> None:
        self.model = model
        self.client = client or voyageai.Client(
            api_key=api_key,
            max_retries=2,
            timeout=30.0,
        )

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        if not text.strip():
            raise EmbeddingProviderError("Embedding text must not be blank")

        response = self.client.embed(
            [text],
            model=self.model,
            input_type=input_type,
            truncation=True,
            output_dimension=EMBEDDING_DIMENSIONS,
        )
        if len(response.embeddings) != 1:
            raise EmbeddingProviderError("Voyage returned an unexpected embedding count")
        return validate_embedding(response.embeddings[0])
