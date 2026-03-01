import os
import uuid
import threading
import time
import json
import logging
import subprocess
import pypandoc

from flask import Flask, request, render_template_string, jsonify
from docx import Document
from pypdf import PdfReader
import markdown
from werkzeug.utils import secure_filename

from ai import run_ollama_agent
from utils.md2docx import html_to_docx


UPLOAD_DIR = "uploads"
TMP_DIR = "tmp"
LOG_DIR = "logs"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

app = Flask(__name__)

tasks: dict[str, dict] = {}

# ---------- LOGGER ----------

logger = logging.getLogger("ai_service")
logger.setLevel(logging.INFO)

handler = logging.FileHandler(os.path.join(LOG_DIR, "events.jsonl"), encoding="utf-8")
logger.addHandler(handler)


def log_event(event: str, **data):
    logger.info(json.dumps({
        "event": event,
        "time": time.time(),
        **data
    }, ensure_ascii=False))


# ---------- FILE TEXT EXTRACTION ----------
# def convert_pdf_to_html(pdf_path: str, output_dir: str = "output") -> str:
#     """
#     Converts a PDF file to HTML using the pdf2htmlEX command-line tool.
#     """
#     # Ensure output directory exists
#     os.makedirs(output_dir, exist_ok=True)

#     # Define the output HTML file path
#     output_file = os.path.join(output_dir, "converted.html")

#     # Build the command
#     # '--zoom 1.5' adjusts scaling, '--dest-dir' sets output folder
#     command = [
#         "pdf2htmlEX",
#         "--zoom", "1.5",
#         "--dest-dir", output_dir,
#         pdf_path,
#         output_file
#     ]

#     try:
#         # Execute the command
#         result = subprocess.run(command, check=True, capture_output=True, text=True)
#         logger.debug("Conversion successful!")
#         logger.debug(f"HTML saved to: {output_file}")
#         return output_file
#     except subprocess.CalledProcessError as e:
#         logger.error(f"Conversion failed: {e.stderr}")
#         return None
#     except FileNotFoundError:
#         logger.error("Error: pdf2htmlEX is not installed or not in your system's PATH.")
#         return None


def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
        # output_filepath = convert_pdf_to_html(file_path, TMP_DIR)
        # with open(output_filepath) as fp:
        #     return fp.read()

    elif ext == ".docx":
        markdown_text = pypandoc.convert_file(file_path, "markdown-simple_tables-grid_tables-multiline_tables")
        # doc = Document(file_path)
        # return "\n".join(p.text for p in doc.paragraphs)
        return markdown_text

    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    else:
        raise ValueError(f"Unsupported file type: {ext}")


# ---------- BACKGROUND JOB ----------

def background_task(task_id: str, user_prompt: str, file_paths: list[str]):
    try:
        log_event("task_started", task_id=task_id, files=len(file_paths))
        tasks[task_id]["status"] = "processing"

        final_prompt = user_prompt

        for path in file_paths:
            log_event("file_processing", task_id=task_id, file=path)
            text = extract_text(path)
            final_prompt += f"\n\n{file_paths}:\n{text}"

        log_event("llm_call_started", task_id=task_id)

        start = time.time()
        md_result = run_ollama_agent(final_prompt)
        duration = time.time() - start

        log_event(
            "llm_call_finished",
            task_id=task_id,
            duration=duration,
            input_size=len(final_prompt),
            output_size=len(md_result)
        )

        html_result = markdown.markdown(
            md_result,
            extensions=["extra", "sane_lists", "nl2br"]
        )

        html_path = os.path.join(TMP_DIR, f"{task_id}.html")
        docx_path = os.path.join(TMP_DIR, f"{task_id}.docx")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_result)

        log_event("html_created", task_id=task_id)

        html_to_docx(html_path, docx_path)

        log_event("docx_created", task_id=task_id, path=docx_path)

        tasks[task_id]["status"] = "done"
        tasks[task_id]["result"] = docx_path

        log_event("task_completed", task_id=task_id)

    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)

        log_event("task_failed", task_id=task_id, error=str(e))


# ---------- WEB ----------

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
<title>AI Report Generator</title>
</head>
<body>

<h2>Генерация отчёта</h2>

<form id="form">
<input type="file" name="files" multiple><br><br>

<textarea name="prompt" rows="10" cols="80"
placeholder="Введите пользовательский prompt"></textarea><br><br>

<button type="submit">Отправить</button>
</form>

<p id="status"></p>

<script>
const form = document.getElementById("form");
const status = document.getElementById("status");

form.onsubmit = async (e) => {
    e.preventDefault();

    const formData = new FormData(form);

    status.innerText = "Задача запущена...";

    const res = await fetch("/start", {
        method: "POST",
        body: formData
    });

    const data = await res.json();
    const taskId = data.task_id;

    const interval = setInterval(async () => {
        const r = await fetch("/status/" + taskId);
        const s = await r.json();

        if (s.status === "processing") {
            status.innerText = "Обработка...";
        }

        if (s.status === "done") {
            clearInterval(interval);
            status.innerHTML = "Готово! Файл создан: " + s.result;
        }

        if (s.status === "error") {
            clearInterval(interval);
            status.innerText = "Ошибка: " + s.error;
        }
    }, 2000);
};
</script>

</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/start", methods=["POST"])
def start():
    task_id = str(uuid.uuid4())
    tasks[task_id] = {"status": "queued"}

    user_prompt = request.form.get("prompt", "")
    files = request.files.getlist("files")

    saved_paths = []

    for file in files:
        if file.filename:
            filename = secure_filename(f"{task_id}_{file.filename}")
            path = os.path.join(UPLOAD_DIR, filename)
            file.save(path)
            saved_paths.append(path)

    log_event("task_queued", task_id=task_id, files=len(saved_paths))

    thread = threading.Thread(
        target=background_task,
        args=(task_id, user_prompt, saved_paths)
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/status/<task_id>")
def status(task_id):
    return jsonify(tasks.get(task_id, {"status": "unknown"}))


if __name__ == "__main__":
    app.run(debug=True)
