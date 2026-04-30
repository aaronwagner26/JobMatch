const DEFAULT_SERVER_URL = 'http://127.0.0.1:8181';

const serverUrlInput = document.querySelector('#serverUrl');
const tokenInput = document.querySelector('#token');
const testButton = document.querySelector('#testButton');
const captureButton = document.querySelector('#captureButton');
const statusNode = document.querySelector('#status');

void init();

async function init() {
  const stored = await chrome.storage.local.get(['jobmatchServerUrl', 'jobmatchToken']);
  serverUrlInput.value = stored.jobmatchServerUrl || DEFAULT_SERVER_URL;
  tokenInput.value = stored.jobmatchToken || '';
  testButton.addEventListener('click', () => void testConnection());
  captureButton.addEventListener('click', () => void captureVisibleJobs());
}

async function saveConfig() {
  const serverUrl = normalizeServerUrl(serverUrlInput.value);
  const token = tokenInput.value.trim();
  await chrome.storage.local.set({
    jobmatchServerUrl: serverUrl,
    jobmatchToken: token,
  });
  return { serverUrl, token };
}

async function testConnection() {
  setBusy(true);
  try {
    const { serverUrl } = await saveConfig();
    const response = await fetch(`${serverUrl}/api/browser-capture/status`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    setStatus(`Connected to ${payload.app}.\nPOST ${payload.capture_endpoint}`);
  } catch (error) {
    setStatus(`Connection failed.\n${errorMessage(error)}`);
  } finally {
    setBusy(false);
  }
}

async function captureVisibleJobs() {
  setBusy(true);
  try {
    const { serverUrl, token } = await saveConfig();
    if (!token) {
      throw new Error('Enter the browser capture token from JobMatch Settings first.');
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error('Could not find the active tab.');
    }
    const capture = await chrome.tabs.sendMessage(tab.id, { type: 'jobmatch_capture_page' });
    if (!capture?.ok) {
      throw new Error(capture?.error || 'Could not read jobs from the current page.');
    }
    if (!Array.isArray(capture.payload?.jobs) || capture.payload.jobs.length === 0) {
      throw new Error('No jobs were detected on the current page.');
    }
    const response = await fetch(`${serverUrl}/api/browser-capture`, {
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

function normalizeServerUrl(value) {
  const trimmed = value.trim().replace(/\/+$/, '');
  return trimmed || DEFAULT_SERVER_URL;
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function setBusy(isBusy) {
  testButton.disabled = isBusy;
  captureButton.disabled = isBusy;
}

function setStatus(text) {
  statusNode.textContent = text;
}
