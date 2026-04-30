from dataclasses import dataclass
from pathlib import Path

import structlog

FilePath = str | Path


logger = structlog.get_logger(__name__)


@dataclass
class Document:
    filepath: FilePath
    content: str


@dataclass(frozen=True, slots=True)
class ImageDocument:
    """Изображение с описанием от пользователя."""

    filepath: FilePath
    description: str
