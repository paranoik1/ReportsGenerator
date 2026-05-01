"""Базовый класс оркестратора с общей инфраструктурой."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog
from openai import OpenAI
from openai.types.chat.chat_completion import ChatCompletion

from config import Settings, get_settings
from report_generator.orchestrator.models.ai_model import AiModel

from .models import AgentConfigs, AgentModelConfig, FilePath
from .rate_limiter import RateLimiter

logger = structlog.get_logger(__name__)


def _get_agent_config(
    agent_configs: AgentConfigs | None,
    agent_name: str,
) -> AgentModelConfig | None:
    """Получает конфигурацию для конкретного агента."""
    if agent_configs is None:
        return None
    return getattr(agent_configs, agent_name, None)


def _create_client(
    agent_config: AgentModelConfig | None,
    settings: Settings,
) -> tuple[OpenAI, str, str]:
    """Создаёт OpenAI клиент с учётом пользовательской конфигурации.

    Возвращает: (client, base_url, api_key)
    """
    if agent_config and agent_config.base_url:
        base_url = agent_config.base_url
    else:
        base_url = settings.llm_base_url

    if agent_config and agent_config.api_key:
        api_key = agent_config.api_key
    else:
        api_key = settings.llm_api_key

    client = OpenAI(base_url=base_url, api_key=api_key)
    return client, base_url, api_key


class BaseOrchestrator:
    """Базовый класс оркестратора с общей LLM-инфраструктурой."""

    def __init__(
        self,
        output_dir: FilePath,
        task_id: str,
        settings: Settings | None = None,
        models_roles: dict[str, AiModel] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.output_dir = Path(output_dir)
        self.task_id = task_id
        self.log = logger.bind(task_id=self.task_id)

        # Модели для ролей (будут установлены в Orchestrator)
        self.MODELS_ROLES = models_roles or {}

        # По умолчанию используем один клиент для всех агентов
        # (можно переопределить для отдельных агентов)
        self.client = OpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
        )

        # Rate limiter для контроля частоты запросов
        self.rate_limiter = RateLimiter(min_delay=self.settings.rate_limit_delay)

        # Thread pool для параллельного выполнения
        self.executor = ThreadPoolExecutor(
            max_workers=self.settings.max_parallel_workers
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.executor.shutdown(wait=True)

    def _execute_request(
        self,
        model: AiModel,
        messages: list[dict],
        client: OpenAI | None = None,
        is_json: bool = False,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Приватный метод для общей логики запроса к Ollama API."""
        if is_json:
            kwargs["response_format"] = {"type": "json_object"}

        # Применяем rate limiting перед запросом
        self.rate_limiter.acquire()

        # Используем переданный клиент или общий
        effective_client = client or self.client

        # Формируем базовые параметры запроса
        request_params: dict[str, Any] = {
            "model": model.name,
            "messages": messages,
            "timeout": self.settings.llm_timeout,
        }

        # Добавляем temperature только если она указана
        if model.temperature is not None:
            request_params["temperature"] = model.temperature

        if model.reasoning_effort:
            request_params["reasoning_effort"] = model.reasoning_effort

        request_params.update(kwargs)

        start_time = time.time()
        try:
            response = effective_client.chat.completions.create(**request_params)  # type: ignore
        except:
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

    def get_client_for_agent(
        self,
        agent_name: str,
        agent_configs: AgentConfigs | None = None,
    ) -> OpenAI:
        """Получает или создаёт клиент для конкретного агента.

        Если для агента указана пользовательская конфигурация (base_url или api_key),
        создаётся отдельный клиент. Иначе возвращается общий клиент.
        """
        agent_config = _get_agent_config(agent_configs, agent_name)
        client, _, _ = _create_client(agent_config, self.settings)
        return client

    def run_agent(
        self,
        model_name: str,
        messages: list[dict],
        agent_configs: AgentConfigs | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Возвращает текстовый ответ от LLM.

        Args:
            model_name: Имя роли агента (document_analyst, formatter и т.д.)
            messages: Сообщения для LLM
            agent_configs: Пользовательские конфигурации агентов (если None - используется self._agent_configs)
            **kwargs: Дополнительные параметры для запроса
        """
        # Используем переданные конфигурации или из инстанса
        if agent_configs is None:
            agent_configs = getattr(self, "_agent_configs", None)

        # Получаем конфигурацию модели для этого агента
        model = self.MODELS_ROLES.get(model_name)
        if model is None:
            self.log.error("model_not_found", model_name=model_name)
            return None

        # Получаем клиент для этого агента (с учётом пользовательских настроек)
        agent_config = _get_agent_config(agent_configs, model_name)

        # Используем агент-специфичный клиент
        client, base_url, api_key = _create_client(agent_config, self.settings)

        try:
            response = self._execute_request(
                model=model,
                messages=messages,
                client=client,
                **kwargs,
            )
        except Exception as ex:
            self.log.exception("llm_request_error", error=str(ex))
            return None

        if not response.choices:
            self.log.warning("empty_choices_response")
            return None

        answer = response.choices[0].message.content
        if answer is None:
            self.log.warning("Ответ пустой")

        return answer
