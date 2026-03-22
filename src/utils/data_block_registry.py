import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from models import FilePath

IdBlock = str


class DataBlock(BaseModel):
    description: str
    content: str


class DataBlockWithId(DataBlock):
    id: IdBlock

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        id = data.get("id")
        description = data.get("description")
        content = data.get("content")

        if not (id and description and content):
            raise ValueError(
                f"Недостаточно данных для создания блоков, полученные данные: {data=}"
            )

        return cls(id=id, description=description, content=content)


class DataBlocksRegistry:
    def __init__(self) -> None:
        self._blocks: dict[IdBlock, DataBlock] = {}

    def add_block_from_params(self, id: IdBlock, description: str, content: str):
        self._blocks[id] = DataBlock(description=description, content=content)

    def add_block(self, id: IdBlock, block: DataBlock):
        self._blocks[id] = block

    def add_block_from_dto(self, block: DataBlockWithId):
        self._blocks[block.id] = DataBlock(
            description=block.description, content=block.content
        )

    def read_block(self, id: IdBlock) -> str:
        try:
            return self._blocks[id].content
        except KeyError:
            raise ValueError(f"Блок с {id=} не был найден")

    def get_blocks_context(self) -> str:
        return "\n".join(
            [f"{id} - {block.description}" for id, block in self._blocks.items()]
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


if __name__ == "__main__":
    dbr = DataBlocksRegistry()
    dbr.add_block("id", DataBlock(description="Описание", content="Content"))
    blocks = dbr.get_blocks()
    blocks["id"].content = ""
    print(dbr.get_blocks())
    print(blocks)
