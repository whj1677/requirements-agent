'use strict';

(() => {
  const element = (id) => document.getElementById(id);
  const state = { csrf: '', licensed: false, busy: false, stopped: false, selectedFile: null };
  const maxLicenseBytes = 32 * 1024;
  const controls = {
    request: element('request-button'), install: element('install-button'),
    start: element('start-button'), refresh: element('refresh-status'), shutdown: element('shutdown-button'),
  };

  function syncControls() {
    const blocked = state.busy || state.stopped;
    controls.request.disabled = blocked || !state.csrf;
    controls.install.disabled = blocked || !state.csrf || !state.selectedFile;
    controls.start.disabled = blocked || !state.csrf || !state.licensed;
    controls.refresh.disabled = blocked;
    controls.shutdown.disabled = blocked || !state.csrf;
    element('display-name').disabled = blocked;
    element('license-file').disabled = blocked;
    element('replace-license').disabled = blocked;
    element('activation-flow').setAttribute('aria-busy', String(state.busy));
  }

  function notify(message, kind = 'success') {
    const notice = element('notice');
    notice.textContent = message;
    notice.className = 'notice' + (kind === 'error' ? ' notice-error' : kind === 'info' ? ' notice-info' : '');
    notice.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    notice.hidden = false;
  }

  function updateStatus(value) {
    state.csrf = typeof value.csrf === 'string' ? value.csrf : state.csrf;
    state.licensed = value.licensed === true;
    const subject = typeof value.subject === 'string' ? value.subject : '';
    element('license-status').textContent = state.licensed ? '本机已授权' : '等待本机授权';
    element('license-description').textContent = state.licensed
      ? '设备授权有效，可以进入需求工作台。'
      : (value.message || '首次使用，请完成下方申请与导入步骤。');
    element('status-dot').className = 'status-dot' + (state.licensed ? ' valid' : '');
    element('license-subject').textContent = subject ? '授权给：' + subject : '';
    element('license-subject').hidden = !state.licensed || !subject;
    element('start-card').classList.toggle('ready', state.licensed);
    element('start-heading').textContent = state.licensed ? '准备好了，开始你的项目' : '授权完成后，开始你的项目';
    element('start-description').textContent = value.business_ready === true
      ? '工作台已在运行。点击继续使用已有项目。'
      : state.licensed ? '进入后配置自己的模型 API Key，即可开始整理需求。' : '现有授权会自动校验，无需每次重新申请。';
    syncControls();
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const method = options.method || 'GET';
    if (method !== 'GET') headers.set('X-Activation-CSRF', state.csrf);
    let response;
    try {
      response = await fetch('/activation/api/' + path, {
        ...options, method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      });
    } catch (_) {
      throw new Error('无法连接本机服务。请确认需求 Agent 仍在运行，然后刷新状态。');
    }
    let data;
    try {
      data = await response.json();
    } catch (_) {
      throw new Error('本机服务未返回有效结果，请刷新状态后重试。');
    }
    if (!response.ok) {
      const error = new Error(data.message || (response.status === 409
        ? '仍有材料读取或模型任务正在执行，请等待结束后重试。'
        : '操作未完成，请核对文件或刷新授权状态后重试。'));
      error.code = typeof data.code === 'string' ? data.code : '';
      if (error.code) error.message += '（' + error.code + '）';
      throw error;
    }
    return data;
  }

  async function act(action) {
    if (state.busy || state.stopped) return;
    state.busy = true;
    syncControls();
    try {
      await action();
    } catch (error) {
      notify(error.message || '操作未完成，请重试。', 'error');
    } finally {
      state.busy = false;
      syncControls();
    }
  }

  async function refresh() {
    try {
      const status = await api('status');
      if (typeof status.csrf !== 'string' || !status.csrf || typeof status.licensed !== 'boolean') {
        throw new Error('授权状态暂不可用，请重新打开需求 Agent。');
      }
      updateStatus(status);
    } catch (error) {
      state.csrf = '';
      state.licensed = false;
      element('license-status').textContent = '暂时无法连接';
      element('license-description').textContent = error.message;
      element('status-dot').className = 'status-dot error';
      element('license-subject').hidden = true;
      element('start-card').classList.remove('ready');
      throw error;
    }
  }

  function downloadRequest(request) {
    const blob = new Blob([JSON.stringify(request, null, 2) + '\n'], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'requirements-agent-request.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  element('request-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const displayName = element('display-name').value.trim();
    if (!displayName) {
      element('display-name').focus();
      notify('请先填写个人或单位名称，再保存授权申请。', 'error');
      return;
    }
    act(async () => {
      const request = await api('request', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: displayName }),
      });
      downloadRequest(request);
      notify('授权申请已生成并开始下载。请将 JSON 文件交给软件拥有者，收到许可证后在右侧导入。');
    });
  });

  element('license-file').addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    state.selectedFile = null;
    if (!file) {
      element('file-label').textContent = '选择许可证 JSON 文件';
      element('file-description').textContent = '保留收到的原始文件，无需编辑';
    } else if (!file.size || file.size > maxLicenseBytes) {
      event.target.value = '';
      element('file-label').textContent = '请重新选择许可证文件';
      element('file-description').textContent = '文件不能为空，且不能超过 32 KiB';
      notify('所选文件为空或超过 32 KiB，请选择软件拥有者提供的原始许可证 JSON。', 'error');
    } else {
      state.selectedFile = file;
      element('file-label').textContent = file.name;
      element('file-description').textContent = '已选择 · ' + Math.max(1, Math.round(file.size / 1024)) + ' KiB';
    }
    syncControls();
  });

  element('install-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!state.selectedFile) return;
    act(async () => {
      const replace = element('replace-license').checked;
      const documentText = await state.selectedFile.text();
      const result = await api('install' + (replace ? '?replace=true' : ''), {
        method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: documentText,
      });
      if (result.licensed !== true) throw new Error('许可证尚未被确认有效，请刷新状态后重试。');
      await refresh();
      element('replace-license').checked = false;
      notify('许可证已导入并完成本机验证。现在可以进入工作台。');
    });
  });

  controls.start.addEventListener('click', () => act(async () => {
    notify('正在准备工作台，请稍候…', 'info');
    const result = await api('start', { method: 'POST' });
    if (result.ready !== true || result.url !== '/') throw new Error('工作台尚未就绪，请稍后重试。');
    window.location.assign('/');
  }));

  controls.refresh.addEventListener('click', () => act(async () => {
    await refresh();
    notify(state.licensed ? '授权状态已更新，本机授权有效。' : '授权状态已更新，请完成申请与导入。', 'info');
  }));

  const shutdownDialog = element('shutdown-dialog');
  controls.shutdown.addEventListener('click', () => shutdownDialog.showModal());
  element('cancel-shutdown').addEventListener('click', () => shutdownDialog.close());
  element('confirm-shutdown').addEventListener('click', () => {
    shutdownDialog.close();
    act(async () => {
      const result = await api('shutdown', { method: 'POST' });
      if (result.status !== 'STOPPING') throw new Error('本机服务尚未确认退出，请稍后重试。');
      state.stopped = true;
      state.csrf = '';
      element('activation-flow').hidden = true;
      element('notice').hidden = true;
      element('stopped-card').hidden = false;
      element('license-status').textContent = '正在安全退出';
      element('license-description').textContent = '服务已接收退出请求，正在结束本机运行。';
      element('status-dot').className = 'status-dot';
      element('stopped-heading').textContent = '本机服务正在退出';
    });
  });

  window.addEventListener('pageshow', (event) => {
    if (event.persisted && !state.stopped) act(refresh);
  });
  act(refresh);
})();
