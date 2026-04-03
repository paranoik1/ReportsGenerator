import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

import structlog

from config import get_settings
from report_generator import ReportGenerator

if TYPE_CHECKING:
    from models import Task
    from storage import TaskStorage


class TaskWorkerPool:
    """Пул воркеров для выполнения задач."""

    def __init__(self, storage: "TaskStorage", max_workers: int | None = None):
        settings = get_settings()
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers or settings.max_workers
        )
        self.storage = storage
        self._log = structlog.get_logger(__name__)

    def submit_task(self, task: "Task") -> Future:
        """Отправляет задачу в пул."""
        task.status = "queued"
        self.storage.save_task(task)

        future = self.executor.submit(self._execute_task, task)
        task._future = future

        self._log.info("task_submitted", task_id=task.task_id)
        return future

    def _execute_task(self, task: "Task"):
        """Выполняет задачу (в отдельном потоке)."""
        task.status = "processing"
        task.started_at = time.time()
        task._worker_thread = threading.current_thread().name
        self.storage.save_task(task)

        start_time = time.time()
        log = self._log.bind(
            task_id=task.task_id,
            worker_pid=os.getpid(),
            worker_thread=task._worker_thread,
        )

        log.info("task_started", files_count=len(task.file_paths))

        try:
            report_generator = ReportGenerator(
                task_id=task.task_id,
                output_dir=task.tmp_dir,
            )

            state = report_generator.generate_report(
                user_prompt=task.user_prompt,
                file_paths=task.file_paths,
                template_path=task.template_path,
                images=task.images,
            )

            task.state = state
            task.status = "done"

            duration = time.time() - start_time
            log.info(
                "task_completed",
                duration_sec=round(duration, 2),
                result_path=state.report_docx_path,
            )

        except Exception as e:
            duration = time.time() - start_time
            task.status = "error"
            task.error = str(e)

            log.exception(
                "task_failed",
                duration_sec=round(duration, 2),
                error_type=type(e).__name__,
            )
            raise

        finally:
            task.completed_at = time.time()
            self.storage.save_task(task)

    def shutdown(self, wait: bool = True):
        """Останавливает пул воркеров."""
        self.executor.shutdown(wait=wait)

    def restore_queued_tasks(self):
        """Восстанавливает задачи из БД при рестарте приложения."""
        queued_tasks = self.storage.get_queued_tasks()

        for task in queued_tasks:
            # Сбрасываем статус на queued для перезапуска
            task.status = "queued"
            task.started_at = None
            self.submit_task(task)

        if queued_tasks:
            self._log.info("restored_tasks", count=len(queued_tasks))
