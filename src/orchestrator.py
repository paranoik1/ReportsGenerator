import json
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import structlog
from openai import OpenAI
from openai.types import ReasoningEffort
from openai.types.chat.chat_completion import ChatCompletion

from models import Document, FilePath, StateAgents
from utils.data_block_registry import DataBlock, DataBlocksRegistry
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


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """Описание задачи для параллельного выполнения."""

    name: str
    func: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    is_required: bool = True  # Можно пометить задачу как опциональную


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Результат выполнения задачи анализа."""

    task_name: str
    success: bool
    result: Any = None
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
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

    # === Методы класса Orchestrator ===

    def _submit_task(self, task_def: TaskDefinition) -> Future[TaskResult]:
        """
        Отправляет задачу в пул потоков с обработкой исключений.

        Returns:
            Future[TaskResult]: Future с результатом выполнения задачи.
        """

        def wrapper() -> TaskResult:
            try:
                result = task_def.func(*task_def.args, **task_def.kwargs)
                return TaskResult(task_name=task_def.name, success=True, result=result)
            except Exception as ex:
                return TaskResult(task_name=task_def.name, success=False, error=ex)

        return self.executor.submit(wrapper)

    def _process_task_result(self, state: StateAgents, task_result: TaskResult) -> None:
        """
        Обрабатывает результат задачи анализа и добавляет блоки в registry.

        Raises:
            RuntimeError: Если критическая задача завершилась с ошибкой.
        """
        if not task_result.success:
            self.log.exception(
                "analysis_task_failed",
                task_name=task_result.task_name,
                error=str(task_result.error),
            )
            if task_result.task_name == "template":
                raise RuntimeError(
                    f"Критическая ошибка анализа шаблона: {task_result.error}"
                )
            return  # Некритические ошибки просто логируем

        dbr = state.data_blocks_registry
        result = task_result.result
        task_name = task_result.task_name

        if task_name == "documents":
            for block in result:
                dbr.add_block(block)
            self.log.info(
                "analysis_task_completed", task_name=task_name, blocks_count=len(result)
            )

        elif task_name == "template":
            dbr.add_block(result)
            self.log.info("analysis_task_completed", task_name=task_name)

        elif task_name == "user_prompt":
            for i, block in enumerate(result):
                if block.description == "user_prompt" or i == len(result) - 1:
                    state.user_prompt_cleaned = block.content
                    continue
                dbr.add_block(block)
            self.log.info(
                "analysis_task_completed", task_name=task_name, blocks_count=len(result)
            )

    def _build_analysis_tasks(self, state: StateAgents) -> list[TaskDefinition]:
        """
        Формирует список задач анализа на основе состояния.

        Returns:
            list[TaskDefinition]: Список задач для выполнения.
        """
        tasks = []

        # Задача анализа документов (только если есть документы)
        if state.documents:
            tasks.append(
                TaskDefinition(
                    name="documents",
                    func=self._documents_summarize,
                    args=(state.documents,),
                    is_required=False,  # Можно работать и без документов
                )
            )

        # Задача анализа шаблона (только если есть шаблон)
        if state.template:
            tasks.append(
                TaskDefinition(
                    name="template",
                    func=self._template_specs_extract,
                    args=(state.template,),
                    is_required=True,  # Шаблон критичен, если указан
                )
            )

        # Задача анализа user prompt (всегда обязательна)
        tasks.append(
            TaskDefinition(
                name="user_prompt",
                func=self._user_prompt_data_extract,
                args=(state.user_prompt,),
                is_required=True,
            )
        )

        return tasks

    def fill_data_blocks_registry(self, state: StateAgents) -> None:
        """
        Параллельно запускает задачи анализа и объединяет результаты в registry.

        Задачи:
        1. Анализ документов (параллельно по каждому документу внутри _documents_summarize)
        2. Анализ шаблона (если предоставлен)
        3. Анализ user prompt (всегда)

        Raises:
            RuntimeError: Если критическая задача анализа завершилась с ошибкой.
        """
        self.log.info("fill_data_blocks_registry_start")

        # 1. Формируем и отправляем задачи
        tasks = self._build_analysis_tasks(state)
        futures = [self._submit_task(task) for task in tasks]

        # 2. Обрабатываем результаты по мере завершения
        for future in as_completed(futures):
            task_result = future.result()  # Получаем TaskResult
            self._process_task_result(state, task_result)

        # 3. Сохраняем registry
        data_blocks_path = self.output_dir / "data_blocks.json"
        dbr = state.data_blocks_registry
        dbr.save(data_blocks_path)

        self.log.info(
            "data_blocks_saved",
            blocks_count=len(dbr.get_blocks()),
            path=str(data_blocks_path),
        )

    def _prepare_formatter_context(self, state: StateAgents) -> tuple[str, str]:
        """
        Подготавливает контекст блоков данных и изображений для системного промпта.

        Returns:
            Tuple[str, str]: (blocks_context, images_context)
        """
        dbr = state.data_blocks_registry
        blocks_context = dbr.get_blocks_context() or "Нет доступных блоков"

        images_context = "\n".join(
            f"- {img.description}: {img.filepath}" for img in state.images
        )
        return blocks_context, images_context

    def _build_formatter_messages(
        self,
        model: AiModel,
        state: StateAgents,
        blocks_context: str,
        images_context: str,
    ) -> list[dict[str, str]]:
        """
        Формирует начальные сообщения для диалога с моделью-форматтером.
        """
        full_system_prompt = model.render_system_prompt(
            blocks_context=blocks_context,
            images_context=images_context,
        )

        return [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": state.user_prompt_cleaned or state.user_prompt},
        ]

    def _log_model_reasoning(self, message: Any) -> None:
        """
        Логирует рассуждения модели (reasoning), если они присутствуют в ответе.
        """
        model_extra = getattr(message, "model_extra", None)
        if not model_extra:
            return

        reasoning = model_extra.get("reasoning")
        if reasoning:
            reasoning_clean = reasoning.strip().replace("\n", "  ")
            self.log.info("reasoning_llm", reasoning=reasoning_clean)

    def _handle_read_block_tool(
        self, dbr: DataBlocksRegistry, func_args: dict[str, Any]
    ) -> dict[str, str]:
        """
        Обрабатывает вызов инструмента read_block.

        Returns:
            dict с результатом: {"content_block": "..."} или {"error": "..."}
        """
        block_id = func_args.get("block_id")

        if block_id is None:
            return {"error": "read_block tool принимает 1 аргумент - block_id: int"}

        if isinstance(block_id, str) and not block_id.isdigit():
            return {"error": "block_id должен быть типом int"}

        try:
            block_id_int = int(block_id)
        except (ValueError, TypeError):
            return {"error": "block_id должен быть типом int"}

        block_content = dbr.read_block(block_id_int)
        if block_content is None:
            return {"error": f"Блок '{block_id}' не найден"}

        return {"content_block": block_content}

    def _handle_write_section_tool(
        self, func_args: dict[str, Any], report_parts: list[str]
    ):
        content = func_args.get("content")
        section_hint = func_args.get("section_name", "unknown")

        if not content:
            return {"error": "write_section требует content"}

        # Добавляем контент в буфер отчёта
        report_parts.append(content)

        # Логируем для наблюдаемости
        self.log.info(
            "section_written",
            section_hint=section_hint,
            content_len=len(content),
            total_parts=len(report_parts),
        )

        return {"status": "ok", "section_hint": section_hint}

    def _handle_tool_call(
        self, state: StateAgents, tool_call: Any, report_parts: list[str]
    ) -> tuple[bool, dict[str, Any] | None]:
        """
        Обрабатывает вызов инструмента и возвращает результат.

        Returns:
            Tuple[bool, dict|None]:
            - (True, None) если вызван finish и отчёт завершён
            - (False, result) если инструмент выполнен, нужно продолжить цикл
            - (False, {"error": ...}) если произошла ошибка
        """
        if tool_call.type != "function":
            return False, {
                "error": f"Неподдерживаемый тип инструмента: {tool_call.type}"
            }

        func_name = tool_call.function.name
        func_args = json.loads(tool_call.function.arguments)

        self.log.debug("tool_call", tool_name=func_name, tool_args=func_args)

        # Обработка finish
        if func_name == "finish":
            if not report_parts:
                return False, {"error": "Нельзя вызывать finish, пока отчёт пуст"}
            return True, None  # Сигнал о завершении

        # Обработка read_block
        if func_name == "read_block":
            result = self._handle_read_block_tool(state.data_blocks_registry, func_args)
            return False, result

        if func_name == "write_section":
            result = self._handle_write_section_tool(func_args, report_parts)
            return False, result

        # Неизвестный инструмент
        return False, {"error": f"Неизвестный tool: {func_name}"}

    def _process_llm_response(
        self,
        state: StateAgents,
        messages: list[dict],
        report_parts: list[str],
        response: ChatCompletion,
    ) -> bool:
        """
        Обрабатывает ответ от LLM: логирует reasoning, извлекает tool calls,
        обновляет сообщения и выполняет tools.

        Returns:
            - True, если отчёт завершён
            - False, если нужно продолжить итерации
        """
        message = response.choices[0].message

        # Логируем рассуждения модели
        self._log_model_reasoning(message)

        # Сохраняем сообщение в историю
        messages.append(message.model_dump())

        tool_calls = message.tool_calls

        # Если нет tool calls — добавляем контент в отчёт и просим продолжить
        if not tool_calls:
            content = message.content
            if content:
                report_parts.append(content)
            messages.append({"role": "user", "content": "Продолжай"})
            return False

        # Обрабатываем каждый tool call
        for tool_call in tool_calls:
            is_finished, tool_result = self._handle_tool_call(
                state, tool_call, report_parts
            )

            if is_finished:
                return True

            # Формируем ответ для инструмента
            tool_result_json = json.dumps(
                tool_result or {"status": "ok"}, ensure_ascii=False
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result_json,
                }
            )

        return False

    def _finalize_report(
        self,
        state: StateAgents,
        report_parts: list[str],
        log_event: str,
        log_level: str = "info",
    ) -> str:
        """
        Финализирует отчёт: объединяет части, сохраняет в state и логирует результат.

        Returns:
            str: готовый markdown-отчёт
        """
        report = "\n".join(report_parts)
        state.report_markdown = report

        log_method = getattr(self.log, log_level)
        log_method(
            log_event,
            report_len=len(report),
            iterations=state.iteration,
        )
        return report

    def formatter_agent(self, state: StateAgents) -> str:
        """
        Формирует финальный markdown-отчёт с использованием Chain-of-Thought и tools.

        Args:
            state: Текущее состояние агентов с данными и настройками

        Returns:
            str: Сгенерированный markdown-отчёт
        """
        self.log.info("formatter_agent_start")

        model = self.MODELS_ROLES["formatter"]
        blocks_context, images_context = self._prepare_formatter_context(state)
        messages = self._build_formatter_messages(
            model, state, blocks_context, images_context
        )

        # Основной цикл генерации
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

            # Обработка ответа LLM
            is_finished = self._process_llm_response(
                state, messages, state.report_parts, response
            )

            if is_finished:
                return self._finalize_report(
                    state, state.report_parts, "formatter_agent_done"
                )

        return self._finalize_report(
            state, state.report_parts, "formatter_agent_max_iterations", "warning"
        )

    def run(self, state: StateAgents) -> StateAgents:
        """Основной цикл выполнения задачи."""
        self.log.info("orchestrator_run_start")
        self.fill_data_blocks_registry(state)

        self.formatter_agent(state)

        state.finished = True

        self.log.info("orchestrator_run_done")

        return state
