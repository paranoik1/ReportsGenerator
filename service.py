import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from ai import ReportGeneratorLLM, StateAgents

UPLOAD_DIR = "uploads"
TMP_DIR = "tmp"
LOG_DIR = "logs"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

app = Flask(__name__)


@dataclass
class Task:
    """Задача на генерацию отчёта."""

    task_id: str
    status: str = "queued"
    user_prompt: str = ""
    file_paths: list[str] = field(default_factory=list)
    template_path: str | None = None

    # Состояние генерации отчета
    state: StateAgents | None = None

    error: str | None = None


tasks: dict[str, Task] = {}

# ---------- LOGGER ----------

logger = logging.getLogger("ai_service")
logger.setLevel(logging.INFO)

handler = logging.FileHandler(os.path.join(LOG_DIR, "events.jsonl"), encoding="utf-8")
logger.addHandler(handler)


def log_event(event: str, **data):
    logger.info(
        json.dumps({"event": event, "time": time.time(), **data}, ensure_ascii=False)
    )


# ---------- BACKGROUND JOB ----------


def background_task(task: Task):
    """Фоновая задача генерации отчета."""
    try:
        log_event("task_started", task_id=task.task_id, files=len(task.file_paths))

        orchestrator = ReportGeneratorLLM()

        state = orchestrator.generate_report(
            user_prompt=task.user_prompt,
            file_paths=task.file_paths,
            template_path=task.template_path,
            task_id=task.task_id,
            output_dir=TMP_DIR,
        )

        task.state = state
        task.status = "done"

        log_event("task_completed", task_id=task.task_id, result=state.report_docx_path)

    except Exception as e:
        task.status = "error"
        task.error = str(e)
        log_event("task_failed", task_id=task.task_id, error=str(e))


# ---------- WEB ----------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    task_id = str(uuid.uuid4())

    user_prompt = request.form.get("prompt", "")
    files = request.files.getlist("files")
    template_file = request.files.get("template")

    saved_paths = []
    for file in files:
        if file.filename:
            filename = secure_filename(f"{task_id}_{file.filename}")
            path = os.path.join(UPLOAD_DIR, filename)
            file.save(path)
            saved_paths.append(path)

    template_path = None
    if template_file and template_file.filename:
        filename = secure_filename(f"{task_id}_template_{template_file.filename}")
        template_path = os.path.join(UPLOAD_DIR, filename)
        template_file.save(template_path)

    task = Task(
        task_id=task_id,
        user_prompt=user_prompt,
        file_paths=saved_paths,
        template_path=template_path,
    )
    tasks[task_id] = task

    log_event("task_queued", task_id=task_id, files=len(saved_paths))

    thread = threading.Thread(target=background_task, args=(task,))
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
