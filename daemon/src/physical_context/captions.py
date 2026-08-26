from pathlib import Path
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CaptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StructuredCaption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: CaptionText = Field(description="Concise, factual description of the scene")
    details: list[CaptionText] = Field(description="Important observable details")
    visible_text: list[CaptionText] = Field(description="Text legibly visible in the image")
    spatial_relationships: list[CaptionText] = Field(
        description="Relevant positions and relationships between visible items"
    )
    changes: list[CaptionText] = Field(
        description="Observable differences from the supplied previous capture"
    )
    uncertainties: list[CaptionText] = Field(
        description="Details that cannot be identified confidently"
    )

    def to_search_text(self) -> str:
        sections = [self.summary]
        groups = (
            ("Details", self.details),
            ("Visible text", self.visible_text),
            ("Spatial relationships", self.spatial_relationships),
            ("Changes", self.changes),
            ("Uncertainties", self.uncertainties),
        )
        sections.extend(f"{label}: {'; '.join(values)}" for label, values in groups if values)
        return "\n".join(sections)


class CaptionProvider(Protocol):
    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption: ...


class CaptionProviderError(RuntimeError):
    pass


class UnavailableCaptionProvider:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        raise CaptionProviderError(self.reason)
