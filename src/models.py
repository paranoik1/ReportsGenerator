from pypandoc import convert_file
from pypdf import PdfReader
from dataclasses import dataclass, field
from utils.data_block_registry import DataBlocksRegistry
from pathlib import Path

FilePath = str | Path


@dataclass
class Document:
    filepath: FilePath
    content: str = field(default="", init=False)

    def __post_init__(self):
        self.content = self._extract_text()

    def _extract_text(self) -> str:
        """Извлекает текст из файла в зависимости от расширения."""
        ext = Path(self.filepath).suffix.lower()

        if ext == ".pdf":
            reader = PdfReader(self.filepath)
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        elif ext == ".docx":
            return convert_file(
                self.filepath, "markdown-simple_tables-grid_tables-multiline_tables"
            )

        elif ext in [".txt", ".md"]:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return f.read()

        else:
            raise ValueError(f"Unsupported file type: {ext}")


@dataclass
class ImageDocument:
    """Изображение с описанием от пользователя."""
    filepath: FilePath
    description: str



@dataclass
class StateAgents:
    task_id: str
    user_prompt: str

    data_blocks_registry: DataBlocksRegistry = field(default_factory=DataBlocksRegistry)
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

