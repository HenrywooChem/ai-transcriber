/* =============================================
   视频转录服务 - 前端逻辑（含用户认证）
   ============================================= */

// ============ 认证状态 ============
const AUTH_KEY = 'transcriber_token';
const USER_KEY = 'transcriber_user';

let authToken = localStorage.getItem(AUTH_KEY) || '';
let authUser = localStorage.getItem(USER_KEY) || '';

function isLoggedIn() { return !!authToken; }

function saveAuth(token, username) {
    authToken = token;
    authUser = username;
    localStorage.setItem(AUTH_KEY, token);
    localStorage.setItem(USER_KEY, username);
}

function clearAuth() {
    authToken = '';
    authUser = '';
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(USER_KEY);
}

// 带认证的 fetch 封装
async function authFetch(url, options = {}) {
    const headers = options.headers || {};
    if (authToken) {
        headers['Authorization'] = 'Bearer ' + authToken;
    }
    if (!options.method || options.method === 'GET') {
        // 对于 GET 请求，不设置 Content-Type，JSON Body 也不该有
        options.headers = headers;
    } else if (options.body && typeof options.body === 'string') {
        headers['Content-Type'] = 'application/json';
        options.headers = headers;
    } else {
        options.headers = headers;
    }
    const res = await fetch(url, options);
    if (res.status === 401) {
        // Token 过期，清除登录状态
        clearAuth();
        updateAuthUI();
        showLoginModal();
        throw new Error('登录已过期，请重新登录');
    }
    return res;
}

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

    authArea: $('auth-area'),
    loginBtn: $('login-btn'),

    authModal: $('auth-modal'),
    loginUsername: $('login-username'),
    loginPassword: $('login-password'),
    loginError: $('login-error'),
    regUsername: $('reg-username'),
    regPassword: $('reg-password'),
    regConfirm: $('reg-confirm'),
    regError: $('reg-error'),
};

// ============ 认证 UI ============
function updateAuthUI() {
    if (!el.authArea) return;
    if (isLoggedIn()) {
        const initial = authUser.charAt(0).toUpperCase();
        el.authArea.innerHTML = `
            <div class="auth-avatar">
                <span class="avatar-icon">${escapeHtml(initial)}</span>
                <span>${escapeHtml(authUser)}</span>
                <span class="logout-link" onclick="doLogout()">退出</span>
            </div>
        `;
    } else {
        el.authArea.innerHTML = '<button id="login-btn" class="btn btn-sm btn-primary" type="button" onclick="showLoginModal()">登录</button>';
    }
}

function showLoginModal() {
    el.authModal.style.display = 'flex';
    switchAuthTab('login');
    el.loginUsername.value = '';
    el.loginPassword.value = '';
    el.loginError.style.display = 'none';
}

function hideAuthModal() {
    el.authModal.style.display = 'none';
}

function switchAuthTab(name) {
    document.querySelectorAll('.modal-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.auth-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('auth-tab-' + name)?.classList.add('active');
    document.getElementById('auth-panel-' + name)?.classList.add('active');
}

async function doLogin() {
    const username = el.loginUsername.value.trim();
    const password = el.loginPassword.value;
    if (!username || !password) {
        el.loginError.textContent = '❌ 请输入用户名和密码';
        el.loginError.style.display = 'block';
        return;
    }
    el.loginError.style.display = 'none';
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '登录失败');
        saveAuth(data.token, data.username);
        updateAuthUI();
        hideAuthModal();
        showToast('✅ 登录成功，欢迎回来！', 'success');
    } catch (err) {
        el.loginError.textContent = '❌ ' + err.message;
        el.loginError.style.display = 'block';
    }
}

async function doRegister() {
    const username = el.regUsername.value.trim();
    const password = el.regPassword.value;
    const confirm = el.regConfirm.value;
    if (!username || !password) {
        el.regError.textContent = '❌ 请填写用户名和密码';
        el.regError.style.display = 'block';
        return;
    }
    if (password.length < 4) {
        el.regError.textContent = '❌ 密码至少4个字符';
        el.regError.style.display = 'block';
        return;
    }
    if (password !== confirm) {
        el.regError.textContent = '❌ 两次密码不一致';
        el.regError.style.display = 'block';
        return;
    }
    el.regError.style.display = 'none';
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '注册失败');
        saveAuth(data.token, data.username);
        updateAuthUI();
        hideAuthModal();
        showToast('🎉 注册成功！', 'success');
    } catch (err) {
        el.regError.textContent = '❌ ' + err.message;
        el.regError.style.display = 'block';
    }
}

function doLogout() {
    clearAuth();
    updateAuthUI();
    showToast('已退出登录');
}

// Enter 键快捷登录/注册
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && el.authModal.style.display === 'flex') {
        const loginActive = el.loginPassword.closest('.auth-panel.active');
        if (loginActive) doLogin();
        else doRegister();
    }
});

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
    if (!isLoggedIn()) {
        showLoginModal();
        return;
    }
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
        const res = await authFetch('/api/transcribe/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '上传失败');
        showToast('✅ 上传成功，开始转录...', 'success');
        el.uploadBtn.disabled = true;
        el.uploadBtn.textContent = '⏳ 转录中...';
        startPolling(data.task_id);
    } catch (err) {
        if (err.message !== '登录已过期，请重新登录') {
            showError(el.uploadError, err.message);
        }
    }
});

// ============ URL转写 ============
function extractUrl(text) {
    const m = text.match(/https?:\/\/[^\s]+/);
    return m ? m[0] : text;
}

el.urlBtn.addEventListener('click', async () => {
    if (!isLoggedIn()) {
        showLoginModal();
        return;
    }
    showError(el.urlError);
    let url = el.urlInput.value.trim();
    url = extractUrl(url);
    if (!url) {
        showError(el.urlError, '请粘贴视频链接');
        return;
    }

    try {
        const res = await authFetch('/api/transcribe/url', {
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
        if (err.message !== '登录已过期，请重新登录') {
            showError(el.urlError, err.message);
        }
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
    updateAuthUI();
    loadHistory();
});
