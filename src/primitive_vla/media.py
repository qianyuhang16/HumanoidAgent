"""Portable multimodal references shared by tools, observations and backends.

The core describes resources; adapters decide how to load each modality.
"""

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, JsonValue, model_serializer

from primitive_vla.contracts import Message


class Artifact(Message):
    kind: str = Field(min_length=1, description="image, point_cloud, depth, tensor, audio, or an adapter-defined kind")
    uri: str = Field(min_length=1, description="Resource reference understood by the receiving adapter")
    media_type: str | None = None
    metadata: dict[str, JsonValue] | None = None

    @model_serializer(mode="wrap")
    def compact(self, handler):
        # Keep explicitly supplied nulls; omit unused optional metadata on the wire.
        return {key: value for key, value in handler(self).items()
                if key in self.model_fields_set or key in ("kind", "uri")}


# Null slots mean unavailable resources; a null collection means no resources.
# Legacy paths stay strings so old observations and snapshots remain comparable.
Artifacts = Annotated[dict[str, Artifact | str | None] | None,
                      AfterValidator(lambda value: {} if value is None else value)]


def artifact_uri(value):
    if isinstance(value, str):
        return value
    if isinstance(value, Artifact):
        return value.uri
    if isinstance(value, dict):
        return value.get("uri")
    return None


def image_refs(artifacts):
    """Project only actual image references, never depth/tensors/point clouds."""
    images = {}
    for name, value in (artifacts or {}).items():
        uri = artifact_uri(value)
        if not uri:
            continue
        if isinstance(value, str):
            is_image = urlsplit(uri).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        else:
            kind = value.kind if isinstance(value, Artifact) else value.get("kind")
            is_image = kind == "image"
        if is_image:
            images[name] = uri
    return images
