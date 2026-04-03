"""Оркестратор LLM-агентов для генерации отчётов."""

from openai.types import ReasoningEffort

from config import get_settings
from models import StateAgents
from orchestrator.analyzer import AnalyzerMixin
from orchestrator.base import BaseOrchestrator
from orchestrator.formatter import FormatterMixin
from orchestrator.models import AiModel

__all__ = ["Orchestrator"]


def _build_models_roles() -> dict[str, AiModel]:
    """Создаёт конфигурацию моделей из настроек."""
    settings = get_settings()
    return {
        "document_analyst": AiModel(
            name=settings.model_analyst,
            system_prompt_template="document_analyst.j2",
            temperature=0,
        ),
        "template_analyst": AiModel(
            name=settings.model_analyst,
            system_prompt_template="template_analyst.j2",
            temperature=0,
        ),
        "user_prompt_analyst": AiModel(
            name=settings.model_analyst,
            system_prompt_template="user_prompt_analyst.j2",
            temperature=0,
        ),
        "formatter": AiModel(
            name=settings.model_formatter,
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

    MODELS_ROLES = _build_models_roles()

    def run(self, state: StateAgents) -> StateAgents:
        """Основной цикл выполнения задачи."""
        self.log.info("orchestrator_run_start")
        self.fill_data_blocks_registry(state)

        self.formatter_agent(state)

        state.finished = True

        self.log.info("orchestrator_run_done")

        return state
