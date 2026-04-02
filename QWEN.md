# ReportsGen — Проект

## Обзор проекта

**ReportsGen** — это ИИ-агент для автоматического создания отчётов по практическим и лабораторным работам на основе предоставленных пользователем документов, шаблонов и изображений.

### Архитектура

Проект представляет собой Flask-вебсервис с асинхронной обработкой задач через пул воркеров:

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Flask     │────▶│  TaskWorkerPool   │────▶│ ReportGenerator │
│   Service   │     │  (ThreadPool)     │     │                 │
└─────────────┘     └──────────────────┘     └─────────────────┘
                                                   │
                                                   ▼
                                          ┌─────────────────┐
                                          │  Orchestrator   │
                                          │  (LLM Pipeline) │
                                          └─────────────────┘
```

### Ключевые компоненты

| Компонент | Описание |
|-----------|----------|
| `service.py` | Flask-вебсервис с API для загрузки задач и получения статуса |
| `task_worker_pool.py` | Пул потоков для параллельной обработки задач (MAX_WORKERS=5) |
| `report_generator.py` | Генерация отчёта в форматах Markdown, HTML, DOCX |
| `orchestrator.py` | Координация LLM-агентов: анализ документов, шаблонов, промптов |
| `models.py` | Модели данных: `Document`, `StateAgents`, `Task`, `ImageDocument` |
| `storage.py` | SQLite-хранилище для персистентности задач |
| `utils/` | Утилиты: `PromptManager`, `DataBlocksRegistry`, `md2docx`, логирование |

### LLM-агенты (Orchestrator)

1. **Document Analyst** (`kimi-k2-thinking:cloud`) — извлекает данные из документов
2. **Template Analyst** (`kimi-k2-thinking:cloud`) — анализирует структуру шаблона
3. **User Prompt Analyst** (`kimi-k2-thinking:cloud`) — извлекает требования из промпта
4. **Formatter** (`qwen3.5:cloud`) — формирует финальный отчёт с использованием tools

### Технологический стек

- **Язык:** Python 3.13
- **Менеджер зависимостей:** Poetry
- **Веб-фреймворк:** Flask 3.1+
- **LLM API:** OpenAI-compatible (Ollama)
- **Шаблонизатор:** Jinja2
- **Обработка документов:** python-docx, pypdf, pypandoc, pandoc
- **Логирование:** structlog
- **Валидация:** mypy, Black, isort, pre-commit

---

## Установка и запуск

### Требования

- Python 3.13
- Poetry
- Ollama с моделями: `kimi-k2-thinking:cloud`, `qwen3.5:cloud`
- pandoc (системная утилита)
- soffice (LibreOffice) для расширенного извлечения из DOCX/ODT

### Установка зависимостей

```bash
poetry install
```

### Запуск Flask-сервиса

```bash
poetry run python src/service.py
```

Или через отладчик (VS Code конфигурация в `.vscode/launch.json`):
- **Start flask service** — запуск `service.py` с отладкой
- **Python Debugger: Current File** — запуск текущего файла

### API эндпоинты

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/` | Главная страница (index.html) |
| POST | `/start` | Создание задачи (multipart: prompt, files, template, images) |
| GET | `/status/<task_id>` | Статус задачи |
| GET | `/tasks` | Список всех задач |
| GET | `/view_html/<task_id>` | Просмотр HTML-отчёта |
| GET | `/download/<task_id>` | Скачивание DOCX-файла |

### Формат запроса `/start`

```
POST /start
Content-Type: multipart/form-data

prompt: "Текст задания"
files: [документы...]
template: файл шаблона (опционально)
image_0: файл изображения
desc_0: "Описание изображения"
...
```

---

## Структура проекта

```
ReportsGen/
├── src/
│   ├── service.py              # Flask-вебсервис
│   ├── task_worker_pool.py     # Пул воркеров
│   ├── report_generator.py     # Генерация отчётов (MD/HTML/DOCX)
│   ├── orchestrator.py         # LLM-оркестрация агентов
│   ├── models.py               # Модели данных
│   ├── storage.py              # SQLite-хранилище задач
│   ├── utils/
│   │   ├── prompt_manager.py   # Jinja2-шаблоны промптов
│   │   ├── data_block_registry.py  # Реестр блоков данных
│   │   ├── md2docx.py          # Конвертация HTML → DOCX
│   │   ├── docx_styles.py      # Стили DOCX
│   │   └── log.py              # Настройка structlog
│   ├── templates/
│   │   └── index.html          # Главная страница
│   └── static/
├── prompts/
│   ├── document_analyst.j2     # Промпт для анализа документов
│   ├── template_analyst.j2     # Промпт для анализа шаблона
│   ├── user_prompt_analyst.j2  # Промпт для анализа промпта
│   ├── formatter.j2            # Промпт для форматирования отчёта
│   └── _common/
│       ├── format_answer_blocks.j2
│       └── task_other_llm.j2
├── tests/                      # Тесты (пока пусто)
├── dataset/user_prompts/       # Примеры пользовательских промптов
├── uploads/                    # Загруженные файлы (игнорируется git)
├── tmp/                        # Временные файлы задач (игнорируется git)
├── logs/                       # Логи: events.jsonl, journal.log
├── tasks.db                    # SQLite-база задач
└── pyproject.toml              # Конфигурация Poetry
```

---

## Разработка

### Команды

```bash
# Запуск тестов
poetry run pytest

# Типизация
poetry run mypy src/

# Форматирование
poetry run black src/ tests/
poetry run isort src/ tests/

# Pre-commit проверка
poetry run pre-commit run --all-files
```

### Конвенции

- **Стиль кода:** Black + isort (профиль black)
- **Типизация:** mypy (строгая, `ignore_missing_imports = True`)
- **Логирование:** structlog с JSON-выводом в `logs/events.jsonl`
- **Структура ответов LLM:** блоки с разделителями `===BLOCK_START===` / `===BLOCK_END===`

### Отладка

Конфигурации VS Code в `.vscode/launch.json`:

```json
{
    "name": "Start flask service",
    "type": "debugpy",
    "request": "launch",
    "program": "service.py",
    "console": "integratedTerminal",
    "cwd": "${workspaceFolder}/src"
}
```

---

## Roadmap (из TODO.md)

- [ ] Сохранение state в базу данных (возобновление прогресса)
- [ ] Более строгие правила извлечения данных для анализаторов
- [ ] Кэширование запросов к LLM (хеширование документов)
- [ ] Общий RateLimiter для всех задач
- [ ] Исправление вставки изображений (обтекание, привязка к символу)
- [ ] Анализ изображений документов пользователя

---

## Примечания

- **Rate Limiting:** Orchestrator использует `RateLimiter` с задержкой 3 секунды между запросами к LLM
- **Параллелизм:** Документы анализируются параллельно через `ThreadPoolExecutor`
- **Форматирование отчёта:** Упрощённый Markdown (без заголовков `#`, только `<center>`, `<strong>`, `<code>`, таблицы)
- **Tools Formatter:** `read_block`, `write_section`, `finish` — используются для пошаговой генерации отчёта
