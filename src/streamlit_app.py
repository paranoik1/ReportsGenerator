"""
Streamlit приложение для AI Report Generator
Заменяет Flask + HTML/CSS/JS интерфейс
"""

import os
import uuid
import time
import base64
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import streamlit as st
import requests
from PIL import Image
import io

# Конфигурация страницы
st.set_page_config(
    page_title="AI Report Generator",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Константы
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")
MAX_FILE_SIZE_MB = 10
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.md'}
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

# Стили CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        text-align: center;
        margin-bottom: 2rem;
    }
    .card {
        background-color: #f9f9f9;
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .image-preview-container {
        border: 2px dashed #ccc;
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        background-color: #fafafa;
    }
    .status-badge {
        padding: 0.5rem 1rem;
        border-radius: 20px;
        font-weight: bold;
        display: inline-block;
    }
    .status-queued { background-color: #6c757d; color: white; }
    .status-processing { background-color: #0d6efd; color: white; }
    .status-done { background-color: #198754; color: white; }
    .status-error { background-color: #dc3545; color: white; }
</style>
""", unsafe_allow_html=True)


@dataclass
class ImageItem:
    """Элемент изображения с описанием"""
    id: str
    file: Optional[bytes] = None
    filename: str = ""
    description: str = ""
    preview_data: Optional[bytes] = None
    ai_generated: bool = False
    ai_model: str = ""


@dataclass
class AgentConfig:
    """Конфигурация AI агента"""
    model: str = ""
    base_url: str = ""
    api_key: str = ""


def init_session_state():
    """Инициализация состояния сессии"""
    if 'images' not in st.session_state:
        st.session_state.images = []
    if 'image_counter' not in st.session_state:
        st.session_state.image_counter = 0
    if 'task_id' not in st.session_state:
        st.session_state.task_id = None
    if 'polling' not in st.session_state:
        st.session_state.polling = False
    if 'current_status' not in st.session_state:
        st.session_state.current_status = None
    if 'result_data' not in st.session_state:
        st.session_state.result_data = None


def generate_image_id() -> str:
    """Генерация уникального ID для изображения"""
    st.session_state.image_counter += 1
    return f"img_{st.session_state.image_counter}_{uuid.uuid4().hex[:8]}"


def validate_file(file, allowed_extensions: set, max_size_mb: int = MAX_FILE_SIZE_MB) -> tuple[bool, str]:
    """Валидация файла"""
    file_ext = Path(file.name).suffix.lower()
    
    if file_ext not in allowed_extensions:
        return False, f"Неподдерживаемый формат: {file_ext}"
    
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > max_size_mb * 1024 * 1024:
        return False, f"Файл слишком большой ({file_size / 1024 / 1024:.2f} MB, макс. {max_size_mb} MB)"
    
    return True, ""


def add_image_from_upload(uploaded_file, description: str = "", ai_generated: bool = False, ai_model: str = ""):
    """Добавление изображения из загруженного файла"""
    image_id = generate_image_id()
    
    # Чтение файла в байты
    file_bytes = uploaded_file.getvalue()
    
    # Создание превью
    image = Image.open(io.BytesIO(file_bytes))
    preview = io.BytesIO()
    image.thumbnail((300, 300))
    image.save(preview, format=image.format or 'PNG')
    preview_data = preview.getvalue()
    
    image_item = ImageItem(
        id=image_id,
        file=file_bytes,
        filename=uploaded_file.name,
        description=description,
        preview_data=preview_data,
        ai_generated=ai_generated,
        ai_model=ai_model
    )
    
    st.session_state.images.append(image_item)


def add_image_from_clipboard(image_data: bytes, description: str = ""):
    """Добавление изображения из буфера обмена"""
    image_id = generate_image_id()
    
    # Создание превью
    image = Image.open(io.BytesIO(image_data))
    preview = io.BytesIO()
    image.thumbnail((300, 300))
    image.save(preview, format=image.format or 'PNG')
    preview_data = preview.getvalue()
    
    image_item = ImageItem(
        id=image_id,
        file=image_data,
        filename=f"clipboard_{image_id}.png",
        description=description,
        preview_data=preview_data
    )
    
    st.session_state.images.append(image_item)


def remove_image(image_id: str):
    """Удаление изображения из списка"""
    st.session_state.images = [img for img in st.session_state.images if img.id != image_id]


def clear_all_images():
    """Очистка всех изображений"""
    st.session_state.images = []
    st.session_state.image_counter = 0


def render_image_preview(image_item: ImageItem, index: int):
    """Отрисовка превью изображения с элементами управления"""
    col1, col2, col3 = st.columns([1, 3, 1])
    
    with col1:
        if image_item.preview_data:
            st.image(image_item.preview_data, use_container_width=True)
        else:
            st.write("Нет превью")
    
    with col2:
        st.caption(f"📁 {image_item.filename}")
        
        # Поле описания
        description_key = f"desc_{image_item.id}"
        new_description = st.text_area(
            "Описание изображения",
            value=image_item.description,
            key=description_key,
            help="Опишите, что изображено на этом фото/скриншоте",
            height=70
        )
        
        # Обновление описания в состоянии
        if new_description != image_item.description:
            st.session_state.images[index].description = new_description
        
        # Опция AI-генерации описания
        ai_enabled = st.checkbox(
            "🤖 Сгенерировать описание с помощью ИИ",
            value=image_item.ai_generated,
            key=f"ai_{image_item.id}",
            help="Автоматически создать описание изображения используя выбранную модель"
        )
        
        if ai_enabled:
            ai_model = st.selectbox(
                "Модель для анализа",
                options=["gpt-4-vision", "claude-3-vision", "llava", "qwen-vl"],
                index=0,
                key=f"model_{image_item.id}",
                label_visibility="collapsed"
            )
            
            # Сохранение настроек AI
            st.session_state.images[index].ai_generated = True
            st.session_state.images[index].ai_model = ai_model
            
            # Кнопка генерации
            if st.button("✨ Сгенерировать", key=f"gen_{image_item.id}"):
                with st.spinner("Анализируем изображение..."):
                    # TODO: Здесь будет вызов API для генерации описания
                    # Пока заглушка
                    time.sleep(1)
                    st.session_state.images[index].description = "[AI] На изображении показано..."
                    st.rerun()
        else:
            st.session_state.images[index].ai_generated = False
    
    with col3:
        if st.button("❌", key=f"remove_{image_item.id}", help="Удалить изображение"):
            remove_image(image_item.id)
            st.rerun()


def render_ai_config_section():
    """Отрисовка секции настройки AI моделей"""
    with st.expander("⚙️ Настройки AI моделей", icon="⚙️"):
        st.markdown("*Настройте подключение к AI моделям для каждого агента. Если оставить пустым — будут использоваться настройки по умолчанию.*")
        
        cols = st.columns(2)
        
        agents = [
            ("📄 Document Analyst", "document_analyst", "Анализ загруженных документов"),
            ("📋 Template Analyst", "template_analyst", "Анализ шаблона отчёта"),
            ("💬 User Prompt Analyst", "user_prompt_analyst", "Анализ запроса пользователя"),
            ("📝 Formatter", "formatter", "Форматирование итогового отчёта"),
        ]
        
        configs = {}
        
        for i, (name, prefix, description) in enumerate(agents):
            col_idx = i % 2
            with cols[col_idx]:
                with st.container(border=True):
                    st.markdown(f"**{name}**")
                    st.caption(description)
                    
                    model = st.text_input(
                        "Модель",
                        placeholder="например: kimi-k2-thinking:cloud",
                        key=f"model_{prefix}",
                        label_visibility="collapsed"
                    )
                    
                    base_url = st.text_input(
                        "Base URL",
                        placeholder="например: http://127.0.0.1:11434/v1",
                        key=f"base_url_{prefix}",
                        label_visibility="collapsed"
                    )
                    
                    api_key = st.text_input(
                        "API Key",
                        type="password",
                        placeholder="например: ollama",
                        key=f"api_key_{prefix}",
                        label_visibility="collapsed"
                    )
                    
                    configs[prefix] = AgentConfig(model=model, base_url=base_url, api_key=api_key)
        
        return configs


def render_main_form():
    """Отрисовка основной формы создания задачи"""
    st.markdown("### 📝 Новая задача")
    
    # Описание задачи
    prompt = st.text_area(
        "Описание задачи",
        placeholder="Опишите, что нужно сделать в практической работе...",
        height=150,
        key="main_prompt"
    )
    
    # Загрузка файлов
    uploaded_files = st.file_uploader(
        "Прикрепить файлы (PDF, DOCX, TXT, MD)",
        type=['pdf', 'docx', 'txt', 'md'],
        accept_multiple_files=True,
        help="Можно выбрать несколько файлов"
    )
    
    # Валидация файлов
    valid_files = []
    if uploaded_files:
        for file in uploaded_files:
            is_valid, error_msg = validate_file(file, ALLOWED_EXTENSIONS)
            if is_valid:
                valid_files.append(file)
            else:
                st.warning(f"⚠️ Файл '{file.name}' пропущен: {error_msg}")
    
    # Шаблон отчёта
    template_file = st.file_uploader(
        "Пример отчёта (опционально)",
        type=['docx', 'pdf'],
        help="Файл-образец, по структуре которого нужно сформировать отчёт"
    )
    
    # Секция изображений
    st.markdown("### 🖼️ Изображения")
    st.caption("Для каждого изображения укажите, что на нём изображено")
    
    # Drag & drop зона (упрощённая реализация через file_uploader)
    uploaded_images = st.file_uploader(
        "Перетащите изображения сюда или выберите файлы",
        type=['png', 'jpg', 'jpeg', 'gif', 'webp'],
        accept_multiple_files=True,
        key="image_uploader"
    )
    
    if uploaded_images:
        for img_file in uploaded_images:
            # Проверка, не добавлено ли уже это изображение
            already_exists = any(img.filename == img_file.name for img in st.session_state.images)
            if not already_exists:
                add_image_from_upload(img_file)
    
    # Отображение добавленных изображений
    if st.session_state.images:
        st.markdown("#### Добавленные изображения:")
        for idx, image_item in enumerate(st.session_state.images):
            with st.container(border=True):
                render_image_preview(image_item, idx)
    
    # Кнопки управления изображениями
    col1, col2 = st.columns(2)
    with col1:
        if st.button("+ Добавить изображение", use_container_width=True):
            # Создаём фиктивный файл для триггера
            st.info("💡 Используйте загрузчик файлов выше для добавления изображений")
    
    with col2:
        if st.button("🗑️ Очистить все изображения", use_container_width=True, type="secondary"):
            clear_all_images()
            st.rerun()
    
    # Кнопка запуска
    st.markdown("---")
    submit_col1, submit_col2 = st.columns([3, 1])
    
    with submit_col1:
        submit_button = st.button(
            "🚀 Запустить генерацию",
            type="primary",
            use_container_width=True,
            disabled=not prompt or st.session_state.polling
        )
    
    return prompt, valid_files, template_file, submit_button


def render_progress_section(task_id: str):
    """Отрисовка секции прогресса выполнения"""
    st.markdown("### ⏳ Прогресс выполнения")
    
    status_placeholder = st.empty()
    progress_placeholder = st.empty()
    
    # Индикатор статуса
    status_badge = """
    <div class="status-badge status-processing">
        Выполняется...
    </div>
    """
    status_placeholder.markdown(status_badge, unsafe_allow_html=True)
    
    # Анимация загрузки
    progress_placeholder.progress(0, text="Генерация отчёта...")
    
    return status_placeholder, progress_placeholder


def render_result_section(result_data: dict):
    """Отрисовка секции результата"""
    st.success("✅ Задача выполнена!", icon="✅")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Скачивание DOCX
        if 'docx_data' in result_data:
            st.download_button(
                label="📥 Скачать DOCX",
                data=result_data['docx_data'],
                file_name=f"report_{result_data['task_id'][:8]}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True
            )
    
    with col2:
        # Просмотр HTML
        if 'html_url' in result_data:
            st.link_button(
                "👁️ Просмотреть HTML",
                url=result_data['html_url'],
                use_container_width=True
            )
    
    # Предпросмотр HTML (если доступен)
    if 'html_content' in result_data:
        with st.expander("📄 Предпросмотр HTML"):
            st.markdown(result_data['html_content'], unsafe_allow_html=True)


def render_error_section(error_message: str):
    """Отрисовка секции ошибки"""
    st.error(f"❌ Ошибка: {error_message}", icon="❌")


def poll_task_status(task_id: str, status_placeholder, progress_placeholder) -> Optional[dict]:
    """Опрос статуса задачи"""
    try:
        response = requests.get(f"{API_BASE_URL}/status/{task_id}", timeout=5)
        
        if response.status_code == 200:
            status_data = response.json()
            status = status_data.get('status', 'unknown')
            
            # Обновление индикатора статуса
            status_class = {
                'queued': 'status-queued',
                'processing': 'status-processing',
                'done': 'status-done',
                'error': 'status-error'
            }.get(status, 'status-queued')
            
            status_text = {
                'queued': 'В очереди',
                'processing': 'Выполняется',
                'done': 'Готово',
                'error': 'Ошибка'
            }.get(status, status)
            
            status_badge = f"""
            <div class="status-badge {status_class}">
                {status_text}
            </div>
            """
            status_placeholder.markdown(status_badge, unsafe_allow_html=True)
            
            if status == 'done':
                progress_placeholder.progress(1.0, text="Завершено!")
                
                # Получение результата
                docx_response = requests.get(f"{API_BASE_URL}/download/{task_id}", timeout=10)
                html_response = requests.get(f"{API_BASE_URL}/view_html/{task_id}", timeout=10)
                
                result_data = {
                    'task_id': task_id,
                    'docx_data': docx_response.content if docx_response.status_code == 200 else None,
                    'html_url': f"{API_BASE_URL}/view_html/{task_id}",
                    'html_content': html_response.text if html_response.status_code == 200 else None
                }
                
                return result_data
            
            elif status == 'error':
                progress_placeholder.empty()
                raise Exception(status_data.get('error', 'Неизвестная ошибка'))
            
            elif status == 'processing':
                progress_placeholder.progress(0.5, text="Обработка...")
            
            return None
        
        else:
            raise Exception(f"Ошибка сервера: {response.status_code}")
    
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка соединения: {str(e)}")


def submit_task(prompt: str, files: list, template_file, images: list[ImageItem], agent_configs: dict):
    """Отправка задачи на сервер"""
    try:
        # Подготовка данных для отправки
        form_data = {'prompt': prompt}
        files_payload = []
        
        # Добавление файлов
        for file in files:
            files_payload.append(('files', (file.name, file.getvalue(), 'application/octet-stream')))
        
        # Добавление шаблона
        if template_file:
            files_payload.append(('template', (template_file.name, template_file.getvalue(), 'application/octet-stream')))
        
        # Добавление изображений с описаниями
        for idx, image_item in enumerate(images):
            if image_item.file and image_item.description:
                files_payload.append((
                    f'image_{idx}',
                    (image_item.filename, image_item.file, 'image/png')
                ))
                form_data[f'desc_{idx}'] = image_item.description
        
        # Добавление конфигураций AI агентов
        has_custom_config = False
        for prefix, config in agent_configs.items():
            if config.model:
                form_data[f'model_{prefix}'] = config.model
                has_custom_config = True
            if config.base_url:
                form_data[f'base_url_{prefix}'] = config.base_url
            if config.api_key:
                form_data[f'api_key_{prefix}'] = config.api_key
        
        # Отправка запроса
        response = requests.post(
            f"{API_BASE_URL}/start",
            data=form_data,
            files=files_payload,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()['task_id']
        else:
            error_data = response.json()
            raise Exception(error_data.get('error', 'Ошибка создания задачи'))
    
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка соединения с сервером: {str(e)}")


def main():
    """Основная функция приложения"""
    
    # Заголовок
    st.markdown('<h1 class="main-header">🤖 Генератор отчётов</h1>', unsafe_allow_html=True)
    
    # Инициализация состояния
    init_session_state()
    
    # Боковая панель с информацией
    with st.sidebar:
        st.header("ℹ️ Информация")
        st.markdown("""
        ### Как использовать:
        1. Опишите задачу
        2. Прикрепите файлы
        3. Добавьте изображения с описаниями
        4. Настройте AI модели (опционально)
        5. Запустите генерацию
        
        ---
        **Статус сервера:** 
        """)
        
        # Проверка доступности сервера
        try:
            health_response = requests.get(f"{API_BASE_URL}/", timeout=2)
            if health_response.status_code == 200:
                st.success("🟢 Сервер доступен")
            else:
                st.error("🔴 Сервер недоступен")
        except:
            st.error("🔴 Сервер недоступен")
        
        st.caption(f"API: {API_BASE_URL}")
        
        # Кнопка сброса
        if st.button("🔄 Сбросить форму", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key not in ['polling']:
                    del st.session_state[key]
            st.rerun()
    
    # Основной контент
    ai_configs = render_ai_config_section()
    
    prompt, files, template_file, submit_button = render_main_form()
    
    # Обработка отправки формы
    if submit_button:
        try:
            with st.spinner("🚀 Запуск задачи..."):
                # Проверка наличия описаний у изображений
                images_without_desc = [img for img in st.session_state.images if not img.description]
                if images_without_desc:
                    st.warning("⚠️ У некоторых изображений нет описания. Заполните их или удалите изображения.")
                    st.stop()
                
                # Отправка задачи
                task_id = submit_task(
                    prompt=prompt,
                    files=files,
                    template_file=template_file,
                    images=st.session_state.images,
                    agent_configs=ai_configs
                )
                
                st.session_state.task_id = task_id
                st.session_state.polling = True
                st.session_state.result_data = None
                
                st.rerun()
        
        except Exception as e:
            render_error_section(str(e))
            st.session_state.polling = False
    
    # Опрос статуса задачи
    if st.session_state.polling and st.session_state.task_id:
        status_placeholder, progress_placeholder = render_progress_section(st.session_state.task_id)
        
        try:
            result = poll_task_status(
                st.session_state.task_id,
                status_placeholder,
                progress_placeholder
            )
            
            if result:
                st.session_state.result_data = result
                st.session_state.polling = False
                st.session_state.current_status = 'done'
                st.rerun()
            
            else:
                time.sleep(2)
                st.rerun()
        
        except Exception as e:
            render_error_section(str(e))
            st.session_state.polling = False
    
    # Отображение результата
    if st.session_state.result_data:
        render_result_section(st.session_state.result_data)
        
        # Кнопка новой задачи
        if st.button("📝 Создать новую задачу", type="primary"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


if __name__ == "__main__":
    main()
