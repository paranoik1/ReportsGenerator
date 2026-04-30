"""Централизованная конфигурация приложения через pydantic-settings."""

from pathlib import Path
from typing import Self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]
APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    Конфигурация приложения.

    Загружает значения из переменных окружения и файла .env.
    Приоритет: переменные окружения > .env > значения по умолчанию.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === Директории ===
    upload_dir: Path = Field(
        default=APP_DIR / "uploads", description="Директория загруженных файлов"
    )
    tmp_dir: Path = Field(
        default=APP_DIR / "tmp", description="Директория временных файлов"
    )
    log_dir: Path = Field(default=APP_DIR / "logs", description="Директория логов")
    database_path: Path = Field(
        default=APP_DIR / "tasks.db", description="Путь к SQLite базе задач"
    )
    prompts_path: Path = Field(default=APP_DIR / "prompts", description="Директория промптов")

    # === LLM API ===
    llm_base_url: str = Field(
        default="http://127.0.0.1:11434/v1/",
        description="Базовый URL LLM API (OpenAI-compatible)",
    )
    llm_api_key: str = Field(default="ollama", description="API ключ для LLM")
    llm_timeout: int = Field(default=20, description="Таймаут запроса к LLM (секунды)")

    # === Rate Limiting ===
    rate_limit_delay: float = Field(
        default=10.0,
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
        default="minimax-m2.7:cloud",
        description="Модель для анализаторов (документы, шаблоны, промпты)",
    )
    model_formatter: str = Field(
        default="qwen3.5:cloud",
        description="Модель для форматирования отчёта",
    )

    # === Flask ===
    flask_host: str = Field(default="127.0.0.1", description="Хост Flask сервера")
    flask_port: int = Field(default=5000, description="Порт Flask сервера")

    debug: bool = Field(default=True, description="Режим отладки")

    def ensure_dirs(self) -> Self:
        """Создаёт необходимые директории."""
        for dir_path in (self.upload_dir, self.tmp_dir, self.log_dir):
            dir_path.mkdir(parents=True, exist_ok=True)
        return self
    
    def check_dirs(self) -> tuple[bool, str]:
        """Проверят на наличие всех нужных файлов/директорий"""
        for dir_path in (self.upload_dir, self.tmp_dir, self.log_dir, self.prompts_path):
            if not dir_path.exists():
                return False, str(dir_path)

        return True, ""

# Синглтон-инстанс настроек
_settings: Settings | None = None


def init_settings() -> Settings:
    global _settings
    _settings = Settings().ensure_dirs()

    is_exists, not_found_path = _settings.check_dirs()
    if not is_exists:
        raise RuntimeError(f'Отсутствуют нужные файлы/директории в корне проекта "{APP_DIR}": {not_found_path}')
    
    return _settings


def get_settings() -> Settings:
    """
    Возвращает синглтон-инстанс настроек.

    При первом вызове создаёт новый экземпляр, при последующих — возвращает
    кэшированный. Это гарантирует однократное чтение .env и переменных окружения.
    """
    if not _settings:
        raise ValueError("settings не проинициализированы")
    
    return _settings
