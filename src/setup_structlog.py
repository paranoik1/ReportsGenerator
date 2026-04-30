import logging
import os
from pathlib import Path

import structlog

from config import get_settings


class ExcludeLoggerFilter(logging.Filter):
    def __init__(self, *exclude_names: str):
        self.exclude_names = exclude_names

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(record.name.startswith(name) for name in self.exclude_names)


class IncludeOnlyFilter(logging.Filter):
    """Разрешает только логи от указанных логгеров."""

    def __init__(self, *include_names: str):
        self.include_names = include_names

    def filter(self, record: logging.LogRecord) -> bool:
        return any(record.name.startswith(name) for name in self.include_names)


def setup_logging(
    json_log_file: str = "events.jsonl", journal_log_file: str = "journal.log"
) -> None:
    settings = get_settings()
    log_dir = settings.log_dir
    os.makedirs(log_dir, exist_ok=True)
    include_filter = IncludeOnlyFilter(
        "flask_service",
        "report_generator",
        "orchestrator",
        "utils",
        "task_worker_pool",
        "models",
        "werkzeug",
    )

    # Хендлер для json логов в файл
    file_handler = logging.FileHandler(log_dir / json_log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(include_filter)

    foreign_pre_chain = [
        structlog.stdlib.ExtraAdder(),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
    ]

    # Используем встроенный JSONRenderer от structlog
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=foreign_pre_chain,  # type: ignore
    )
    file_handler.setFormatter(file_formatter)

    # Хендлер для вывода в консоль
    console_handler = logging.StreamHandler()
    # console_handler.setLevel(logging.DEBUG)

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=foreign_pre_chain,  # type: ignore
    )
    console_handler.addFilter(include_filter)
    console_handler.setFormatter(console_formatter)

    # Хендлеры для записи в файл (формат логов такой же, как и в консоле)
    journal_handler = logging.FileHandler(log_dir / journal_log_file)
    # journal_handler.setLevel(logging.DEBUG)

    journal_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=foreign_pre_chain,  # type: ignore
    )
    journal_handler.addFilter(include_filter)
    journal_handler.setFormatter(journal_formatter)

    # Создаем корневой логгер и добавляем хендлеры
    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(journal_handler)

    root_logger.setLevel(logging.DEBUG)

    # Настраиваем сам structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Отправка в стандартный logging
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
