"""Модели данных для оркестратора LLM-агентов."""

from dataclasses import dataclass, field
from typing import Any, Callable

from openai.types import ReasoningEffort

from utils.prompt_manager import PromptManager

# Глобальный менеджер промптов
_prompt_manager = PromptManager()


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
    """Конфигурация AI-модели для конкретного агента."""

    name: str
    system_prompt_template: str
    reasoning_effort: ReasoningEffort = "medium"
    temperature: float | None = None

    def render_system_prompt(self, **context: Any) -> str:
        """Рендерит системный промпт с переданными переменными."""
        return _prompt_manager.render(self.system_prompt_template, **context)
