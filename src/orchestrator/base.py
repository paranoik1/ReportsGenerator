"""Базовый класс оркестратора с общей инфраструктурой."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog
from openai import OpenAI
from openai.types.chat.chat_completion import ChatCompletion

from config import Settings, get_settings
from llm.rate_limiter import RateLimiter
from models import FilePath
from orchestrator.models import AiModel
from utils.data_block_registry import DataBlock

logger = structlog.get_logger(__name__)


class BaseOrchestrator:
    """Базовый класс оркестратора с общей LLM-инфраструктурой."""

    def __init__(
        self,
        output_dir: FilePath,
        task_id: str,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = OpenAI(
            base_url=self.settings.llm_base_url, api_key=self.settings.llm_api_key
        )
        self.output_dir = Path(output_dir)
        self.task_id = task_id
        self.log = logger.bind(task_id=self.task_id)

        # Rate limiter для контроля частоты запросов
        self.rate_limiter = RateLimiter(min_delay=self.settings.rate_limit_delay)

        # Thread pool для параллельного выполнения
        self.executor = ThreadPoolExecutor(
            max_workers=self.settings.max_parallel_workers
        )

    def __del__(self) -> None:
        """Освобождает ресурсы при уничтожении объекта."""
        self.executor.shutdown(wait=False)

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
                logger.warning("parse_block_error", lines=lines)
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
        **kwargs: Any,
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
                timeout=self.settings.llm_timeout,
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

    def run_agent(
        self, model: AiModel, messages: list[dict], **kwargs: Any
    ) -> str | None:
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
