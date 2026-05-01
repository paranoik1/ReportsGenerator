"""Модуль анализа документов, шаблонов и пользовательских промптов."""

import re
from concurrent.futures import Future, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import structlog

from .models import AgentConfigs, AiModel, DataBlock, Document

if TYPE_CHECKING:
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from structlog import BoundLogger

    from .models.state import StateAgents
    from .rate_limiter import RateLimiter


logger = structlog.get_logger(__name__)


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


class AnalyzerMixin:
    """Миксин для анализа документов, шаблонов и промптов."""

    if TYPE_CHECKING:
        MODELS_ROLES: dict[str, AiModel]
        log: "BoundLogger"
        executor: "ThreadPoolExecutor"
        rate_limiter: "RateLimiter"
        output_dir: Path

        def run_agent(
            self,
            model_name: str,
            messages: list[dict],
            agent_configs: AgentConfigs | None = None,
            **kwargs: Any,
        ) -> str | None:
            pass

    @staticmethod
    def parse_blocks(response_text: str) -> list[DataBlock]:
        """Извлекает блоки данных из ответа LLM."""
        blocks = []
        text = response_text.replace("\r\n", "\n").replace("\r", "\n")
        pattern = r"===BLOCK_START===\n(.*?)\n===BLOCK_END==="
        matches = re.finditer(pattern, text, re.DOTALL)

        for match in matches:
            block_text = match.group(1).strip()
            lines = block_text.split("\n", maxsplit=1)

            if len(lines) < 2:
                logger.warning("parsing_block_error", lines=lines)
                continue

            description = lines[0].strip()
            content = lines[1].strip()
            blocks.append(DataBlock(description=description, content=content))

        return blocks

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

        response = self.run_agent("document_analyst", messages)
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

        if len(all_blocks) == 0:
            self.log.warning("documents_summarized_failed")
            return []

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

        content = self.run_agent("template_analyst", messages)
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

        response = self.run_agent("user_prompt_analyst", messages)
        if not response:
            return []

        blocks = self.parse_blocks(response)
        self.log.info("user_prompt_analyzed", blocks_count=len(blocks))
        return blocks

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

    def _process_task_result(
        self, state: "StateAgents", task_result: TaskResult
    ) -> None:
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
            # if task_result.task_name == "template":
            #     raise RuntimeError(
            #         f"Критическая ошибка анализа шаблона: {task_result.error}"
            #     )
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
            for block in result:
                if block.description == "user_prompt":
                    state.user_prompt_cleaned = block.content
                    continue
                dbr.add_block(block)
            self.log.info(
                "analysis_task_completed", task_name=task_name, blocks_count=len(result)
            )

    def _build_analysis_tasks(self, state: "StateAgents") -> list[TaskDefinition]:
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

    def fill_data_blocks_registry(self, state: "StateAgents") -> None:
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
