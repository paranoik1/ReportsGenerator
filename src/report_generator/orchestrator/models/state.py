from dataclasses import dataclass, field

from .data_block_registry import DataBlocksRegistry
from .documents import Document, ImageDocument


@dataclass
class StateAgents:
    task_id: str
    user_prompt: str
    user_prompt_cleaned: str | None = None

    data_blocks_registry: DataBlocksRegistry = field(default_factory=DataBlocksRegistry)
    report_parts: list[str] = field(default_factory=list)
    report_markdown: str | None = None

    report_markdown_path: str | None = None
    report_html_path: str | None = None
    report_docx_path: str | None = None

    iteration: int = 0
    max_iterations: int = 50

    finished: bool = False
    documents: list[Document] = field(default_factory=list)
    template: Document | None = None
    images: list[ImageDocument] = field(default_factory=list)
