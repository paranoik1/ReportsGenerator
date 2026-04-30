import json
from copy import deepcopy
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

if TYPE_CHECKING:
    from models import FilePath

IdBlock = int


class DataBlock(BaseModel):
    description: str
    content: str


class DataBlocksRegistry:
    def __init__(self) -> None:
        self._blocks: dict[IdBlock, DataBlock] = {}
        self._id_counter = 0
        self.logger = structlog.get_logger(__name__)

    def add_block(self, block: DataBlock):
        self._blocks[self._id_counter] = block
        self._id_counter += 1

    def read_block(self, id: IdBlock) -> str | None:
        try:
            return self._blocks[id].content
        except KeyError:
            self.logger.info(f"Блок с {id=} не был найден")
            return None

    def get_blocks_context(self) -> str:
        return "\n".join(
            [f"[{id}] {block.description}" for id, block in self._blocks.items()]
        )

    def get_blocks(self) -> dict[IdBlock, DataBlock]:
        """
        Возвращает независимый словарь блоков (копию - deepcopy)
        """
        return deepcopy(self._blocks)

    def save(self, filepath: "FilePath"):
        blocks = {id: block.model_dump() for id, block in self._blocks.items()}
        with open(filepath, "w") as fp:
            json.dump(blocks, fp, ensure_ascii=False, indent=4)
