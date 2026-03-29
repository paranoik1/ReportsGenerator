import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any

from models import Task

DATABASE_PATH = "tasks.db"


class TaskStorage(ABC):
    @abstractmethod
    def save_task(self, task: Task) -> None: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Task | None: ...

    @abstractmethod
    def get_all_tasks(self) -> list[Task]: ...

    @abstractmethod
    def get_queued_tasks(self) -> list[Task]: ...


class SQLiteTaskStorage(TaskStorage):
    """Персистентное хранилище задач в SQLite."""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Создаёт таблицу задач."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    upload_dir TEXT NOT NULL,
                    tmp_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_prompt TEXT,
                    file_paths TEXT,
                    template_path TEXT,
                    images TEXT,
                    error TEXT,
                    created_at REAL,
                    started_at REAL,
                    completed_at REAL
                )
            """)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """Контекстный менеджер для подключения к БД."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save_task(self, task: Task):
        """Сохраняет или обновляет задачу в БД."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks 
                (task_id, upload_dir, tmp_dir, status, user_prompt, file_paths, 
                 template_path, images, error, created_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    task.task_id,
                    task.upload_dir,
                    task.tmp_dir,
                    task.status,
                    task.user_prompt,
                    str(task.file_paths),
                    task.template_path,
                    str(task.images),
                    task.error,
                    task.created_at,
                    task.started_at,
                    task.completed_at,
                ),
            )
            conn.commit()

    @staticmethod
    def _task_factory(row: dict[str, Any]):
        return Task(
            task_id=row["task_id"],
            upload_dir=row["upload_dir"],
            tmp_dir=row["tmp_dir"],
            status=row["status"],
            user_prompt=row["user_prompt"] or "",
            file_paths=eval(row["file_paths"] or "[]"),
            template_path=row["template_path"],
            images=eval(row["images"] or "[]"),
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def get_task(self, task_id: str) -> Task | None:
        """Получает задачу по ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()

            if not row:
                return None

            return self._task_factory(row)

    def get_all_tasks(self) -> list[Task]:
        """Получает все задачи."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ).fetchall()

            return [self._task_factory(row) for row in rows]

    def get_queued_tasks(self) -> list[Task]:
        """Получает задачи в очереди (для восстановления при рестарте)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('queued', 'processing') ORDER BY created_at"
            ).fetchall()

            return [self._task_factory(row) for row in rows]
