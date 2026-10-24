#!/usr/bin/env node
'use strict';

/**
 * Connected-state visual evidence from browser route fixtures only.
 * No stored auth session, database row, provider call, OAuth flow or email send.
 * Run after deploying the source-linked Inbox meeting review.
 *
 * NAVOX_PLAYWRIGHT_MODULE=/absolute/path/to/playwright
 * NAVOX_QA_OUTPUT_DIR=/tmp/navox-inbox-fixtures
 * NAVOX_QA_BASE_URL=https://vox-test.66-175-235-169.nip.io
 * NAVOX_QA_CHROMIUM_PATH=/optional/browser/executable
 * NAVOX_QA_RELEASE=deployed-git-sha
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const LABEL = 'QA fixture — simulated provider responses';
const ZONE = 'America/New_York';
const USER = '11111111-1111-4111-8111-111111111111';
const WORKSPACE = '22222222-2222-4222-8222-222222222222';
const MAILBOX = '33333333-3333-4333-8333-333333333333';
const DRAFT = '44444444-4444-4444-8444-444444444444';
const OWNER = `${WORKSPACE}:${USER}`;
const MINUTE = 60_000;

function localDate(instant) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(instant));
  return ['year', 'month', 'day'].map((type) => parts.find((part) => part.type === type).value).join('-');
}
function addDays(day, amount) {
  const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + amount); return date.toISOString().slice(0, 10);
}
function localInstant(day, clock) {
  const local = `${day}T${clock}`;
  const nominal = Date.parse(`${local}:00Z`);
  function wall(instant) {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(instant));
    const part = (type) => parts.find((value) => value.type === type).value;
    return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`;
  }
  const candidates = [...new Set([-36, -12, 0, 12, 36].map((hours) => {
    const sample = nominal + hours * 60 * MINUTE;
    const offset = Date.parse(`${wall(sample)}:00Z`) - sample;
    return new Date(nominal - offset).toISOString();
  }))].filter((instant) => wall(instant) === local);
  assert.equal(candidates.length, 1, 'Fixture time must be unambiguous in the saved timezone.');
  return candidates[0];
}
function fixtureData(now = Date.now()) {
  const day = addDays(localDate(now), 2);
  const sentAt = new Date(now - 30 * MINUTE).toISOString();
  const common = { to: ['qa-student@example.test'], sent_at: sentAt, open_url: null, truncated: false };
  const messages = [
    {
      ...common, id: 'qa-meeting/opaque+source=', sender: 'qa-professor@example.test',
      subject: 'QA · Office-hours meeting', preview: `Could we meet on ${day} at 2:00 PM?`,
      body: `${LABEL}. This is synthetic acceptance data.\n\nCan we meet on ${day} at 2:00 PM for 30 minutes? Please suggest another time if your schedule conflicts.`,
      reason: 'Synthetic meeting request needs a reply',
    },
    {
      ...common, id: 'qa-project/opaque+source=', sender: 'qa-teammate@example.test',
      subject: 'QA · Project outline review', preview: 'Please review the updated project outline and reply with your feedback.',
      body: `${LABEL}. This is synthetic acceptance data.\n\nPlease review the updated project outline and reply with your feedback. This request does not propose a meeting.`,
      reason: 'Synthetic feedback request needs a reply',
    },
  ];
  const draft = {
    id: DRAFT, revision: 2, payload_hash: 'a'.repeat(64), connection_id: MAILBOX,
    source_message_id: messages[1].id, sender: 'qa-student@example.test', to: ['qa-teammate@example.test'],
    subject: `Re: ${messages[1].subject}`,
    body: 'QA fixture — saved draft, not sent.\n\nThanks for sharing the outline. I will review it and reply with my comments.',
    status: 'review', expires_at: new Date(now + 15 * MINUTE).toISOString(), verified_at: null,
    notice: `${LABEL}. This saved draft exists only in this browser test response. Nothing has been sent.`,
    content_available: true, sent_at: null, reply_state: 'not_checked', reply_checked_at: null,
    reply_received_at: null, reply_scan_complete: false, reply_scan_limit: 50, reply_notice: null,
  };
  const preferences = {
    wake_time: '07:00', sleep_time: '23:00', focus_windows: [], never_schedule: [],
    max_study_minutes: 90, break_minutes: 15, wake_times: Array(7).fill('07:00'), travel_minutes: 0,
    alarm: { enabled: false, days: [0, 1, 2, 3, 4], smart: false, prep_minutes: 30, sound: 'gentle', snooze_minutes: 10, skip_date: null },
  };
  return {
    day, localStart: `${day}T14:00`, meetingStart: localInstant(day, '14:00'), messages, draft, preferences,
    account: { id: USER, email: 'qa-student@example.test', display_name: 'QA fixture student', timezone: ZONE,
      workspace: { id: WORKSPACE, name: 'QA fixture workspace', workspace_type: 'personal' } },
    connection: { id: MAILBOX, email: 'qa-student@example.test', status: 'CONNECTED', read_enabled: true, send_enabled: true },
  };
}
function fixtureSnapshot(fixture, request, now = Date.now()) {
  const checked = new Date(now).toISOString(), expires = new Date(now + 60_000).toISOString();
  const owned = { user_id: USER, workspace_id: WORKSPACE };
  const sourceIds = ['qa-student-schedule', 'qa-provider-calendar'];
  const interval = { start_at: request.start_at, end_at: request.end_at };
  const busy = { start_at: localInstant(fixture.day, '13:50'), end_at: localInstant(fixture.day, '15:00') };
  const opening = (day, start, end) => {
    const start_at = localInstant(day, start), end_at = localInstant(day, end);
    return { start_at, end_at, minutes: (Date.parse(end_at) - Date.parse(start_at)) / MINUTE,
      reasons: [], unchecked_source_ids: [], unknown_travel_record_ids: [], displaced_study_block_ids: [] };
  };
  const confirmed = request.routine_confirmed && request.travel_assumption === 'zero';
  return {
    input: {
      ...interval, ...owned, timezone: ZONE, now: checked, preferences: fixture.preferences,
      routine_confirmed: request.routine_confirmed, required_source_ids: sourceIds,
      coverage: sourceIds.map((source_id) => ({ ...interval, ...owned, source_id, state: 'complete', observed_at: checked, fresh_until: expires, revision: 'qa-fixture-v1' })),
      busy: [{ ...busy, ...owned, id: 'qa-class-conflict', source_id: sourceIds[0], kind: 'class', travel_required: false, travel_before_minutes: 0, travel_after_minutes: 0 }],
      study_blocks: [], uncertainties: [], duration_minutes: request.duration_minutes, include_short_slots: request.include_short_slots,
    },
    result: {
      timezone: ZONE, checked_at: checked, scope: 'provided_owned_sources_only',
      status: request.routine_confirmed ? 'complete' : 'needs_routine',
      confirmed_openings: confirmed ? [opening(fixture.day, '15:10', '16:00'), opening(addDays(fixture.day, 1), '10:00', '12:00')] : [],
      provisional_openings: [], busy: [busy], warnings: [], changes_applied: false,
    },
    checked_at: checked, expires_at: expires,
    revision: { student_revision: 'a'.repeat(64), planning_revision: 1, preferences_revision: request.preferences_revision, availability_generation: 1, availability_revision: 1 },
    source_summary: sourceIds.map((source_id) => ({ source_id, state: 'complete', calendar_count: source_id === sourceIds[1] ? 1 : 0 })),
    changes_applied: false,
  };
}
function playwrightModule() {
  if (process.env.NAVOX_PLAYWRIGHT_MODULE) return require(process.env.NAVOX_PLAYWRIGHT_MODULE);
  for (const name of ['playwright', '@playwright/test', 'playwright-core']) {
    try { return require(name); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
  }
  throw new Error('Playwright is not available. Set NAVOX_PLAYWRIGHT_MODULE to its installed module path.');
}
async function main() {
  const base = new URL(process.env.NAVOX_QA_BASE_URL || 'https://vox-test.66-175-235-169.nip.io');
  assert.equal(base.protocol, 'https:', 'This script expects the HTTPS test deployment.');
  const output = process.env.NAVOX_QA_OUTPUT_DIR || path.join(os.tmpdir(), `navox-inbox-fixtures-${new Date().toISOString().replace(/[:.]/g, '-')}`);
  fs.mkdirSync(output, { recursive: true });
  const fixture = fixtureData();
  const report = {
    schema: 'navox.inbox.route-fixture-qa.v1', label: LABEL, base_url: base.origin,
    release: process.env.NAVOX_QA_RELEASE || null,
    started_at: new Date().toISOString(), completed_at: null, status: 'running',
    fixture_scope: {
      account: 'Synthetic browser response; no account created or authenticated on the server.',
      providers: 'Outlook connection, two source messages and one saved review are synthetic route responses only.',
      calendars: 'Routine, owned class conflict, source coverage and two openings are synthetic contract responses only.',
      source_ids: fixture.messages.map((message) => message.id), owner_key: OWNER, saved_timezone: ZONE,
      source_meeting_local: fixture.localStart, provider_scan_limit: 50,
      actual_provider_accounts_used: false, database_mutations: false, provider_reads: false,
      provider_send: false, provider_oauth: false, ai_generation: false,
      verified_by_this_run: ['Connected Inbox master/detail rendering', 'Exact opened-email evidence and proposed date', 'Explicit review before checking', 'Clash and two contract-valid checked alternatives', 'Saved draft rendering with Send disabled before approval', 'Desktop/mobile and dark/light layouts'],
      not_verified_by_this_run: ['Real Google or Microsoft OAuth consent', 'Real mailbox reads', 'Real provider busy-time availability', 'Real email delivery or Sent receipt', 'General ongoing Inbox triage'],
    },
    cases: [], requests: [], blocked_requests: [], page_errors: [], failures: [],
  };
  const reportPath = path.join(output, 'fixture-scope-and-results.json');
  const saveReport = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
  saveReport();
  const { chromium } = playwrightModule();
  const browser = await chromium.launch({ headless: true, ...(process.env.NAVOX_QA_CHROMIUM_PATH ? { executablePath: process.env.NAVOX_QA_CHROMIUM_PATH } : {}) });
  try {
    for (const screen of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'mobile', width: 375, height: 812 }]) {
      for (const theme of ['dark', 'light']) {
        const caseName = `${screen.name}-${theme}`;
        const item = { name: caseName, viewport: screen, theme, screenshots: [], checks: [], status: 'running' };
        report.cases.push(item);
        const context = await browser.newContext({ viewport: { width: screen.width, height: screen.height }, isMobile: screen.name === 'mobile', hasTouch: screen.name === 'mobile', colorScheme: theme, timezoneId: 'UTC', locale: 'en-US', serviceWorkers: 'block', permissions: [], reducedMotion: 'reduce' });
        // https://playwright.dev/docs/api/class-websocketroute — no connectToServer call.
        assert.equal(typeof context.routeWebSocket, 'function', 'Fixture QA requires Playwright 1.48+ to block all sockets.');
        await context.routeWebSocket(/.*/, async (socket) => {
          report.blocked_requests.push({ case: caseName, method: 'WEBSOCKET', url: socket.url(), reason: 'Sockets are prohibited in read-only fixture QA' });
          await socket.close({ code: 1000, reason: 'Read-only QA fixture' });
        });
        const page = await context.newPage();
        page.setDefaultTimeout(20_000);
        page.on('pageerror', (error) => report.page_errors.push({ case: caseName, message: error.message }));
        await context.addInitScript(({ owner, theme, label }) => {
          localStorage.setItem(`navox:appearance:${owner}`, theme);
          const install = () => {
            if (!document.body || document.getElementById('qa-fixture-banner')) return;
            const banner = document.createElement('div'); banner.id = 'qa-fixture-banner'; banner.textContent = label;
            Object.assign(banner.style, { position: 'fixed', top: '0', left: '0', right: '0', zIndex: '2147483647', padding: '9px 12px', background: '#f7d24c', color: '#171717', borderBottom: '2px solid #171717', font: 'bold 13px/1.3 system-ui,sans-serif', textAlign: 'center', pointerEvents: 'none' });
            banner.setAttribute('role', 'note'); document.body.append(banner);
            document.documentElement.style.scrollPaddingTop = '48px';
          };
          document.addEventListener('DOMContentLoaded', install);
          new MutationObserver(install).observe(document, { childList: true, subtree: true });
        }, { owner: OWNER, theme, label: LABEL });
        const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'private, no-store', 'X-NavoX-QA-Fixture': 'simulated-provider-responses' }, body: JSON.stringify(value) });
        await context.route('**/*', async (route) => {
          const request = route.request(), url = new URL(request.url()), method = request.method();
          if (url.origin !== base.origin) {
            report.blocked_requests.push({ case: caseName, method, url: url.origin + url.pathname, reason: 'External traffic prohibited' });
            return route.abort('blockedbyclient');
          }
          if (!url.pathname.startsWith('/api/v1/')) {
            if (url.pathname.startsWith('/api/')) {
              report.blocked_requests.push({ case: caseName, method, url: url.pathname, reason: 'API namespace outside the declared fixture contract' });
              return json(route, { detail: 'This API namespace is outside the QA fixture scope.' }, 403);
            }
            if (method === 'GET' || method === 'HEAD') return route.continue();
            report.blocked_requests.push({ case: caseName, method, url: url.pathname, reason: 'Non-read application request prohibited' });
            return json(route, { detail: 'QA fixture: external actions are disabled.' }, 403);
          }
          const endpoint = url.pathname.slice('/api/v1'.length).replace(/\/$/, '');
          report.requests.push({ case: caseName, method, endpoint, owner: request.headers()['x-navox-account'] || null, handled: 'synthetic route response' });
          if (method === 'POST' && endpoint === '/student/availability/preview') {
            const input = request.postDataJSON();
            if (request.headers()['x-navox-account'] !== OWNER || input.expected_timezone !== ZONE || input.preferences_revision !== 5) {
              report.failures.push({ case: caseName, message: 'Availability check did not bind the synthetic owner, saved timezone and fresh routine revision.' });
              return json(route, { detail: 'Synthetic availability request did not match its reviewed context.' }, 409);
            }
            return json(route, fixtureSnapshot(fixture, input));
          }
          if (method !== 'GET') {
            report.blocked_requests.push({ case: caseName, method, url: endpoint, reason: 'Writes, sends, generation and OAuth are prohibited' });
            return json(route, { detail: 'QA fixture: writes, sends, generation and OAuth are disabled.' }, 403);
          }
          if (endpoint === '/auth/me') return json(route, fixture.account);
          if (endpoint === '/connections/google') return json(route, []);
          if (endpoint === '/student/profile') return json(route, { school_name: 'QA fixture school', lms: 'none', display_name: 'QA fixture student', timezone: ZONE, term_start: null, term_end: null, revision: 1 });
          if (endpoint === '/today') return json(route, { generated_at: new Date().toISOString(), timezone: ZONE, total: 0, needs_attention: [], coming_up: [], renewals: [], waiting_on: [], completed_recently: [], set_aside: [] });
          if (['/plans', '/communication-drafts', '/actions', '/memory'].includes(endpoint)) return json(route, []);
          if (endpoint === '/agent/state') return json(route, { paused: false });
          if (endpoint === '/thread-watches') return json(route, { items: [] });
          if (endpoint === '/outlook/status') return json(route, { configured: true, connections: [fixture.connection], notice: LABEL });
          if (endpoint === `/outlook/connections/${MAILBOX}/messages`) return json(route, { messages: fixture.messages.map(({ body, to, truncated, ...message }) => message), has_more: false, notice: `${LABEL}. Only two synthetic candidate messages are supplied.` });
          const prefix = `/outlook/connections/${MAILBOX}/messages/`;
          if (endpoint.startsWith(prefix)) {
            const sourceId = decodeURIComponent(endpoint.slice(prefix.length));
            const message = fixture.messages.find((value) => value.id === sourceId);
            return json(route, message || { detail: 'Synthetic message not found.' }, message ? 200 : 404);
          }
          if (endpoint === '/outlook/drafts') return json(route, [fixture.draft]);
          if (endpoint === '/student/availability/routine') return json(route, { ...fixture.preferences, timezone: ZONE, revision: 5 });
          report.blocked_requests.push({ case: caseName, method, url: endpoint, reason: 'Unspecified API read; never forwarded to a real account' });
          return json(route, { detail: 'This API response is outside the declared QA fixture scope.' }, 404);
        });
        async function capture(state, focus) {
          const dismissTip = page.getByRole('button', { name: 'Dismiss Vox tip', exact: true });
          if (await dismissTip.isVisible()) await dismissTip.click();
          async function saveCapture(fullPage, framing = null) {
            assert.equal(await page.locator('#qa-fixture-banner').isVisible(), true, 'Every capture must visibly identify synthetic data.');
            const dimensions = await page.evaluate(() => ({ viewport: window.innerWidth, page: document.documentElement.scrollWidth, theme: document.documentElement.dataset.navoxTheme, scroll_x: window.scrollX, scroll_y: window.scrollY }));
            assert.equal(dimensions.theme, theme);
            assert.ok(dimensions.page <= dimensions.viewport + 1, `Horizontal overflow in ${caseName}/${state}: ${JSON.stringify(dimensions)}`);
            if (fullPage) assert.equal(dimensions.scroll_y, 0, 'Full-page screenshots must start at the document top.');
            const filename = `${caseName}-${state}-QA-FIXTURE-${fullPage ? 'full' : 'viewport'}.png`;
            await page.screenshot({ path: path.join(output, filename), fullPage, animations: 'disabled' });
            item.screenshots.push({ file: filename, state, full_page: fullPage, visible_label: LABEL, dimensions, framing });
          }
          // Fixed chrome must be captured from the top, not stitched into the
          // middle of a long page after a preceding control scrolled into view.
          await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
          await page.waitForFunction(() => window.scrollY === 0 && window.scrollX === 0);
          await saveCapture(true);

          // Frame the meaningful content rather than an arbitrarily tall panel.
          const target = state === 'meeting-clash' ? focus.locator('..')
            : state === 'source-review' ? focus.getByRole('region', { name: 'Meeting source evidence', exact: true })
              : state === 'saved-draft' ? focus.locator('textarea').locator('..')
                : state === 'master-detail' ? focus.getByRole('heading', { name: 'Outlook', exact: true }) : focus;
          const framing = await target.evaluate(async (element, state) => {
            const rect = node => {
              if (!node) return null;
              const box = node.getBoundingClientRect();
              const style = getComputedStyle(node);
              return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                ? { top: box.top, bottom: box.bottom, left: box.left, right: box.right, width: box.width, height: box.height } : null;
            };
            const safeBounds = () => {
              const banner = rect(document.getElementById('qa-fixture-banner'));
              const header = document.getElementById('workspace-content')?.previousElementSibling;
              const headerBox = header && ['fixed', 'sticky'].includes(getComputedStyle(header).position) ? rect(header) : null;
              const nav = rect(document.querySelector('nav[aria-label="Mobile navigation"]'));
              const vox = rect(document.querySelector('[data-vox-launcher="floating"]'));
              return {
                top: Math.max(banner?.bottom ?? 0, headerBox?.bottom ?? 0) + 16,
                bottom: Math.min(window.innerHeight, nav?.top ?? window.innerHeight, vox?.top ?? window.innerHeight) - 16,
              };
            };
            const before = rect(element), initialSafe = safeBounds();
            if (!before) return { target_visible: false, reason: 'Framing target is not visible.' };
            const usable = initialSafe.bottom - initialSafe.top;
            const desiredTop = initialSafe.top + Math.max(0, (usable - before.height) / 2);
            window.scrollBy({ top: before.top - desiredTop, left: 0, behavior: 'instant' });
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const target = rect(element), safe = safeBounds();
            if (!target) return { target_visible: false, reason: 'Framing target disappeared.' };
            const intersection = {
              top: Math.max(target.top, safe.top), bottom: Math.min(target.bottom, safe.bottom),
              left: Math.max(target.left, 0), right: Math.min(target.right, window.innerWidth),
            };
            const intersects = intersection.bottom > intersection.top && intersection.right > intersection.left;
            const hit = intersects ? document.elementFromPoint((intersection.left + intersection.right) / 2, (intersection.top + intersection.bottom) / 2) : null;
            const alternatives = state === 'meeting-clash' ? [...element.querySelectorAll('li')].map(rect) : [];
            return {
              target, unobscured: safe, intersection, scroll_y: window.scrollY,
              target_visible: intersects && !!hit && (hit === element || element.contains(hit)),
              alternatives_visible: state !== 'meeting-clash' || (alternatives.length === 2 && alternatives.every(box => box && box.top >= safe.top && box.bottom <= safe.bottom && box.left >= 0 && box.right <= window.innerWidth)),
              alternatives,
            };
          }, state);
          assert.equal(framing.target_visible, true, `Capture target is obscured in ${caseName}/${state}: ${JSON.stringify(framing)}`);
          assert.equal(framing.alternatives_visible, true, `Both checked alternatives must fit above fixed controls in ${caseName}/${state}: ${JSON.stringify(framing)}`);
          await saveCapture(false, framing);
          saveReport();
        }
        try {
          await page.goto(`${base.origin}/inbox?qa_fixture=simulated-provider-responses`, { waitUntil: 'domcontentloaded' });
          const inbox = page.getByRole('region', { name: 'Outlook inbox', exact: true });
          await inbox.waitFor({ state: 'visible' });
          await inbox.getByRole('button', { name: 'Needs reply · 2', exact: true }).waitFor();
          await page.waitForFunction((wanted) => document.documentElement.dataset.navoxTheme === wanted, theme);
          await capture('master-detail', inbox);
          await inbox.getByRole('button').filter({ hasText: fixture.messages[0].subject }).first().click();
          await inbox.locator('article').getByText(fixture.messages[0].subject, { exact: true }).waitFor();
          const review = inbox.locator('details').filter({ has: page.getByText('Review the meeting proposed in this email', { exact: true }) });
          await review.getByLabel('I checked the source, date, time, timezone and duration above.', { exact: true }).waitFor();
          assert.equal(await review.locator('input[type="datetime-local"]').inputValue(), fixture.localStart);
          assert.equal(await review.getByRole('button', { name: 'Check this time', exact: true }).isDisabled(), true);
          assert.equal(report.requests.filter((row) => row.case === caseName && row.endpoint === '/student/availability/preview').length, 0);
          await capture('source-review', review);
          await review.getByLabel('I checked the source, date, time, timezone and duration above.', { exact: true }).check();
          await review.getByLabel('My saved wake, sleep and protected times are up to date.', { exact: true }).check();
          await review.getByLabel('Assume no extra travel around commitments for this check.', { exact: true }).check();
          await review.getByRole('button', { name: 'Check this time', exact: true }).click();
          await review.getByText('This time conflicts with your saved schedule or its buffers.', { exact: true }).waitFor();
          await review.getByText('Two checked alternatives:', { exact: true }).waitFor();
          const checkedAlternatives = await review.locator('[role="status"] ul > li').allTextContents();
          assert.equal(checkedAlternatives.length, 2, 'The reviewed clash must show exactly two checked alternatives.');
          assert.equal(new Set(checkedAlternatives).size, 2, 'The checked alternatives must be distinct.');
          item.checks.push('Opened-email evidence matched source; no automatic availability check; explicit review returned clash and exactly two confirmed alternatives.');
          await capture('meeting-clash', review.getByText('Two checked alternatives:', { exact: true }));
          await review.getByRole('button', { name: 'Use these times in reply instructions', exact: true }).click();
          const editor = inbox.getByRole('region', { name: 'Outlook reply review', exact: true });
          const instructions = await editor.getByLabel('What should the reply say?').inputValue();
          assert.ok(instructions.includes(`(${ZONE.replaceAll('_', ' ')})`), 'Reply instructions must preserve the displayed saved timezone.');
          for (const alternative of checkedAlternatives) {
            assert.ok(alternative.trim() && instructions.includes(alternative), `Reply instructions lost the exact checked alternative: ${alternative}`);
          }
          assert.ok(instructions.includes('Do not invent personal reasons or promise that a meeting has been booked.'), 'Reply instructions must retain the no-invention and no-booking safeguards.');
          item.checks.push('Reply instructions retained both exact checked alternative labels, the displayed saved timezone and the no-invention/no-booking safeguards.');
          await inbox.getByRole('button', { name: 'Drafts · 1', exact: true }).click();
          await inbox.getByRole('button').filter({ hasText: fixture.draft.subject }).first().click();
          await inbox.locator('article').getByText(fixture.messages[1].subject, { exact: true }).waitFor();
          // getByLabel's text matcher includes a nested textarea's initial saved
          // text. Scope the unique control and verify its own label caption.
          const savedReply = editor.locator('textarea');
          await savedReply.waitFor();
          assert.equal(await savedReply.count(), 1, 'The saved draft must expose one reply body in its own review.');
          assert.equal(await savedReply.evaluate(element => element.closest('label')?.firstChild?.textContent?.trim()), 'Reply');
          assert.equal(await savedReply.inputValue(), fixture.draft.body);
          assert.equal(await editor.getByLabel('Subject', { exact: true }).inputValue(), fixture.draft.subject);
          assert.equal(await editor.getByRole('button', { name: 'Send this reply', exact: true }).isDisabled(), true);
          assert.equal(await editor.getByLabel('I checked the recipient and the complete reply.', { exact: true }).isChecked(), false);
          item.checks.push('Second synthetic source reopened its saved revision; content matched; Send remained disabled and was never clicked.');
          await capture('saved-draft', editor);
          assert.equal(report.page_errors.filter((row) => row.case === caseName).length, 0, 'Browser runtime error in fixture flow.');
          assert.equal(report.blocked_requests.filter((row) => row.case === caseName && row.method !== 'GET').length, 0, 'Unexpected forbidden action was attempted.');
          item.status = 'passed';
        } catch (error) {
          item.status = 'failed'; report.failures.push({ case: caseName, message: error.message });
          try { await page.screenshot({ path: path.join(output, `${caseName}-failure-QA-FIXTURE.png`), fullPage: true }); } catch {}
        } finally {
          await context.close(); saveReport();
        }
      }
    }
  } finally {
    await browser.close();
    report.completed_at = new Date().toISOString();
    report.status = report.failures.length ? 'failed' : 'passed'; saveReport();
    console.log(JSON.stringify({ status: report.status, cases: report.cases.map(({ name, status, screenshots }) => ({ name, status, screenshots: screenshots.length })), failures: report.failures, output, report: reportPath }, null, 2));
  }
  if (report.failures.length) process.exitCode = 1;
}

module.exports = { fixtureData, fixtureSnapshot, OWNER, LABEL, ZONE };
if (require.main === module) main().catch((error) => { console.error(error.message); process.exitCode = 1; });
