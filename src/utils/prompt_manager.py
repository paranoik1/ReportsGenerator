"""Менеджер промптов для рендеринга шаблонов через Jinja2."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class PromptManager:
    """Класс для загрузки и рендеринга промптов из шаблонов Jinja2."""

    def __init__(
        self,
        prompts_dir: str | Path | None = None,
        trim_blocks: bool = True,
        lstrip_blocks: bool = True,
    ) -> None:
        """
        Инициализация менеджера промптов.

        Args:
            prompts_dir: Директория с шаблонами промптов. Если None, используется
                         директория по умолчанию './prompts'.
            trim_blocks: Удалять первый перевод строки после блока.
            lstrip_blocks: Удалять пробелы в начале строки перед блоком.
        """
        if prompts_dir is None:
            prompts_dir = Path(__file__).parent.parent.parent / "prompts"

        self.prompts_dir = Path(prompts_dir)
        self.env = Environment(
            loader=FileSystemLoader(self.prompts_dir),
            trim_blocks=trim_blocks,
            lstrip_blocks=lstrip_blocks,
            undefined=StrictUndefined,
        )

    def render(self, template_name: str, **context: Any) -> str:
        """
        Рендеринг шаблона промпта.

        Args:
            template_name: Имя файла шаблона в prompts_dir.
            **context: Переменные для подстановки в шаблон.

        Returns:
            Отрендеренный промпт.

        Raises:
            TemplateNotFound: Если шаблон не найден.
            UndefinedError: Если в шаблоне используется неопределённая переменная.
        """
        template = self.env.get_template(template_name)
        return template.render(**context)
