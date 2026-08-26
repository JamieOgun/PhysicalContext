import base64
from pathlib import Path
from typing import Protocol

from anthropic import Anthropic

from physical_context.captions import CaptionProviderError, StructuredCaption

SYSTEM_PROMPT = """You create factual visual context for later retrieval.
Describe only what is observable in the image. Do not assume the scene belongs to a
particular domain. Record uncertainty instead of guessing. Report changes only when they
are supported by the supplied previous-caption context."""


class _Messages(Protocol):
    def parse(self, **kwargs: object) -> object: ...


class _AnthropicClient(Protocol):
    messages: _Messages


class AnthropicCaptionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: _AnthropicClient | None = None,
    ) -> None:
        self.model = model
        self.client = client or Anthropic(api_key=api_key, timeout=30.0)

    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        previous_context = previous_caption or "No previous caption is available."

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": encoded_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Describe this capture using the requested schema.\n\n"
                                f"Previous caption:\n{previous_context}"
                            ),
                        },
                    ],
                }
            ],
            output_format=StructuredCaption,
        )
        parsed_output = getattr(response, "parsed_output", None)
        if parsed_output is None:
            raise CaptionProviderError("Anthropic returned no structured caption")

        try:
            return StructuredCaption.model_validate(parsed_output)
        except ValueError as error:
            raise CaptionProviderError(
                "Anthropic returned an invalid structured caption"
            ) from error
