# from os import getenv
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import cached_property
from os.path import join as path_join
from typing import Any, Literal

from dotenv import load_dotenv
from openai import OpenAI
from openai.types import ReasoningEffort

load_dotenv()


CODE_RUN_TIMEOUT = 60

# OLLAMA_API_KEY = getenv("OLLAMA_API_KEY", None)
# assert OLLAMA_API_KEY is not None, "Необходимо добавить OLLAMA_API_KEY в .env"


@dataclass
class Document:
    filepath: str

    @cached_property
    def raw_text(self):
        with open(self.filepath) as fp:
            return fp.read()


# @dataclass
# class DocumentSummary:
#     title: str
#     description: str
#     summary: str | None = None


@dataclass
class Model:
    name: str
    system_prompt_file: str
    reasoning_effort: ReasoningEffort = "medium"
    temperature: float | None = None
    response_format: str | None = None  # 'json_object' или None

    tools: list[dict] | None = field(default_factory=list)

    @cached_property
    def system_prompt(self):
        with open(self.system_prompt_file) as fp:
            return fp.read()


@dataclass
class Step:
    agent: str
    task: str


@dataclass
class CodeResult:
    code: str
    output: str | None = None
    error: str | None = None
    approved: bool = False
    edited: bool = False


@dataclass
class DiagramResult:
    diagram_type: Literal["mermaid", "plantuml"]
    code: str
    image_path: str | None = None


@dataclass
class AgentState:
    task_id: str
    user_prompt: str
    steps: list[Step] = field(default_factory=list)

    generated_codes: list[CodeResult] = field(default_factory=list)
    diagrams: list[DiagramResult] = field(default_factory=list)
    report_markdown: str | None = None

    current_step: int = 0
    iteration: int = 0
    max_iterations: int = 10
    finished: bool = False
    documents: list[Document] = field(default_factory=list)

    # Для Human-in-the-loop
    pending_approval: CodeResult | None = None
    working_dir: str = field(
        default_factory=lambda: tempfile.mkdtemp(prefix="reports_gen_")
    )


def prompt_path_file(prompt_file_name):
    return path_join("prompts", prompt_file_name)


class Orchestrator:
    MODELS_ROLES = {
        "document_analyst": Model(
            name="kimi-k2.5:cloud",
            system_prompt_file=prompt_path_file("document_analyst.md"),
        ),
        "supervisor": Model(
            name="qwen3.5:cloud",
            system_prompt_file=prompt_path_file("supervisor.md"),
            response_format="json_object",
        ),
        "coder": Model(
            name="qwen3-coder-next:cloud",
            system_prompt_file=prompt_path_file("coder.md"),
        ),
        "diagram_creator": Model(
            name="qwen3-coder-next:cloud",
            system_prompt_file=prompt_path_file("diagram_creator.md"),
            response_format="json_object",
        ),
        "formatter": Model(
            name="kimi-k2.5:cloud",
            system_prompt_file=prompt_path_file("formatter.md"),
        ),
    }

    def __init__(
        self, base_url: str = "http://127.0.0.1:11434/v1", api_key: str = "ollama"
    ) -> None:
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    # -----------------------------
    # LLM CALL
    # -----------------------------

    def run_agent(
        self, model: Model, messages: list[dict], **response_create_kwargs
    ) -> str:
        # response_format из модели, но можно переопределить через kwargs
        if "response_format" not in response_create_kwargs and model.response_format:
            response_create_kwargs["response_format"] = {"type": model.response_format}

        response = self.client.chat.completions.create(
            model=model.name,
            messages=messages,  # type: ignore
            tools=model.tools,  # type: ignore
            reasoning_effort=model.reasoning_effort,
            temperature=model.temperature,
            **response_create_kwargs,
        )

        return response.choices[0].message.content or ""

    # -----------------------------
    # DOCUMENT ANALYST
    # -----------------------------

    def document_analyst_agent(self, state: AgentState):
        """
        Анализирует документы и создает краткое описание каждого.
        В текущей версии возвращает None, делегируя супервизору.
        """
        pass

    # -----------------------------
    # SUPERVISOR
    # -----------------------------

    def supervisor_agent(self, state: AgentState) -> list[Step]:
        model = self.MODELS_ROLES["supervisor"]

        docs_info_map = map(
            lambda doc: doc.filepath + "\n" + doc.raw_text, state.documents
        )
        docs_context = "\n\n".join(docs_info_map)

        full_system_prompt = model.system_prompt.format(docs_context=docs_context)

        system_message = {"role": "system", "content": full_system_prompt}
        user_message = {"role": "user", "content": state.user_prompt}
        messages = [system_message, user_message]

        for i in range(5):
            response = self.run_agent(model=model, messages=messages)

            # Парсим ответ как JSON со списком шагов
            try:
                steps_data = json.loads(response)
                steps = [Step(agent=s["agent"], task=s["task"]) for s in steps_data]
                return steps
            except Exception as e:
                # Если не удалось распарсить JSON, возвращаем пустой список
                print(f"Ошибка парсинга ответа супервизора: {e}")
                return []

        raise ValueError("Не удалось получить json ответ от supervisor")

    # -----------------------------
    # CODER AGENT
    # -----------------------------

    def coder_agent(self, state: AgentState, step: Step) -> CodeResult:
        """
        Пишет код исходя из задачи.
        Возвращает результат с кодом, который требует Human-in-the-loop проверки.
        """
        model = self.MODELS_ROLES["coder"]

        # Собираем контекст
        codes_context = "\n\n".join(cr.code for cr in state.generated_codes)

        full_system_prompt = model.system_prompt.format(
            working_dir=state.working_dir,
            previous_codes=codes_context or "Нет предыдущего кода",
        )

        system_message = {"role": "system", "content": full_system_prompt}
        user_message = {
            "role": "user",
            "content": f"""Задача:
{step.task}

Общий контекст работы:
{state.user_prompt}
""",
        }
        messages = [system_message, user_message]

        code = self.run_agent(model, messages)

        # Создаем результат с ожиданием проверки
        result = CodeResult(code=code, approved=False, edited=False)
        state.pending_approval = result

        return result

    # -----------------------------
    # CODE EXECUTION
    # -----------------------------

    def execute_code(
        self, code: str, language: str = "python", use_docker: bool = False
    ) -> CodeResult:
        """
        Запускает код в безопасной среде.

        Args:
            code: Код для выполнения
            language: Язык программирования (пока поддерживается python)
            use_docker: Если True, запускать в Docker контейнере

        Returns:
            CodeResult с результатом выполнения
        """
        if language.lower() != "python":
            return CodeResult(
                code=code,
                error=f"Язык '{language}' пока не поддерживается для автоматического запуска",
            )

        try:
            # Создаем временный файл с кодом
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                dir=state.working_dir if "state" in locals() else None,
            ) as f:
                f.write(code)
                temp_path = f.name

            try:
                # Запускаем код
                result = subprocess.run(
                    ["python", temp_path],
                    capture_output=True,
                    text=True,
                    timeout=CODE_RUN_TIMEOUT,  # Таймаут 30 секунд
                    cwd=state.working_dir if "state" in locals() else None,
                )

                return CodeResult(
                    code=code,
                    output=result.stdout if result.stdout else None,
                    error=result.stderr if result.stderr else None,
                    approved=True,
                )
            finally:
                # Удаляем временный файл
                os.unlink(temp_path)

        except subprocess.TimeoutExpired:
            return CodeResult(
                code=code,
                error=f"Превышено время выполнения ({CODE_RUN_TIMEOUT} секунд)",
            )
        except Exception as e:
            return CodeResult(code=code, error=str(e))

    # -----------------------------
    # DIAGRAM CREATOR
    # -----------------------------

    def diagram_creator_agent(self, state: AgentState, step: Step) -> DiagramResult:
        """
        Создает код диаграммы в формате Mermaid или PlantUML.
        """
        model = self.MODELS_ROLES["diagram_creator"]

        # Собираем контекст
        diagrams_context = "\n\n".join(d.code for d in state.diagrams)

        full_system_prompt = model.system_prompt.format(
            previous_diagrams=diagrams_context or "Нет предыдущих диаграмм"
        )

        system_message = {"role": "system", "content": full_system_prompt}
        user_message = {
            "role": "user",
            "content": f"""Задача:
{step.task}

Общий контекст работы:
{state.user_prompt}

Верни ответ в формате JSON:
{{
    "type": "mermaid" или "plantuml",
    "code": "код диаграммы"
}}
""",
        }
        messages = [system_message, user_message]

        response = self.run_agent(model, messages)

        # Парсим ответ
        try:
            data = json.loads(response)
            diagram_type = data.get("type", "mermaid")
            diagram_code = data.get("code", "")

            # Сохраняем код диаграммы в файл
            ext = "mmd" if diagram_type == "mermaid" else "puml"
            diagram_filename = f"diagram_{len(state.diagrams) + 1}.{ext}"
            diagram_path = os.path.join(state.working_dir, diagram_filename)

            with open(diagram_path, "w") as f:
                f.write(diagram_code)

            result = DiagramResult(
                diagram_type=diagram_type,
                code=diagram_code,
                image_path=None,  # Будет сгенерировано позже при рендеринге
            )

            state.diagrams.append(result)
            return result

        except Exception as e:
            # Если не удалось распарсить JSON, создаем заглушку
            result = DiagramResult(
                diagram_type="mermaid",
                code=f"// Ошибка генерации: {e}\ngraph TD\n    A[Ошибка генерации диаграммы]",
                image_path=None,
            )
            state.diagrams.append(result)
            return result

    # -----------------------------
    # FORMATTER
    # -----------------------------

    def formatter_agent(self, state: AgentState) -> str:
        """
        Формирует финальный markdown отчёт.
        """
        model = self.MODELS_ROLES["formatter"]

        # Собираем весь контекст
        codes_context = "\n\n".join(
            f"```python\n{cr.code}\n```" if cr.code else ""
            for cr in state.generated_codes
        )

        diagrams_context = "\n\n".join(
            f"```{d.diagram_type}\n{d.code}\n```" for d in state.diagrams
        )

        docs_context = "\n\n".join(doc.raw_text for doc in state.documents)

        full_system_prompt = model.system_prompt.format(
            codes_context=codes_context or "Нет кода",
            diagrams_context=diagrams_context or "Нет диаграмм",
            docs_context=docs_context or "Нет документов",
        )

        system_message = {"role": "system", "content": full_system_prompt}
        user_message = {
            "role": "user",
            "content": f"""Задача пользователя:
{state.user_prompt}

Сформируй структурированный отчёт по выполненной работе.
""",
        }
        messages = [system_message, user_message]

        report = self.run_agent(model, messages)
        state.report_markdown = report

        return report

    # -----------------------------
    # HUMAN-IN-THE-LOOP
    # -----------------------------

    def approve_code(
        self, state: AgentState, approved: bool, edited_code: str | None = None
    ) -> CodeResult:
        """
        Обрабатывает решение пользователя по проверке кода.

        Args:
            state: Состояние агента
            approved: True если код одобрен
            edited_code: Отредактированный код пользователя (если был изменен)

        Returns:
            CodeResult с обновленным статусом
        """
        if state.pending_approval is None:
            raise ValueError("Нет кода на проверке")

        result = state.pending_approval

        if edited_code is not None:
            result.code = edited_code
            result.edited = True

        result.approved = approved

        if approved:
            state.generated_codes.append(result)

        state.pending_approval = None

        return result

    # -----------------------------
    # MAIN LOOP
    # -----------------------------

    def run(self, state: AgentState):
        """
        Основной цикл выполнения задачи.

        Возвращает состояние с результатами работы всех агентов.
        """
        # 0. DocumentAnalyst читает документы и сокращает их
        # state.documents_summary = self.document_analyst_agent(state)

        # 1. Supervisor создаёт план
        state.steps = self.supervisor_agent(state)

        # 2. Agent loop
        while not state.finished and state.iteration < state.max_iterations:
            state.iteration += 1

            if state.current_step >= len(state.steps):
                state.finished = True
                break

            step = state.steps[state.current_step]
            agent_name = step.agent

            if agent_name == "coder":
                code_result = self.coder_agent(state, step)
                # Код требует Human-in-the-loop проверки
                # Останавливаем выполнение и ждем подтверждения от пользователя
                break  # Выходим из цикла, ждем approve_code()

            elif agent_name == "diagram_creator":
                self.diagram_creator_agent(state, step)
                state.current_step += 1

            elif agent_name == "formatter":
                self.formatter_agent(state)
                state.finished = True
                break

            else:
                # Неизвестный агент, пропускаем
                state.current_step += 1

        return state

    def resume_after_approval(self, state: AgentState):
        """
        Возобновляет выполнение после Human-in-the-loop проверки.
        """
        state.current_step += 1
        return self.run(state)


# -----------------------------
# CONVENIENCE FUNCTIONS
# -----------------------------


def create_state(
    user_prompt: str,
    documents: list[Document] | None = None,
    task_id: str | None = None,
) -> AgentState:
    """
    Создает начальное состояние для Orchestrator.
    """
    import uuid

    return AgentState(
        task_id=task_id or str(uuid.uuid4()),
        user_prompt=user_prompt,
        documents=documents or [],
    )


if __name__ == "__main__":
    # Пример использования
    orchestrator = Orchestrator()

    state = create_state(
        user_prompt="Создай простую программу на Python, которая выводит приветствие",
        documents=[],
    )

    print(f"Запуск задачи {state.task_id}")
    print(f"User prompt: {state.user_prompt}")

    result_state = orchestrator.run(state)

    print(f"\nШаги плана: {len(result_state.steps)}")
    for i, step in enumerate(result_state.steps):
        print(f"  {i+1}. {step.agent}: {step.task}")

    if result_state.pending_approval:
        print("\n⚠️  Ожидается Human-in-the-loop проверка кода")
        print(f"Код:\n{result_state.pending_approval.code}")
