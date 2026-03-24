import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic
import structlog
from openai import InternalServerError, OpenAI
from openai.types import ReasoningEffort
from openai.types.chat.chat_completion import ChatCompletion

from models import Document, FilePath, StateAgents
from utils.data_block_registry import DataBlock
from utils.prompt_manager import PromptManager

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
TIMEOUT_LLM = 5 * 60


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

    def parse_blocks(self, response_text: str) -> list[DataBlock]:
        """
        Извлекает блоки данных из ответа LLM.

        Args:
            response_text: Текст ответа от LLM с блоками формата ===BLOCK_START=== ... ===BLOCK_END===

        Returns:
            Список блоков
        """
        blocks = []

        # Нормализуем окончания строк
        text = response_text.replace("\r\n", "\n").replace("\r", "\n")

        # Находим все блоки по разделителям
        pattern = r"===BLOCK_START===\n(.*?)\n===BLOCK_END==="
        matches = re.finditer(pattern, text, re.DOTALL)

        for match in matches:
            block_text = match.group(1).strip()
            lines = block_text.split("\n", maxsplit=1)

            if len(lines) < 2:
                self.log.warning("parse_block_error", lines=lines)
                continue

            description = lines[0].strip()
            content = lines[1].strip()

            blocks.append(DataBlock(description=description, content=content))

        return blocks

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
                timeout=TIMEOUT_LLM,
                **kwargs,
            )
        except InternalServerError as ex:
            duration = time.time() - start_time
            self.log.exception(
                "llm_request_failed", model=model.name, duration_sec=round(duration, 2)
            )
            raise ex

        duration = time.time() - start_time
        self.log.debug(
            "llm_request_completed",
            model=model.name,
            duration_sec=round(duration, 2),
        )

        return response

    def run_agent(self, model: AiModel, messages: list[dict], **kwargs) -> str | None:
        """
        Возвращает текстовый ответ от LLM
        """
        try:
            response = self._execute_request(model=model, messages=messages, **kwargs)
        except:
            return None

        answer = response.choices[0].message.content
        if answer is None:
            self.log.exception("Ответ пустой")

        return answer

    def _documents_summirize(self, docs: list[Document]) -> list[DataBlock]:
        model = self.MODELS_ROLES["document_analyst"]

        full_system_prompt = model.render_system_prompt()
        system_message = {"role": "system", "content": full_system_prompt}

        blocks: list[DataBlock] = []
        for doc in docs:
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

            response = self.run_agent(model, messages)
            if not response:
                continue

            doc_blocks = self.parse_blocks(response)
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

        response = self.run_agent(model, messages)
        if not response:
            return []

        blocks = self.parse_blocks(response)

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
        blocks_context = dbr.get_blocks_context()
        images_context = "\n".join(
            [f"- {img.description}: {img.filepath}" for img in state.images]
        )

        full_system_prompt = model.render_system_prompt(
            blocks_context=blocks_context or "Нет доступных блоков",
            images_context=images_context,
        )

        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": state.user_prompt_cleaned or state.user_prompt},
        ]

        self.log.info("formatter_agent_start")

        report_parts: list[str] = []

        def finish(log_event: str, log_method: str = "info") -> str:
            report = "\n".join(report_parts)
            state.report_markdown = report
            log = getattr(self.log, log_method)

            log(
                log_event,
                report_len=len(report),
                iterations=state.iteration,
            )
            return report

        # Цикл работы с tools
        while state.iteration < state.max_iterations:
            state.iteration += 1

            response = self._execute_request(
                model=model,
                messages=messages,
                tools=FORMATTER_TOOLS,
            )

            message = response.choices[0].message
            model_extra = message.model_extra

            if model_extra:
                reasoning = model_extra.get("reasoning")
                if reasoning:
                    reasoning_clean = reasoning.strip().replace("\n", " ")

                    self.log.info("reasoning_llm", reasoning=reasoning_clean)

            # Проверяем, есть ли вызовы tools
            tool_calls = message.tool_calls

            messages.append(message.model_dump())

            if not tool_calls:
                # Если нет tool calls, просто добавляем ответ
                content = message.content
                if content:
                    report_parts.append(content)

                messages.append(
                    {
                        "role": "user",
                        "content": "Продолжай или подверди окончание написания отчета, вызвав finish tool",
                    }
                )
                continue

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
                    if block_id.isdigit():
                        block_content = dbr.read_block(int(block_id))
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
                    else:
                        tool_result = json.dumps(
                            {"error": "block_id должен быть типом int"},
                            ensure_ascii=False,
                        )
                elif func_name == "finish":
                    return finish("formatter_agent_done")
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
        return finish("formatter_agent_max_iterations", "warning")

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
