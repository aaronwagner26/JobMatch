const DEFAULT_SERVER_URL = '';
const STORAGE_KEYS = ['jobmatchServerUrl', 'jobmatchToken', 'jobmatchPageCount'];

const serverUrlInput = document.querySelector('#serverUrl');
const tokenInput = document.querySelector('#token');
const pageCountInput = document.querySelector('#pageCount');
const testButton = document.querySelector('#testButton');
const captureButton = document.querySelector('#captureButton');
const fillFormButton = document.querySelector('#fillFormButton');
const fillFocusedButton = document.querySelector('#fillFocusedButton');
const statusNode = document.querySelector('#status');

void init();

async function init() {
  const stored = await loadConfig();
  serverUrlInput.value = stored.jobmatchServerUrl || DEFAULT_SERVER_URL;
  tokenInput.value = stored.jobmatchToken || '';
  pageCountInput.value = String(normalizePageCount(stored.jobmatchPageCount));

  testButton.addEventListener('click', () => void testConnection());
  captureButton.addEventListener('click', () => void captureVisibleJobs());
  fillFormButton.addEventListener('click', () => void fillApplication('common'));
  fillFocusedButton.addEventListener('click', () => void fillApplication('focused'));

  if (!serverUrlInput.value) {
    const discovered = await discoverServerFromOpenTabs();
    if (discovered.serverUrl) {
      serverUrlInput.value = discovered.serverUrl;
    }
    if (discovered.token && !tokenInput.value) {
      tokenInput.value = discovered.token;
    }
    if (discovered.serverUrl || discovered.token) {
      await saveConfig();
      setStatus(`Discovered JobMatch at ${serverUrlInput.value}.`);
    }
  } else if (!tokenInput.value) {
    await hydrateTokenFromServer(false);
  }
}

async function loadConfig() {
  const [syncData, localData] = await Promise.all([
    chrome.storage.sync.get(STORAGE_KEYS),
    chrome.storage.local.get(STORAGE_KEYS),
  ]);
  return {
    jobmatchServerUrl: syncData.jobmatchServerUrl || localData.jobmatchServerUrl || '',
    jobmatchToken: syncData.jobmatchToken || localData.jobmatchToken || '',
    jobmatchPageCount: syncData.jobmatchPageCount || localData.jobmatchPageCount || 1,
  };
}

async function saveConfig() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  const token = tokenInput.value.trim();
  const pageCount = normalizePageCount(pageCountInput.value);
  pageCountInput.value = String(pageCount);
  const payload = {
    jobmatchServerUrl: serverUrl,
    jobmatchToken: token,
    jobmatchPageCount: pageCount,
  };
  await Promise.all([
    chrome.storage.sync.set(payload),
    chrome.storage.local.set(payload),
  ]);
  return { serverUrl, token, pageCount };
}

async function hydrateTokenFromServer(showStatus = true) {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  if (!serverUrl) {
    return { ok: false };
  }
  try {
    const payload = await fetchStatus(serverUrl);
    if (payload.server_origin) {
      serverUrlInput.value = normalizeServerUrl(payload.server_origin);
    }
    if (payload.browser_token) {
      tokenInput.value = String(payload.browser_token);
      await saveConfig();
      if (showStatus) {
        setStatus(`Connected to ${payload.app}.\nSaved token from ${serverUrlInput.value}.`);
      }
      return { ok: true, payload };
    }
    return { ok: true, payload };
  } catch (error) {
    if (showStatus) {
      setStatus(`Connection failed.\n${errorMessage(error)}`);
    }
    return { ok: false, error };
  }
}

async function testConnection() {
  setBusy(true);
  try {
    const { serverUrl } = await saveConfig();
    const payload = await fetchStatus(serverUrl);
    if (payload.server_origin) {
      serverUrlInput.value = normalizeServerUrl(payload.server_origin);
    }
    if (payload.browser_token) {
      tokenInput.value = String(payload.browser_token);
    }
    await saveConfig();
    setStatus(`Connected to ${payload.app}.\nPOST ${payload.capture_endpoint}\nActive resume: ${payload.active_resume_name || 'not set'}`);
  } catch (error) {
    setStatus(`Connection failed.\n${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

async function captureVisibleJobs() {
  setBusy(true);
  try {
    setStatus('Capturing visible jobs from the current page.\nWalking visible result details when available...');
    const config = await saveConfig();
    if (!config.serverUrl) {
      throw new Error('Enter the JobMatch server URL first.');
    }
    let token = config.token;
    if (!token) {
      const hydrated = await hydrateTokenFromServer(false);
      token = hydrated.payload?.browser_token || tokenInput.value.trim();
    }
    if (!token) {
      throw new Error('Could not obtain the browser capture token from JobMatch.');
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error('Could not find the active tab.');
    }
    const capture = await chrome.tabs.sendMessage(tab.id, {
      type: 'jobmatch_capture_page',
      maxPages: config.pageCount,
    });
    if (!capture?.ok) {
      throw new Error(capture?.error || 'Could not read jobs from the current page.');
    }
    if (!Array.isArray(capture.payload?.jobs) || capture.payload.jobs.length === 0) {
      throw new Error('No jobs were detected on the current page.');
    }
    const response = await fetch(`${config.serverUrl}/api/browser-capture`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(capture.payload),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    setStatus(
      [
        `Imported into ${payload.source_name}.`,
        `${config.pageCount} page(s) requested`,
        `${payload.jobs_imported} job(s) parsed`,
        `${payload.jobs_created} new, ${payload.jobs_updated} updated, ${payload.jobs_unchanged} unchanged`,
      ].join('\n')
    );
  } catch (error) {
    setStatus(`Capture failed.\n${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

async function fillApplication(mode) {
  setBusy(true);
  try {
    const config = await saveConfig();
    if (!config.serverUrl) {
      throw new Error('Enter the JobMatch server URL first.');
    }
    let token = config.token;
    if (!token) {
      const hydrated = await hydrateTokenFromServer(false);
      token = hydrated.payload?.browser_token || tokenInput.value.trim();
    }
    if (!token) {
      throw new Error('Could not obtain the browser capture token from JobMatch.');
    }
    const profilePayload = await fetchApplicationProfile(config.serverUrl, token);
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error('Could not find the active tab.');
    }
    const response = await chrome.tabs.sendMessage(tab.id, {
      type: 'jobmatch_fill_application',
      mode,
      profile: profilePayload.profile,
    });
    if (!response?.ok) {
      throw new Error(response?.error || 'Could not fill fields on the current page.');
    }
    setStatus(
      [
        mode === 'focused' ? 'Filled focused field.' : 'Filled common application fields.',
        `${response.filled || 0} field(s) updated`,
        response.preview ? `Examples: ${response.preview}` : '',
      ].filter(Boolean).join('\n')
    );
  } catch (error) {
    setStatus(`Fill failed.\n${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

async function discoverServerFromOpenTabs() {
  const tabs = await chrome.tabs.query({});
  const candidates = [];
  const seen = new Set();

  for (const tab of tabs) {
    const url = normalizeServerUrl(tab.url || '');
    if (!url || seen.has(url)) {
      continue;
    }
    seen.add(url);
    const title = String(tab.title || '');
    const priority = /jobmatch/i.test(title) ? 0 : 1;
    candidates.push({ url, priority });
  }

  candidates.sort((left, right) => left.priority - right.priority || left.url.localeCompare(right.url));
  for (const candidate of candidates.slice(0, 12)) {
    try {
      const payload = await fetchStatus(candidate.url);
      return {
        serverUrl: normalizeServerUrl(payload.server_origin || candidate.url),
        token: String(payload.browser_token || ''),
      };
    } catch (_error) {
      continue;
    }
  }
  return { serverUrl: '', token: '' };
}

async function fetchStatus(serverUrl) {
  const response = await fetch(`${normalizeServerUrl(serverUrl)}/api/browser-capture/status`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function fetchApplicationProfile(serverUrl, token) {
  const response = await fetch(`${normalizeServerUrl(serverUrl)}/api/application-profile`, {
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

function normalizeServerUrl(value) {
  const trimmed = String(value || '').trim().replace(/\/+$/, '');
  if (!trimmed) {
    return DEFAULT_SERVER_URL;
  }
  try {
    const url = new URL(trimmed);
    return `${url.protocol}//${url.host}`;
  } catch (_error) {
    return trimmed;
  }
}

function normalizePageCount(value) {
  const parsed = Number.parseInt(String(value || '1'), 10);
  if (!Number.isFinite(parsed)) {
    return 1;
  }
  return Math.max(1, Math.min(parsed, 5));
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function setBusy(isBusy) {
  testButton.disabled = isBusy;
  captureButton.disabled = isBusy;
  fillFormButton.disabled = isBusy;
  fillFocusedButton.disabled = isBusy;
}

function setStatus(text) {
  statusNode.textContent = text;
}
