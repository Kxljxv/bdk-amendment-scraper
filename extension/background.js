/**
 * Background service worker.
 *
 * Responsibilities:
 *   - keep the credentials and the endpoint configuration out of the page
 *   - hold the user session (username/password login, session token)
 *   - fetch the dataset from the Cloudflare Worker, revalidating on every
 *     page load so the data is always up to date
 *   - cache the dataset in chrome.storage.local
 *   - answer content script requests with the cached dataset
 */

const DEFAULT_SETTINGS = {
    workerUrl: 'https://antragsgruen-amendment-sync.koljav.workers.dev',
    convention: '52bdk',
    refreshMinutes: 5,
    enabled: true,
};

const SETTINGS_KEY = 'settings';
const DATASET_KEY = 'dataset';
const SESSION_KEY = 'session';
const ALARM_NAME = 'antragsgruen-sync-refresh';

/**
 * @returns {Promise<typeof DEFAULT_SETTINGS>}
 */
async function getSettings() {
    const stored = await chrome.storage.local.get(SETTINGS_KEY);
    const settings = stored[SETTINGS_KEY] || {};
    return { ...DEFAULT_SETTINGS, ...settings };
}

/**
 * @param {Partial<typeof DEFAULT_SETTINGS>} changes
 * @returns {Promise<void>}
 */
async function saveSettings(changes) {
    const settings = { ...(await getSettings()), ...changes };
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    await scheduleRefresh(settings);
}

/**
 * @returns {Promise<object|null>}
 */
async function getSession() {
    const stored = await chrome.storage.local.get(SESSION_KEY);
    const session = stored[SESSION_KEY];
    if (!session || !session.token) {
        return null;
    }
    if (session.expiresAt && Date.parse(session.expiresAt) < Date.now()) {
        await clearSession();
        return null;
    }
    return session;
}

/**
 * @returns {Promise<void>}
 */
async function clearSession() {
    await chrome.storage.local.remove(SESSION_KEY);
}

/**
 * (Re)creates the periodic refresh alarm.
 *
 * @param {typeof DEFAULT_SETTINGS} settings
 */
async function scheduleRefresh(settings) {
    await chrome.alarms.clear(ALARM_NAME);
    const minutes = Math.max(1, Number(settings.refreshMinutes) || DEFAULT_SETTINGS.refreshMinutes);
    chrome.alarms.create(ALARM_NAME, { periodInMinutes: minutes, delayInMinutes: 1 });
}

/**
 * Builds the URL of the read endpoint.
 *
 * @param {typeof DEFAULT_SETTINGS} settings
 * @param {string|null} motion
 * @returns {string}
 */
function dataUrl(settings, motion) {
    const base = String(settings.workerUrl || '').replace(/\/+$/, '');
    const convention = encodeURIComponent(settings.convention || DEFAULT_SETTINGS.convention);
    let url = `${base}/api/data?convention=${convention}&includeVisible=false`;
    if (motion) {
        // The diff texts are only transferred for the motion that is displayed.
        url += `&motion=${encodeURIComponent(motion)}`;
    }
    return url;
}

/**
 * The cache key of a dataset. Every motion has its own cache entry, because
 * only the data of the displayed motion contains the diff texts.
 *
 * @param {string|null} motion
 * @returns {string}
 */
function datasetKey(motion) {
    return motion ? `${DATASET_KEY}:${motion}` : DATASET_KEY;
}

/**
 * Logs a user in and stores the session.
 *
 * @param {string} username
 * @param {string} password
 * @returns {Promise<object>}
 */
async function login(username, password, context) {
    const settings = await getSettings();
    const base = String(settings.workerUrl || '').replace(/\/+$/, '');
    if (!base) {
        throw new Error('Keine Worker-URL konfiguriert.');
    }

    const response = await fetch(`${base}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            username,
            password,
            pageUrl: (context && context.pageUrl) || '',
            pageTitle: (context && context.pageTitle) || '',
            device: (context && context.device) || {},
        }),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.error || `Anmeldung fehlgeschlagen (HTTP ${response.status}).`);
    }

    await chrome.storage.local.set({
        [SESSION_KEY]: {
            token: payload.token,
            expiresAt: payload.expiresAt,
            user: payload.user,
        },
    });

    await loadDataset({ revalidate: true });
    await notifyTabs();

    return payload.user;
}

/**
 * Ends the session.
 *
 * @returns {Promise<void>}
 */
async function logout() {
    const session = await getSession();
    const settings = await getSettings();
    const base = String(settings.workerUrl || '').replace(/\/+$/, '');

    if (session && base) {
        try {
            await fetch(`${base}/api/auth/logout`, {
                method: 'POST',
                headers: { Authorization: `Session ${session.token}` },
            });
        } catch (error) {
            // The session is removed locally in any case.
        }
    }

    await clearSession();
    const all = await chrome.storage.local.get(null);
    const toRemove = Object.keys(all).filter((key) => key.startsWith(DATASET_KEY));
    if (toRemove.length) {
        await chrome.storage.local.remove(toRemove);
    }
    await notifyTabs();
}

/**
 * Loads the dataset from the Cloudflare Worker.
 *
 * With `revalidate` the cached ETag is sent, so the worker can answer with
 * "304 Not Modified" and no payload has to be transferred.
 *
 * @param {{revalidate?: boolean}} [options]
 * @returns {Promise<{ok: boolean, dataset?: object, error?: string}>}
 */
async function loadDataset(options = {}) {
    const settings = await getSettings();
    if (!settings.enabled) {
        return { ok: false, error: 'Synchronisierung ist deaktiviert.' };
    }

    const session = await getSession();
    if (!session) {
        return { ok: false, error: 'not_authenticated' };
    }

    const motion = options.motion || null;
    const key = datasetKey(motion);

    const stored = await chrome.storage.local.get(key);
    const cached = stored[key];

    const headers = {
        Authorization: `Session ${session.token}`,
        Accept: 'application/json',
    };
    if (options.revalidate && cached && cached.etag) {
        headers['If-None-Match'] = cached.etag;
    }

    let response;
    try {
        response = await fetch(dataUrl(settings, motion), { headers });
    } catch (error) {
        const message = error && error.message ? error.message : String(error);
        await setDatasetError(key, message);
        return { ok: false, error: message };
    }

    if (response.status === 401) {
        await clearSession();
        await notifyTabs();
        return { ok: false, error: 'not_authenticated' };
    }

    if (response.status === 304 && cached && cached.dataset) {
        await chrome.storage.local.set({
            [key]: { ...cached, fetchedAt: Date.now(), error: null },
        });
        return { ok: true, dataset: cached.dataset };
    }

    if (!response.ok) {
        const message = `Worker antwortete mit HTTP ${response.status}.`;
        await setDatasetError(key, message);
        return { ok: false, error: message };
    }

    const payload = await response.json();
    if (!payload || payload.ok !== true) {
        const message = 'Unerwartete Antwort vom Worker.';
        await setDatasetError(key, message);
        return { ok: false, error: message };
    }

    await chrome.storage.local.set({
        [key]: {
            dataset: payload,
            etag: response.headers.get('etag'),
            fetchedAt: Date.now(),
            error: null,
        },
    });

    return { ok: true, dataset: payload };
}

/**
 * @param {string} key
 * @param {string} message
 * @returns {Promise<void>}
 */
async function setDatasetError(key, message) {
    const stored = await chrome.storage.local.get(key);
    await chrome.storage.local.set({
        [key]: { ...(stored[key] || {}), error: message },
    });
}

/**
 * Records a usage event ("when and from which device did the user access
 * Antragsgrün") in the worker.
 *
 * @param {object} payload
 * @returns {Promise<boolean>}
 */
async function reportUsage(payload) {
    const settings = await getSettings();
    const session = await getSession();
    const base = String(settings.workerUrl || '').replace(/\/+$/, '');

    if (!session || !base) {
        return false;
    }

    try {
        const response = await fetch(`${base}/api/usage`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Session ${session.token}`,
            },
            body: JSON.stringify(payload),
        });
        return response.ok;
    } catch (error) {
        // A failing usage report must never break the extension.
        return false;
    }
}

/**
 * Tells every open Antragsgrün tab that the state changed.
 */
async function notifyTabs() {
    const tabs = await chrome.tabs.query({ url: 'https://antraege.gruene.de/*' });
    for (const tab of tabs) {
        if (typeof tab.id === 'undefined') {
            continue;
        }
        chrome.tabs.sendMessage(tab.id, { type: 'dataUpdated' }).catch(() => {
            // The tab may not have a content script (e.g. a PDF view).
        });
    }
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(async () => {
    const settings = await getSettings();
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    await scheduleRefresh(settings);
    await loadDataset({ revalidate: true });
});

chrome.runtime.onStartup.addListener(async () => {
    const settings = await getSettings();
    await scheduleRefresh(settings);
    await loadDataset({ revalidate: true });
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) {
        loadDataset({ revalidate: true });
    }
});

chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes[SETTINGS_KEY]) {
        scheduleRefresh(changes[SETTINGS_KEY].newValue || DEFAULT_SETTINGS);
    }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    (async () => {
        try {
            switch (message && message.type) {
                case 'getData': {
                    const session = await getSession();
                    if (!session) {
                        sendResponse({ ok: false, error: 'not_authenticated' });
                        return;
                    }
                    const result = await loadDataset({
                        revalidate: message.revalidate !== false,
                        motion: message.motion || null,
                    });
                    sendResponse({
                        ok: result.ok,
                        dataset: result.dataset,
                        error: result.error,
                        session,
                    });
                    break;
                }
                case 'refresh': {
                    const result = await loadDataset({
                        revalidate: true,
                        motion: message.motion || null,
                    });
                    sendResponse(result);
                    break;
                }
                case 'login': {
                    const user = await login(
                        message.username,
                        message.password,
                        message.context,
                    );
                    sendResponse({ ok: true, user });
                    break;
                }
                case 'logout': {
                    await logout();
                    sendResponse({ ok: true });
                    break;
                }
                case 'reportUsage': {
                    const result = await reportUsage(message.payload || {});
                    sendResponse({ ok: true, recorded: result });
                    break;
                }
                case 'getSettings': {
                    sendResponse({ ok: true, settings: await getSettings() });
                    break;
                }
                case 'saveSettings': {
                    await saveSettings(message.settings || {});
                    sendResponse({ ok: true, settings: await getSettings() });
                    break;
                }
                case 'getStatus': {
                    const stored = await chrome.storage.local.get(DATASET_KEY);
                    sendResponse({
                        ok: true,
                        session: await getSession(),
                        dataset: stored[DATASET_KEY] || null,
                    });
                    break;
                }
                default:
                    sendResponse({ ok: false, error: 'Unbekannte Nachricht' });
            }
        } catch (error) {
            sendResponse({
                ok: false,
                error: error && error.message ? error.message : String(error),
            });
        }
    })();
    return true; // keep the message channel open for the async response
});
