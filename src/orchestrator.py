import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import structlog
from openai import OpenAI
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
                    "type": "integer",
                    "description": "ID блока данных для чтения",
                }
            },
            "required": ["block_id"],
        },
    },
    {"type": "function", "name": "finish", "description": "Завершает работу"},
]

TIMEOUT_LLM = 5 * 60


# ========== RATE LIMITER ==========
class RateLimiter:
    """
    Контролирует частоту запросов к API.
    Использует простую задержку между запросами.
    """

    def __init__(self, min_delay: float = 1.0):
        self.min_delay = min_delay  # Минимальная задержка между запросами (сек)
        self._lock = threading.Lock()
        self._last_call_time = 0.0
        # self._cond = threading.Condition()

    def acquire(self):
        """Ждёт, пока можно будет сделать запрос."""
        # FIXME: use Condition without Lock
        with self._lock:
            elapsed = time.time() - self._last_call_time
            if elapsed < self.min_delay:
                sleep_time = self.min_delay - elapsed
                time.sleep(sleep_time)
            self._last_call_time = time.time()


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
        rate_limit_delay: float = 3.0,
        max_parallel_workers: int = 5,
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.output_dir = Path(output_dir)
        self.task_id = task_id
        self.log = logger.bind(task_id=self.task_id)

        # Rate limiter для контроля частоты запросов
        self.rate_limiter = RateLimiter(min_delay=rate_limit_delay)

        # Thread pool для параллельного выполнения
        self.executor = ThreadPoolExecutor(max_workers=max_parallel_workers)

    def __del__(self):
        """Освобождает ресурсы при уничтожении объекта."""
        self.executor.shutdown(wait=False)

    @staticmethod
    def convert_raw_text_to_block(raw_text: str) -> DataBlock:
        """Парсит сырой текст в блок: первая строка - description, остальное - content."""
        description, content = raw_text.split("\n", maxsplit=1)
        return DataBlock(description=description.strip(), content=content.strip())

    def parse_blocks(self, response_text: str) -> list[DataBlock]:
        """Извлекает блоки данных из ответа LLM."""
        blocks = []
        text = response_text.replace("\r\n", "\n").replace("\r", "\n")
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
        """Приватный метод для общей логики запроса."""
        if is_json:
            kwargs["response_format"] = {"type": "json_object"}

        # Применяем rate limiting перед запросом
        self.rate_limiter.acquire()

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
        except Exception as ex:
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
        """Возвращает текстовый ответ от LLM."""
        try:
            response = self._execute_request(model=model, messages=messages, **kwargs)
        except Exception as ex:
            self.log.exception("llm_request_error", error=str(ex))
            return None

        answer = response.choices[0].message.content
        if answer is None:
            self.log.warning("Ответ пустой")

        return answer

    def _analyze_single_document(self, doc: Document) -> list[DataBlock]:
        """Анализирует один документ (вызывается в отдельном потоке)."""
        model = self.MODELS_ROLES["document_analyst"]
        full_system_prompt = model.render_system_prompt()
        system_message = {"role": "system", "content": full_system_prompt}
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
            self.log.warning("document_analysis_failed", doc_path=doc.filepath)
            return []

        doc_blocks = self.parse_blocks(response)
        self.log.info(
            "document_analyzed",
            doc_path=doc.filepath,
            blocks_count=len(doc_blocks),
        )
        return doc_blocks

    def _documents_summarize(self, docs: list[Document]) -> list[DataBlock]:
        """
        Параллельно анализирует все документы.
        Каждый документ обрабатывается в отдельном потоке с rate limiting.
        """
        if not docs:
            return []

        self.log.info("documents_summarize_start", docs_count=len(docs))
        all_blocks: list[DataBlock] = []

        # Создаём задачи для каждого документа
        futures = {
            self.executor.submit(self._analyze_single_document, doc): doc
            for doc in docs
        }

        # Собираем результаты по мере завершения
        for future in as_completed(futures):
            doc = futures[future]
            try:
                blocks = future.result()
                all_blocks.extend(blocks)
            except Exception as ex:
                self.log.exception(
                    "document_analysis_exception",
                    doc_path=doc.filepath,
                    error=str(ex),
                )

        self.log.info("documents_summarized", blocks_count=len(all_blocks))
        return all_blocks

    def _template_specs_extract(self, template: Document) -> DataBlock:
        """Извлекает спецификации из шаблона."""
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
            raise RuntimeError("Не удалось извлечь спецификации шаблона")

        self.log.info("template_specs_extracted", content_len=len(content))
        return DataBlock(
            description="Структура и форматирование шаблона отчёта",
            content=content,
        )

    def _user_prompt_data_extract(self, user_prompt: str) -> list[DataBlock]:
        """Извлекает данные из пользовательского промпта."""
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
        Параллельно запускает 3 задачи анализа:
        1. Анализ документов (параллельно по каждому документу)
        2. Анализ шаблона
        3. Анализ user prompt

        Затем объединяет результаты в registry.
        """
        self.log.info("fill_data_blocks_registry_start")
        dbr = state.data_blocks_registry

        # Создаём задачи для параллельного выполнения
        futures: list[Future] = []

        def run_future(task_name: str, func: Callable, *args, **kwargs):
            """
            Функция для запуска в пуле, которая возвращает наименование задачи и результат ее работы
            """
            return task_name, func(*args, **kwargs)

        # Задача анализа документов
        if state.documents:
            _docs_future = self.executor.submit(
                run_future, "documents", self._documents_summarize, state.documents
            )
            futures.append(_docs_future)

        # Задача анализа шаблона
        if state.template:
            _temp_future = self.executor.submit(
                run_future, "template", self._template_specs_extract, state.template
            )
            futures.append(_temp_future)

        # Задача анализа user prompt
        _user_prompt_future = self.executor.submit(
            run_future, "user_prompt", self._user_prompt_data_extract, state.user_prompt
        )
        futures.append(_user_prompt_future)

        for future in as_completed(futures):
            try:
                task_name, result = future.result()

                if task_name == "documents":
                    for block in result:
                        dbr.add_block(block)
                elif task_name == "template":
                    dbr.add_block(result)
                elif task_name == "user_prompt":
                    for i, block in enumerate(result):
                        if block.description == "user_prompt" or i == len(result) - 1:
                            state.user_prompt_cleaned = block.content
                            continue
                        dbr.add_block(block)

                self.log.info("analysis_task_completed", task_name=task_name)

            except Exception as ex:
                self.log.exception(
                    "analysis_task_failed",
                    task_name=task_name,
                    error=str(ex),
                )

        # Сохраняем registry
        data_blocks_path = self.output_dir / "data_blocks.json"
        dbr.save(data_blocks_path)
        self.log.info(
            "data_blocks_saved",
            blocks_count=len(dbr.get_blocks()),
            path=str(data_blocks_path),
        )

    def formatter_agent(self, state: StateAgents) -> str:
        """Формирует финальный markdown отчёт с использованием Chain-of-Thought и tools."""
        model = self.MODELS_ROLES["formatter"]
        dbr = state.data_blocks_registry

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

        def exec_func(func_name: str, func_args: dict) -> dict[str, str]:
            if func_name == "read_block":
                block_id = func_args.get("block_id")
                if not block_id:
                    return {
                        "error": "read_block tool принимает 1 аргумент - block_id: int"
                    }

                if not block_id.isdigit():
                    return {"error": "block_id должен быть типом int"}

                block_content = dbr.read_block(int(block_id))
                if not block_content:
                    return {"error": f"Блок '{block_id}' не найден"}

                return {"content_block": block_content}

            return {"error": f"Неизвестный tool: {func_name}"}

        while state.iteration < state.max_iterations:
            state.iteration += 1

            try:
                response = self._execute_request(
                    model=model,
                    messages=messages,
                    tools=FORMATTER_TOOLS,
                )
            except Exception as ex:
                self.log.exception("formatter_exception")
                continue

            message = response.choices[0].message
            model_extra = message.model_extra

            if model_extra:
                reasoning = model_extra.get("reasoning")
                if reasoning:
                    reasoning_clean = reasoning.strip().replace("\n", "  ")
                    self.log.info("reasoning_llm", reasoning=reasoning_clean)

            tool_calls = message.tool_calls
            messages.append(message.model_dump())

            if not tool_calls:
                content = message.content
                if content:
                    content_preview = (
                        content[:200] + "...[!TRUNCATED!]..." + content[-200:]
                        if len(content) > 400
                        else content
                    )
                    self.log.debug(
                        "write_part_report",
                        content_len=len(content),
                        content_preview=content_preview,
                    )
                    report_parts.append(content)
                    message_content = "Продолжай"
                else:
                    self.log.warning("empty_message_received")
                    message_content = (
                        "Было получено пустое сообщение. Продолжай писать отчет"
                    )

                messages.append(
                    {
                        "role": "user",
                        "content": message_content,
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

                if func_name == "finish":
                    self.log.info(
                        "finish_requested",
                        report_parts_count=len(report_parts),
                        last_content_preview=(
                            report_parts[-1][:200] if report_parts else None
                        ),
                    )

                    if len(report_parts) == 0:
                        tool_result = {
                            "error": "Нельзя вызывать finish tool до тех пор, пока длина отчета равна 0 (не начился писаться)"
                        }
                    else:
                        return finish("formatter_agent_done")
                else:
                    tool_result = exec_func(func_name, func_args)

                tool_result_json = json.dumps(tool_result, ensure_ascii=False)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result_json,
                    }
                )

        return finish("formatter_agent_max_iterations", "warning")

    def run(self, state: StateAgents) -> StateAgents:
        """Основной цикл выполнения задачи."""
        self.log.info("orchestrator_run_start")
        self.fill_data_blocks_registry(state)

        self.formatter_agent(state)

        state.finished = True

        self.log.info("orchestrator_run_done")

        return state
