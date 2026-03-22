import json
import os
from dataclasses import asdict
from pathlib import Path

import markdown

from models import Document, ImageDocument, StateAgents
from orchestrator import Orchestrator
from utils.md2docx import html_to_docx


class ReportGenerator:
    def generate_report(
        self,
        user_prompt: str,
        file_paths: list[str],
        template_path: str | None = None,
        images: list[tuple[str, str]] | None = None,
        task_id: str = "default",
        output_dir: str = "tmp",
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
        os.makedirs(output_dir, exist_ok=True)

        documents = [Document(filepath=path) for path in file_paths]
        template = Document(filepath=template_path) if template_path else None
        image_docs = [
            ImageDocument(filepath=path, description=desc)
            for path, desc in (images or [])
        ]

        state = StateAgents(
            task_id=task_id,
            user_prompt=user_prompt,
            documents=documents,
            template=template,
            images=image_docs,
        )

        llm_pipeline = Orchestrator(output_dir)
        llm_pipeline.run(state)

        if not state.report_markdown:
            raise ValueError("Отчет не был сгенерирован")

        markdown_path = self._save_markdown(state.report_markdown, task_id, output_dir)
        html_path = self._save_html(state.report_markdown, task_id, output_dir)
        docx_path = self._save_docx(html_path, task_id, output_dir)

        state.report_markdown_path = markdown_path
        state.report_html_path = html_path
        state.report_docx_path = docx_path

        return state

    def _save_markdown(
        self, markdown_content: str, task_id: str, output_dir: str
    ) -> str:
        md_path = os.path.join(output_dir, f"{task_id}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return md_path

    def _save_html(self, markdown_content: str, task_id: str, output_dir: str) -> str:
        """Сохраняет отчет в HTML формате."""
        html_content = markdown.markdown(
            markdown_content, extensions=["extra", "sane_lists", "nl2br"]
        )

        html_path = os.path.join(output_dir, f"{task_id}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return html_path

    def _save_docx(self, html_path: str, task_id: str, output_dir: str) -> str:
        """Сохраняет отчет в DOCX формате."""
        docx_path = os.path.join(output_dir, f"{task_id}.docx")
        html_to_docx(html_path, docx_path)
        return docx_path
