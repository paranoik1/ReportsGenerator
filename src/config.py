"""Централизованная конфигурация приложения через pydantic-settings."""

import threading
from pathlib import Path
from typing import Self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """
    Конфигурация приложения.

    Загружает значения из переменных окружения и файла .env.
    Приоритет: переменные окружения > .env > значения по умолчанию.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="REPORTS_",
        extra="ignore",
    )

    # === Директории ===
    upload_dir: Path = Field(
        default=Path("uploads"), description="Директория загруженных файлов"
    )
    tmp_dir: Path = Field(
        default=Path("tmp"), description="Директория временных файлов"
    )
    log_dir: Path = Field(default=Path("logs"), description="Директория логов")
    database_path: Path = Field(
        default=Path("tasks.db"), description="Путь к SQLite базе задач"
    )

    # === LLM API ===
    llm_base_url: str = Field(
        default="http://127.0.0.1:11434/v1",
        description="Базовый URL LLM API (OpenAI-compatible)",
    )
    llm_api_key: str = Field(default="ollama", description="API ключ для LLM")
    llm_timeout: int = Field(default=20, description="Таймаут запроса к LLM (секунды)")

    # === Rate Limiting ===
    rate_limit_delay: float = Field(
        default=3.0,
        description="Минимальная задержка между запросами к LLM (секунды)",
    )

    # === Пул воркеров ===
    max_workers: int = Field(default=5, description="Максимум одновременных задач")
    max_parallel_workers: int = Field(
        default=5,
        description="Максимум параллельных потоков для анализа документов",
    )

    # === Модели LLM ===
    model_analyst: str = Field(
        default="kimi-k2-thinking:cloud",
        description="Модель для анализаторов (документы, шаблоны, промпты)",
    )
    model_formatter: str = Field(
        default="qwen3.5:cloud",
        description="Модель для форматирования отчёта",
    )

    # === Flask ===
    flask_debug: bool = Field(default=True, description="Режим отладки Flask")
    flask_host: str = Field(default="127.0.0.1", description="Хост Flask сервера")
    flask_port: int = Field(default=5000, description="Порт Flask сервера")

    def ensure_dirs(self) -> Self:
        """Создаёт необходимые директории."""
        for dir_path in (self.upload_dir, self.tmp_dir, self.log_dir):
            dir_path.mkdir(parents=True, exist_ok=True)
        return self


# Синглтон-инстанс настроек
_settings: Settings | None = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    """
    Возвращает синглтон-инстанс настроек.

    При первом вызове создаёт новый экземпляр, при последующих — возвращает
    кэшированный. Это гарантирует однократное чтение .env и переменных окружения.
    """
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = Settings().ensure_dirs()

    return _settings


def reset_settings() -> None:
    """Сбрасывает кэшированные настройки (полезно для тестов)."""
    global _settings
    _settings = None
