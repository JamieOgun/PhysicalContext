from physical_context.anthropic_caption import AnthropicCaptionProvider
from physical_context.captions import CaptionProvider, UnavailableCaptionProvider
from physical_context.config import Settings
from physical_context.embeddings import EmbeddingProvider, UnavailableEmbeddingProvider
from physical_context.voyage_embedding import VoyageEmbeddingProvider


def build_caption_provider(settings: Settings) -> CaptionProvider:
    if settings.local_caption:
        return UnavailableCaptionProvider("Local captioning is deferred to T-015")

    api_key = settings.anthropic_api_key
    model = settings.anthropic_model
    if api_key is None or model is None or not model.strip():
        return UnavailableCaptionProvider(
            "Set PCL_ANTHROPIC_API_KEY and PCL_ANTHROPIC_MODEL to enable captioning"
        )

    return AnthropicCaptionProvider(api_key=api_key.get_secret_value(), model=model)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.local_embed:
        return UnavailableEmbeddingProvider("Local embedding is deferred to T-016")

    api_key = settings.voyage_api_key
    model = settings.voyage_model
    if api_key is None or model is None or not model.strip():
        return UnavailableEmbeddingProvider(
            "Set PCL_VOYAGE_API_KEY and PCL_VOYAGE_MODEL to enable embeddings"
        )

    return VoyageEmbeddingProvider(api_key=api_key.get_secret_value(), model=model)
