"""Оркестратор LLM-агентов для генерации отчётов."""

from config import get_settings

from .analyzer import AnalyzerMixin
from .base import BaseOrchestrator, _get_agent_config
from .formatter import FormatterMixin
from .models import AgentConfigs, AgentModelConfig, AiModel, StateAgents

__all__ = ["Orchestrator"]


def _build_models_roles(
    agent_configs: AgentConfigs | None = None,
) -> dict[str, AiModel]:
    """Создаёт конфигурацию моделей из настроек или пользовательских конфигураций."""
    settings = get_settings()

    # Функция для получения конфигурации для конкретного агента
    def get_agent_config(agent_name: str):
        return _get_agent_config(agent_configs, agent_name)

    def resolve_model(default_model: str, agent_config: AgentModelConfig | None) -> str:
        """Определяет имя модели: пользовательское или по умолчанию."""
        if agent_config and agent_config.model:
            return agent_config.model
        return default_model

    # Document Analyst
    doc_config = get_agent_config("document_analyst")
    model_doc = resolve_model(settings.model_analyst, doc_config)

    # Template Analyst
    tmpl_config = get_agent_config("template_analyst")
    model_tmpl = resolve_model(settings.model_analyst, tmpl_config)

    # User Prompt Analyst
    user_config = get_agent_config("user_prompt_analyst")
    model_user = resolve_model(settings.model_analyst, user_config)

    # Formatter
    fmt_config = get_agent_config("formatter")
    model_fmt = resolve_model(settings.model_formatter, fmt_config)

    return {
        "document_analyst": AiModel(
            name=model_doc,
            system_prompt_template="document_analyst.j2",
            temperature=0,
        ),
        "template_analyst": AiModel(
            name=model_tmpl,
            system_prompt_template="template_analyst.j2",
            temperature=0,
        ),
        "user_prompt_analyst": AiModel(
            name=model_user,
            system_prompt_template="user_prompt_analyst.j2",
            temperature=0,
        ),
        "formatter": AiModel(
            name=model_fmt,
            system_prompt_template="formatter.j2",
        ),
    }


class Orchestrator(BaseOrchestrator, AnalyzerMixin, FormatterMixin):
    """
    Оркестратор LLM-агентов.

    Объединяет:
    - Базовую инфраструктуру (клиент, rate limiter, executor)
    - Анализ документов, шаблонов и промптов
    - Форматирование финального отчёта
    """

    def __init__(
        self,
        output_dir,
        task_id: str,
        agent_configs: AgentConfigs | None = None,
        settings=None,
    ) -> None:
        # Сохраняем конфигурации для использования при создании клиентов
        self._agent_configs = agent_configs
        # Создаём модели ролей с учётом пользовательских конфигураций
        models_roles = _build_models_roles(agent_configs)
        # Вызываем родительский __init__ с моделями
        super().__init__(
            output_dir,
            task_id=task_id,
            models_roles=models_roles,
            settings=settings,
        )

    def run(self, state: StateAgents) -> StateAgents:
        """Основной цикл выполнения задачи."""
        self.log.info("orchestrator_run_start")
        self.fill_data_blocks_registry(state)

        self.formatter_agent(state)

        state.finished = True

        self.log.info("orchestrator_run_done")

        return state
