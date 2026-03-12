import json
import logging
import pypandoc
import os
import threading
import uuid
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import markdown
from flask import Flask, jsonify, render_template, request, send_file
from pypdf import PdfReader
from werkzeug.utils import secure_filename

from ai import Orchestrator, AgentState, Document, create_state
from utils.md2docx import html_to_docx

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

    # Для Orchestrator
    orchestrator: Orchestrator | None = None
    state: AgentState | None = None

    # Прогресс
    steps: list[dict] = field(default_factory=list)
    current_step: int = 0

    # Human-in-the-loop
    pending_task: str = ""
    pending_code: str = ""

    # Результат
    result_path: str | None = None
    html_path: str | None = None
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


# ---------- FILE TEXT EXTRACTION ----------

def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    elif ext == ".docx":
        markdown_text = pypandoc.convert_file(
            file_path, "markdown-simple_tables-grid_tables-multiline_tables"
        )
        return markdown_text

    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ---------- HELPER FUNCTIONS ----------

def check_pending_approval(task: Task) -> bool:
    """
    Проверяет, есть ли ожидание проверки кода.
    Если есть — обновляет статус задачи и возвращает True.
    """
    state = task.state
    if not state or not state.pending_approval:
        return False

    task.status = "pending_approval"
    task.pending_task = state.steps[state.current_step].task if state.current_step < len(state.steps) else ""
    task.pending_code = state.pending_approval.code
    log_event("task_pending_approval", task_id=task.task_id)
    return True


def run_orchestrator_step(task: Task):
    """
    Запускает следующий шаг orchestrator.
    Проверяет pending_approval, завершение задачи или ошибки.
    """
    orchestrator = task.orchestrator
    state = task.state

    if not (orchestrator and state):
        return

    task.status = "processing"

    # Определяем, с чего начать: новый запуск или продолжение
    if state.current_step == 0 and not state.steps:
        state = orchestrator.run(state)
    else:
        state = orchestrator.resume_after_approval(state)

    if TYPE_CHECKING:
        if not state:
            return

    task.state = state
    task.current_step = state.current_step

    # Сохраняем шаги если они появились
    if state.steps and not task.steps:
        task.steps = [
            {"agent": step.agent, "task": step.task}
            for step in state.steps
        ]

    # Проверяем состояние после шага
    if check_pending_approval(task):
        return

    if state.finished and state.report_markdown:
        finalize_task(task)


# ---------- BACKGROUND JOB ----------

def background_task(task: Task):
    try:
        log_event("task_started", task_id=task.task_id, files=len(task.file_paths))

        # Создаём документы из файлов
        documents = [Document(filepath=path) for path in task.file_paths]

        # Создаём orchestrator и состояние
        orchestrator = Orchestrator()
        state = create_state(
            user_prompt=task.user_prompt,
            documents=documents,
            task_id=task.task_id
        )

        task.orchestrator = orchestrator
        task.state = state

        # Запускаем первый шаг
        run_orchestrator_step(task)

    except Exception as e:
        task.status = "error"
        task.error = str(e)
        log_event("task_failed", task_id=task.task_id, error=str(e))


def resume_task(task: Task):
    """Возобновляет выполнение задачи после проверки кода."""
    try:
        run_orchestrator_step(task)
    except Exception as e:
        task.status = "error"
        task.error = str(e)
        log_event("task_failed", task_id=task.task_id, error=str(e))


def finalize_task(task: Task):
    """Финализирует задачу: генерирует HTML и DOCX."""
    try:
        log_event("finalizing_task", task_id=task.task_id)

        if not task.state or not task.state.report_markdown:
            raise ValueError("Нет отчёта для финализации")

        md_result = task.state.report_markdown

        html_result = markdown.markdown(
            md_result, extensions=["extra", "sane_lists", "nl2br"]
        )

        html_path = os.path.join(TMP_DIR, f"{task.task_id}.html")
        docx_path = os.path.join(TMP_DIR, f"{task.task_id}.docx")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_result)

        log_event("html_created", task_id=task.task_id)

        html_to_docx(html_path, docx_path)

        log_event("docx_created", task_id=task.task_id, path=docx_path)

        task.status = "done"
        task.result_path = docx_path
        task.html_path = html_path

        log_event("task_completed", task_id=task.task_id)

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

    saved_paths = []
    for file in files:
        if file.filename:
            filename = secure_filename(f"{task_id}_{file.filename}")
            path = os.path.join(UPLOAD_DIR, filename)
            file.save(path)
            saved_paths.append(path)

    # Создаём задачу
    task = Task(
        task_id=task_id,
        user_prompt=user_prompt,
        file_paths=saved_paths
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
        "steps": task.steps,
        "current_step": task.current_step,
    }

    if task.status == "pending_approval":
        response["pending_task"] = task.pending_task
        response["pending_code"] = task.pending_code

    elif task.status == "done":
        response["result"] = task.result_path
        response["html_result"] = f"/view_html/{task_id}"

    elif task.status == "error":
        response["error"] = task.error

    return jsonify(response)


@app.route("/approve/<task_id>", methods=["POST"])
def approve(task_id):
    """Обработка решения пользователя по проверке кода."""
    try:
        data = request.get_json()
        approved = data.get("approved", False)
        edited_code = data.get("edited_code")

        task = tasks.get(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        if not task.orchestrator or not task.state:
            return jsonify({"error": "Orchestrator state not found"}), 404

        # Обрабатываем решение
        task.orchestrator.approve_code(task.state, approved, edited_code)

        if approved:
            log_event("code_approved", task_id=task_id, edited=edited_code is not None)
            resume_task(task)
        else:
            log_event("code_rejected", task_id=task_id)
            task.status = "error"
            task.error = "Код отклонён пользователем"

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/view_html/<task_id>")
def view_html(task_id):
    """Просмотр HTML версии отчёта."""
    task = tasks.get(task_id)
    if task and task.html_path and os.path.exists(task.html_path):
        return send_file(task.html_path, mimetype="text/html")
    return jsonify({"error": "HTML not found"}), 404


@app.route("/download/<task_id>")
def download(task_id):
    """Скачивание DOCX файла."""
    task = tasks.get(task_id)
    if task and task.result_path and os.path.exists(task.result_path):
        return send_file(
            task.result_path,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=f"report_{task_id[:8]}.docx"
        )
    return jsonify({"error": "File not found"}), 404


if __name__ == "__main__":
    app.run(debug=True)
