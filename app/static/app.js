/* =============================================
   视频转录服务 - 前端逻辑（含用户认证 + 额度 + 签到 + 支付）
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
        options.headers = headers;
    } else if (options.body && typeof options.body === 'string') {
        headers['Content-Type'] = 'application/json';
        options.headers = headers;
    } else {
        options.headers = headers;
    }
    const res = await fetch(url, options);
    if (res.status === 401) {
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
let quotaTimer = null;

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
    quotaArea: $('quota-area'),
    quotaText: $('quota-text'),
    checkinBtn: $('checkin-btn'),
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
        // 显示额度区域
        if (el.quotaArea) el.quotaArea.style.display = 'inline-flex';
        loadQuota();
        // 启动自动刷新（每60秒）
        if (quotaTimer) clearInterval(quotaTimer);
        quotaTimer = setInterval(loadQuota, 60000);
    } else {
        el.authArea.innerHTML = '<button id="login-btn" class="btn btn-sm btn-primary" type="button" onclick="showLoginModal()">登录</button>';
        if (el.quotaArea) el.quotaArea.style.display = 'none';
        if (quotaTimer) { clearInterval(quotaTimer); quotaTimer = null; }
    }
}

function showLoginModal() {
    $('auth-modal').style.display = 'flex';
    switchAuthTab('login');
    $('login-username').value = '';
    $('login-password').value = '';
    $('login-error').style.display = 'none';
}

function hideAuthModal() {
    $('auth-modal').style.display = 'none';
}

function switchAuthTab(name) {
    document.querySelectorAll('.modal-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.auth-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('auth-tab-' + name)?.classList.add('active');
    document.getElementById('auth-panel-' + name)?.classList.add('active');
}

async function doLogin() {
    const username = $('login-username').value.trim();
    const password = $('login-password').value;
    if (!username || !password) {
        $('login-error').textContent = '❌ 请输入用户名和密码';
        $('login-error').style.display = 'block';
        return;
    }
    $('login-error').style.display = 'none';
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
        $('login-error').textContent = '❌ ' + err.message;
        $('login-error').style.display = 'block';
    }
}

async function doRegister() {
    const username = $('reg-username').value.trim();
    const password = $('reg-password').value;
    const confirm = $('reg-confirm').value;
    if (!username || !password) {
        $('reg-error').textContent = '❌ 请填写用户名和密码';
        $('reg-error').style.display = 'block';
        return;
    }
    if (password.length < 4) {
        $('reg-error').textContent = '❌ 密码至少4个字符';
        $('reg-error').style.display = 'block';
        return;
    }
    if (password !== confirm) {
        $('reg-error').textContent = '❌ 两次密码不一致';
        $('reg-error').style.display = 'block';
        return;
    }
    $('reg-error').style.display = 'none';
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
        showToast('🎉 注册成功！每月30分钟免费额度已发放', 'success');
    } catch (err) {
        $('reg-error').textContent = '❌ ' + err.message;
        $('reg-error').style.display = 'block';
    }
}

function doLogout() {
    clearAuth();
    updateAuthUI();
    showToast('已退出登录');
}

// Enter 键快捷登录/注册
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && $('auth-modal').style.display === 'flex') {
        const loginActive = $('login-password').closest('.auth-panel.active');
        if (loginActive) doLogin();
        else doRegister();
    }
});

// ============ 额度 & 签到 ============
async function loadQuota() {
    if (!isLoggedIn()) return;
    try {
        const res = await authFetch('/api/quota');
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);
        updateQuotaUI(data);
    } catch (err) {
        if (el.quotaText) el.quotaText.textContent = '额度查询失败';
    }
}

function updateQuotaUI(data) {
    if (!el.quotaText) return;
    const avail = data.available_minutes || 0;
    const total = data.free_minutes || 30;
    const bonus = Math.round((data.bonus_seconds || 0) / 60);
    const used = data.used_minutes || 0;

    // 导航栏显示
    let color = '#22c55e';
    if (avail < 5) color = '#ef4444';
    else if (avail < 15) color = '#f59e0b';
    const icon = avail <= 0 ? '⚠️' : '⏱';
    el.quotaText.innerHTML = `${icon} <span style="color:${color};font-weight:700">${avail.toFixed(1)}</span> 分钟`;

    // 签到按钮状态
    const checkinBtn = el.checkinBtn;
    const checkinBtnInline = $('checkin-btn-inline');
    if (data.can_checkin) {
        if (checkinBtn) { checkinBtn.style.display = 'inline-flex'; checkinBtn.textContent = '✅ 签到'; }
        if (checkinBtnInline) { checkinBtnInline.style.display = 'inline-flex'; checkinBtnInline.textContent = '✅ 每日签到 +5分钟'; checkinBtnInline.disabled = false; }
    } else {
        if (checkinBtn) { checkinBtn.style.display = 'none'; }
        if (checkinBtnInline) { checkinBtnInline.textContent = '✅ 今日已签到'; checkinBtnInline.disabled = true; checkinBtnInline.style.opacity = '0.5'; }
    }

    // 刷新额度详情（如果弹窗开着）
    const quotaDetail = $('quota-detail');
    if (quotaDetail && quotaDetail.querySelector('.quota-loading') === null) {
        renderQuotaDetail(data);
    }
}

async function doCheckin() {
    if (!isLoggedIn()) { showLoginModal(); return; }
    try {
        const res = await authFetch('/api/checkin', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '签到失败');
        showToast('🎉 ' + data.message, 'success');
        loadQuota();
    } catch (err) {
        showToast('❌ ' + err.message, 'error');
    }
}

// ============ 套餐/额度弹窗 ============
function showPricingModal() {
    if (!isLoggedIn()) { showLoginModal(); return; }
    const modal = $('pricing-modal');
    modal.style.display = 'flex';

    // 加载额度详情
    loadQuotaDetail();

    // 加载套餐
    loadPackages();

    // 加载充值记录
    loadPaymentHistory();
}

function hidePricingModal() {
    $('pricing-modal').style.display = 'none';
}

async function loadQuotaDetail() {
    try {
        const res = await authFetch('/api/quota');
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail);
        renderQuotaDetail(data);
    } catch (err) {
        $('quota-detail').innerHTML = '<div class="empty-state">加载失败</div>';
    }
}

function renderQuotaDetail(data) {
    const bonus = Math.round((data.bonus_seconds || 0) / 60);
    const used = data.used_minutes || 0;
    const avail = data.available_minutes || 0;
    const free = data.free_minutes || 30;
    const total = free + bonus;
    const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;

    $('quota-detail').innerHTML = `
        <div class="quota-bar-section">
            <div class="quota-stats">
                <span>📅 ${data.month}</span>
                <span>已用 <strong>${used.toFixed(1)}</strong> / ${total} 分钟</span>
                <span>剩余 <strong style="color:${avail < 5 ? '#ef4444' : avail < 15 ? '#f59e0b' : '#22c55e'}">${avail.toFixed(1)}</strong> 分钟</span>
            </div>
            <div class="progress-bar quota-progress-bar">
                <div class="progress-fill" style="width:${pct}%;background:${pct > 80 ? '#ef4444' : pct > 50 ? '#f59e0b' : '#6c63ff'}"></div>
            </div>
            <div class="quota-breakdown">
                <span>🎁 免费额度: ${free}分钟</span>
                <span>🎯 签到奖励: +${bonus}分钟</span>
                <span>⭐ 已用: ${used.toFixed(1)}分钟</span>
            </div>
        </div>
    `;
}

async function loadPackages() {
    try {
        const res = await fetch('/api/quota/pricing');
        const data = await res.json();
        const pkgList = $('pkg-list');
        let html = '';
        data.packages.forEach(pkg => {
            const isPopular = pkg.name === '300分钟包';
            const isUnlimited = pkg.name === '无限月卡';
            html += `
                <div class="pkg-card ${isPopular ? 'popular' : ''}">
                    ${isPopular ? '<div class="pkg-badge">推荐</div>' : ''}
                    <div class="pkg-name">${pkg.name}</div>
                    <div class="pkg-price">¥${pkg.price_yuan}</div>
                    <div class="pkg-desc">${isUnlimited ? '当月不限量转录' : pkg.minutes + '分钟转录时长'}</div>
                    <button class="btn btn-primary" onclick="buyPackage(${pkg.minutes})" type="button">
                        ${isUnlimited ? '🚀 开通' : '💳 购买'}
                    </button>
                </div>
            `;
        });
        pkgList.innerHTML = html;
    } catch (err) {
        $('pkg-list').innerHTML = '<div class="empty-state">加载套餐失败</div>';
    }
}

async function loadPaymentHistory() {
    try {
        const res = await authFetch('/api/payment/history');
        const data = await res.json();
        const container = $('payment-history');
        if (!data.length) {
            container.innerHTML = '<div class="empty-state">暂无充值记录</div>';
            return;
        }
        let html = '';
        data.forEach(o => {
            const statusText = o.status === 'paid' ? '✅ 已支付' : '⏳ 待支付';
            const date = new Date((o.created_at || 0) * 1000).toLocaleDateString('zh-CN');
            html += `<div class="payment-item">
                <span>${o.package_name}</span>
                <span>¥${o.amount_yuan}</span>
                <span class="payment-status ${o.status}">${statusText}</span>
                <span class="payment-date">${date}</span>
            </div>`;
        });
        container.innerHTML = html;
    } catch (err) {
        // 静默失败
    }
}

async function buyPackage(minutes) {
    if (!isLoggedIn()) { showLoginModal(); return; }
    try {
        const res = await authFetch('/api/payment/create-order', {
            method: 'POST',
            body: JSON.stringify({ minutes })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '创建订单失败');
        // 显示支付弹窗
        showPaymentModal(data);
    } catch (err) {
        showToast('❌ ' + err.message, 'error');
    }
}

// ============ 支付弹窗 ============
function showPaymentModal(order) {
    $('payment-modal-title').textContent = `💳 订单 #${order.order_id.slice(0, 8)}`;
    $('payment-detail').innerHTML = `
        <div class="order-detail">
            <div class="order-row"><span>套餐</span><span>${order.package_name}</span></div>
            <div class="order-row"><span>金额</span><span class="order-price">¥${order.amount_yuan}</span></div>
            <div class="order-row"><span>状态</span><span class="payment-status pending">⏳ 待支付</span></div>
        </div>
    `;
    $('payment-error').style.display = 'none';

    // Stripe 支付按钮
    const stripeBtn = $('pay-stripe-btn');
    if (order.payment_url) {
        if (order.payment_method === 'xunhupay') {
            // 虎皮椒微信支付 - 显示二维码+跳转链接
            stripeBtn.style.display = 'block';
            stripeBtn.textContent = '📱 微信扫码支付';
            stripeBtn.onclick = () => { window.open(order.payment_url, '_blank'); };

            if (order.payment_qrcode) {
                // 使用第三方API生成二维码图片
                const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(order.payment_qrcode)}`;
                const detailDiv = $('payment-detail');
                detailDiv.innerHTML = `
                    <div class="order-detail">
                        <div class="order-row"><span>套餐</span><span>${order.package_name}</span></div>
                        <div class="order-row"><span>金额</span><span class="order-price">¥${order.amount_yuan}</span></div>
                        <div class="order-row"><span>状态</span><span class="payment-status pending">⏳ 扫码支付</span></div>
                    </div>
                    <div style="text-align:center;margin-top:16px">
                        <img src="${qrUrl}" alt="微信支付二维码" style="width:220px;height:220px;border-radius:12px;border:2px solid var(--border)">
                        <p style="color:var(--text-muted);font-size:.85rem;margin-top:10px">📱 打开微信扫码支付</p>
                    </div>
                `;
            }
        } else {
            // Stripe 支付
            stripeBtn.style.display = 'block';
            stripeBtn.textContent = '💳 去支付 (Stripe)';
            stripeBtn.onclick = () => { window.open(order.payment_url, '_blank'); };
        }
    } else {
        // 无支付渠道时显示提示
        stripeBtn.style.display = 'block';
        stripeBtn.textContent = '⚠️ 支付暂未开放';
        stripeBtn.disabled = true;
        stripeBtn.style.opacity = '0.5';
        stripeBtn.style.cursor = 'not-allowed';
        // 显示联系管理员提示
        const detailDiv = $('payment-detail');
        detailDiv.innerHTML += `
            <div class="msg-error" style="margin-top:10px">
                在线支付尚未配置。如需购买额度，请联系管理员手动充值。
            </div>
        `;
    }

    $('payment-modal').style.display = 'flex';
}

function hidePaymentModal() {
    $('payment-modal').style.display = 'none';
}

// ============ 检测支付成功回跳 ============
(function checkPaymentReturn() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('payment_success')) {
        showToast('🎉 支付成功！额度已自动到账', 'success');
        setTimeout(() => loadQuota(), 2000);
        window.history.replaceState({}, document.title, window.location.pathname);
    }
    if (params.get('payment_cancelled')) {
        showToast('❌ 支付取消', 'error');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
})();

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

    // 立即显示进度条（不等 POST 返回）
    el.progressSection.classList.add('active');
    el.resultSection.classList.remove('active');
    el.progressFill.style.width = '10%';
    el.progressStatus.textContent = '📤 上传文件中...';
    el.progressPercent.textContent = '10%';
    el.uploadBtn.disabled = true;
    el.uploadBtn.textContent = '⏳ 上传中...';
    el.progressSection.scrollIntoView({ behavior: 'smooth' });

    const formData = new FormData();
    formData.append('file', file);
    formData.append('use_llm', 'true');

    try {
        const res = await authFetch('/api/transcribe/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '上传失败');
        showToast('✅ 上传成功，开始转录...', 'success');
        startPolling(data.task_id);
    } catch (err) {
        // 出错后恢复按钮状态
        el.uploadBtn.disabled = false;
        el.uploadBtn.textContent = '🚀 开始转录';
        el.progressSection.classList.remove('active');
        if (err.message !== '登录已过期，请重新登录') {
            showError(el.uploadError, err.message);
            // 如果额度不足，提示去购买
            if (err.message.includes('额度已用尽') || err.message.includes('402')) {
                showToast('💡 可去签到获取额外额度或购买套餐', '');
            }
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

    // 立即显示进度条（不等 POST 返回）
    el.progressSection.classList.add('active');
    el.resultSection.classList.remove('active');
    el.progressFill.style.width = '10%';
    el.progressStatus.textContent = '📥 准备提交任务...';
    el.progressPercent.textContent = '10%';
    el.urlBtn.disabled = true;
    el.urlBtn.textContent = '⏳ 处理中...';
    el.progressSection.scrollIntoView({ behavior: 'smooth' });

    try {
        const res = await authFetch('/api/transcribe/url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, use_llm: true })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '提交失败');
        showToast('✅ 任务已提交，正在下载...', 'success');
        startPolling(data.task_id);
    } catch (err) {
        // 出错后恢复按钮状态
        el.urlBtn.disabled = false;
        el.urlBtn.textContent = '🚀 转录';
        el.progressSection.classList.remove('active');
        if (err.message !== '登录已过期，请重新登录') {
            showError(el.urlError, err.message);
            if (err.message.includes('额度已用尽') || err.message.includes('402')) {
                showToast('💡 可去签到获取额外额度或购买套餐', '');
            }
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
                // 刷新额度（刚刚扣除了）
                loadQuota();
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

    // 如果已扣除额度，显示在结果区域
    if (data.quota_deducted) {
        el.resultTitle.textContent += ` ⏱ -${data.quota_deducted.toFixed(0)}秒`;
    }

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
