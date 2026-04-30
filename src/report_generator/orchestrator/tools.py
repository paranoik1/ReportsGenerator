"""Определения инструментов (tools) для formatter-агента."""

FORMATTER_TOOLS = [
    {
        "type": "function",
        "name": "read_block",
        "description": "Получает содержимое блока данных по его ID",
        "parameters": {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "integer",
                    "description": "ID блока данных для чтения",
                }
            },
            "required": ["block_id"],
        },
    },
    {
        "type": "function",
        "name": "write_section",
        "description": (
            "Сохраняет часть отчёта и сигнализирует о прогрессе. "
            "section_name — опциональная подсказка для логирования."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Содержимое раздела(ов) в упрощённом Markdown",
                },
                "section_name": {
                    "type": "string",
                    "description": (
                        "Опционально: название раздела(ов) для логирования, "
                        "например 'Введение' или 'Ход выполнения: часть 1'"
                    ),
                },
            },
            "required": ["content"],
        },
    },
    {"type": "function", "name": "finish", "description": "Завершает работу"},
]
