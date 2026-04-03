import os
import uuid

import structlog
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from config import get_settings
from models import Task
from storage import SQLiteTaskStorage
from task_worker_pool import TaskWorkerPool
from utils.log import setup_logging

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
    task_id = str(uuid.uuid4())
    task_upload_dir, task_tmp_dir = create_task_dirs(task_id)

    user_prompt = request.form.get("prompt", "")
    files = request.files.getlist("files")
    template_file = request.files.get("template")

    saved_paths = []
    for file in files:
        if file.filename:
            filename = secure_filename(file.filename)
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

    task = Task(
        task_id=task_id,
        upload_dir=task_upload_dir,
        tmp_dir=task_tmp_dir,
        user_prompt=user_prompt,
        file_paths=saved_paths,
        template_path=template_path,
        images=images,
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
    if task and task.state and task.state.report_html_path:
        return send_file(task.state.report_html_path, mimetype="text/html")
    return jsonify({"error": "HTML not found"}), 404


@app.route("/download/<task_id>")
def download(task_id):
    """Скачивание DOCX файла."""
    task = task_storage.get_task(task_id)
    if task and task.state and task.state.report_docx_path:
        return send_file(
            task.state.report_docx_path,
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
