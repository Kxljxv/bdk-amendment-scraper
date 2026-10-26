/**
 * Popup of the extension: shows the sync status, the login state and allows
 * changing the Worker configuration.
 */

'use strict';

const elements = {
    status: document.getElementById('status'),
    loginCard: document.getElementById('loginCard'),
    accountCard: document.getElementById('accountCard'),
    currentUser: document.getElementById('currentUser'),
    username: document.getElementById('username'),
    password: document.getElementById('password'),
    login: document.getElementById('login'),
    logout: document.getElementById('logout'),
    enabled: document.getElementById('enabled'),
    workerUrl: document.getElementById('workerUrl'),
    convention: document.getElementById('convention'),
    refreshMinutes: document.getElementById('refreshMinutes'),
    save: document.getElementById('save'),
    refresh: document.getElementById('refresh'),
};

/**
 * @param {string} message
 * @param {string} kind
 */
function setStatus(message, kind = '') {
    elements.status.textContent = message;
    elements.status.className = `status ${kind}`.trim();
}

/**
 * @param {number} timestamp
 * @returns {string}
 */
function formatTime(timestamp) {
    if (!timestamp) {
        return 'nie';
    }
    try {
        return new Date(timestamp).toLocaleString('de-DE');
    } catch (error) {
        return 'unbekannt';
    }
}

/**
 * Renders the status and the login state.
 *
 * @param {object} status
 */
function renderStatus(status) {
    const session = status && status.session;
    const entry = status && status.dataset;

    if (session && session.user) {
        elements.loginCard.hidden = true;
        elements.accountCard.hidden = false;
        elements.currentUser.textContent = session.user.username;
    } else {
        elements.loginCard.hidden = false;
        elements.accountCard.hidden = true;
    }

    if (entry && entry.error) {
        setStatus(`Letzter Abruf fehlgeschlagen: ${entry.error}`, 'error');
        return;
    }

    if (!session) {
        setStatus('Nicht angemeldet – die Erweiterung bleibt inaktiv.', 'error');
        return;
    }

    if (!entry || !entry.dataset) {
        setStatus('Noch keine Daten geladen.', '');
        return;
    }

    const dataset = entry.dataset;
    const hidden = (dataset.amendments || []).filter((amendment) => !amendment.visible).length;
    const motions = Object.keys(dataset.motions || {}).length;

    setStatus(
        `Synchronisiert: ${formatTime(entry.fetchedAt)}\n` +
        `${hidden} nicht öffentlich gelistete Änderungsanträge in ${motions} Anträgen.\n` +
        `Server-Stand: ${formatTime(dataset.lastSyncAt ? Date.parse(dataset.lastSyncAt) : 0)}`,
        'ok',
    );
}

async function loadSettings() {
    const response = await chrome.runtime.sendMessage({ type: 'getSettings' });
    const settings = (response && response.settings) || {};
    elements.enabled.checked = settings.enabled !== false;
    elements.workerUrl.value = settings.workerUrl || '';
    elements.convention.value = settings.convention || '52bdk';
    elements.refreshMinutes.value = settings.refreshMinutes || 5;
}

async function loadStatus() {
    const response = await chrome.runtime.sendMessage({ type: 'getStatus' });
    renderStatus(response || {});
}

elements.login.addEventListener('click', async () => {
    setStatus('Anmeldung läuft …', '');
    const response = await chrome.runtime.sendMessage({
        type: 'login',
        username: elements.username.value.trim(),
        password: elements.password.value,
    });
    elements.password.value = '';
    if (response && response.ok) {
        setStatus('Angemeldet.', 'ok');
    } else {
        setStatus((response && response.error) || 'Anmeldung fehlgeschlagen.', 'error');
    }
    await loadStatus();
});

elements.logout.addEventListener('click', async () => {
    await chrome.runtime.sendMessage({ type: 'logout' });
    setStatus('Abgemeldet.', '');
    await loadStatus();
});

elements.save.addEventListener('click', async () => {
    await chrome.runtime.sendMessage({
        type: 'saveSettings',
        settings: {
            enabled: elements.enabled.checked,
            workerUrl: elements.workerUrl.value.trim(),
            convention: elements.convention.value.trim() || '52bdk',
            refreshMinutes: Number(elements.refreshMinutes.value) || 5,
        },
    });
    setStatus('Gespeichert.', 'ok');
    await loadStatus();
});

elements.refresh.addEventListener('click', async () => {
    setStatus('Aktualisiere …', '');
    const result = await chrome.runtime.sendMessage({ type: 'refresh' });
    if (result && result.ok) {
        setStatus('Aktualisiert.', 'ok');
    } else {
        setStatus(`Fehler: ${(result && result.error) || 'unbekannt'}`, 'error');
    }
    await loadStatus();
});

(async () => {
    await loadSettings();
    await loadStatus();
})();
