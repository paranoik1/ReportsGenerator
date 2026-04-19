from pathlib import Path

import markdown
import structlog

from models import AgentConfigs, Document, ImageDocument, StateAgents
from orchestrator import Orchestrator
from utils.md2docx import html_to_docx

logger = structlog.get_logger(__name__)


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
            task_id: Идентификатор задачи
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

        documents = [Document(filepath=path) for path in file_paths]
        template = None
        if template_path:
            ext = Path(template_path).suffix.lower()
            # soffice используется только для .docx/.odt/.doc файлов
            # для остальных - дефолтный метод (конвертация через pypandoc или чтение текста)
            extractor = "soffice" if ext in {".docx", ".odt", ".doc"} else "default"
            template = Document(filepath=template_path, extractor=extractor)
        image_docs = [
            ImageDocument(filepath=path, description=desc)
            for path, desc in (images or [])
        ]

        state = StateAgents(
            task_id=self.task_id,
            user_prompt=user_prompt,
            documents=documents,
            template=template,
            images=image_docs,
        )

        llm_pipeline = Orchestrator(
            self.output_dir,
            task_id=self.task_id,
            agent_configs=self.agent_configs,
        )
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
        html_content = markdown.markdown(
            markdown_content, extensions=["extra", "sane_lists", "nl2br"]
        )

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
