import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Literal

from report_generator.orchestrator.models import AgentConfigs, StateAgents

TaskStatus = Literal["queued", "processing", "done", "error"]


@dataclass
class Task:
    """Задача на генерацию отчёта."""

    task_id: str
    upload_dir: str
    tmp_dir: str
    status: TaskStatus = "queued"
    user_prompt: str = ""
    file_paths: list[str] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)
    agent_configs: AgentConfigs | None = None

    state: StateAgents | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    _future: Future | None = None
    _worker_thread: str | None = None
