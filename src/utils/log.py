import logging
import os
from pathlib import Path

import structlog

LOG_DIR = Path("logs")


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


def setup_logging(log_file: str = "events.jsonl"):
    os.makedirs(LOG_DIR, exist_ok=True)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Обработчик для файла (JSON)
    file_handler = logging.FileHandler(LOG_DIR / log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    # file_handler.addFilter(ExcludeLoggerFilter(
    #     "werkzeug",           # Flask/Werkzeug HTTP logs
    #     "uvicorn.access",     # Uvicorn access logs
    #     "urllib3",            # HTTP client logs
    #     "boto3",              # AWS SDK logs
    #     "openai"
    # ))
    file_handler.addFilter(
        IncludeOnlyFilter("ai_service", "report_generator", "orchestrator")
    )

    # Используем встроенный JSONRenderer от structlog
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
        ],
    )
    file_handler.setFormatter(file_formatter)

    # 3. Настраиваем formatter для консоли (Читаемый текст)
    # ConsoleRenderer делает логи цветными и удобными для чтения
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=[
            structlog.stdlib.ExtraAdder(),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
        ],
    )
    console_handler.setFormatter(console_formatter)

    # 4. Создаем корневой логгер и добавляем обработчики
    root_logger = logging.getLogger()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.DEBUG)

    # 5. Настраиваем сам structlog
    structlog.configure(
        processors=[
            # Контекст (time, level, event и т.д.)
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
