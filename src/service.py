import os
import uuid
from pathlib import Path

import structlog
from flask import Flask, jsonify, render_template, request, send_file, make_response
from werkzeug.utils import secure_filename

from config import get_settings
from models import AgentConfigs, AgentModelConfig, Task
from storage import SQLiteTaskStorage
from task_worker_pool import TaskWorkerPool
from utils.log import setup_logging

APP_DIR = Path(__file__).parent.parent

settings = get_settings()
setup_logging()

app = Flask(__name__)
logger = structlog.get_logger("flask_service")


task_storage = SQLiteTaskStorage(db_path=str(settings.database_path))
worker_pool = TaskWorkerPool(task_storage)


def create_task_dirs(task_id: str) -> tuple[str, str]:
    """Создаёт директории для задачи и возвращает пути к ним."""
    task_upload_dir = str(settings.upload_dir / task_id)
    task_tmp_dir = str(settings.tmp_dir / task_id)
    os.makedirs(task_upload_dir, exist_ok=True)
    os.makedirs(task_tmp_dir, exist_ok=True)
    return task_upload_dir, task_tmp_dir


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    user_prompt = request.form.get("prompt")
    files = request.files.getlist("files")
    template_file = request.files.get("template")

    if not user_prompt:
        jsonify_response = jsonify({'error': 'Отсутствует пользовательский запрос'})
        return make_response(jsonify_response, 400)
    
    task_id = str(uuid.uuid4())
    task_upload_dir, task_tmp_dir = create_task_dirs(task_id)

    saved_paths = []
    for i, file in enumerate(files, start=1):
        if file.filename:
            filename = secure_filename(file.filename).strip() or f'upload_file_{i}'
            path = os.path.join(task_upload_dir, filename)
            file.save(path)
            saved_paths.append(path)

    template_path = None
    if template_file and template_file.filename:
        filename = secure_filename(f"template_{template_file.filename}")
        template_path = os.path.join(task_upload_dir, filename)
        template_file.save(template_path)

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
    template_analyst_config = parse_agent_config("template_analyst")
    user_prompt_analyst_config = parse_agent_config("user_prompt_analyst")
    formatter_config = parse_agent_config("formatter")

    # Создаём AgentConfigs только если хотя бы одна конфигурация заполнена
    if any(
        [
            document_analyst_config.is_configured(),
            template_analyst_config.is_configured(),
            user_prompt_analyst_config.is_configured(),
            formatter_config.is_configured(),
        ]
    ):
        agent_configs = AgentConfigs(
            document_analyst=document_analyst_config,
            template_analyst=template_analyst_config,
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
        template_path=template_path,
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
        return send_file(APP_DIR / task.tmp_dir / f"{task_id}.html", mimetype="text/html")
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
        debug=settings.flask_debug,
        host=settings.flask_host,
        port=settings.flask_port,
        use_reloader=False,
        threaded=True,
    )
