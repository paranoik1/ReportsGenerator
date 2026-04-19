const state = {
    taskId: null,
    pollInterval: null,
    imageCount: 0,
    images: []  // Хранилище для изображений: {file, description, id, preview}
};

let form, progressSection, resultSection, errorSection, dropZone, previewsContainer;

function initApp() {
    form = document.getElementById('taskForm');
    progressSection = document.getElementById('progressSection');
    resultSection = document.getElementById('resultSection');
    errorSection = document.getElementById('errorSection');
    dropZone = document.getElementById('dropZone');
    previewsContainer = document.getElementById('previewsContainer');

    initDragAndDrop();
    initFormSubmit();
}

// Обработка drag-and-drop
function initDragAndDrop() {
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
    });

    dropZone.addEventListener('drop', handleDrop, false);
    document.addEventListener('paste', handlePaste, false);
}

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

function handleDrop(e) {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFiles(files);
}

function handlePaste(e) {
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
        if (items[i].type.indexOf('image') !== -1) {
            const file = items[i].getAsFile();
            handleFiles([file]);
        }
    }
}

function handleFiles(files) {
    for (let file of files) {
        if (file.type.startsWith('image/')) {
            addImageFromClipboard(file);
        }
    }
}

function addImageFromClipboard(file) {
    const id = state.imageCount++;
    const reader = new FileReader();

    reader.onload = function(e) {
        const preview = document.createElement('div');
        preview.className = 'image-preview';
        preview.id = `preview-${id}`;
        preview.innerHTML = `
            <img src="${e.target.result}" alt="Preview">
            <div class="image-info">
                <input type="text" class="form-control" name="desc_clipboard_${id}" placeholder="Опишите, что изображено на этом скриншоте" required>
            </div>
            <button type="button" class="remove-btn" onclick="removeImage(${id})">×</button>
        `;
        previewsContainer.appendChild(preview);

        state.images.push({id, file, description: '', isClipboard: true});
    };

    reader.readAsDataURL(file);
}

function removeImage(id) {
    const preview = document.getElementById(`preview-${id}`);
    if (preview) {
        preview.remove();
    }
    state.images = state.images.filter(img => img.id !== id);
}

function addImageField() {
    const index = state.imageCount++;

    const preview = document.createElement('div');
    preview.className = 'image-preview';
    preview.id = `preview-${index}`;
    preview.innerHTML = `
        <img id="img-preview-${index}" src="" alt="Preview" style="display:none;">
        <div class="image-info">
            <input type="file" class="form-control mb-2" accept="image/*" onchange="handleFileSelect(this, ${index})">
            <input type="text" class="form-control" name="desc_${index}" placeholder="Опишите, что изображено на этом фото/скриншоте" required>
        </div>
        <button type="button" class="remove-btn" onclick="removeImage(${index})">×</button>
    `;
    previewsContainer.appendChild(preview);

    state.images.push({id: index, file: null, description: '', isClipboard: false});
}

function handleFileSelect(input, index) {
    const file = input.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            const img = document.getElementById(`img-preview-${index}`);
            img.src = e.target.result;
            img.style.display = 'block';

            const imgState = state.images.find(img => img.id === index);
            if (imgState) {
                imgState.file = file;
            }
        };
        reader.readAsDataURL(file);
    }
}

function initFormSubmit() {
    form.onsubmit = async (e) => {
        e.preventDefault();

        const formData = new FormData();
        formData.append('prompt', document.getElementById('prompt').value);

        const filesInput = document.getElementById('files');
        for (let file of filesInput.files) {
            formData.append('files', file);
        }

        const templateInput = document.getElementById('template');
        if (templateInput.files[0]) {
            formData.append('template', templateInput.files[0]);
        }

        let imageIndex = 0;
        for (let img of state.images) {
            if (img.file) {
                let descField;
                if (img.isClipboard) {
                    descField = document.querySelector(`input[name="desc_clipboard_${img.id}"]`);
                } else {
                    descField = document.querySelector(`input[name="desc_${img.id}"]`);
                }

                if (descField && descField.value.trim()) {
                    formData.append(`image_${imageIndex}`, img.file);
                    formData.append(`desc_${imageIndex}`, descField.value.trim());
                    imageIndex++;
                }
            }
        }

        document.getElementById('submitText').textContent = 'Запуск...';
        document.getElementById('submitSpinner').classList.remove('d-none');
        form.querySelector('button[type="submit"]').disabled = true;

        try {
            const res = await fetch('/start', {
                method: 'POST',
                body: formData
            });

            const data = await res.json();

            if (res.status != 200) {
                throw data.error
            }

            state.taskId = data.task_id;

            state.pollInterval = setInterval(pollStatus, 2000);
            progressSection.classList.remove('d-none');

        } catch (err) {
            showError('Ошибка запуска задачи: ' + err);
        }
    };
}

async function pollStatus() {
    if (!state.taskId) return;

    try {
        const res = await fetch('/status/' + state.taskId);
        const status = await res.json();
        updateStatus(status);
    } catch (err) {
        console.error('Ошибка опроса:', err);
    }
}

function updateStatus(status) {
    document.getElementById('taskStatus').textContent = status.status;

    const badge = document.getElementById('taskStatus');
    badge.className = 'badge status-badge';

    if (status.status === 'queued') {
        badge.classList.add('bg-secondary');
    } else if (status.status === 'processing') {
        badge.classList.add('bg-primary');
    } else if (status.status === 'done') {
        badge.classList.add('bg-success');
        clearInterval(state.pollInterval);
        showResult(status);
    } else if (status.status === 'error') {
        badge.classList.add('bg-danger');
        clearInterval(state.pollInterval);
        showError(status.error);
    }
}

function showResult(status) {
    resultSection.classList.remove('d-none');

    const downloadLink = document.getElementById('downloadLink');
    const viewHtmlLink = document.getElementById('viewHtmlLink');

    // Скачивание через запрос на backend
    downloadLink.href = '#';
    downloadLink.onclick = async (e) => {
        e.preventDefault();
        await downloadFile(state.taskId);
    };

    viewHtmlLink.href = status.html_result || '/view_html/' + state.taskId;
    viewHtmlLink.target = '_blank';

    resetForm();
}

async function downloadFile(taskId) {
    try {
        const res = await fetch('/download/' + taskId);
        if (!res.ok) {
            const error = await res.json();
            showError(error.error || 'Ошибка скачивания');
            return;
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `report_${taskId.slice(0, 8)}.docx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } catch (err) {
        showError('Ошибка скачивания: ' + err.message);
    }
}

function showError(message) {
    errorSection.classList.remove('d-none');
    document.getElementById('errorMessage').textContent = message;
    resetForm();
}

function resetForm() {
    document.getElementById('submitText').textContent = 'Запустить генерацию';
    document.getElementById('submitSpinner').classList.add('d-none');
    form.querySelector('button[type="submit"]').disabled = false;
}

// Инициализация после загрузки DOM
document.addEventListener('DOMContentLoaded', initApp);
