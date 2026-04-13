import subprocess
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal

import structlog
from bs4 import BeautifulSoup
from pypandoc import convert_file
from pypdf import PdfReader

from utils.data_block_registry import DataBlocksRegistry

FilePath = str | Path
TaskStatus = Literal["queued", "processing", "done", "error"]


logger = structlog.get_logger(__name__)

SUPPORTED_TEXT_EXTENSIONS = [
    ".txt",
    ".md",
    ".html",
    ".py",
    ".sql",
    ".css",
    ".js",
    ".cs",
    ".cpp",
    ".h",
    ".hpp",
    ".log",
    ".json",
    "jsonl",
    ".csv",
]


@dataclass
class Document:
    filepath: FilePath
    extractor: Literal["default", "soffice"] = "default"

    def __post_init__(self):
        self.filepath = Path(self.filepath)

    @cached_property
    def content(self):
        return self._extract_text()

    @staticmethod
    def __clean_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["img", "head"]):
            tag.decompose()

        return soup.prettify()

    def _soffice_extract_text(self) -> str:
        # NOTE: For MYPY
        if not isinstance(self.filepath, Path):
            self.filepath = Path(self.filepath)

        workdir = self.filepath.parent
        cmd = f"soffice --headless --convert-to html:HTML:EmbedImages {self.filepath} --outdir {workdir}".split()

        log = logger.bind(cmd=cmd)

        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        except subprocess.CalledProcessError as exc:
            log.exception(
                "failed_call_soffice", return_code=exc.returncode, output=exc.output
            )
            raise
        else:
            log.info("success_call_soffice", output=output)

        html_filepath = self.filepath.with_suffix(".html")
        with open(html_filepath) as fp:
            content_html = fp.read()

        cleaned_html = self.__clean_html(content_html)
        cleaned_html_filepath = html_filepath.with_suffix(".clean.html")

        with open(cleaned_html_filepath, "w") as fp:
            fp.write(cleaned_html)

        logger.info(
            "clean_html",
            content_cleaned_len=len(cleaned_html),
            content_len=len(content_html),
            cleaned_filepath=cleaned_html_filepath,
        )

        return cleaned_html

    def _extract_text(self) -> str:
        """Извлекает текст из файла в зависимости от расширения."""
        # NOTE: For MYPY
        if not isinstance(self.filepath, Path):
            self.filepath = Path(self.filepath)

        ext = self.filepath.suffix.lower()

        if self.extractor == "soffice":
            if ext not in {".docx", ".odt", ".doc"}:
                raise ValueError(
                    "Extractor soffice поддерживает только файлы документов"
                )

            return self._soffice_extract_text()

        if ext == ".pdf":
            reader = PdfReader(self.filepath)
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        elif ext == ".docx":
            return convert_file(
                self.filepath,
                "markdown-simple_tables-grid_tables-multiline_tables-link_attributes-raw_html",
            )

        elif ext in SUPPORTED_TEXT_EXTENSIONS:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return f.read()

        else:
            raise ValueError(f"Unsupported file type: {ext}")


@dataclass(frozen=True, slots=True)
class ImageDocument:
    """Изображение с описанием от пользователя."""

    filepath: FilePath
    description: str


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


@dataclass
class Task:
    """Задача на генерацию отчёта."""

    task_id: str
    upload_dir: str
    tmp_dir: str
    status: TaskStatus = "queued"
    user_prompt: str = ""
    file_paths: list[str] = field(default_factory=list)
    template_path: str | None = None
    images: list[tuple[str, str]] = field(default_factory=list)

    state: StateAgents | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    _future: Future | None = None
    _worker_thread: str | None = None
