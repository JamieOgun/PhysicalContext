import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from physical_context.anthropic_caption import AnthropicCaptionProvider
from physical_context.captions import CaptionProviderError, StructuredCaption


class FakeMessages:
    def __init__(self, parsed_output: object) -> None:
        self.parsed_output = parsed_output
        self.request: dict[str, object] | None = None

    def parse(self, **kwargs: object) -> object:
        self.request = kwargs
        return SimpleNamespace(parsed_output=self.parsed_output)


class FakeClient:
    def __init__(self, parsed_output: object) -> None:
        self.messages = FakeMessages(parsed_output)


def make_caption() -> StructuredCaption:
    return StructuredCaption(
        summary="A desk with a notebook and a mug.",
        details=["The notebook is open."],
        visible_text=["Notes"],
        spatial_relationships=["The mug is to the right of the notebook."],
        changes=[],
        uncertainties=[],
    )


def test_anthropic_provider_sends_image_context_and_schema(tmp_path: Path) -> None:
    image_path = tmp_path / "capture.jpg"
    image_path.write_bytes(b"jpeg-data")
    client = FakeClient(make_caption())
    provider = AnthropicCaptionProvider(api_key="test-key", model="test-model", client=client)

    result = provider.caption(image_path, "A closed notebook was on the desk.")

    assert result == make_caption()
    request = client.messages.request
    assert request is not None
    assert request["model"] == "test-model"
    assert request["output_format"] is StructuredCaption
    content = request["messages"][0]["content"]
    assert content[0]["source"]["data"] == base64.b64encode(b"jpeg-data").decode("ascii")
    assert "A closed notebook was on the desk." in content[1]["text"]


def test_anthropic_provider_rejects_missing_structured_output(tmp_path: Path) -> None:
    image_path = tmp_path / "capture.jpg"
    image_path.write_bytes(b"jpeg-data")
    provider = AnthropicCaptionProvider(
        api_key="test-key",
        model="test-model",
        client=FakeClient(None),
    )

    with pytest.raises(CaptionProviderError, match="no structured caption"):
        provider.caption(image_path, None)
