from config import init_settings, APP_DIR
from report_generator.orchestrator.prompt_manager import get_prompt_manager

settings = init_settings()

import io
import json
import os
import uuid
import structlog
import base64
from flask import Flask, jsonify, make_response, render_template, request, send_file
from werkzeug.utils import secure_filename
from openai import OpenAI
from report_generator.orchestrator.models import AgentConfigs, AgentModelConfig
from setup_structlog import setup_logging
from task_manage import SQLiteTaskStorage, TaskWorkerPool, Task


setup_logging(settings.log_dir)

app = Flask(__name__)
logger = structlog.get_logger("flask_service")


task_storage = SQLiteTaskStorage(db_path=str(settings.database_path))
worker_pool = TaskWorkerPool(task_storage, settings.max_workers)


def create_task_dirs(task_id: str) -> tuple[str, str]:
    """Создаёт директории для задачи и возвращает пути к ним."""
    task_upload_dir = str(settings.upload_dir / task_id)
    task_tmp_dir = str(settings.tmp_dir / task_id)
    os.makedirs(task_upload_dir, exist_ok=True)
    os.makedirs(task_tmp_dir, exist_ok=True)
    return task_upload_dir, task_tmp_dir


@app.route("/")
def index():
    return jsonify({'status': 'run'})


@app.route("/start", methods=["POST"])
def start():
    user_prompt = request.form.get("prompt")
    files = request.files.getlist("files")

    if not user_prompt:
        jsonify_response = jsonify({"error": "Отсутствует пользовательский запрос"})
        return make_response(jsonify_response, 400)

    task_id = str(uuid.uuid4())
    task_upload_dir, task_tmp_dir = create_task_dirs(task_id)

    saved_paths = []
    for i, file in enumerate(files, start=1):
        if file.filename:
            filename = secure_filename(file.filename).strip() or f"upload_file_{i}"
            path = os.path.join(task_upload_dir, filename)
            file.save(path)
            saved_paths.append(path)

    # Обработка изображений с описаниями
    images = []
    idx = 0
    while True:
        image_file = request.files.get(f"image_{idx}")
        description = request.form.get(f"desc_{idx}", "")
        if not image_file or not image_file.filename:
            break
        if description:
            filename = secure_filename(f"img_{idx}_{image_file.filename}")
            path = os.path.join(task_upload_dir, filename)
            image_file.save(path)
            images.append((path, description))
        idx += 1

    # Парсинг конфигураций моделей AI
    agent_configs = None

    def parse_agent_config(prefix: str) -> AgentModelConfig:
        model = request.form.get(f"model_{prefix}")
        base_url = request.form.get(f"base_url_{prefix}")
        api_key = request.form.get(f"api_key_{prefix}")
        config = AgentModelConfig(
            model=model.strip() if model else None,
            base_url=base_url.strip() if base_url else None,
            api_key=api_key.strip() if api_key else None,
        )
        return config

    document_analyst_config = parse_agent_config("document_analyst")
    user_prompt_analyst_config = parse_agent_config("user_prompt_analyst")
    formatter_config = parse_agent_config("formatter")

    # Создаём AgentConfigs только если хотя бы одна конфигурация заполнена
    if any(
        [
            document_analyst_config.is_configured(),
            user_prompt_analyst_config.is_configured(),
            formatter_config.is_configured(),
        ]
    ):
        agent_configs = AgentConfigs(
            document_analyst=document_analyst_config,
            user_prompt_analyst=user_prompt_analyst_config,
            formatter=formatter_config,
        )
        logger.info("custom_agent_configs_provided", task_id=task_id)

    task = Task(
        task_id=task_id,
        upload_dir=task_upload_dir,
        tmp_dir=task_tmp_dir,
        user_prompt=user_prompt,
        file_paths=saved_paths,
        images=images,
        agent_configs=agent_configs,
    )

    # Отправляем задачу в пул воркеров
    worker_pool.submit_task(task)

    logger.info(
        "task_queued",
        task_id=task_id,
        files_count=len(saved_paths),
        images_count=len(images),
    )

    return jsonify({"task_id": task_id})


@app.route("/status/<task_id>")
def status(task_id):
    task = task_storage.get_task(task_id)

    if not task:
        return jsonify({"status": "unknown"})

    response = {
        "status": task.status,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }

    if task.status == "done" and task.state:
        response["result"] = task.state.report_docx_path
        response["html_result"] = f"/view_html/{task_id}"

    elif task.status == "error":
        response["error"] = task.error

    return jsonify(response)


@app.route("/tasks")
def list_tasks():
    """Список всех задач."""
    tasks = task_storage.get_all_tasks()
    return jsonify(
        [
            {
                "task_id": t.task_id,
                "status": t.status,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "error": t.error,
            }
            for t in tasks
        ]
    )


@app.route("/view_html/<task_id>")
def view_html(task_id):
    """Просмотр HTML версии отчёта."""
    task = task_storage.get_task(task_id)
    # FIXME: task_storage не сохраняет AgentsState в базе
    # if task and task.state and task.state.report_html_path:
    if task and task.status == "done":
        return send_file(
            APP_DIR / task.tmp_dir / f"{task_id}.html", mimetype="text/html"
        )
    return jsonify({"error": "HTML not found"}), 404


@app.route("/download/<task_id>")
def download(task_id):
    """Скачивание DOCX файла."""
    task = task_storage.get_task(task_id)
    # FIXME: task_storage не сохраняет AgentsState в базе
    # if task and task.state and task.state.report_docx_path:
    if task and task.status == "done":
        return send_file(
            APP_DIR / task.tmp_dir / f"{task_id}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=f"report_{task_id[:8]}.docx",
        )
    return jsonify({"error": "File not found"}), 404


@app.route("/describe-image", methods=["POST"])
def describe_image():
    """Генерация описания изображения с помощью LLM."""
    # Получаем файл изображения
    image_file = request.files.get("image")
    if not image_file or not image_file.filename:
        return jsonify({"error": "Изображение не предоставлено"}), 400

    # Опциональная пользовательская конфигурация модели
    model_name = request.form.get("model", settings.model_image_describer)
    base_url = request.form.get("base_url", settings.llm_base_url)
    api_key = request.form.get("api_key", settings.llm_api_key)

    # Читаем и кодируем изображение в base64
    image_bytes = image_file.read()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = image_file.content_type or "image/png"
    image_url = f"data:{mime_type};base64,{image_b64}"

    # Рендерим промпт через Jinja2
    prompt_manager = get_prompt_manager()
    system_prompt = prompt_manager.render("image_describer.j2")

    # Создаём клиент и вызываем LLM
    client = OpenAI(base_url=base_url, api_key=api_key)

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                }
            ],
        },
    ]

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0,
            timeout=settings.llm_timeout,
        )

        description = response.choices[0].message.content

        logger.info("image_description_generated", model=model_name)
        return jsonify({"description": description})

    except Exception as e:
        logger.exception("image_description_failed", error=str(e))
        return jsonify({"error": f"Ошибка генерации описания: {str(e)}"}), 500


def on_start():
    """Вызывается при старте приложения."""
    logger.info("application_starting")
    # Восстанавливаем задачи из БД (опционально)
    worker_pool.restore_queued_tasks()


def on_shutdown():
    """Вызывается при остановке приложения."""
    logger.info("application_shutting_down")
    worker_pool.shutdown(wait=False)


import atexit

atexit.register(on_shutdown)

if __name__ == "__main__":
    on_start()
    app.run(
        debug=settings.debug,
        host=settings.flask_host,
        port=settings.flask_port,
        use_reloader=False,
        threaded=True,
    )
