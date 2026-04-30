from typing import TYPE_CHECKING

from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Mm, Pt

if TYPE_CHECKING:
    from docx.styles.style import BaseStyle, ParagraphStyle


def configure_normal_style(doc: DocumentObject) -> "ParagraphStyle":
    """
    Настраивает стиль по умолчанию
    - Normal: Times New Roman 14, межстрочный 1.5, отступ первой строки 1.25 см
    """
    normal_style: "ParagraphStyle" = doc.styles["Normal"]

    # Настройка шрифта
    normal_style.font.name = "Times New Roman"
    normal_style.font.size = Pt(14)

    # Настройка абзаца (отступы и интервалы)
    normal_style.paragraph_format.first_line_indent = Cm(1.25)  # Красная строка
    normal_style.paragraph_format.line_spacing = 1.5  # Межстрочный интервал
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(0)
    normal_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    return normal_style


def configure_lists_styles(
    doc: DocumentObject, base_style: "ParagraphStyle"
) -> list["ParagraphStyle"]:
    list_style: "ParagraphStyle" = doc.styles["List Bullet"]
    list_style.base_style = base_style
    list_style.paragraph_format.first_line_indent = Cm(1.25)  # Красная строка
    list_style.paragraph_format.left_indent = Cm(0)  # Красная строка
    list_style

    list_numbered_style: "ParagraphStyle" = doc.styles["List Number"]
    list_numbered_style.base_style = base_style
    list_numbered_style.paragraph_format.first_line_indent = Cm(1.25)  # Красная строка
    list_numbered_style.paragraph_format.left_indent = Cm(0)  # Красная строка

    return [list_numbered_style, list_style]


def create_code_style(doc: DocumentObject, base_style: "BaseStyle") -> "ParagraphStyle":
    code_style = doc.styles.add_style("Code Style", WD_STYLE_TYPE.PARAGRAPH)

    # Наследуем базовые настройки от base_style (чтобы не копировать всё вручную)
    code_style.base_style = base_style

    # Переопределяем параметры для кода
    code_style.font.name = "Courier New"
    code_style.font.size = Pt(11)

    # Убираем отступ первой строки и уменьшаем межстрочный интервал
    code_style.paragraph_format.first_line_indent = Cm(0)
    code_style.paragraph_format.line_spacing = 1.0
    code_style.paragraph_format.space_before = Mm(2)
    # отступ снизу в мм
    code_style.paragraph_format.space_after = Mm(2)
    code_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    return code_style


def create_content_table_style(
    doc: DocumentObject, base_style: "BaseStyle"
) -> "ParagraphStyle":
    table_content_style = doc.styles.add_style("Table Content", WD_STYLE_TYPE.PARAGRAPH)

    table_content_style.base_style = base_style

    table_content_style.font.name = "Times New Roman"
    table_content_style.font.size = Pt(14)

    table_content_style.paragraph_format.first_line_indent = Cm(0)
    table_content_style.paragraph_format.line_spacing = 1.15

    return table_content_style


def setup_document_styles(doc: DocumentObject):
    """
    Настраивает стили документа:

    - Normal:
    Times New Roman 14 pt,
    межстрочный интервал 1.5,
    выравнивание по ширине,
    отступ первой строки 1.25 см,
    без дополнительных интервалов до и после абзаца.

    - List Bullet и List Number:
    наследуют Normal,
    шрифт и интервалы как у основного текста,
    отступ первой строки 1.25 см,
    левый отступ 0 см.

    - Code Style:
    Courier New 11 pt,
    межстрочный интервал 1.0,
    без отступа первой строки,
    небольшие интервалы сверху и снизу,
    используется для фрагментов кода.

    - Table Content:
    Times New Roman 14 pt,
    межстрочный интервал 1.15,
    без отступа первой строки,
    применяется для текста внутри таблиц.
    """

    # Получаем существующий стиль Normal (встроенный стиль по умолчанию)
    normal_style = configure_normal_style(doc)

    configure_lists_styles(doc, normal_style)
    # Создаём новый пользовательский стиль CodeStyle на основе Normal
    create_code_style(doc, normal_style)
    create_content_table_style(doc, normal_style)

    return doc
