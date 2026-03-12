"""
RAG система для поиска информации в документации по Mermaid и PlantUML.
Использует RapidFuzz для нечёткого поиска.
"""
from pathlib import Path
from dataclasses import dataclass

from rapidfuzz import fuzz, process


@dataclass
class DocumentChunk:
    """Фрагмент документа для поиска."""
    source: str  # Имя файла источника
    section: str  # Название раздела
    content: str  # Текст фрагмента (включая примеры кода)


class DocumentationRAG:
    """
    RAG система для поиска по документации Mermaid/PlantUML.
    Использует RapidFuzz для нечёткого поиска по ключевым словам.
    """
    
    def __init__(self, docs_dir: str = "docs"):
        self.docs_dir = Path(docs_dir)
        self.chunks: list[DocumentChunk] = []
        self._load_documents()
    
    def _load_documents(self):
        """Загружает документы из директории docs."""
        if not self.docs_dir.exists():
            return
        
        for md_file in self.docs_dir.glob("*.md"):
            self._parse_markdown(md_file)
    
    def _parse_markdown(self, filepath: Path):
        """
        Парсит Markdown файл на разделы и примеры кода.
        Разбивает по заголовкам ## и ###.
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lines = content.split('\n')
        current_section = ""
        current_content: list[str] = []
        
        for line in lines:
            # Проверка на заголовок второго уровня
            if line.startswith('## '):
                # Сохраняем предыдущий раздел
                if current_section and current_content:
                    self._add_chunk(
                        source=filepath.name,
                        section=current_section,
                        content='\n'.join(current_content)
                    )
                current_section = line[3:].strip()
                current_content = []
            
            # Проверка на заголовок третьего уровня
            elif line.startswith('### '):
                if current_section and current_content:
                    self._add_chunk(
                        source=filepath.name,
                        section=current_section,
                        content='\n'.join(current_content)
                    )
                current_section = line[4:].strip()
                current_content = []
            
            else:
                current_content.append(line)
        
        # Сохраняем последний раздел
        if current_section and current_content:
            self._add_chunk(
                source=filepath.name,
                section=current_section,
                content='\n'.join(current_content)
            )
    
    def _add_chunk(self, source: str, section: str, content: str):
        """Добавляет фрагмент в коллекцию."""
        if not content.strip():
            return
        
        self.chunks.append(DocumentChunk(
            source=source,
            section=section,
            content=content
        ))
    
    def search(self, query: str, top_k: int = 3) -> list[DocumentChunk]:
        """
        Ищет наиболее релевантные фрагменты по запросу.
        Использует RapidFuzz для нечёткого поиска.
        
        Args:
            query: Поисковый запрос
            top_k: Количество результатов
        
        Returns:
            Список наиболее релевантных фрагментов
        """
        if not self.chunks:
            return []
        
        # Создаём поисковые индексы для каждого чанка
        # Ищем по названию раздела и полному содержимому
        search_texts = []
        for i, chunk in enumerate(self.chunks):
            search_text = f"{chunk.section} {chunk.content}"
            search_texts.append((search_text, i))
        
        # Используем process.extract для поиска лучших совпадений
        # token_set_ratio хорошо работает с перестановками слов
        results = process.extract(
            query,
            search_texts,
            scorer=fuzz.token_set_ratio,
            limit=top_k
        )
        
        # Возвращаем чанки в порядке релевантности
        return [self.chunks[idx] for _, _, idx in results]
    
    def get_context_for_query(self, query: str, top_k: int = 3) -> str:
        """
        Возвращает контекст для запроса в формате для промпта.
        
        Args:
            query: Поисковый запрос
            top_k: Количество результатов
        
        Returns:
            Строка с контекстом для включения в промпт
        """
        results = self.search(query, top_k)
        
        if not results:
            return "Не найдено релевантной документации."
        
        context_parts = []
        for chunk in results:
            part = f"## {chunk.section} (из {chunk.source})\n"
            part += f"{chunk.content}\n"
            context_parts.append(part)
        
        return "\n---\n\n".join(context_parts)


# Пример использования
if __name__ == "__main__":
    rag = DocumentationRAG()
    
    print("Загружено фрагментов:", len(rag.chunks))
    print("RapidFuzz установлен ✓")
    
    # Тестовый поиск
    query = "блок-схема стрелка связь"
    print(f"\nПоиск по запросу: {query}")
    
    results = rag.search(query)
    for i, chunk in enumerate(results, 1):
        print(f"\n{i}. {chunk.section} (из {chunk.source})")
        print(f"   {chunk.content[:100]}...")
