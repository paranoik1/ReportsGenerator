from os import getenv
from patchright.sync_api import sync_playwright
from dotenv import load_dotenv
from ollama import Client
from pathlib import Path

load_dotenv()


OLLAMA_API_KEY = getenv("OLLAMA_API_KEY", None)
assert OLLAMA_API_KEY is not None, "Необходимо добавить OLLAMA_API_KEY в .env"

OLLAMA_MODEL = getenv("OLLAMA_MODEL", None)
assert OLLAMA_MODEL is not None, "Необходимо добавить OLLAMA_MODEL в .env"

PROMPT_FILE = Path('prompts') / "reports-agent" / "v2-markdown.md"


with open(PROMPT_FILE) as fp:
    SYSTEM_PROMPT: str = fp.read()


def run_ollama_agent(user_prompt: str) -> str:
    client = Client(
        host="https://ollama.com",
        headers={"Authorization": "Bearer " + OLLAMA_API_KEY},  # type: ignore
    )

    messages = [
        {
            "role": "user",
            "content": SYSTEM_PROMPT.format(prompt=user_prompt),
        },
    ]

    response = client.chat(OLLAMA_MODEL, messages=messages, think="high") # type: ignore
    return response.message.content


# NOTE: Не доделан
def run_qwen_agent(user_prompt: str) -> str:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context("context-playwright", headless=False)
        page = context.new_page()
        page.goto("https://chat.qwen.ai/")
        page.wait_for_load_state()

        page.type('textarea', user_prompt)

    return ""


if __name__ == '__main__':
    run_qwen_agent("")
