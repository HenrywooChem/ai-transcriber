/* =============================================
   视频转录服务 - 前端逻辑
   ============================================= */

// ============ 状态 ============
let pollTimer = null;
let currentTaskId = null;

// ============ DOM 引用 ============
const $ = id => document.getElementById(id);
const el = {
    fileInput: $('file-input'),
    uploadZone: $('upload-zone'),
    fileName: $('file-name'),
    uploadBtn: $('upload-btn'),
    uploadError: $('upload-error'),

    urlInput: $('url-input'),
    urlBtn: $('url-btn'),
    urlError: $('url-error'),

    modeUpload: $('mode-upload'),
    modeUrl: $('mode-url'),
    panelUpload: $('panel-upload'),
    panelUrl: $('panel-url'),

    progressSection: $('progress-section'),
    progressFill: $('progress-fill'),
    progressStatus: $('progress-status'),
    progressPercent: $('progress-percent'),

    resultSection: $('result-section'),
    resultTitle: $('result-title'),
    segList: $('seg-list'),
    correctedBox: $('corrected-box'),
    summaryBox: $('summary-box'),
    exportTxt: $('export-txt'),
    exportSrt: $('export-srt'),
    exportJson: $('export-json'),

    taskList: $('task-list'),

    toast: $('toast'),
    toastMsg: $('toast-msg'),
};

// ============ Toast ============
function showToast(msg, type = '') {
    el.toastMsg.textContent = msg;
    el.toast.className = 'toast ' + type;
    el.toast.classList.add('show');
    setTimeout(() => el.toast.classList.remove('show'), 3500);
}

// ============ 内联错误 ============
function showError(elError, msg) {
    if (!elError) return;
    if (msg) {
        elError.textContent = '❌ ' + msg;
        elError.style.display = 'block';
    } else {
        elError.style.display = 'none';
    }
}

// ============ 模式切换 ============
el.modeUpload.addEventListener('click', () => switchMode('upload'));
el.modeUrl.addEventListener('click', () => switchMode('url'));

function switchMode(mode) {
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.mode-panel').forEach(p => p.classList.remove('active'));
    showError(el.uploadError);
    showError(el.urlError);
    if (mode === 'upload') {
        el.modeUpload.classList.add('active');
        el.panelUpload.classList.add('active');
    } else {
        el.modeUrl.classList.add('active');
        el.panelUrl.classList.add('active');
    }
}

// ============ 拖拽上传 ============
el.uploadZone.addEventListener('dragover', e => {
    e.preventDefault();
    el.uploadZone.classList.add('dragover');
});
el.uploadZone.addEventListener('dragleave', () => {
    el.uploadZone.classList.remove('dragover');
});
el.uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    el.uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
        el.fileInput.files = e.dataTransfer.files;
        showSelectedFile();
    }
});
// 透明盖层也要捕获拖拽事件
el.fileInput.addEventListener('dragover', e => {
    e.preventDefault();
    el.uploadZone.classList.add('dragover');
});
el.fileInput.addEventListener('dragleave', () => {
    el.uploadZone.classList.remove('dragover');
});
el.fileInput.addEventListener('drop', e => {
    e.preventDefault();
    el.uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
        el.fileInput.files = e.dataTransfer.files;
        showSelectedFile();
    }
});
// 点击标签手动触发的兜底方案（有些浏览器 <label for> 不工作）
el.uploadZone.addEventListener('click', () => {
    el.fileInput.click();
});
el.fileInput.addEventListener('change', showSelectedFile);

function showSelectedFile() {
    showError(el.uploadError);
    const file = el.fileInput.files[0];
    if (file) {
        const size = (file.size / 1024 / 1024).toFixed(1);
        el.fileName.innerHTML = `📄 ${file.name} (${size} MB)`;
        el.fileName.style.display = 'inline-flex';
    } else {
        el.fileName.style.display = 'none';
    }
}

// ============ 上传文件 ============
el.uploadBtn.addEventListener('click', async () => {
    showError(el.uploadError);
    const file = el.fileInput.files[0];
    if (!file) {
        showError(el.uploadError, '请先点击上方区域选择一个文件');
        return;
    }
    if (file.size > 500 * 1024 * 1024) {
        showError(el.uploadError, '文件超过 500MB 限制');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('use_llm', 'true');

    try {
        const res = await fetch('/api/transcribe/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '上传失败');
        showToast('✅ 上传成功，开始转录...', 'success');
        el.uploadBtn.disabled = true;
        el.uploadBtn.textContent = '⏳ 转录中...';
        startPolling(data.task_id);
    } catch (err) {
        showError(el.uploadError, err.message);
    }
});

// ============ URL转写 ============
function extractUrl(text) {
    // 从包含标题的粘贴文本中提取实际 URL
    const m = text.match(/https?:\/\/[^\s]+/);
    return m ? m[0] : text;
}

el.urlBtn.addEventListener('click', async () => {
    showError(el.urlError);
    let url = el.urlInput.value.trim();
    url = extractUrl(url);
    if (!url) {
        showError(el.urlError, '请粘贴视频链接');
        return;
    }

    try {
        const res = await fetch('/api/transcribe/url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, use_llm: true })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '提交失败');
        showToast('✅ 任务已提交，正在下载...', 'success');
        el.urlBtn.disabled = true;
        el.urlBtn.textContent = '⏳ 处理中...';
        startPolling(data.task_id);
    } catch (err) {
        showError(el.urlError, err.message);
    }
});

// ============ 轮询进度 ============
function startPolling(taskId) {
    if (pollTimer) clearInterval(pollTimer);
    currentTaskId = taskId;

    el.progressSection.classList.add('active');
    el.resultSection.classList.remove('active');
    el.progressFill.style.width = '0%';
    el.progressStatus.textContent = '⏳ 排队中...';
    el.progressPercent.textContent = '0%';

    el.progressSection.scrollIntoView({ behavior: 'smooth' });

    pollTimer = setInterval(async () => {
        try {
            const res = await fetch(`/api/task/${taskId}`);
            const data = await res.json();
            if (!res.ok) throw new Error('任务查询失败');

            updateProgress(data);

            if (data.status === 'completed') {
                clearInterval(pollTimer);
                pollTimer = null;
                el.uploadBtn.disabled = false;
                el.uploadBtn.textContent = '🚀 开始转录';
                el.urlBtn.disabled = false;
                el.urlBtn.textContent = '🚀 转录';
                showResult(data.result || data);
                showToast('✅ 转录完成！', 'success');
                loadHistory();
            } else if (data.status === 'failed') {
                clearInterval(pollTimer);
                pollTimer = null;
                el.uploadBtn.disabled = false;
                el.uploadBtn.textContent = '🚀 开始转录';
                el.urlBtn.disabled = false;
                el.urlBtn.textContent = '🚀 转录';
                el.progressStatus.textContent = '❌ ' + (data.error || '转录失败');
                showToast('❌ ' + (data.error || '转录失败'), 'error');
            }
        } catch (err) {
            if (err.name !== 'AbortError') {
                console.error('Poll error:', err);
            }
        }
    }, 2000);
}

function updateProgress(data) {
    const pct = data.progress || 0;
    el.progressFill.style.width = pct + '%';
    el.progressPercent.textContent = pct + '%';
    el.progressStatus.textContent = data.message || '处理中...';
}

// ============ 显示结果 ============
function showResult(data) {
    const result = data.result || data;
    if (!result || !result.segments) return;

    el.resultSection.classList.add('active');
    el.progressSection.classList.remove('active');

    const title = data.title || '转录结果';
    el.resultTitle.textContent = `📝 ${title}`;

    const segs = result.segments;
    let segHtml = '';
    segs.forEach(s => {
        const m = Math.floor(s.start / 60);
        const sec = Math.floor(s.start % 60);
        segHtml += `<div class="segment-row">
            <span class="seg-time">${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}</span>
            <span class="seg-text">${escapeHtml(s.text)}</span>
        </div>`;
    });
    el.segList.innerHTML = segHtml;

    el.correctedBox.textContent = result.corrected_text || '(无纠错数据)';
    el.summaryBox.textContent = result.summary || '(无总结数据)';

    const taskId = currentTaskId;
    el.exportTxt.href = `/api/export/${taskId}?fmt=txt`;
    el.exportSrt.href = `/api/export/${taskId}?fmt=srt`;
    el.exportJson.href = `/api/export/${taskId}?fmt=json`;

    switchResultTab('summary');

    setTimeout(() => {
        el.resultSection.scrollIntoView({ behavior: 'smooth' });
    }, 100);
}

// ============ 结果Tab切换 ============
function switchResultTab(name) {
    document.querySelectorAll('.result-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.result-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('rtab-' + name)?.classList.add('active');
    document.getElementById('rpanel-' + name)?.classList.add('active');
}

// ============ 加载历史列表 ============
async function loadHistory() {
    try {
        const res = await fetch('/api/tasks?limit=20');
        const tasks = await res.json();
        if (!tasks.length) {
            el.taskList.innerHTML = '<div class="empty-state">暂无历史记录</div>';
            return;
        }

        let html = '';
        tasks.forEach(t => {
            const statusCls = getStatusClass(t.status);
            const time = formatTime(t.created_at);
            const label = t.title || t.url || '未知来源';
            html += `<div class="task-item" onclick="viewTask('${t.task_id}')">
                <div class="task-info">
                    <div class="task-title">${escapeHtml(truncate(label, 50))}</div>
                    <div class="task-meta">${time} · ${t.mode || 'upload'}</div>
                </div>
                <span class="task-status ${statusCls}">${getStatusLabel(t.status)}</span>
            </div>`;
        });
        el.taskList.innerHTML = html;
    } catch (err) {
        el.taskList.innerHTML = '<div class="empty-state">加载失败，请刷新页面</div>';
    }
}

function getStatusClass(status) {
    if (status === 'completed') return 'completed';
    if (status === 'failed') return 'failed';
    if (['queued','downloading','transcribing','processing'].includes(status)) return 'processing';
    return 'queued';
}

function getStatusLabel(status) {
    const map = {
        completed: '✅ 完成', failed: '❌ 失败',
        queued: '⏳ 排队', downloading: '📥 下载中',
        transcribing: '🎙️ 转写中', processing: '🤖 处理中'
    };
    return map[status] || status;
}

function formatTime(ts) {
    const d = new Date(ts * 1000);
    const now = new Date();
    const diff = Math.floor((now - d) / 60000);
    if (diff < 1) return '刚刚';
    if (diff < 60) return diff + '分钟前';
    if (diff < 1440) return Math.floor(diff / 60) + '小时前';
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function truncate(s, n) {
    return s.length > n ? s.slice(0, n) + '…' : s;
}

async function viewTask(taskId) {
    if (currentTaskId === taskId) {
        document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });
        return;
    }
    try {
        const res = await fetch(`/api/task/${taskId}`);
        const data = await res.json();
        if (data.status === 'completed' && data.result) {
            currentTaskId = taskId;
            showResult(data);
        } else if (data.status === 'failed') {
            showToast('❌ 该任务已失败', 'error');
        } else {
            startPolling(taskId);
        }
    } catch (err) {
        showToast('❌ 加载失败', 'error');
    }
}

// ============ 工具函数 ============
function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
});
