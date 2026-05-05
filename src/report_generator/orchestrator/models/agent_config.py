from dataclasses import dataclass, field


@dataclass
class AgentModelConfig:
    """Конфигурация модели для конкретного AI агента."""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    def is_configured(self) -> bool:
        """Проверяет, настроена ли конфигурация."""
        return bool(self.model or self.base_url or self.api_key)


@dataclass
class AgentConfigs:
    """Конфигурации моделей для всех AI агентов."""

    document_analyst: AgentModelConfig = field(default_factory=AgentModelConfig)
    user_prompt_analyst: AgentModelConfig = field(default_factory=AgentModelConfig)
    formatter: AgentModelConfig = field(default_factory=AgentModelConfig)
