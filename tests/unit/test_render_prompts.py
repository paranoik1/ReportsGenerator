import pytest

from src.utils.prompt_manager import PromptManager

prompt_manager = PromptManager()


@pytest.mark.parametrize(
    "prompt_name, context",
    [
        pytest.param(
            "formatter.j2",
            {"images_context": "Нет изображений", "blocks_context": "1 - Блок"},
            id="formatter_prompt",
        ),
        pytest.param("template_analyst.j2", {}, id="template_analyst_prompt"),
        pytest.param("document_analyst.j2", {}, id="document_analyst_prompt"),
        pytest.param("user_prompt_analyst.j2", {}, id="user_prompt_analyst"),
    ],
)
def test_render(prompt_name: str, context: dict):
    prompt_manager.render(prompt_name, **context)
