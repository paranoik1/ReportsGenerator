import os
import json
from os.path import join as path_join
import markdown
from dotenv import load_dotenv
from openai import OpenAI
from openai.types import ReasoningEffort
from pypandoc import convert_file
from pypdf import PdfReader
from pathlib import Path
from dataclasses import dataclass, field, asdict
from functools import cached_property
from typing import TypeVar, Type, Literal

from utils.md2docx import html_to_docx
from utils.data_block_registry import DataBlockWithId, DataBlocksRegistry, BaseModel

# load_dotenv()

FilePath = str | Path
T = TypeVar('T', bound=BaseModel)


class ListDataBlocks(BaseModel):
    blocks: list[DataBlockWithId]


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


def prompt_path_file(prompt_file_name):
    return path_join('prompts', prompt_file_name)


class LLMPipeline:
    MODELS_ROLES = {
        "document_analyst": Model(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("document_analyst.md"),
        ),

        "template_analyst": Model(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("template_analyst.md"),
        ),

        "user_prompt_analyst": Model(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("user_prompt_analyst.md"),
        ),

        "planner": Model(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("planner.md"),
            reasoning_effort='high'
        ),

        "formatter": Model(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("formatter.md"),
        )
    }

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434/v1", api_key: str = "ollama", output_dir: str = '/tmp'
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.output_dir = Path(output_dir)

    def _documents_summirize(self, docs: list[Document], blocks_ids_context: str = "") -> list[DataBlockWithId]:
        model = self.MODELS_ROLES['document_analyst']

        full_system_prompt = model.system_prompt.format(
            task="Создать план по написанию отчета по практической работе",
            blocks_ids_context=blocks_ids_context
        )
        system_message = {'role': 'system', 'content': full_system_prompt}

        blocks: list[DataBlockWithId] = []
        for doc in docs:
            messages = [system_message, {'role': 'user', 'content': f'Документ:\n{doc.content}'}]
            list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)
            blocks += list_blocks.blocks

        return blocks

    def _template_specs_extract(self, template: Document) -> DataBlockWithId:
        model = self.MODELS_ROLES['template_analyst']

        full_system_prompt = model.system_prompt.format(task="Создать план по написанию отчета по практической работе")
        messages = [
            {'role': 'system', 'content': full_system_prompt}, 
            {'role': 'user', 'content': f'Документ:\n{template.content}'}
        ]

        block = self.run_agent_structured(model, messages, response_model=DataBlockWithId)
        # block_dict = json.loads(block_json)
        block.id = 'template_specs'

        return block

    def _user_prompt_data_extract(self, user_prompt: str, blocks_ids_context: str = "") -> list[DataBlockWithId]:
        model = self.MODELS_ROLES['user_prompt_analyst']

        full_system_prompt = model.system_prompt.format(
            task="Создать план по написанию отчета по практической работе",
            blocks_ids_context=blocks_ids_context
        )
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)

        return list_blocks.blocks
    
    def _save_data_blocks(self, data_blocks_registry: DataBlocksRegistry):
        blocks = [
            DataBlockWithId(id=id, description=block.description, content=block.content).model_dump() 
            for id, block in data_blocks_registry.blocks.items()
        ]
        with open(self.output_dir / "data_blocks.json", "w") as fp:
            dump(blocks, fp, ensure_ascii=False, indent=4)

    def fill_data_blocks_registry(self, state: StateAgents):
        dbr = state.data_blocks_registry

        if state.template:
            template_data_block = self._template_specs_extract(state.template)
            dbr.add_block_from_dto(template_data_block)

        docs_summirized_blocks = self._documents_summirize(state.documents, dbr.get_blocks_context())
        for block in docs_summirized_blocks:
            dbr.add_block_from_dto(block)

        user_data_blocks = self._user_prompt_data_extract(state.user_prompt, dbr.get_blocks_context())

        for block in user_data_blocks:
            dbr.add_block_from_dto(block)

        self._save_data_blocks(dbr)
       
    def formatter_agent(self, state: StateAgents) -> str:
        """
        Формирует финальный markdown отчёт и сохраняет его в StateAgents
        """
        model = self.MODELS_ROLES["formatter"]

        docs_context = "\n\n".join(
            doc.content for doc in state.documents
        )

        images_context = ""
        if state.images:
            images_context = "\n\n".join(
                f"Путь к файлу: {img.filepath}\nОписание: {img.description}\n------\n"
                for img in state.images
            )

        template_context = ""
        if state.template:
            template_context = state.template.content

        full_system_prompt = model.system_prompt.format(
            docs_context=docs_context or "Нет документов",
            template_context=template_context or 'Нет примера отчета',
            images_context=images_context or 'Пользователь не предоставил изображений',
        )

        user_message = {
            "role": "user",
            "content": state.user_prompt
        }
        messages = [{"role": "system", "content": full_system_prompt}, user_message]

        report = self.run_agent(model, messages)
        state.report_markdown = report

        return report

    def _execute_request(
        self, 
        model: Model, 
        messages: list[dict], 
        method_name: Literal['parse', 'create'],
        response_format: Type[BaseModel] | None = None,
        **kwargs
    ):
        """
        Приватный метод для общей логики запроса
        """
        openai_method = getattr(self.client.responses, method_name)
        
        # Формируем аргументы
        call_kwargs = {
            'model': model.name,
            'input': messages,
            'reasoning': {'effort': model.reasoning_effort},
            'temperature': model.temperature,
            **kwargs
        }

        # Если это parse, нужно передать формат (зависит от версии API, но логически так)
        if method_name == 'parse' and response_format:
            call_kwargs['text_format'] = response_format # или другой параметр в зависимости от API

        return openai_method(**call_kwargs)

    def run_agent(self, model: Model, messages: list[dict], **kwargs) -> str:
        """
        Метод для обычного текста
        """
        response = self._execute_request(
            model=model, 
            messages=messages, 
            method_name='create', 
            **kwargs
        )

        return response.output 

    def run_agent_structured(self, model: Model, messages: list[dict], response_model: Type[T], **kwargs) -> T:
        """
        Метод для структурированного ответа 
        """
        response = self._execute_request(
            model=model, 
            messages=messages, 
            method_name='parse', 
            response_format=response_model,
            **kwargs
        )

        if response.output_parsed is None:
            raise ValueError("Failed to parse structured output")
            
        return response.output_parsed

    def planner_agent(self, state: StateAgents):
        model = self.MODELS_ROLES['planner']
        
    def run(self, state: StateAgents) -> StateAgents:
        """
        Основной цикл выполнения задачи.

        Возвращает состояние с результатами работы всех агентов.
        """
        self.fill_data_blocks_registry(state)
        self.formatter_agent(state)
        state.finished = True

        return state


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
        image_docs = [ImageDocument(filepath=path, description=desc) for path, desc in (images or [])]

        state = StateAgents(
            task_id=task_id,
            user_prompt=user_prompt,
            documents=documents,
            template=template,
            images=image_docs,
        )

        llm_pipeline = LLMPipeline(output_dir)
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
    
    def _save_markdown(self, markdown_content: str, task_id: str, output_dir: str) -> str:
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


if __name__ == '__main__':
    from pprint import pprint
    from pathlib import Path
    from json import dump, load
    from subprocess import Popen

    llm_pipeline = LLMPipeline()
    task_id = "18ebf7db-284a-4f2a-b9a3-aed1ff5f0c10"

    with open('prompts/users/praktika11.md') as fp:
        user_prompt = fp.read()

    docs = [Document(f"uploads/{task_id}/Pr11_.docx")]
    task_dir = Path('uploads') / task_id
    image_docs_json_filepath = task_dir / 'image_docs.json'
    image_docs = []

    if image_docs_json_filepath.exists():
        with open(image_docs_json_filepath) as fp:
            image_docs_dict = load(fp)
            for image_doc in image_docs_dict:
                image_docs.append(ImageDocument(**image_doc))
    else:
        image_docs_dict = []

        for image in task_dir.glob('*.png'):
            Popen(["viewnior", image])
            description = input(str(image) + ": ")
            image_doc = ImageDocument(task_dir / image, description=description)
            image_docs.append(image_doc)
            image_docs_dict.append(asdict(image_doc))
            
        with open(image_docs_json_filepath, "w") as fp:
            dump(image_docs_dict, fp, ensure_ascii=False, indent=4)

    state = StateAgents(
        task_id='test',
        user_prompt=user_prompt,
        documents=docs,
        template=Document(f"uploads/{task_id}/template_10.docx"),
        images=image_docs,
    )

    llm_pipeline.fill_data_blocks_registry(state)
    blocks = [
        DataBlockWithId(id=id, description=block.description, content=block.content).model_dump() 
        for id, block in state.data_blocks_registry.blocks.items()
    ]
    with open(task_dir / 'data_blocks.json', "w") as fp:
        dump(blocks, fp, ensure_ascii=False, indent=4)
