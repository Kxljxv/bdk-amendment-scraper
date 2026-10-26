/**
 * Content script for https://antraege.gruene.de
 *
 * Injects the amendments that exist on the platform but are not listed
 * publicly into the places where Antragsgrün renders amendments. The markup is
 * a byte-for-byte copy of the templates in the Antragsgrün sources
 * (`views/consultation/LayoutHelper.php` and
 * `models/layoutHooks/StdHooks.php`), so injected entries are indistinguishable
 * from natively rendered ones.
 *
 * Motion detail page – the amendments are placed in the right hand sidebar,
 * next to the motion text:
 *
 *   <li><a class="amendment25530" href="...">25530</a> (Initiator, Status)</li>
 *
 * Motion list page (`li.motion > ul.amendments`):
 *
 *   <li class="amendmentRow25530 amendment"><span class="privateCommentHolder"></span>
 *   <a href="..." class="amendmentTitle amendment25530">25530</a>
 *   <p class="date"><span class="sr-only">Angelegt am</span> Gestern</p>
 *   <span class="info">Initiator<span class="womenQuota">(...)</span></span>
 *   <span class='clearfix'></span>
 *   </li>
 */

'use strict';

const INJECTED_ATTR = 'data-agx-injected';
const AMENDMENT_LABEL = 'Änderungsantrag';
const CREATED_ON_LABEL = 'Angelegt am';
const INITIATED_BY = 'gestellt von';
const LOGIN_ATTR = 'data-agx-login';
const TOGGLER_ATTR = 'data-agx-toggler';
const BOUND_ATTR = 'data-agx-toggler-bound';
const SIDEBAR_ATTR = 'data-agx-amendments';

const GERMAN_MONTHS = [
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
];

const state = {
    dataset: null,
    byMotion: new Map(),
    isInjecting: false,
    observer: null,
    scheduled: false,
    promptOpen: false,
    usageReported: false,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Escapes a value for safe usage in HTML text and attributes.
 *
 * @param {*} value
 * @returns {string}
 */
function escapeHtml(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * @param {Date} date
 * @returns {string}
 */
function isoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

/**
 * Reproduces `Tools::formatMysqlDateWithAria()` for the German locale.
 *
 * @param {string} value ISO date (YYYY-MM-DD)
 * @returns {string}
 */
function formatDateWithAria(value) {
    if (!value) {
        return '';
    }
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
    if (!match) {
        return escapeHtml(value);
    }

    const now = new Date();
    const yesterday = new Date(now.getTime());
    yesterday.setDate(now.getDate() - 1);

    if (value === isoDate(now)) {
        return 'Heute';
    }
    if (value === isoDate(yesterday)) {
        return 'Gestern';
    }

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const spoken = `${day}. ${GERMAN_MONTHS[month - 1]} ${year}`;
    const visible = `${String(day).padStart(2, '0')}.${String(month).padStart(2, '0')}.${year}`;

    return (
        `<time datetime="${escapeHtml(value)}">` +
        `<span class="sr-only">${escapeHtml(spoken)}</span>` +
        `<span aria-hidden="true">${escapeHtml(visible)}</span>` +
        '</time>'
    );
}

/**
 * Converts an absolute URL into a site relative one.
 *
 * Antragsgrün renders its links relative ("/52bdk/<slug>/<id>"), so the
 * injected markup stays byte identical to the native output.
 *
 * @param {string} url
 * @returns {string}
 */
function toRelativeUrl(url) {
    if (!url) {
        return '';
    }
    try {
        const parsed = new URL(url, window.location.origin);
        if (parsed.origin === window.location.origin) {
            return `${parsed.pathname}${parsed.search}${parsed.hash}`;
        }
        return parsed.href;
    } catch (error) {
        return String(url);
    }
}

/**
 * Returns the slug of the motion the current page belongs to, or null.
 *
 * @returns {string|null}
 */
function currentMotionSlug() {
    const parts = window.location.pathname.split('/').filter(Boolean);
    if (parts.length !== 2) {
        // "/" -> convention start page, "/conv/slug/id" -> amendment page
        return null;
    }
    return parts[1];
}

/**
 * Returns the slug of the motion a list item belongs to.
 *
 * Regular motions link to their page with a "motionLink<id>" class, statutes
 * ("Gesetze") with an "amendmentLink<id>" class. Both point at the parent
 * motion, which is the slug the dataset uses.
 *
 * @param {HTMLElement} item
 * @returns {string|null}
 */
function motionSlugOfListItem(item) {
    const links = item.querySelectorAll('a[href]');
    for (const link of links) {
        const classes = link.getAttribute('class') || '';
        if (!/(motionLink|amendmentLink)\d+/.test(classes)) {
            continue;
        }
        const href = link.getAttribute('href') || '';
        // Regular motion: "/<convention>/<motion slug>"
        let match = /^\/[^/]+\/([^/?#]+)\/?$/.exec(href);
        if (match) {
            return match[1];
        }
        // Statute: "/<convention>/<motion slug>/<statute id>"
        match = /^\/[^/]+\/([^/?#]+)\/\d+\/?$/.exec(href);
        if (match) {
            return match[1];
        }
    }
    return null;
}

/**
 * Builds the list entry of the motion detail page and the sidebar.
 *
 * Mirrors `StdHooks::getMotionFormattedAmendmentList()`. The link text is
 * always the id of the amendment, so that it is unambiguous.
 *
 * @param {object} amendment
 * @returns {string}
 */
function buildDetailItem(amendment) {
    const label = String(amendment.id);
    const initiator = amendment.initiator || '';
    const status = amendment.status || '';

    let meta = initiator;
    if (status) {
        meta = meta ? `${meta}, ${status}` : status;
    }

    const suffix = meta ? ` (${escapeHtml(meta)})` : '';

    return (
        `<li ${INJECTED_ATTR}="${escapeHtml(amendment.id)}">` +
        `<a class="amendment${escapeHtml(amendment.id)}" href="${escapeHtml(toRelativeUrl(amendment.url))}">${escapeHtml(label)}</a>` +
        suffix +
        '</li>'
    );
}

/**
 * Builds the list entry of the motion list page.
 *
 * Mirrors `LayoutHelper::getAmendmentLineContent()`.
 *
 * @param {object} amendment
 * @returns {string}
 */
function buildMotionListItem(amendment) {
    const title = String(amendment.id);
    const date = formatDateWithAria(amendment.createdDate)
        || escapeHtml(amendment.createdDisplay || '');
    const quota = amendment.womenQuota || '';
    const initiator = escapeHtml(amendment.initiator || '');

    return (
        `<li class="amendmentRow${escapeHtml(amendment.id)} amendment" ${INJECTED_ATTR}="${escapeHtml(amendment.id)}">` +
        '<span class="privateCommentHolder"></span>' +
        `<a href="${escapeHtml(toRelativeUrl(amendment.url))}" class="amendmentTitle amendment${escapeHtml(amendment.id)}">${escapeHtml(title)}</a>` +
        `<p class="date"><span class="sr-only">${CREATED_ON_LABEL}</span> ${date}</p>\n` +
        `<span class="info">${initiator}${quota}</span>\n` +
        "<span class='clearfix'></span>\n" +
        '</li>'
    );
}

/**
 * @param {number} count
 * @returns {string}
 */
function amendmentCountLabel(count) {
    return count === 1 ? '1 Änderungsantrag' : `${count} Änderungsanträge`;
}

/**
 * Returns the heading level that the platform uses for amendment lists.
 *
 * @returns {string}
 */
function amendmentHeadingLevel() {
    const existing = document.querySelector('.amendmentsListHeader');
    return existing && existing.tagName ? existing.tagName.toLowerCase() : 'h3';
}

/**
 * Returns true when the platform hides the amendment lists by default.
 *
 * @returns {boolean}
 */
function hidesAmendmentsByDefault() {
    const existing = document.querySelector('.amendmentsListHeader');
    return Boolean(existing && existing.classList.contains('amendmentsToggler'));
}

/**
 * Updates the counter in an existing "amendmentsListHeader".
 *
 * @param {HTMLElement} header
 * @param {number} count
 */
function updateAmendmentCounter(header, count) {
    const button = header.querySelector('button');
    const target = button || header;
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    let last = null;
    while (node) {
        if (/\d+\s+(Änderungsantrag|Änderungsanträge)/.test(node.nodeValue)) {
            last = node;
        }
        node = walker.nextNode();
    }
    if (last) {
        last.nodeValue = ` ${amendmentCountLabel(count)}`;
    }
}

/**
 * Binds the expand/collapse behaviour of a generated amendment header.
 *
 * Antragsgrün binds its own handler to the headers that exist when the page
 * is loaded. Headers that are generated later (for motions that only have
 * hidden amendments) need their own handler.
 *
 * This must only be used for headers created by the extension: the natively
 * rendered headers already carry a handler of the platform, and a second
 * handler would toggle the state twice and therefore cancel itself out.
 *
 * @param {HTMLElement} header
 */
function bindToggler(header) {
    if (header.getAttribute(BOUND_ATTR)) {
        return;
    }
    header.setAttribute(BOUND_ATTR, '1');

    const button = header.querySelector('button');
    if (!button) {
        return;
    }

    button.addEventListener('click', (event) => {
        event.preventDefault();
        header.classList.toggle('closed');
        header.classList.toggle('opened');
        const list = header.nextElementSibling;
        if (list && list.classList.contains('amendments')) {
            list.classList.toggle('closed');
        }
    });
}

/**
 * Creates the "amendmentsListHeader" element for a motion list item.
 *
 * @param {number} count
 * @returns {HTMLElement}
 */
function createAmendmentHeader(count) {
    const level = amendmentHeadingLevel();
    const header = document.createElement(level);
    header.className = 'amendmentsListHeader';
    header.setAttribute(TOGGLER_ATTR, '1');

    if (hidesAmendmentsByDefault()) {
        header.classList.add('amendmentsToggler', 'closed');
        const button = document.createElement('button');
        button.className = 'btn-link';
        button.innerHTML =
            '<span class="glyphicon glyphicon-chevron-down" aria-hidden="true"></span>' +
            '<span class="glyphicon glyphicon-chevron-up" aria-hidden="true"></span>';
        button.appendChild(document.createTextNode(` ${amendmentCountLabel(count)}`));
        header.appendChild(button);
    } else {
        header.textContent = 'Änderungsanträge';
    }

    bindToggler(header);
    return header;
}

/**
 * Builds the amendment block of the motion detail page.
 *
 * The block is wrapped in a "well" element, because Antragsgrün only applies
 * its heading style ("h2.green") inside a well.
 *
 * @param {boolean} [smallScreen] true for the collapsed navigation bar
 * @returns {HTMLElement}
 */
function createAmendmentBlock(smallScreen) {
    const wrapper = document.createElement('div');
    wrapper.className = smallScreen ? 'well' : 'well hidden-xs';
    wrapper.setAttribute(SIDEBAR_ATTR, '1');

    const section = document.createElement('section');
    section.className = 'amendments';
    section.setAttribute('aria-labelledby', 'agxAmendmentsTitle');

    const heading = document.createElement('h2');
    heading.className = 'green';
    heading.id = 'agxAmendmentsTitle';
    heading.textContent = 'Änderungsanträge';
    section.appendChild(heading);

    const content = document.createElement('div');
    content.className = 'content';

    const list = document.createElement('ul');
    list.className = 'amendments';
    content.appendChild(list);

    section.appendChild(content);
    wrapper.appendChild(section);
    return wrapper;
}

/**
 * Parses the diff blocks of an amendment.
 *
 * The blocks are stored as JSON by the scraper. Every block describes one
 * affected paragraph of the motion.
 *
 * @param {object} amendment
 * @returns {object[]}
 */
function parseDiffBlocks(amendment) {
    if (!amendment || !amendment.diffHtml) {
        return [];
    }
    try {
        const blocks = JSON.parse(amendment.diffHtml);
        return Array.isArray(blocks) ? blocks : [];
    } catch (error) {
        return [];
    }
}

/**
 * Builds a map of the line numbers of the motion text to their paragraph.
 *
 * @returns {Map<number, HTMLElement>}
 */
function buildLineMap() {
    const map = new Map();
    const paragraphs = document.querySelectorAll('.motionTextHolder .paragraph');
    for (const paragraph of paragraphs) {
        for (const span of paragraph.querySelectorAll('.lineNumber[data-line-number]')) {
            const line = Number.parseInt(span.getAttribute('data-line-number'), 10);
            if (Number.isFinite(line) && !map.has(line)) {
                map.set(line, paragraph);
            }
        }
    }
    return map;
}

/**
 * Creates the marker of an amendment next to the affected paragraph.
 *
 * The markup is identical to the one of Antragsgrün
 * (`views/motion/showSimpleTextSection.php`):
 *
 *   <li class="amendment amendment25530" data-first-line="186">
 *     <a data-id="25530" href="/52bdk/…/25530">25530</a>
 *   </li>
 *
 * @param {object} amendment
 * @param {object} block
 * @param {HTMLElement} paragraph
 * @returns {HTMLElement|null}
 */
function createInlineMarker(amendment, block, paragraph) {
    const markerId = `amendment${amendment.id}`;
    if (paragraph.querySelector(`:scope > ul.bookmarks > li.${markerId}`)) {
        return null;
    }

    let list = paragraph.querySelector(':scope > ul.bookmarks');
    if (!list) {
        list = document.createElement('ul');
        list.className = 'bookmarks';
        paragraph.insertBefore(list, paragraph.firstChild);
    }

    const item = document.createElement('li');
    item.className = `amendment ${markerId}`;
    item.setAttribute('data-first-line', String(block.marker));
    item.setAttribute(INJECTED_ATTR, String(amendment.id));

    const link = document.createElement('a');
    link.setAttribute('data-id', String(amendment.id));
    link.setAttribute('href', toRelativeUrl(amendment.url));
    link.textContent = String(amendment.id);
    item.appendChild(link);

    // Keep the order of the markers (by the affected line), like Antragsgrün.
    const siblings = Array.from(list.children);
    const position = siblings.findIndex((sibling) => {
        const line = Number.parseInt(sibling.getAttribute('data-first-line'), 10);
        return Number.isFinite(line) && line > block.marker;
    });
    if (position === -1) {
        list.appendChild(item);
    } else {
        list.insertBefore(item, siblings[position]);
    }

    return item;
}

/**
 * Creates the block that shows the changed text of a paragraph.
 *
 * @param {object} amendment
 * @param {object} block
 * @returns {HTMLElement}
 */
function createAmendedText(amendment, block) {
    const div = document.createElement('div');
    div.className = `text motionTextFormattings textAmendment hidden amendment${amendment.id}`;

    const preamble = document.createElement('div');
    preamble.className = 'preamble';

    const link = document.createElement('a');
    link.setAttribute('href', toRelativeUrl(amendment.url));

    const heading = document.createElement('h3');
    const icon = document.createElement('span');
    icon.className = 'glyphicon glyphicon-chevron-right';
    icon.setAttribute('aria-hidden', 'true');
    heading.appendChild(icon);
    heading.appendChild(document.createTextNode(amendment.title || String(amendment.id)));
    link.appendChild(heading);
    link.appendChild(document.createTextNode(`, ${INITIATED_BY}: ${amendment.initiator || ''}`));

    preamble.appendChild(link);
    div.appendChild(preamble);

    if (block.html) {
        div.insertAdjacentHTML('beforeend', block.html);
    }

    return div;
}

/**
 * Positions the markers of a paragraph, like `MotionParagraph` does in
 * Antragsgrün (`web/js/modules/frontend/MotionShow.js`).
 *
 * @param {HTMLElement} paragraph
 */
function positionInlineMarkers(paragraph) {
    const firstLineNumber = paragraph.querySelector('.lineNumber');
    if (!firstLineNumber) {
        return;
    }
    const paragraphFirstLine = Number.parseInt(
        firstLineNumber.getAttribute('data-line-number'),
        10,
    );
    if (!Number.isFinite(paragraphFirstLine)) {
        return;
    }
    const lineHeight = firstLineNumber.getBoundingClientRect().height || 20;

    const items = Array.from(paragraph.querySelectorAll(':scope > ul.bookmarks > li.amendment'));
    items.sort((a, b) => {
        const left = Number.parseInt(a.getAttribute('data-first-line'), 10) || 0;
        const right = Number.parseInt(b.getAttribute('data-first-line'), 10) || 0;
        return left - right;
    });

    for (const item of items) {
        const firstLine = Number.parseInt(item.getAttribute('data-first-line'), 10) || 0;
        let delta = (firstLine - paragraphFirstLine) * lineHeight;

        let sibling = item.previousElementSibling;
        while (sibling) {
            delta -= sibling.getBoundingClientRect().height;
            delta -= Number.parseInt(window.getComputedStyle(sibling).marginTop, 10) || 0;
            delta -= 7;
            sibling = sibling.previousElementSibling;
        }

        if (delta < 0) {
            delta = 0;
        }
        item.style.marginTop = `${delta}px`;
    }
}

/**
 * Shows the changed text of a paragraph.
 *
 * @param {HTMLElement} paragraph
 * @param {string} amendmentId
 */
function showInlineAmendment(paragraph, amendmentId) {
    paragraph.querySelector(':scope > .textOrig')?.classList.add('hidden');
    paragraph.querySelectorAll(':scope > .textAmendment').forEach((element) => {
        element.classList.add('hidden');
    });
    paragraph
        .querySelector(`:scope > .textAmendment.amendment${amendmentId}`)
        ?.classList.remove('hidden');
    paragraph
        .querySelector(`:scope > ul.bookmarks li.amendment${amendmentId} a`)
        ?.classList.add('active');
}

/**
 * Hides the changed text of a paragraph.
 *
 * @param {HTMLElement} paragraph
 * @param {string} amendmentId
 */
function hideInlineAmendment(paragraph, amendmentId) {
    paragraph.querySelector(':scope > .textOrig')?.classList.remove('hidden');
    paragraph.querySelectorAll(':scope > .textAmendment').forEach((element) => {
        element.classList.add('hidden');
    });
    paragraph
        .querySelector(`:scope > ul.bookmarks li.amendment${amendmentId} a`)
        ?.classList.remove('active');
}

/**
 * Binds the behaviour of a generated marker.
 *
 * @param {HTMLElement} item
 * @param {HTMLElement} paragraph
 * @param {object} amendment
 */
function bindInlineMarker(item, paragraph, amendment) {
    const amendmentId = String(amendment.id);
    const link = item.querySelector('a');
    const hasHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;

    if (hasHover) {
        item.addEventListener('mouseover', () => showInlineAmendment(paragraph, amendmentId));
        item.addEventListener('mouseout', () => hideInlineAmendment(paragraph, amendmentId));
        return;
    }

    link.addEventListener('click', (event) => {
        event.preventDefault();
        const target = paragraph.querySelector(
            `:scope > .textAmendment.amendment${amendmentId}`,
        );
        if (target && target.classList.contains('hidden')) {
            showInlineAmendment(paragraph, amendmentId);
        } else {
            hideInlineAmendment(paragraph, amendmentId);
        }
    });
}

/**
 * Injects a marker next to every paragraph that a hidden amendment changes.
 *
 * @returns {number} number of injected markers
 */
function injectInlineAmendments() {
    const slug = currentMotionSlug();
    if (!slug) {
        return 0;
    }
    const hidden = state.byMotion.get(slug);
    if (!hidden || !hidden.length) {
        return 0;
    }

    const lineMap = buildLineMap();
    if (!lineMap.size) {
        return 0;
    }

    let injected = 0;
    const touched = new Set();

    for (const amendment of hidden) {
        const blocks = parseDiffBlocks(amendment);
        for (const block of blocks) {
            const marker = Number.parseInt(block.marker, 10);
            if (!Number.isFinite(marker)) {
                continue;
            }
            const paragraph = lineMap.get(marker);
            if (!paragraph) {
                continue;
            }

            const item = createInlineMarker(amendment, block, paragraph);
            if (!item) {
                continue;
            }

            if (!paragraph.querySelector(`:scope > .textAmendment.amendment${amendment.id}`)) {
                const amended = createAmendedText(amendment, block);
                const original = paragraph.querySelector(':scope > .textOrig');
                if (original) {
                    original.parentNode.insertBefore(amended, original.nextSibling);
                } else {
                    paragraph.appendChild(amended);
                }
            }

            bindInlineMarker(item, paragraph, amendment);
            touched.add(paragraph);
            injected += 1;
        }
    }

    for (const paragraph of touched) {
        positionInlineMarkers(paragraph);
    }

    return injected;
}

/**
 * Injects the hidden amendments into the sidebar of a motion detail page.
 *
 * The amendments are placed next to the motion text, in the right hand
 * column of the Antragsgrün layout.
 *
 * @returns {number} number of injected amendments
 */
function injectIntoMotionSidebar() {
    const slug = currentMotionSlug();
    if (!slug) {
        return 0;
    }
    const hidden = state.byMotion.get(slug);
    if (!hidden || !hidden.length) {
        return 0;
    }

    const sidebar = document.querySelector('#sidebar');
    if (!sidebar) {
        return 0;
    }

    let block = sidebar.querySelector(`[${SIDEBAR_ATTR}]`);
    if (!block) {
        block = createAmendmentBlock(false);
        sidebar.appendChild(block);
    }
    const list = block.querySelector('ul.amendments');

    let injected = 0;
    for (const amendment of hidden) {
        if (list.querySelector(`[${INJECTED_ATTR}="${amendment.id}"]`)) {
            continue;
        }
        list.insertAdjacentHTML('beforeend', buildDetailItem(amendment));
        injected += 1;
    }

    // The small screen layout uses a collapsed navigation bar instead of the
    // sidebar, so the amendments are mirrored there.
    const smallList = document.querySelector('#sidebarSmallContent ul');
    if (smallList) {
        let smallBlock = smallList.querySelector(`[${SIDEBAR_ATTR}]`);
        if (!smallBlock) {
            smallBlock = createAmendmentBlock(true);
            const item = document.createElement('li');
            item.appendChild(smallBlock);
            smallList.appendChild(item);
        }
        const smallAmendments = smallBlock.querySelector('ul.amendments');
        for (const amendment of hidden) {
            if (smallAmendments.querySelector(`[${INJECTED_ATTR}="${amendment.id}"]`)) {
                continue;
            }
            smallAmendments.insertAdjacentHTML('beforeend', buildDetailItem(amendment));
        }
    }

    return injected;
}

/**
 * Fallback for layouts without a sidebar: the amendments are appended to the
 * amendment section of the main column.
 *
 * @returns {number}
 */
function injectIntoMotionContent() {
    const slug = currentMotionSlug();
    if (!slug) {
        return 0;
    }
    const hidden = state.byMotion.get(slug);
    if (!hidden || !hidden.length) {
        return 0;
    }

    const section = document.querySelector('#mainContent section.amendments');
    if (!section) {
        return 0;
    }
    const content = section.querySelector(':scope > .content');
    if (!content) {
        return 0;
    }

    let list = content.querySelector(':scope > ul.amendments');
    if (!list) {
        const placeholder = content.querySelector(':scope > em');
        if (placeholder) {
            placeholder.remove();
        }
        list = document.createElement('ul');
        list.className = 'amendments';
        content.appendChild(list);
    }

    let injected = 0;
    for (const amendment of hidden) {
        if (list.querySelector(`[${INJECTED_ATTR}="${amendment.id}"]`)) {
            continue;
        }
        list.insertAdjacentHTML('beforeend', buildDetailItem(amendment));
        injected += 1;
    }
    return injected;
}

/**
 * Injects the hidden amendments into the amendment lists of the motion list.
 *
 * @returns {number} number of injected amendments
 */
function injectIntoMotionList() {
    const items = document.querySelectorAll('li.motion');
    let injected = 0;

    for (const item of items) {
        const slug = motionSlugOfListItem(item);
        if (!slug) {
            continue;
        }
        const hidden = state.byMotion.get(slug);
        if (!hidden || !hidden.length) {
            continue;
        }

        let list = item.querySelector(':scope > ul.amendments');
        const header = item.querySelector(':scope > .amendmentsListHeader');

        if (!list) {
            const total = hidden.length;
            const newHeader = createAmendmentHeader(total);
            list = document.createElement('ul');
            list.className = 'amendments';
            if (hidesAmendmentsByDefault()) {
                list.classList.add('closed');
            }
            item.appendChild(newHeader);
            item.appendChild(list);
        }

        // Only headers that were generated by the extension get an own click
        // handler. The headers rendered by Antragsgrün already have one, and a
        // second handler would toggle the state twice and therefore cancel
        // itself out.

        for (const amendment of hidden) {
            if (list.querySelector(`[${INJECTED_ATTR}="${amendment.id}"]`)) {
                continue;
            }
            list.insertAdjacentHTML('beforeend', buildMotionListItem(amendment));
            injected += 1;
        }

        if (header) {
            updateAmendmentCounter(header, list.children.length);
        }
    }

    return injected;
}

/**
 * Runs the injection for the current page.
 *
 * @returns {number}
 */
function inject() {
    if (state.isInjecting || !state.dataset) {
        return 0;
    }
    state.isInjecting = true;
    let injected = 0;
    try {
        try {
            injected += injectInlineAmendments();
        } catch (error) {
            console.error('[AntragsgruenSync] Inline injection failed:', error);
        }
        try {
            injected += injectIntoMotionSidebar();
            if (injected === 0) {
                injected += injectIntoMotionContent();
            }
        } catch (error) {
            console.error('[AntragsgruenSync] Detail injection failed:', error);
        }
        try {
            injected += injectIntoMotionList();
        } catch (error) {
            console.error('[AntragsgruenSync] List injection failed:', error);
        }
        return injected;
    } finally {
        state.isInjecting = false;
    }
}

/**
 * Groups the amendments of the dataset by their motion.
 *
 * @param {object} dataset
 */
function indexDataset(dataset) {
    state.byMotion = new Map();
    const amendments = (dataset && dataset.amendments) || [];
    for (const amendment of amendments) {
        if (!amendment || amendment.visible) {
            continue;
        }
        const slug = amendment.motionSlug;
        if (!slug) {
            continue;
        }
        if (!state.byMotion.has(slug)) {
            state.byMotion.set(slug, []);
        }
        state.byMotion.get(slug).push(amendment);
    }

    for (const list of state.byMotion.values()) {
        // The platform sorts amendments by their title prefix (natural order,
        // amendments without a prefix come first).
        list.sort((a, b) => {
            const left = String(a.titlePrefix || '');
            const right = String(b.titlePrefix || '');
            const compared = left.localeCompare(right, 'de', { numeric: true, sensitivity: 'base' });
            if (compared !== 0) {
                return compared;
            }
            return String(a.id).localeCompare(String(b.id), 'de', { numeric: true });
        });
    }
}

/**
 * Starts the MutationObserver that re-runs the injection when the page
 * changes (AJAX loading, tab switching, accordions, ...).
 */
function observePage() {
    if (state.observer || !document.body) {
        return;
    }
    state.observer = new MutationObserver(() => {
        if (state.scheduled) {
            return;
        }
        state.scheduled = true;
        window.setTimeout(() => {
            state.scheduled = false;
            inject();
        }, 100);
    });
    state.observer.observe(document.body, { childList: true, subtree: true });
}

// ---------------------------------------------------------------------------
// Login prompt
// ---------------------------------------------------------------------------

/**
 * Shows the login prompt. The prompt can be dismissed; the extension then
 * stays inactive until the next page load.
 */
function showLoginPrompt() {
    if (state.promptOpen || !document.body) {
        return;
    }
    state.promptOpen = true;

    const box = document.createElement('div');
    box.setAttribute(LOGIN_ATTR, '1');
    box.className = 'agx-login';
    box.innerHTML = `
        <div class="agx-login-head">
            <span class="agx-login-title">Antragsgrün Amendment Sync</span>
            <button type="button" class="agx-login-close" title="Schließen" aria-label="Schließen">&times;</button>
        </div>
        <div class="agx-login-body">
            <p class="agx-login-hint">Zum Anzeigen der nicht öffentlich gelisteten Änderungsanträge bitte anmelden.</p>
            <input type="text" class="agx-login-user" placeholder="Benutzername" autocomplete="username" aria-label="Benutzername">
            <input type="password" class="agx-login-pass" placeholder="Passwort" autocomplete="current-password" aria-label="Passwort">
            <div class="agx-login-error" hidden></div>
            <button type="button" class="agx-login-submit">Anmelden</button>
        </div>`;

    const closeButton = box.querySelector('.agx-login-close');
    const userInput = box.querySelector('.agx-login-user');
    const passInput = box.querySelector('.agx-login-pass');
    const errorBox = box.querySelector('.agx-login-error');
    const submitButton = box.querySelector('.agx-login-submit');

    const dismiss = () => {
        box.remove();
        state.promptOpen = false;
    };

    const submit = async () => {
        const username = userInput.value.trim();
        const password = passInput.value;
        if (!username || !password) {
            errorBox.textContent = 'Bitte Benutzername und Passwort eingeben.';
            errorBox.hidden = false;
            return;
        }

        submitButton.disabled = true;
        errorBox.hidden = true;

        const response = await chrome.runtime.sendMessage({
            type: 'login',
            username,
            password,
            context: {
                pageUrl: window.location.href,
                pageTitle: document.title || '',
                device: collectDeviceInfo(),
            },
        });
        if (response && response.ok) {
            dismiss();
            await loadDataset();
            return;
        }

        submitButton.disabled = false;
        errorBox.textContent = (response && response.error) || 'Anmeldung fehlgeschlagen.';
        errorBox.hidden = false;
    };

    closeButton.addEventListener('click', dismiss);
    submitButton.addEventListener('click', submit);
    passInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            submit();
        }
    });

    document.body.appendChild(box);
    userInput.focus();
}

/**
 * Removes the login prompt, if it is open.
 */
function hideLoginPrompt() {
    const box = document.querySelector(`[${LOGIN_ATTR}]`);
    if (box) {
        box.remove();
    }
    state.promptOpen = false;
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

/**
 * Collects the facts about the device the user is working with.
 *
 * @returns {object}
 */
function collectDeviceInfo() {
    return {
        userAgent: navigator.userAgent || '',
        platform: navigator.platform || '',
        screen: `${window.screen.width}x${window.screen.height}x${window.screen.colorDepth}`,
        language: navigator.language || '',
        timezone: (Intl.DateTimeFormat().resolvedOptions().timeZone) || '',
    };
}

/**
 * Reports the access of this page to the worker (usage log).
 */
function reportUsage() {
    if (state.usageReported) {
        return;
    }
    state.usageReported = true;

    chrome.runtime
        .sendMessage({
            type: 'reportUsage',
            payload: {
                event: 'pageview',
                pageUrl: window.location.href,
                pageTitle: document.title || '',
                device: collectDeviceInfo(),
            },
        })
        .catch(() => {
            // The usage log must never break the page.
        });
}

/**
 * Requests the dataset (revalidating it with the worker) and injects it.
 */
async function loadDataset() {
    let response;
    try {
        response = await chrome.runtime.sendMessage({
            type: 'getData',
            revalidate: true,
            motion: currentMotionSlug(),
        });
    } catch (error) {
        return;
    }

    if (!response) {
        return;
    }

    if (!response.ok) {
        if (response.error === 'not_authenticated') {
            hideLoginPrompt();
            showLoginPrompt();
        }
        return;
    }

    hideLoginPrompt();
    state.dataset = response.dataset;
    indexDataset(state.dataset);
    inject();
    observePage();
    reportUsage();
}

chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === 'dataUpdated') {
        loadDataset();
    }
});

loadDataset();
