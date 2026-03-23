import os
import threading
import time
import uuid
from dataclasses import dataclass, field

import structlog
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from report_generator import ReportGenerator, StateAgents
from utils.log import setup_logging

UPLOAD_DIR = "uploads"
TMP_DIR = "tmp"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

setup_logging()

app = Flask(__name__)


@dataclass
class Task:
    """Задача на генерацию отчёта."""

    task_id: str
    upload_dir: str
    tmp_dir: str

    status: str = "queued"
    user_prompt: str = ""

    file_paths: list[str] = field(default_factory=list)
    template_path: str | None = None

    # Состояние генерации отчета
    state: StateAgents | None = None

    error: str | None = None


tasks: dict[str, Task] = {}

logger = structlog.get_logger("ai_service")


def create_task_dirs(task_id: str) -> tuple[str, str]:
    """Создаёт директории для задачи и возвращает пути к ним."""
    task_upload_dir = os.path.join(UPLOAD_DIR, task_id)
    task_tmp_dir = os.path.join(TMP_DIR, task_id)
    os.makedirs(task_upload_dir, exist_ok=True)
    os.makedirs(task_tmp_dir, exist_ok=True)
    return task_upload_dir, task_tmp_dir


# ---------- BACKGROUND JOB ----------


def background_task(task: Task, images: list[tuple[str, str]]):
    """Фоновая задача генерации отчета."""
    start_time = time.time()

    log = logger.bind(
        task_id=task.task_id,
        worker_pid=os.getpid(),
        worker_thread=threading.current_thread().name,
    )
    log.info("task_started", files_count=len(task.file_paths), images_count=len(images))

    report_generator = ReportGenerator()

    try:
        state = report_generator.generate_report(
            user_prompt=task.user_prompt,
            file_paths=task.file_paths,
            template_path=task.template_path,
            images=images,
            task_id=task.task_id,
            output_dir=task.tmp_dir,
        )

        task.state = state
        task.status = "done"

        duration = time.time() - start_time

        log.info(
            "task_completed",
            duration_sec=round(duration, 2),
            result_path=state.report_docx_path,
            status="success",
        )
    except Exception as e:
        duration = time.time() - start_time

        log.exception(
            "task_failed",
            duration_sec=round(duration, 2),
            error_type=type(e).__name__,
            error_msg=str(e),
            status="error",
        )
        raise


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    task_id = str(uuid.uuid4())

    # Создаём директории для задачи
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
        user_prompt=user_prompt,
        file_paths=saved_paths,
        template_path=template_path,
        tmp_dir=task_tmp_dir,
        upload_dir=task_upload_dir,
    )
    tasks[task_id] = task

    logger.info(
        "task_queued",
        task_id=task_id,
        files_count=len(saved_paths),
        images_count=len(images),
    )

    thread = threading.Thread(target=background_task, args=(task, images))
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/status/<task_id>")
def status(task_id):
    task = tasks.get(task_id)

    if not task:
        return jsonify({"status": "unknown"})

    response = {
        "status": task.status,
    }

    if task.status == "done" and task.state:
        response["result"] = task.state.report_docx_path
        response["html_result"] = f"/view_html/{task_id}"

    elif task.status == "error":
        response["error"] = task.error

    return jsonify(response)


@app.route("/view_html/<task_id>")
def view_html(task_id):
    """Просмотр HTML версии отчёта."""
    task = tasks.get(task_id)
    if task and task.state and task.state.report_html_path:
        return send_file(task.state.report_html_path, mimetype="text/html")
    return jsonify({"error": "HTML not found"}), 404


@app.route("/download/<task_id>")
def download(task_id):
    """Скачивание DOCX файла."""
    task = tasks.get(task_id)
    if task and task.state and task.state.report_docx_path:
        return send_file(
            task.state.report_docx_path,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=f"report_{task_id[:8]}.docx",
        )
    return jsonify({"error": "File not found"}), 404


if __name__ == "__main__":
    app.run(debug=True)
