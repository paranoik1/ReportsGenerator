import pytest
from src.orchestrator.base import BaseOrchestrator, DataBlock


@pytest.mark.parametrize(
    "block_text, expected",
    [
        pytest.param(
            "===BLOCK_START===\nSQL скрипт создания таблиц\nCREATE TABLE users (id INT);\n===BLOCK_END===",
            [DataBlock(description="SQL скрипт создания таблиц", content="CREATE TABLE users (id INT);")],
            id="single_block",
        ),
        pytest.param(
            "===BLOCK_START===\nБлок 1\nСодержимое 1\n===BLOCK_END===\n\n===BLOCK_START===\nБлок 2\nСодержимое 2\n===BLOCK_END===",
            [
                DataBlock(description="Блок 1", content="Содержимое 1"),
                DataBlock(description="Блок 2", content="Содержимое 2"),
            ],
            id="two_blocks",
        ),
        pytest.param(
            "===BLOCK_START===\nМногострочный контент\nСтрока 1\nСтрока 2\nСтрока 3\n===BLOCK_END===",
            [DataBlock(description="Многострочный контент", content="Строка 1\nСтрока 2\nСтрока 3")],
            id="multiline_content",
        ),
        pytest.param(
            "Какой-то вводный текст\n\n===BLOCK_START===\nБлок после текста\nКонтент\n===BLOCK_END===\n\nЗавершающий текст",
            [DataBlock(description="Блок после текста", content="Контент")],
            id="blocks_with_surrounding_text",
        ),
        pytest.param(
            "",
            [],
            id="empty_string",
        ),
        pytest.param(
            "Просто текст без блоков",
            [],
            id="no_blocks",
        ),
        pytest.param(
            "===BLOCK_START===\nОдна строка\n===BLOCK_END===",
            [],
            id="single_line_block_skipped",
        ),
        pytest.param(
            "===BLOCK_START===\nWindows CRLF\r\nКонтент с CRLF\r\n===BLOCK_END===",
            [DataBlock(description="Windows CRLF", content="Контент с CRLF")],
            id="crlf_line_endings",
        ),
        pytest.param(
            "===BLOCK_START===\nOld Mac CR\rКонтент с CR\r===BLOCK_END===",
            [DataBlock(description="Old Mac CR", content="Контент с CR")],
            id="cr_line_endings",
        ),
        pytest.param(
            "===BLOCK_START===\nКод с пустыми строками\n\ndef foo():\n    pass\n\n===BLOCK_END===",
            [DataBlock(description="Код с пустыми строками", content="def foo():\n    pass")],
            id="block_with_blank_lines",
        ),
        pytest.param(
            "===BLOCK_START===\nСпецсимволы\nSELECT * FROM users WHERE name = 'O''Brien';\n===BLOCK_END===",
            [DataBlock(description="Спецсимволы", content="SELECT * FROM users WHERE name = 'O''Brien';")],
            id="special_characters",
        ),
        pytest.param(
            "===BLOCK_START===\nПустой контент\n\n===BLOCK_END===",
            [],
            id="empty_content_after_description",
        ),
    ],
)
def test_parse_blocks(block_text: str, expected: list[DataBlock]):
    result = BaseOrchestrator.parse_blocks(block_text)
    assert result == expected
