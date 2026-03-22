import json
from dataclasses import dataclass
from functools import cached_property
from os.path import join as path_join
from pathlib import Path
from typing import Literal, Type, TypeVar

from openai import OpenAI
from openai.types import ReasoningEffort

from models import Document, StateAgents
from utils.data_block_registry import BaseModel, DataBlocksRegistry, DataBlockWithId

T = TypeVar("T", bound=BaseModel)


class ListDataBlocks(BaseModel):
    """
    Нужен для OpenAI метода parse, который требует Pydantic Model в text_format (не принимает list[DataBlockWithId])
    """

    blocks: list[DataBlockWithId]


@dataclass
class AiModel:
    name: str
    system_prompt_file: str
    reasoning_effort: ReasoningEffort = "medium"
    temperature: float | None = None

    @cached_property
    def system_prompt(self):
        with open(self.system_prompt_file) as fp:
            return fp.read()


def prompt_path_file(prompt_file_name):
    return path_join("prompts", prompt_file_name)


class LLMPipeline:
    MODELS_ROLES = {
        "document_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("document_analyst.md"),
        ),
        "template_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("template_analyst.md"),
        ),
        "user_prompt_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_file=prompt_path_file("user_prompt_analyst.md"),
        ),
        "planner": AiModel(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("planner.md"),
            reasoning_effort="high",
        ),
        "formatter": AiModel(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("formatter.md"),
        ),
    }

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str = "ollama",
        output_dir: str = "/tmp",
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.output_dir = Path(output_dir)

    def _documents_summirize(
        self, docs: list[Document], blocks_ids_context: str = ""
    ) -> list[DataBlockWithId]:
        model = self.MODELS_ROLES["document_analyst"]

        full_system_prompt = model.system_prompt.format(
            task="Создать план по написанию отчета по практической работе",
            blocks_ids_context=blocks_ids_context,
        )
        system_message = {"role": "system", "content": full_system_prompt}

        blocks: list[DataBlockWithId] = []
        for doc in docs:
            messages = [
                system_message,
                {"role": "user", "content": f"Документ:\n{doc.content}"},
            ]
            list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)
            blocks += list_blocks.blocks

        return blocks

    def _template_specs_extract(self, template: Document) -> DataBlockWithId:
        model = self.MODELS_ROLES["template_analyst"]

        full_system_prompt = model.system_prompt.format(
            task="Создать план по написанию отчета по практической работе"
        )
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": f"Документ:\n{template.content}"},
        ]

        block = self.run_agent_structured(
            model, messages, response_model=DataBlockWithId
        )
        # block_dict = json.loads(block_json)
        block.id = "template_specs"

        return block

    def _user_prompt_data_extract(
        self, user_prompt: str, blocks_ids_context: str = ""
    ) -> list[DataBlockWithId]:
        model = self.MODELS_ROLES["user_prompt_analyst"]

        full_system_prompt = model.system_prompt.format(
            task="Создать план по написанию отчета по практической работе",
            blocks_ids_context=blocks_ids_context,
        )
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)

        return list_blocks.blocks

    def _save_data_blocks(self, data_blocks_registry: DataBlocksRegistry):
        blocks = [
            DataBlockWithId(
                id=id, description=block.description, content=block.content
            ).model_dump()
            for id, block in data_blocks_registry.blocks.items()
        ]
        with open(self.output_dir / "data_blocks.json", "w") as fp:
            json.dump(blocks, fp, ensure_ascii=False, indent=4)

    def fill_data_blocks_registry(self, state: StateAgents):
        dbr = state.data_blocks_registry

        if state.template:
            template_data_block = self._template_specs_extract(state.template)
            dbr.add_block_from_dto(template_data_block)

        docs_summirized_blocks = self._documents_summirize(
            state.documents, dbr.get_blocks_context()
        )
        for block in docs_summirized_blocks:
            dbr.add_block_from_dto(block)

        user_data_blocks = self._user_prompt_data_extract(
            state.user_prompt, dbr.get_blocks_context()
        )

        for block in user_data_blocks:
            dbr.add_block_from_dto(block)

        self._save_data_blocks(dbr)

    def formatter_agent(self, state: StateAgents) -> str:
        """
        Формирует финальный markdown отчёт и сохраняет его в StateAgents
        """
        model = self.MODELS_ROLES["formatter"]

        docs_context = "\n\n".join(doc.content for doc in state.documents)

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
            template_context=template_context or "Нет примера отчета",
            images_context=images_context or "Пользователь не предоставил изображений",
        )

        user_message = {"role": "user", "content": state.user_prompt}
        messages = [{"role": "system", "content": full_system_prompt}, user_message]

        report = self.run_agent(model, messages)
        state.report_markdown = report

        return report

    def _execute_request(
        self,
        model: AiModel,
        messages: list[dict],
        method_name: Literal["parse", "create"],
        response_format: Type[BaseModel] | None = None,
        **kwargs,
    ):
        """
        Приватный метод для общей логики запроса
        """
        openai_method = getattr(self.client.responses, method_name)

        # Формируем аргументы
        call_kwargs = {
            "model": model.name,
            "input": messages,
            "reasoning": {"effort": model.reasoning_effort},
            "temperature": model.temperature,
            **kwargs,
        }

        # Если это parse, нужно передать формат (зависит от версии API, но логически так)
        if method_name == "parse" and response_format:
            call_kwargs["text_format"] = (
                response_format  # или другой параметр в зависимости от API
            )

        return openai_method(**call_kwargs)

    def run_agent(self, model: AiModel, messages: list[dict], **kwargs) -> str:
        """
        Метод для обычного текста
        """
        response = self._execute_request(
            model=model, messages=messages, method_name="create", **kwargs
        )

        return response.output

    def run_agent_structured(
        self, model: AiModel, messages: list[dict], response_model: Type[T], **kwargs
    ) -> T:
        """
        Метод для структурированного ответа
        """
        response = self._execute_request(
            model=model,
            messages=messages,
            method_name="parse",
            response_format=response_model,
            **kwargs,
        )

        if response.output_parsed is None:
            raise ValueError("Failed to parse structured output")

        return response.output_parsed

    def planner_agent(self, state: StateAgents):
        model = self.MODELS_ROLES["planner"]

    def run(self, state: StateAgents) -> StateAgents:
        """
        Основной цикл выполнения задачи.

        Возвращает состояние с результатами работы всех агентов.
        """
        self.fill_data_blocks_registry(state)
        self.formatter_agent(state)
        state.finished = True

        return state
