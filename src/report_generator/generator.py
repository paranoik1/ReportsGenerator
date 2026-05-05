import subprocess
import structlog
from pathlib import Path
from bs4 import BeautifulSoup
from magic import Magic
from pypandoc import convert_file  # type: ignore
from pypdf import PdfReader
from typing import Literal

from .md2docx import html_to_docx, markdown_to_html_safe
from .orchestrator import Orchestrator
from .orchestrator.models import AgentConfigs, Document, ImageDocument, StateAgents

logger = structlog.get_logger(__name__)

_magic = Magic(mime=True)


def __clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["img", "head"]):
        tag.decompose()

    return soup.prettify()

def __soffice_exec(filepath: Path, convert_to: str) -> str:
    workdir = filepath.parent
    cmd = f"soffice --headless --convert-to {convert_to} {filepath} --outdir {workdir}".split()

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
    
    return output

def _soffice_extract_html(filepath: Path) -> str:
    _ = __soffice_exec(filepath, "html:HTML:EmbedImages")

    html_filepath = filepath.with_suffix(".html")
    with open(html_filepath) as fp:
        content_html = fp.read()

    cleaned_html = __clean_html(content_html)
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


def _soffice_convert_to_docx(doc_filepath: Path) -> Path:
    _ = __soffice_exec(doc_filepath, "docx")
    docx_path = doc_filepath.with_suffix('.docx')
    if not docx_path.exists():
        raise FileNotFoundError('Не найден сконвертированный docx файл: ' + str(docx_path))
    return docx_path


def extract_text(filepath: Path, extractor: Literal["default", "soffice"] = "default"
) -> str:
    """Извлекает текст из файла в зависимости от расширения."""
    mime_type = _magic.from_file(filepath)

    if extractor == "soffice":
        if mime_type not in {
            "application/vnd.oasis.opendocument.text",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }:
            raise ValueError(
                "Extractor soffice поддерживает только файлы документов"
            )

        return _soffice_extract_html(filepath)

    if mime_type == "application/msword":
        filepath = _soffice_convert_to_docx(filepath)

    if mime_type == "application/pdf":
        reader = PdfReader(filepath)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    elif mime_type in {
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return convert_file(
            filepath,
            "markdown-simple_tables-grid_tables-multiline_tables-link_attributes-raw_html",
        )
    elif mime_type.startswith("text/") or (
        mime_type.startswith("application/") and "json" in mime_type
    ):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    raise ValueError(f"Unsupported file type: {mime_type} - {filepath}")


class ReportGenerator:
    def __init__(
        self,
        task_id: str,
        output_dir: str | Path,
        agent_configs: AgentConfigs | None = None,
    ) -> None:
        self.log = logger.bind(task_id=task_id)
        self.task_id = task_id
        self.output_dir = Path(output_dir)
        self.agent_configs = agent_configs

    def generate_report(
        self,
        user_prompt: str,
        file_paths: list[str],
        template_path: str | None = None,
        images: list[tuple[str, str]] | None = None,
    ) -> StateAgents:
        """
        Генерирует отчет на основе предоставленных файлов.

        Args:
            user_prompt: Задача от пользователя
            file_paths: Пути к файлам с документами
            template_path: Путь к файлу с примером отчета (опционально)
            images: Список кортежей (путь_к_файлу, описание)
            output_dir: Директория для сохранения результатов

        Returns:
            StateAgents с результатами генерации (report_markdown, report_html_path, report_docx_path)
        """
        self.log.debug(
            "start_generate_report",
            user_prompt_len=len(user_prompt),
            output_dir=str(self.output_dir),
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)

        documents: list[Document] = []
        for path in file_paths:
            try:
                content = extract_text(Path(path))
                doc = Document(filepath=path, content=content)
                documents.append(doc)
            except (ValueError, subprocess.CalledProcessError):
                self.log.exception('extract_text_failed', file_path=path)

        image_docs = [
            ImageDocument(filepath=path, description=desc)
            for path, desc in (images or [])
        ]

        state = StateAgents(
            task_id=self.task_id,
            user_prompt=user_prompt,
            documents=documents,
            images=image_docs,
        )

        with Orchestrator(
            self.output_dir,
            task_id=self.task_id,
            agent_configs=self.agent_configs,
        ) as llm_pipeline:
            llm_pipeline.run(state)

        if not state.report_markdown:
            self.log.error("report_markdown is None")
            raise ValueError("Отчет не был сгенерирован")

        markdown_path = self._save_markdown(state.report_markdown)
        html_path = self._save_html(state.report_markdown)
        docx_path = self._save_docx(html_path)

        state.report_markdown_path = str(markdown_path)
        state.report_html_path = str(html_path)
        state.report_docx_path = str(docx_path)

        return state

    def _save_markdown(self, markdown_content: str) -> Path:
        md_path = self.output_dir / f"{self.task_id}.md"
        md_path.write_text(markdown_content, encoding="utf-8")

        self.log.debug(
            "saved_markdown", path=str(md_path), content_len=len(markdown_content)
        )

        return md_path

    def _save_html(self, markdown_content: str) -> Path:
        """Сохраняет отчет в HTML формате."""

        html_content = markdown_to_html_safe(markdown_content)

        html_path = self.output_dir / f"{self.task_id}.html"
        html_path.write_text(html_content, encoding="utf-8")

        self.log.debug("saved_html", path=str(html_path), content_len=len(html_content))

        return html_path

    def _save_docx(self, html_path: Path) -> Path:
        """Сохраняет отчет в DOCX формате."""
        docx_path = self.output_dir / f"{self.task_id}.docx"
        html_to_docx(str(html_path), str(docx_path))

        self.log.debug("saved_docx", path=str(docx_path))

        return docx_path
