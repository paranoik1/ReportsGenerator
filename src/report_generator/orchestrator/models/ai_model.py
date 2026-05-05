"""Модели данных для оркестратора LLM-агентов."""

from dataclasses import dataclass
from typing import Any

from openai.types import ReasoningEffort

from ..prompt_manager import get_prompt_manager


@dataclass(frozen=True, slots=True)
class AiModel:
    """Конфигурация AI-модели для конкретного агента."""

    name: str
    system_prompt_template: str
    reasoning_effort: ReasoningEffort = "low"
    temperature: float | None = None

    def render_system_prompt(self, **context: Any) -> str:
        """Рендерит системный промпт с переданными переменными."""
        prompt_manager = get_prompt_manager()
        return prompt_manager.render(self.system_prompt_template, **context)
