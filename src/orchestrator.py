import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type, TypeVar

import pydantic
import structlog
from openai import OpenAI
from openai.types import ReasoningEffort
from openai.types.chat.chat_completion import ChatCompletion

from models import Document, FilePath, StateAgents
from utils.data_block_registry import BaseModel, DataBlockWithId
from utils.prompt_manager import PromptManager

T = TypeVar("T", bound=BaseModel)


logger = structlog.get_logger(__name__)


class ListDataBlocks(BaseModel):
    """
    Нужен для OpenAI метода parse, который требует Pydantic Model в text_format (не принимает list[DataBlockWithId])
    """

    blocks: list[DataBlockWithId]


# Глобальный менеджер промптов
_prompt_manager = PromptManager()


@dataclass
class AiModel:
    name: str
    system_prompt_template: str
    reasoning_effort: ReasoningEffort = "medium"
    temperature: float | None = None

    def render_system_prompt(self, **context: Any) -> str:
        """Рендерит системный промпт с переданными переменными."""
        return _prompt_manager.render(self.system_prompt_template, **context)


class Orchestrator:
    MODELS_ROLES = {
        "document_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_template="document_analyst.j2",
            temperature=0,
        ),
        "template_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_template="template_analyst.j2",
            temperature=0,
        ),
        "user_prompt_analyst": AiModel(
            name="kimi-k2-thinking:cloud",
            system_prompt_template="user_prompt_analyst.j2",
            temperature=0,
        ),
        "planner": AiModel(
            name="qwen3.5:cloud",
            system_prompt_template="planner.j2",
            reasoning_effort="high",
        ),
        "formatter": AiModel(
            name="qwen3.5:cloud",
            system_prompt_template="formatter.j2",
        ),
    }

    def __init__(
        self,
        output_dir: FilePath,
        task_id: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key: str = "ollama",
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.output_dir = Path(output_dir)

        self.task_id = task_id
        self.log = logger.bind(task_id=self.task_id)

    @staticmethod
    def clean_json_from_markdown(text: str) -> str:
        """Удаляет любые markdown-обёртки вокруг JSON"""
        text = text.strip()

        # Удаляем ``` вокруг json
        text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text, flags=re.IGNORECASE)

        # Найти первый { и последний } (если JSON внутри текста)
        if not text.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                text = match.group(0)

        return text.strip()

    def _documents_summirize(
        self, docs: list[Document], blocks_ids_context: str = ""
    ) -> list[DataBlockWithId]:
        model = self.MODELS_ROLES["document_analyst"]

        full_system_prompt = model.render_system_prompt(
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
            self.log.debug(
                "calling_document_analyst",
                doc_path=doc.filepath,
                prompt_len=len(full_system_prompt),
            )
            list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)
            blocks += list_blocks.blocks

        self.log.info("documents_summarized", blocks_count=len(blocks))
        return blocks

    def _template_specs_extract(self, template: Document) -> DataBlockWithId:
        model = self.MODELS_ROLES["template_analyst"]

        full_system_prompt = model.render_system_prompt(
            task="Создать план по написанию отчета по практической работе"
        )
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": f"Документ:\n{template.content}"},
        ]

        self.log.debug(
            "calling_template_analyst",
            template_path=template.filepath,
            prompt_len=len(full_system_prompt),
        )
        block = self.run_agent_structured(
            model, messages, response_model=DataBlockWithId
        )
        block.id = "template_specs"

        self.log.info("template_specs_extracted")
        return block

    def _user_prompt_data_extract(
        self, user_prompt: str, blocks_ids_context: str
    ) -> list[DataBlockWithId]:
        model = self.MODELS_ROLES["user_prompt_analyst"]

        full_system_prompt = model.render_system_prompt(
            task="Создать план по написанию отчета по практической работе",
            blocks_ids_context=blocks_ids_context,
        )
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        self.log.debug(
            "calling_user_prompt_analyst",
            prompt_len=len(full_system_prompt),
            user_prompt_len=len(user_prompt),
        )
        list_blocks = self.run_agent_structured(model, messages, ListDataBlocks)

        self.log.info("user_prompt_analyzed", blocks_count=len(list_blocks.blocks))
        return list_blocks.blocks

    def fill_data_blocks_registry(self, state: StateAgents):
        self.log.info("fill_data_blocks_registry_start")
        dbr = state.data_blocks_registry

        if state.template:
            template_data_block = self._template_specs_extract(state.template)
            dbr.add_block_from_dto(template_data_block)

        blocks_ids = dbr.get_blocks().keys()
        docs_summirized_blocks = self._documents_summirize(
            state.documents, ", ".join(blocks_ids)
        )
        for block in docs_summirized_blocks:
            dbr.add_block_from_dto(block)

        blocks_ids = dbr.get_blocks().keys()
        user_data_blocks = self._user_prompt_data_extract(
            state.user_prompt, ", ".join(blocks_ids)
        )

        for block in user_data_blocks:
            dbr.add_block_from_dto(block)

        data_blocks_path = self.output_dir / "data_blocks.json"
        dbr.save(data_blocks_path)
        self.log.info(
            "data_blocks_saved",
            blocks_count=len(dbr.get_blocks()),
            path=str(data_blocks_path),
        )

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

        full_system_prompt = model.render_system_prompt(
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
        is_json: bool = False,
        **kwargs,
    ) -> ChatCompletion:
        """
        Приватный метод для общей логики запроса
        """
        if is_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(
            model=model.name,
            messages=messages,  # type: ignore
            reasoning_effort=model.reasoning_effort,
            temperature=model.temperature,
            **kwargs,
        )

        return response

    def run_agent(self, model: AiModel, messages: list[dict], **kwargs) -> str:
        """
        Метод для обычного текста
        """
        response = self._execute_request(model=model, messages=messages, **kwargs)

        answer = response.choices[0].message.content
        if answer is None:
            raise ValueError("Ответ пустой")

        return answer

    def run_agent_structured(
        self, model: AiModel, messages: list[dict], response_model: Type[T], **kwargs
    ) -> T:
        """
        Метод для структурированного ответа
        """
        response = self._execute_request(
            model=model,
            messages=messages,
            is_json=True,
            **kwargs,
        )

        raw_answer = response.choices[0].message.content
        if raw_answer is None:
            raise ValueError("Ответ пустой")

        raw_answer = raw_answer.strip()

        try:
            return response_model.model_validate_json(raw_answer)
        except pydantic.ValidationError:
            cleaned_answer = self.clean_json_from_markdown(raw_answer)
            try:
                return response_model.model_validate_json(cleaned_answer)
            except pydantic.ValidationError:
                self.log.critical(
                    "invalid_json",
                    raw_answer=raw_answer[:500],
                    cleaned_answer=cleaned_answer[:500],
                )
                raise ValueError("LLM вернула невалидный JSON")

    def planner_agent(self, state: StateAgents):
        model = self.MODELS_ROLES["planner"]

    def run(self, state: StateAgents) -> StateAgents:
        """
        Основной цикл выполнения задачи.

        Возвращает состояние с результатами работы всех агентов.
        """
        self.log.info("orchestrator_run_start")

        self.fill_data_blocks_registry(state)
        self.formatter_agent(state)

        state.finished = True

        self.log.info("orchestrator_run_done")

        return state
