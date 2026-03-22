from pydantic import BaseModel
from typing import Any

IdBlock = str


class DataBlock(BaseModel):
    description: str
    content: str


class DataBlockWithId(DataBlock):
    id: IdBlock

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        id = data.get('id')
        description = data.get('description')
        content = data.get('content')

        if not (id and description and content):
            raise ValueError(f"Недостаточно данных для создания блоков, полученные данные: {data=}")

        return cls(
            id=id,
            description=description,
            content=content
        )


class DataBlocksRegistry:
    def __init__(self) -> None:
        self.blocks: dict[IdBlock, DataBlock] = {}

    def add_block_from_params(self, id: IdBlock, description: str, content: str):
        self.blocks[id] = DataBlock(description=description, content=content)

    def add_block(self, id: IdBlock, block: DataBlock):
        self.blocks[id] = block

    def add_block_from_dto(self, block: DataBlockWithId):
        self.blocks[block.id] = DataBlock(description=block.description, content=block.content)

    def read_block(self, id: IdBlock) -> str:
        try:
            return self.blocks[id].content
        except KeyError:
            raise ValueError(f'Блок с {id=} не был найден')
        
    def get_blocks_context(self):
        return "\n".join(
            [
                f'{id} - {block.description}' 
                for id, block in self.blocks.items()
            ]
        )


if __name__ == '__main__':
    dbr = DataBlocksRegistry()
    # print("Добавление блоков данных в registry")
    # dbr.add_block_from_params('content', 'Контент документа', ';....;')
    # dbr.add_block('content_2', DataBlock('2 content doc', ';;.....'))
    # dbr.add_block_from_dto(DataBlockWithId('description', 'Контент документа', 'content_id'))

    # print("Вывод контекста с блоков")
    # print(dbr.get_blocks_context())
    # print("Чтение блока 'content'")
    # print(dbr.read_block('content'))
