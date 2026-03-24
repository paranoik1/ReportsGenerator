import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type, TypeVar

import pydantic
import structlog
from openai import InternalServerError, OpenAI
from openai.types import ReasoningEffort
from openai.types.chat.chat_completion import ChatCompletion

from models import Document, FilePath, StateAgents
from utils.data_block_registry import DataBlock
from utils.prompt_manager import PromptManager

T = TypeVar("T", bound=pydantic.BaseModel)


logger = structlog.get_logger(__name__)


# Глобальный менеджер промптов
_prompt_manager = PromptManager()

# Определяем tools
FORMATTER_TOOLS = [
    {
        "type": "function",
        "name": "read_block",
        "description": "Получает содержимое блока данных по его ID",
        "parameters": {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "int",
                    "description": "ID блока данных для чтения",
                }
            },
            "required": ["block_id"],
        },
    },
    {"type": "function", "name": "finish", "description": "Завершает работу"},
]


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
        # "planner": AiModel(
        #     name="qwen3.5:cloud",
        #     system_prompt_template="planner.j2",
        #     reasoning_effort="high",
        # ),
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
    def convert_raw_text_to_block(raw_text: str) -> DataBlock:
        """Парсит сырой текст в блок: первая строка - description, остальное - content."""
        description, content = raw_text.split("\n", maxsplit=1)
        return DataBlock(description=description.strip(), content=content.strip())

    def _extract_blocks_iterative(
        self,
        model: AiModel,
        messages: list[dict],
        log_prefix: str,
    ) -> list[DataBlock]:
        """
        Итеративно извлекает блоки из LLM.

        Args:
            model: Модель для запросов
            messages: Сообщения для отправки модели
            log_prefix: Префикс для логов (например, "document_analyst" или "user_prompt_analyst")

        Returns:
            Список извлечённых блоков
        """
        blocks: list[DataBlock] = []
        iteration = 0
        max_iterations = 20

        while iteration < max_iterations:
            iteration += 1

            block_raw = self.run_agent(model, messages)
            if not block_raw:
                continue

            block_raw = block_raw.strip()

            # Проверяем на finish
            if block_raw.lower() == "finish":
                self.log.debug(
                    f"{log_prefix}_finished",
                    blocks_extracted=len(blocks),
                    iterations=iteration,
                )
                break

            # Парсим блок
            try:
                block = self.convert_raw_text_to_block(block_raw)
                blocks.append(block)
                self.log.debug(
                    "block_extracted",
                    block_description=block.description,
                    content_len=len(block.content),
                )
            except ValueError as e:
                self.log.warning(
                    "failed_to_parse_block",
                    raw_text=block_raw[:200],
                    error=str(e),
                )
                break

            # Добавляем ответ в историю для контекста
            messages.append({"role": "assistant", "content": block_raw})
            messages.append({"role": "user", "content": "Следующий или finish"})

        if iteration >= max_iterations:
            self.log.warning(
                f"{log_prefix}_max_iterations",
                iterations=iteration,
                blocks_extracted=len(blocks),
            )

        return blocks

    def _documents_summirize(self, docs: list[Document]) -> list[DataBlock]:
        model = self.MODELS_ROLES["document_analyst"]

        full_system_prompt = model.render_system_prompt()
        system_message = {"role": "system", "content": full_system_prompt}

        blocks: list[DataBlock] = []
        for i, doc in enumerate(docs):
            doc_prompt = f"Документ:\n{doc.content}"

            messages = [
                system_message,
                {"role": "user", "content": doc_prompt},
            ]
            self.log.debug(
                "calling_document_analyst",
                doc_path=doc.filepath,
                prompt_len=len(full_system_prompt) + len(doc_prompt),
            )

            doc_blocks = self._extract_blocks_iterative(
                model, messages, f"document_analyst_doc{i}"
            )
            blocks.extend(doc_blocks)

        self.log.info("documents_summarized", blocks_count=len(blocks))
        return blocks

    def _template_specs_extract(self, template: Document) -> DataBlock:
        model = self.MODELS_ROLES["template_analyst"]

        full_system_prompt = model.render_system_prompt()
        template_prompt = f"Документ:\n{template.content}"
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": template_prompt},
        ]

        self.log.debug(
            "calling_template_analyst",
            template_path=template.filepath,
            prompt_len=len(full_system_prompt) + len(template_prompt),
        )

        content = self.run_agent(model, messages)
        if not content:
            self.log.critical("template_specs_extracted_failed")
            raise

        self.log.info("template_specs_extracted", content_len=len(content))
        return DataBlock(
            description="Структура и форматирование шаблона отчёта",
            content=content,
        )

    def _user_prompt_data_extract(self, user_prompt: str) -> list[DataBlock]:
        model = self.MODELS_ROLES["user_prompt_analyst"]

        full_system_prompt = model.render_system_prompt()
        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        self.log.debug(
            "calling_user_prompt_analyst",
            prompt_len=len(full_system_prompt),
            user_prompt_len=len(user_prompt),
        )

        blocks = self._extract_blocks_iterative(model, messages, "user_prompt_analyst")

        self.log.info("user_prompt_analyzed", blocks_count=len(blocks))
        return blocks

    def fill_data_blocks_registry(self, state: StateAgents):
        """
        Функция для создания блоков данных через LLM
        """
        self.log.info("fill_data_blocks_registry_start")
        dbr = state.data_blocks_registry

        if state.template:
            try:
                template_data_block = self._template_specs_extract(state.template)
                dbr.add_block(template_data_block)
            except:
                self.log.exception("fill_exception")

        docs_summirized_blocks = self._documents_summirize(state.documents)
        for block in docs_summirized_blocks:
            dbr.add_block(block)

        user_data_blocks = self._user_prompt_data_extract(state.user_prompt)

        for block in user_data_blocks:
            if block.description == "user_prompt":
                state.user_prompt_cleaned = block.content
                continue

            dbr.add_block(block)

        data_blocks_path = self.output_dir / "data_blocks.json"
        dbr.save(data_blocks_path)
        self.log.info(
            "data_blocks_saved",
            blocks_count=len(dbr.get_blocks()),
            path=str(data_blocks_path),
        )

    def formatter_agent(self, state: StateAgents) -> str:
        """
        Формирует финальный markdown отчёт с использованием Chain-of-Thought и tools.
        """
        model = self.MODELS_ROLES["formatter"]
        dbr = state.data_blocks_registry

        # Формируем контекст блоков для промпта
        blocks_context = "\n".join(
            f"- `{block_id}`: {block.description}"
            for block_id, block in dbr.get_blocks().items()
        )

        full_system_prompt = model.render_system_prompt(
            blocks_context=blocks_context or "Нет доступных блоков"
        )

        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": state.user_prompt_cleaned or state.user_prompt},
        ]

        self.log.info("formatter_agent_start")

        # Цикл работы с tools
        report_parts = []

        while state.iteration < state.max_iterations:
            state.iteration += 1

            response = self._execute_request(
                model=model,
                messages=messages,
                tools=FORMATTER_TOOLS,
            )

            # Проверяем, есть ли вызовы tools
            tool_calls = response.choices[0].message.tool_calls

            if not tool_calls:
                # Если нет tool calls, просто добавляем ответ
                content = response.choices[0].message.content
                if content:
                    report_parts.append(content)
                continue

            # Обрабатываем tool calls
            messages.append(response.choices[0].message.model_dump())

            for tool_call in tool_calls:
                if tool_call.type != "function":
                    continue

                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)

                self.log.debug(
                    "tool_call",
                    tool_name=func_name,
                    tool_args=func_args,
                )

                if func_name == "read_block":
                    block_id = func_args.get("block_id")
                    block_content = dbr.read_block(block_id)
                    if block_content:
                        tool_result = json.dumps(
                            {"content_block": block_content},
                            ensure_ascii=False,
                        )
                    else:
                        tool_result = json.dumps(
                            {"error": f"Блок '{block_id}' не найден"},
                            ensure_ascii=False,
                        )

                elif func_name == "finish":
                    report = "\n".join(report_parts)
                    state.report_markdown = report

                    self.log.info(
                        "formatter_agent_done",
                        report_len=len(report),
                        iterations=state.iteration,
                    )
                    return report

                else:
                    tool_result = json.dumps(
                        {"error": f"Неизвестный tool: {func_name}"},
                        ensure_ascii=False,
                    )

                # Добавляем результат tool call в messages
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }
                )

        # Если цикл завершился без finish
        report = "\n".join(report_parts)
        state.report_markdown = report
        self.log.warning(
            "formatter_agent_max_iterations",
            iterations=state.iteration,
            report_len=len(report),
        )
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

        start_time = time.time()
        try:
            response = self.client.chat.completions.create(
                model=model.name,
                messages=messages,  # type: ignore
                reasoning_effort=model.reasoning_effort,
                temperature=model.temperature,
                **kwargs,
            )
        except InternalServerError:
            duration = time.time() - start_time
            self.log.exception(
                "llm_request_failed", model=model.name, duration_sec=round(duration, 2)
            )
            raise

        duration = time.time() - start_time
        self.log.debug(
            "llm_request_completed",
            model=model.name,
            duration_sec=round(duration, 2),
        )

        return response

    def run_agent(self, model: AiModel, messages: list[dict], **kwargs) -> str | None:
        """
        Метод для обычного текста
        """
        try:
            response = self._execute_request(model=model, messages=messages, **kwargs)
        except:
            return None

        answer = response.choices[0].message.content
        if answer is None:
            raise ValueError("Ответ пустой")

        return answer

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
