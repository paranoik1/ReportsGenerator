"""Модели данных для оркестратора LLM-агентов."""

from dataclasses import dataclass
from typing import Any

from openai.types import ReasoningEffort

from report_generator.orchestrator.prompt_manager import PromptManager
from config import get_settings

# Глобальный менеджер промптов
prompts_dir = get_settings().prompts_path
_prompt_manager = PromptManager(prompts_dir)


@dataclass(frozen=True, slots=True)
class AiModel:
    """Конфигурация AI-модели для конкретного агента."""

    name: str
    system_prompt_template: str
    reasoning_effort: ReasoningEffort = "low"
    temperature: float | None = None

    def render_system_prompt(self, **context: Any) -> str:
        """Рендерит системный промпт с переданными переменными."""
        return _prompt_manager.render(self.system_prompt_template, **context)
