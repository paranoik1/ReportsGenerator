"""Formatter-агент для формирования финального отчёта."""

import json
from typing import TYPE_CHECKING, Any

import structlog
from openai.types.chat.chat_completion import ChatCompletion

from orchestrator.tools import FORMATTER_TOOLS
from utils.data_block_registry import DataBlocksRegistry

if TYPE_CHECKING:
    from openai.types import ChatCompletion
    from structlog import BoundLogger

    from models import StateAgents
    from orchestrator.models import AiModel


logger = structlog.get_logger(__name__)


class FormatterMixin:
    """Миксин для formatter-агента."""

    if TYPE_CHECKING:
        MODELS_ROLES: dict[str, "AiModel"]
        log: "BoundLogger"

        def _execute_request(
            self,
            model: AiModel,
            messages: list[dict],
            is_json: bool = False,
            **kwargs: Any,
        ) -> "ChatCompletion": ...

    def _prepare_formatter_context(self, state: "StateAgents") -> tuple[str, str]:
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
        model: "AiModel",
        state: "StateAgents",
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
    ) -> dict[str, Any]:
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
        self, state: "StateAgents", tool_call: Any, report_parts: list[str]
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
        state: "StateAgents",
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
        state: "StateAgents",
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

    def formatter_agent(self, state: "StateAgents") -> str:
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
