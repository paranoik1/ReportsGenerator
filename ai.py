import os
from os.path import join as path_join
from pathlib import Path

import markdown
from dotenv import load_dotenv
from openai import OpenAI
from openai.types import ReasoningEffort
from pypandoc import convert_file
from pypdf import PdfReader
from dataclasses import dataclass, field
from functools import cached_property

from utils.md2docx import html_to_docx

load_dotenv()


@dataclass
class Model:
    name: str
    system_prompt_file: str
    reasoning_effort: ReasoningEffort = 'medium'
    temperature: float | None = None

    @cached_property
    def system_prompt(self):
        with open(self.system_prompt_file) as fp:
            return fp.read()


@dataclass
class Document:
    filepath: str
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
class StateAgents:
    task_id: str
    user_prompt: str

    report_markdown: str | None = None
    report_html_path: str | None = None
    report_docx_path: str | None = None

    iteration: int = 0
    max_iterations: int = 10
    finished: bool = False
    documents: list[Document] = field(default_factory=list)
    template: Document | None = None


def prompt_path_file(prompt_file_name):
    return path_join('prompts', prompt_file_name)


class ReportGeneratorLLM:
    MODELS_ROLES = {
        "document_analyst": Model(
            name="kimi-k2.5:cloud",
            system_prompt_file=prompt_path_file("document_analyst.md"),
        ),

        "formatter": Model(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("formatter.md"),
        )
    }

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434/v1", api_key: str = "ollama"
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def run(self, state: StateAgents) -> StateAgents:
        """
        Основной цикл выполнения задачи.

        Возвращает состояние с результатами работы всех агентов.
        """
        self.formatter_agent(state)
        state.finished = True

        return state

    def generate_report(
        self,
        user_prompt: str,
        file_paths: list[str],
        template_path: str | None = None,
        task_id: str = "default",
        output_dir: str = "tmp",
    ) -> StateAgents:
        """
        Генерирует отчет на основе предоставленных файлов.

        Args:
            user_prompt: Задача от пользователя
            file_paths: Пути к файлам с документами
            template_path: Путь к файлу с примером отчета (опционально)
            task_id: Идентификатор задачи
            output_dir: Директория для сохранения результатов

        Returns:
            StateAgents с результатами генерации (report_markdown, report_html_path, report_docx_path)
        """
        os.makedirs(output_dir, exist_ok=True)

        documents = [Document(filepath=path) for path in file_paths]
        template = Document(filepath=template_path) if template_path else None

        state = StateAgents(
            task_id=task_id,
            user_prompt=user_prompt,
            documents=documents,
            template=template,
        )

        state = self.run(state)

        if not state.report_markdown:
            raise ValueError("Отчет не был сгенерирован")

        html_path = self._save_html(state.report_markdown, task_id, output_dir)
        docx_path = self._save_docx(html_path, task_id, output_dir)

        state.report_html_path = html_path
        state.report_docx_path = docx_path

        return state

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

    def run_agent(self, model: Model, messages: list[dict], **response_create_kwargs) -> str:
        response = self.client.chat.completions.create(
            model=model.name,
            messages=messages, # type: ignore
            reasoning_effort=model.reasoning_effort,
            temperature=model.temperature,
            **response_create_kwargs
        )

        return response.choices[0].message.content or ""

    def formatter_agent(self, state: StateAgents) -> str:
        """
        Формирует финальный markdown отчёт и сохраняет его в StateAgents
        """
        model = self.MODELS_ROLES["formatter"]

        docs_context = "\n\n".join(
            doc.content for doc in state.documents
        )

        template_context = ""
        if state.template:
            template_context = f"\n\nПример отчета (ориентируйся на эту структуру):\n{state.template.content}"

        full_system_prompt = model.system_prompt.format(
            docs_context=docs_context or "Нет документов",
            codes_context="",
            diagrams_context="",
            template_context=template_context,
        )

        user_message = {
            "role": "user",
            "content": f"""Задача пользователя:
{state.user_prompt}

Сформируй структурированный отчёт по выполненной работе.
{template_context}
"""
        }
        messages = [{"role": "system", "content": full_system_prompt}, user_message]

        report = self.run_agent(model, messages)
        state.report_markdown = report

        return report


if __name__ == '__main__':
    pass
