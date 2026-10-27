/* Opt-in isolated-site synthetic spoken-input qualification. No transcript/audio logs. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { installSyntheticMic } = require('./synthetic-mic.cjs');
const SITE = 'https://vox-test.66-175-235-169.nip.io';
const NOTICE = 'This reply may include details from your private saved conversation.';
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = text => crypto.createHash('sha256').update(text).digest('hex');

function options(argv) {
  if (argv[0] !== '--authorized-live-voice-qualification' || !/^[a-f0-9]{40}$/.test(argv[1] || '')) {
    throw new Error('Explicit root authorization flag and exact source SHA required');
  }
  const result = { sha: argv[1], profile: 'full-three-calls', maxCalls: 3,
    callLimitMs: 40000, maxTransportMs: 135000, stopThresholdUsd: 2,
    priorCallCount: 0, priorConnectedMs: 0, priorProviderUsd: 0, totalCallCeiling: 3 };
  if (argv.length === 2) return result;
  if (argv.length !== 10 || argv[2] !== '--profile' || argv[3] !== 'neutral-private-only' ||
      argv[4] !== '--prior-provider-usd' || argv[6] !== '--prior-connected-ms' ||
      argv[8] !== '--prior-call-count' || argv[9] !== '1') {
    throw new Error('Unknown qualification profile or missing prior usage');
  }
  const cost = validDecimal(argv[5]), connected = Number(argv[7]);
  if (cost === null || cost >= 2 || !Number.isSafeInteger(connected) || connected <= 0 || connected > 45000) {
    throw new Error('Prior root-verified usage exceeds continuation limits');
  }
  // Stop input early enough to leave a small native hang-up/settlement margin.
  return { ...result, profile: 'neutral-private-only', maxCalls: 2,
    maxTransportMs: 135000 - connected, stopThresholdUsd: 2 - cost,
    priorCallCount: 1, priorConnectedMs: connected, priorProviderUsd: cost };
}
function validDecimal(value) {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const result = Number(value);
  return Number.isFinite(result) && result >= 0 ? result : null;
}
function qaError(code) { const error = new Error('Qualification boundary failed'); error.qaCode = code; return error; }
function assert(condition, boundary) { if (!condition) throw qaError(boundary); }
function currentAdmission(admitted, serial, launches, completed) {
  return !!admitted && UUID.test(admitted.client_call_id || '') && launches.get(admitted.client_call_id) === serial && !completed.has(admitted.client_call_id);
}

function blockedWriteDiagnostic(method, pathname) {
  const fixed = new Set(['/api/v1/vox/calls', '/api/v1/workspace/timezone/initialize',
    '/api/v1/assistant/sessions', '/api/v1/auth/logout', '/api/v1/memory/privacy']);
  let template = fixed.has(pathname) ? pathname : 'unclassified';
  const call = /^\/api\/v1\/vox\/calls\/[^/]+\/(end|renew|messages|media)$/.exec(pathname);
  if (call) template = '/api/v1/vox/calls/:call/' + call[1];
  // Dynamic identifiers, query strings and request bodies never enter evidence.
  return { method: ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'].includes(method) ? method : 'OTHER',
    route_template: template, pathname_sha256: hash(pathname) };
}
function timezoneInitialization(payload, secret, browserTimezone) {
  return !!payload && typeof payload === 'object' && !Array.isArray(payload) &&
    Object.keys(payload).sort().join(',') === 'timezone,user_id,workspace_id' &&
    payload.user_id === secret.user_id && payload.workspace_id === secret.workspace_id &&
    typeof browserTimezone === 'string' && payload.timezone === browserTimezone;
}
function mediaReady(value) {
  return value.liveSyntheticMicrophone === true && value.liveRemoteAudioTracks > 0 &&
    value.attemptedRemoteAudioTracks > 0 && value.sentBytes > 0;
}
function responseAudioReady(value, baseline) {
  return value.receivedBytes > baseline.receivedBytes && value.receivedEnergy > baseline.receivedEnergy &&
    value.playingRemoteAudioTracks > 0 && value.playReady > 0;
}
function startWatchdog({ guard, budget, stop, fail, failed }, schedule = setInterval) {
  let budgetBusy = false;
  const retire = async error => {
    if (failed()) return;
    fail(error);
    await stop().catch(() => {});
  };
  return schedule(async () => {
    if (failed()) return;
    // A slow usage request must not hold the wall-clock cutoff. Every tick
    // checks the deadline even while an earlier tick is awaiting that request.
    try { await guard(); }
    catch (error) { await retire(error); return; }
    if (budgetBusy || failed()) return;
    budgetBusy = true;
    try { await budget(); }
    catch (error) { await retire(error); }
    finally { budgetBusy = false; }
  }, 750);
}

async function run(settings, secret) {
  const { chromium } = require('playwright');
  const evidence = {
    schema: 'navox-live-synthetic-voice-qualification.v1', sha: settings.sha,
    qualification_profile: settings.profile, pass_scope: 'selected-profile-only',
    scope: 'isolated-test', physical_phone_acceptance: false,
    human_microphone_acceptance: false, input: 'offline synthetic speech via actual LiveKit capture track',
    output: 'real provider audio via browser playback path',
    fixture_native_topic_or_archive_injection: false, typed_vox_requests: 0,
    configured_policy_changed: false, max_calls: settings.maxCalls,
    prior_call_count: settings.priorCallCount, total_call_ceiling: settings.totalCallCeiling,
    prior_known_provider_estimated_cost_usd: String(settings.priorProviderUsd),
    prior_transport_connected_ms: settings.priorConnectedMs,
    per_call_input_stop_ms: settings.callLimitMs, target_call_limit_ms: 45000,
    max_transport_usage_ms: settings.maxTransportMs,
    known_provider_cost_stop_threshold_usd: settings.stopThresholdUsd,
    transport_usd_known: secret.transport_pricing_known === true,
    transport_estimated_cost_usd: null, combined_estimated_cost_usd: null,
    transcript_or_provider_audio_saved: false, calls: [], checkpoints: [],
    blocked_external_requests: 0, blocked_unexpected_writes: 0,
    blocked_write_diagnostics: [],
    allowed_timezone_initialization: { route_template: '/api/v1/workspace/timezone/initialize', count: 0 },
    cross_call_recall: { status: 'not_run', live_qualified: false },
    host_notice_before_history: { status: 'not_run', live_qualified: false },
    private_withheld_from_voice: { status: 'not_run', live_qualified: false },
    first_failure_boundary: null, failure_class: null, passed: false,
  };
  let browser, context, page, lastCall = null, currentStart = 0, browserTimezone = null;
  let boundary = 'private-stdin', fatal = null, stopping = null, sequence = 0;
  let watchdog, expectedStart = false, privateReceipt = null, privateSuggestionId = null, launchSerial = 0;
  const errors = [], warnings = [], endSequences = new Map(), callIds = new Set();
  const accountKey = secret.workspace_id + ':' + secret.user_id;
  const fixtureMetadata = JSON.parse(fs.readFileSync(path.join(__dirname, 'audio-metadata.json'), 'utf8'));
  const files = new Map(fixtureMetadata.fixtures.map(item => [item.id, item]));
  const nodeAdmissionAttempts = new Set();
  const admissionLaunch = new Map();
  const check = (value, name) => { assert(value, name); evidence.checkpoints.push(name); };
  const blockedWrite = request => {
    evidence.blocked_unexpected_writes++;
    if (evidence.blocked_write_diagnostics.length < 8)
      evidence.blocked_write_diagnostics.push(blockedWriteDiagnostic(request.method(), new URL(request.url()).pathname));
  };
  const networkAllowed = url => {
    if (url.origin === SITE) return true;
    const base = secret.livekit_host;
    return ['https:', 'wss:'].includes(url.protocol) &&
      (url.hostname === base || (base.endsWith('.livekit.cloud') && url.hostname.endsWith('.livekit.cloud')));
  };
  async function owned(method, resource, body) {
    if (!context) throw new Error('No browser API context');
    const response = await context.request.fetch(SITE + '/api/v1' + resource, {
      method, headers: { Origin: SITE, 'X-NavoX-Account': accountKey },
      ...(body === undefined ? {} : { data: body }), timeout: 10000,
    });
    if (!response.ok()) throw qaError('ownedHTTP' + response.status());
    return response.status() === 204 ? null : response.json();
  }
  async function stopCurrent() {
    if (stopping) return stopping;
    stopping = (async () => {
      if (!context) return;
      try {
        const button = page?.getByRole('button', { name: 'Hang up', exact: true });
        if (button && await button.count() && await button.isVisible()) await button.click({ timeout: 3000 });
      } catch {}
      const before = await owned('GET', '/vox/calls/context');
      if (before.call?.status === 'reserved') {
        assert(UUID.test(before.call.client_call_id), 'invalid-cleanup-call-id');
        await owned('POST', '/vox/calls/' + before.call.client_call_id + '/end', { generation: before.call.generation });
      }
      const after = await owned('GET', '/vox/calls/context');
      assert(!after.call || after.call.status !== 'reserved', 'native-call-cleanup-unconfirmed');
      lastCall = null;
      currentStart = 0;
    })().finally(() => { stopping = null; });
    return stopping;
  }
  async function budget() {
    const value = await owned('GET', '/vox/usage/status');
    const known = validDecimal(value.daily?.known_estimated_cost_usd);
    const providerKnown = known === null ? null : Math.max(0, known - (secret.transport_pricing_known ? Number(secret.transport_rate) * Number(value.daily.connected_ms) / 60000 : 0));
    if (providerKnown === null || !Number.isFinite(providerKnown) || value.policy_revision !== secret.policy_revision || value.blocked || providerKnown >= settings.stopThresholdUsd || Number(value.daily.connected_ms) + Number(value.daily.reserved_voice_ms) >= settings.maxTransportMs - 5000) {
      throw qaError('budget-or-policy-stop');
    }
    value.known_provider_estimate = providerKnown;
    return value;
  }
  async function guard() {
    if (fatal) throw fatal;
    if (currentStart && Date.now() - currentStart >= settings.callLimitMs) throw qaError('call-wall-clock-stop');
    if (Date.now() - startedAt > 240000) throw qaError('run-wall-clock-stop');
  }
  async function stats() {
    return page.evaluate(async () => {
      const state = window.__qaVoice;
      let receivedBytes = 0, receivedEnergy = 0, sentBytes = 0, sentEnergy = 0;
      for (const peer of state?.peers || []) {
        if (peer.connectionState === 'closed') continue;
        for (const row of (await peer.getStats()).values()) {
          const audio = row.kind === 'audio' || row.mediaType === 'audio';
          if (row.type === 'inbound-rtp' && audio) { receivedBytes += row.bytesReceived || 0; receivedEnergy += row.totalAudioEnergy || 0; }
          if (row.type === 'outbound-rtp' && audio) sentBytes += row.bytesSent || 0;
          if (row.type === 'media-source' && audio) sentEnergy += row.totalAudioEnergy || 0;
        }
      }
      return { receivedBytes, receivedEnergy, sentBytes, sentEnergy,
        liveSyntheticMicrophone: state?.current?.track?.readyState === 'live',
        liveRemoteAudioTracks: [...(state?.remoteAudioTracks || [])].filter(track => track.readyState === 'live').length,
        attemptedRemoteAudioTracks: [...(state?.attemptedAudioElements || [])].filter(element =>
          !element.muted && [...(element.srcObject?.getAudioTracks?.() || [])].some(track =>
            state.remoteAudioTracks.has(track) && track.readyState === 'live')).length,
        playingRemoteAudioTracks: [...(state?.playingAudioElements || [])].filter(element =>
          !element.muted && !element.paused && [...(element.srcObject?.getAudioTracks?.() || [])].some(track =>
            state.remoteAudioTracks.has(track) && track.readyState === 'live')).length,
        playReady: state?.playbackResolved || 0, fixturePlays: state?.fixturePlays || 0,
        injectedSamples: state?.injectedSamples || 0, dataFrames: state?.dataFrames || 0,
        contextsCreated: state?.contextsCreated || 0, contextsClosed: state?.contextsClosed || 0 };
    });
  }
  async function settleCall(call, item) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const report = await owned('GET', '/vox/usage/calls/' + call.id);
      const expectedUnknown = secret.transport_pricing_known ? 0 : 1;
      // The DB enforces one transport stream per call. With its price absent and
      // connected_ms>0 it necessarily accounts for one unknown component. Any
      // additional unknown is a provider/reservation problem and blocks progress.
      if (report.metering_started && report.totals.connected_ms > 0 && report.totals.unknown_cost_components === expectedUnknown && report.totals.reserved_tokens === 0 && report.totals.reserved_voice_ms === 0 && report.totals.uncertain_voice_ms === 0) {
        const cost = validDecimal(report.totals.known_estimated_cost_usd);
        assert(cost !== null, 'settled-provider-cost-unknown');
        const transportKnown = secret.transport_pricing_known ? Number(secret.transport_rate) * Number(report.totals.connected_ms) / 60000 : 0;
        item.known_provider_estimated_cost_usd = String(Math.max(0, cost - transportKnown));
        item.transport_estimated_cost_usd = secret.transport_pricing_known ? String(transportKnown) : null;
        item.combined_estimated_cost_usd = secret.transport_pricing_known ? String(cost) : null;
        item.transport_cost_basis = secret.transport_pricing_known ? 'estimate' : 'unknown';
        item.connected_ms = report.totals.connected_ms;
        item.input_audio_or_transcription_metered = report.totals.tokens > 0;
        item.connected_ms_metered = report.totals.connected_ms > 0;
        item.metering_complete = true;
        await budget();
        return;
      }
      await pause(500);
    }
    throw qaError('metering-remains-uncertain');
  }
  async function startCall(index) {
    await budget();
    const remaining = settings.maxTransportMs - Number((await budget()).daily.connected_ms);
    assert(remaining >= 45000, 'insufficient-remaining-transport-window');
    boundary = 'call' + index + ':start';
    // A late JSON body from an earlier launch may not restore its old identity
    // or move the current call's watchdog. The response handler also fences it.
    launchSerial++;
    lastCall = null;
    currentStart = 0;
    expectedStart = false;
    await page.goto(SITE + '/', { waitUntil: 'domcontentloaded' });
    await page.locator('[data-vox-launcher]').first().waitFor({ timeout: 15000 });
    expectedStart = true;
    await page.locator('[data-vox-launcher]').first().click({ timeout: 10000 });
    const textbox = page.getByRole('textbox', { name: 'Message NavoX', exact: true });
    await textbox.waitFor({ timeout: 25000 });
    const ready = Date.now() + 20000;
    while (!(await textbox.isEnabled()) && Date.now() < ready) { await guard(); await pause(200); }
    check(await textbox.isEnabled(), 'call' + index + ':media-ready');
    if (!lastCall) lastCall = (await owned('GET', '/vox/calls/context')).call;
    assert(lastCall?.status === 'reserved' && UUID.test(lastCall.id) && UUID.test(lastCall.client_call_id), 'call-admission-unconfirmed');
    if (!currentStart) currentStart = Number.isFinite(Date.parse(lastCall.created_at)) ? Date.parse(lastCall.created_at) : Date.now();
    expectedStart = false;
    assert(!callIds.has(lastCall.client_call_id), 'call-identity-reused');
    callIds.add(lastCall.client_call_id);
    const item = { number: index, call_hash: hash(lastCall.client_call_id), native_call_hash: hash(lastCall.id), source_sha: settings.sha, turns: [] };
    evidence.calls.push(item);
    const privacy = await owned('GET', '/vox/calls/' + lastCall.client_call_id + '/privacy');
    check(privacy.mode === 'saved-history' && privacy.retention_days === 30, 'call' + index + ':server-retention-proof');
    // The textbox alone used to become enabled before the native worker warmed.
    // Product transport admits only the leased agent's output track. Require its
    // actual current remote attachment and play completion, plus live input RTP.
    let media;
    const mediaDeadline = Date.now() + 15000;
    do {
      await guard();
      media = await stats();
      if (mediaReady(media)) break;
      await pause(100);
    } while (Date.now() < mediaDeadline);
    check(mediaReady(media), 'call' + index + ':actual-owned-output-and-input-ready');
    item.actual_owned_output_and_input_ready = true;
    return { call: lastCall, item };
  }
  async function speak(id, record) {
    boundary = 'call' + record.item.number + ':' + id;
    await guard();
    const resume = page.getByRole('button', { name: 'Resume conversation', exact: true });
    if (await resume.count() && await resume.isVisible()) await resume.click({ timeout: 2000 });
    const fixture = files.get(id);
    assert(fixture && /^[a-z-]+$/.test(id), 'unknown-speech-fixture');
    const bytes = fs.readFileSync(path.join(__dirname, 'fixtures', id + '.wav'));
    assert(hash(bytes) === fixture.sha256, 'speech-fixture-hash-mismatch');
    const before = await owned('GET', '/vox/calls/' + record.call.client_call_id + '/transcript?after=0');
    const previous = new Set((before.turns || []).map(turn => turn.sequence ?? JSON.stringify(turn)));
    const baseline = await stats();
    check(mediaReady(baseline), 'call' + record.item.number + ':' + id + ':current-owned-media-ready');
    await page.evaluate(encoded => window.__qaSpeakFixture(encoded), bytes.toString('base64'));
    let completed, completedCount = 0, completedRevision = null;
    while (!completed) {
      await guard();
      const transcript = await owned('GET', '/vox/calls/' + record.call.client_call_id + '/transcript?after=0');
      const fresh = transcript.turns?.filter(turn => turn.kind === 'dialogue' && !previous.has(turn.sequence ?? JSON.stringify(turn)) && typeof turn.question === 'string' && turn.question.trim() && typeof turn.answer === 'string' && turn.answer.trim()) || [];
      completed = fresh[0]; completedCount = fresh.length; completedRevision = transcript.revision;
      if (!completed) await pause(500);
    }
    const audio = await stats();
    assert(audio.sentBytes > baseline.sentBytes, 'spoken-input-did-not-reach-rtc');
    assert(responseAudioReady(audio, baseline), 'browser-provider-playback-unconfirmed');
    const item = { fixture: id, fixture_sha256: fixture.sha256,
      completed_spoken_turn: true, transcribed_question_sha256: hash(completed.question),
      new_completed_dialogue_count: completedCount,
      transcript_revision: Number.isSafeInteger(completedRevision) ? completedRevision : null,
      answer_sha256: hash(completed.answer), input_rtp_increased: true,
      received_audio_energy_increased: true, remote_audio_bytes_increased: true,
      browser_play_promise_resolved: true, injected_duration_ms: fixture.duration_ms };
    record.item.turns.push(item);
    return { question: completed.question, answer: completed.answer, item };
  }
  async function closeCall(record) {
    boundary = 'call' + record.item.number + ':hangup';
    await stopCurrent();
    record.item.elapsed_ms = Date.now() - record.started;
    record.item.ended = true;
    const retired = await context.request.get(SITE + '/api/v1/vox/calls/' + record.call.client_call_id + '/transcript?after=0', { headers: { Origin: SITE, 'X-NavoX-Account': accountKey } });
    check(retired.status() === 410, 'call' + record.item.number + ':transient-transcript-retired');
    await settleCall(record.call, record.item);
  }
  const startedAt = Date.now();
  try {
    assert(UUID.test(secret.user_id) && UUID.test(secret.workspace_id) && Array.isArray(secret.cookies), 'private-stdin-shape');
    assert(typeof secret.livekit_host === 'string' && /^[a-z0-9.-]+$/i.test(secret.livekit_host), 'livekit-host-invalid');
    browser = await chromium.launch({ headless: true, args: [
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
      '--use-file-for-fake-audio-capture=/test/m4-silence.wav',
      '--autoplay-policy=no-user-gesture-required',
    ] });
    context = await browser.newContext({ viewport: { width: 390, height: 844 }, permissions: ['microphone'], serviceWorkers: 'block' });
    await context.addCookies(secret.cookies);
    await context.addInitScript(installSyntheticMic);
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (!networkAllowed(url)) { evidence.blocked_external_requests++; await route.abort(); return; }
      if (url.origin === SITE && url.pathname.startsWith('/api/v1/') && !['GET', 'HEAD'].includes(request.method())) {
        if (/\/vox\/calls\/[^/]+\/messages$/.test(url.pathname)) {
          evidence.typed_vox_requests++; await route.abort(); return;
        }
        if (url.pathname === '/api/v1/vox/calls' && request.method() === 'POST') {
          let payload; try { payload = request.postDataJSON(); } catch {}
          if (!payload || !UUID.test(payload.client_call_id)) { await route.abort(); return; }
          if (!nodeAdmissionAttempts.has(payload.client_call_id)) {
            if (!expectedStart || nodeAdmissionAttempts.size >= settings.maxCalls) {
              blockedWrite(request); await route.abort(); return;
            }
            nodeAdmissionAttempts.add(payload.client_call_id);
            admissionLaunch.set(payload.client_call_id, launchSerial);
          }
        } else if (url.pathname === '/api/v1/workspace/timezone/initialize' && request.method() === 'POST') {
          let payload; try { payload = request.postDataJSON(); } catch {}
          if (!timezoneInitialization(payload, secret, browserTimezone)) { blockedWrite(request); await route.abort(); return; }
          evidence.allowed_timezone_initialization.count++;
        } else if (!url.pathname.startsWith('/api/v1/vox/calls/') && url.pathname !== '/api/v1/memory/history/page' && url.pathname !== '/api/v1/memory/learning/' + privateSuggestionId + '/confirm') {
          blockedWrite(request); await route.abort(); return;
        }
      }
      await route.continue();
    });
    if (typeof context.routeWebSocket === 'function') await context.routeWebSocket('**', route => {
      if (networkAllowed(new URL(route.url()))) route.connectToServer();
      else { evidence.blocked_external_requests++; route.close(); }
    });
    page = await context.newPage();
    browserTimezone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    new Intl.DateTimeFormat('en', { timeZone: browserTimezone }).format();
    page.on('pageerror', error => errors.push(error?.name || 'Error'));
    page.on('console', event => { if (event.type() === 'error' || event.type() === 'warning') warnings.push(event.type()); });
    page.on('request', request => {
      const current = ++sequence;
      const url = new URL(request.url());
      if (request.method() === 'POST' && /\/vox\/calls\/[^/]+\/end$/.test(url.pathname)) {
        endSequences.set(url.pathname.split('/').at(-2), current);
      }
      if (request.method() === 'POST' && url.pathname === '/api/v1/memory/learning/' + privateSuggestionId + '/confirm') evidence.private_confirmation_request_sequence = current;
    });
    page.on('response', async response => {
      try {
        const request = response.request(), url = new URL(response.url());
        if (request.method() === 'POST' && url.pathname === '/api/v1/vox/calls' && response.status() === 201) {
          const admitted = await response.json();
          if (currentAdmission(admitted, launchSerial, admissionLaunch, callIds)) {
            lastCall = admitted;
            currentStart = Number.isFinite(Date.parse(admitted.created_at)) ? Date.parse(admitted.created_at) : Date.now();
          }
        }
        if (request.method() === 'POST' && url.pathname === '/api/v1/memory/learning/' + privateSuggestionId + '/confirm' && [200, 201].includes(response.status())) {
          privateReceipt = await response.json(); evidence.private_confirmation_status = response.status();
        }
      } catch {}
    });
    const me = await owned('GET', '/auth/me');
    check(me.id === secret.user_id && me.workspace.id === secret.workspace_id, 'exact-browser-account');
    const privacy = await owned('GET', '/memory/privacy');
    check(privacy.history_enabled && privacy.retention_days === 30 && !privacy.off_the_record, 'explicit-30day-disclosure-ready');
    const learning = await owned('GET', '/memory/learning');
    // Native wiring is independent of the background-only feature flag. This
    // current account must additionally have retained consent and qualified use.
    check(learning.enabled === true && learning.configured === true && learning.status_reason === 'ready' && learning.generation === privacy.generation, 'native-semantic-learning-ready-for-current-privacy');
    const initialBudget = await budget();
    check(initialBudget.remaining_voice_ms >= settings.maxCalls * settings.callLimitMs && initialBudget.remaining_tokens >= 40000, 'existing-allowance-sufficient');
    watchdog = startWatchdog({ guard, budget, stop: stopCurrent,
      failed: () => fatal !== null, fail: error => { fatal = error; } });

    const one = await startCall(1); one.started = currentStart;
    const neutral = await speak('neutral-source', one);
    check(/brief|short|concise/i.test(neutral.question) && /Python/i.test(neutral.question), 'real-asr-neutral-source');
    const factsBefore = await owned('GET', '/memory/facts');
    check(factsBefore.length === 0, 'multi-statement-source-not-whole-turn-literal');
    const remembered = await speak('learn-earlier', one);
    check(/evaporation|condensation/i.test(remembered.question), 'real-asr-later-history-source');
    const facts = await owned('GET', '/memory/facts');
    const replyFact = facts.find(fact => !fact.sensitive && /brief|short|concise/i.test(fact.content) && /repl|answer|respons/i.test(fact.content));
    const languageFact = facts.find(fact => !fact.sensitive && /Python/i.test(fact.content));
    check(replyFact && languageFact && replyFact.source_kind === 'vox' && languageFact.source_kind === 'vox' && replyFact.source_conversation_id === languageFact.source_conversation_id, 'two-source-linked-semantic-facts');
    const neutralSource = replyFact.source_conversation_id;
    await closeCall(one);

    const two = await startCall(2); two.started = currentStart;
    const privateTurn = await speak('private-source', two);
    check(/cobalt/i.test(privateTurn.question) && /lantern/i.test(privateTurn.question) && /arch/i.test(privateTurn.question), 'real-asr-private-source');
    await speak('private-review', two);
    const suggestions = await owned('GET', '/memory/learning');
    const suggestion = suggestions.pending?.find(item => item.source_kind === 'vox' && item.sensitive === true && privateTurn.question.includes(item.content) && /cobalt/i.test(item.content) && /lantern/i.test(item.content) && /arch/i.test(item.content));
    check(suggestion && UUID.test(suggestion.id) && suggestion.source_conversation_id !== neutralSource, 'real-model-private-suggestion-queued');
    privateSuggestionId = suggestion.id;
    const beforePrivateSave = await owned('GET', '/memory/facts');
    check(!beforePrivateSave.some(item => /cobalt|lantern/i.test(item.content)), 'unknown-private-detail-not-automatically-saved');
    const nativeReview = page.getByRole('region', { name: 'Vox memory save review', exact: true });
    evidence.existing_candidate_topic_observed = !!(await nativeReview.count() && await nativeReview.isVisible());
    if (evidence.existing_candidate_topic_observed) evidence.existing_candidate_data_frames_observed = (await stats()).dataFrames > 0;
    // learn_completed_memory intentionally returns counts and a fixed Settings
    // instruction, not a fabricated version of the old candidate RTC topic.
    const dialog = page.getByRole('dialog', { name: 'NavoX Vox', exact: true });
    const link = dialog.locator('a[href="/settings#memory-privacy"]').first();
    if (!(await link.isVisible())) await dialog.locator('summary[aria-label="Vox settings"]').click();
    await link.click();
    await page.waitForURL(url => url.pathname === '/settings' && url.hash === '#memory-privacy', { timeout: 8000 });
    const panel = page.getByRole('region', { name: 'Private memory suggestions', exact: true });
    await panel.getByRole('button', { name: 'Check learning and suggestions', exact: true }).click();
    const review = panel.getByRole('article', { name: 'Private suggestion: ' + suggestion.label, exact: true });
    await review.waitFor({ timeout: 8000 });
    check(!(await review.getByRole('checkbox').isChecked()), 'private-learning-agreement-initially-unchecked');
    check(!(await review.getByRole('button', { name: 'Save this suggestion', exact: true }).isEnabled()), 'private-learning-save-disabled-before-agreement');
    await review.getByRole('checkbox').check();
    await review.getByRole('button', { name: 'Save this suggestion', exact: true }).click();
    const receiptDeadline = Date.now() + 5000;
    while (!privateReceipt && Date.now() < receiptDeadline) { await guard(); await pause(100); }
    check(privateReceipt && privateReceipt.id === suggestion.id && privateReceipt.source_kind === 'vox' && privateReceipt.sensitive === true && privateReceipt.label === suggestion.label && privateReceipt.content === suggestion.content && privateReceipt.source_conversation_id === suggestion.source_conversation_id, 'exact-real-private-receipt');
    const endedBefore = endSequences.get(two.call.client_call_id);
    check(endedBefore && endedBefore < evidence.private_confirmation_request_sequence, 'native-hangup-before-private-confirmation');
    evidence.private_native_end_sequence = endedBefore;
    await closeCall(two);

    if (settings.profile === 'full-three-calls') {
      const three = await startCall(3); three.started = currentStart;
      const recalled = await speak('recall-and-withhold', three);
      check(/brief|short|concise/i.test(recalled.answer) && /Python/i.test(recalled.answer), 'cross-call-neutral-recall');
      evidence.cross_call_recall = { status: 'passed', live_qualified: true };
      check(recalled.answer.startsWith(NOTICE) && /water cycle|evaporation|condensation/i.test(recalled.answer), 'host-privacy-notice-before-historical-answer');
      evidence.host_notice_before_history = { status: 'passed', live_qualified: true };
      check(!/\bcobalt\b|\blantern\b|\barch\b/i.test(recalled.answer), 'private-source-withheld-from-voice');
      evidence.private_withheld_from_voice = { status: 'passed', live_qualified: true };
      await closeCall(three);
    }
    const finalBudget = await budget();
    evidence.final_known_provider_estimated_cost_usd = String(finalBudget.known_provider_estimate);
    evidence.final_transport_connected_ms = finalBudget.daily.connected_ms;
    evidence.final_unknown_cost_components = finalBudget.daily.unknown_cost_components;
    evidence.total_known_provider_estimated_cost_usd = String(settings.priorProviderUsd + finalBudget.known_provider_estimate);
    evidence.total_transport_connected_ms = settings.priorConnectedMs + Number(finalBudget.daily.connected_ms);
    evidence.total_native_calls = settings.priorCallCount + evidence.calls.length;
    check(evidence.calls.every(item => item.metering_complete) && finalBudget.known_provider_estimate <= settings.stopThresholdUsd, 'all-invoked-provider-usage-known');
    check(finalBudget.daily.connected_ms <= settings.maxTransportMs && evidence.calls.every(item => item.connected_ms <= 45000), 'bounded-transport-time-with-explicit-usd-status');
    check(evidence.calls.length === settings.maxCalls && evidence.total_native_calls <= settings.totalCallCeiling && evidence.total_transport_connected_ms <= 135000 && Number(evidence.total_known_provider_estimated_cost_usd) <= 2 && evidence.typed_vox_requests === 0 && evidence.blocked_unexpected_writes === 0 && evidence.blocked_external_requests === 0 && errors.length === 0, 'bounded-no-typed-no-unexpected-traffic');
    boundary = 'completed';
    evidence.passed = true;
  } catch (error) {
    evidence.first_failure_boundary = fatal ? 'watchdog:' + boundary : boundary;
    evidence.failure_class = error?.name || 'Error';
    const safeCode = (fatal || error)?.qaCode;
    if (typeof safeCode === 'string' && /^[a-zA-Z0-9:-]+$/.test(safeCode)) evidence.failure_code = safeCode;
    const code = /HTTP(\d{3})/.exec(safeCode || '');
    if (code) evidence.failure_http_status = Number(code[1]);
  } finally {
    clearInterval(watchdog);
    if (context) {
      try { await stopCurrent(); evidence.calls_stopped = true; }
      catch { evidence.calls_stopped = false; evidence.passed = false; }
    }
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    evidence.page_error_count = errors.length;
    evidence.console_warning_or_error_count = warnings.length;
    evidence.duration_ms = Date.now() - startedAt;
    evidence.native_call_admission_attempts = nodeAdmissionAttempts.size;
    // Parent retains the same browser session in RAM and performs independent
    // erasure/logout even if this process or Chromium fails. No session is saved.
    return evidence;
  }
}

if (require.main === module) {
  if (process.argv[2] === '--self-test') {
    const assert = require('node:assert/strict');
    assert.throws(() => options([]));
    assert.throws(() => options(['--authorized-live-voice-qualification', 'not-a-sha']));
    assert.equal(options(['--authorized-live-voice-qualification', 'a'.repeat(40)]).maxCalls, 3);
    const continuation = ['--authorized-live-voice-qualification', 'a'.repeat(40), '--profile', 'neutral-private-only', '--prior-provider-usd', '0.125', '--prior-connected-ms', '27800', '--prior-call-count', '1'];
    const limited = options(continuation);
    assert.equal(limited.maxCalls, 2);
    assert.equal(limited.priorCallCount + limited.maxCalls, limited.totalCallCeiling);
    assert.equal(limited.maxTransportMs + limited.priorConnectedMs, 135000);
    assert.equal(limited.stopThresholdUsd + limited.priorProviderUsd, 2);
    assert.throws(() => options(continuation.slice(0, -2)));
    for (const [index, invalid] of [[3, 'other'], [5, '2'], [5, 'NaN'], [5, '-1'], [7, '0'], [7, '45001'], [7, '1.5'], [9, '0'], [9, '2']]) {
      const changed = [...continuation]; changed[index] = invalid;
      assert.throws(() => options(changed));
    }
    assert.equal(validDecimal('0E-10'), 0);
    assert.equal(validDecimal('unknown'), null);
    assert.equal(validDecimal('-1'), null);
    const identity = { user_id: 'test-user', workspace_id: 'test-workspace' };
    const timezone = { ...identity, timezone: 'UTC' };
    assert.equal(timezoneInitialization(timezone, identity, 'UTC'), true);
    assert.equal(timezoneInitialization({ ...timezone, extra: true }, identity, 'UTC'), false);
    assert.equal(timezoneInitialization({ ...timezone, user_id: 'other' }, identity, 'UTC'), false);
    assert.equal(timezoneInitialization(timezone, identity, 'Europe/London'), false);
    assert.equal(timezoneInitialization(timezone, identity, null), false);
    assert.equal(blockedWriteDiagnostic('POST', '/api/v1/assistant/sessions').route_template, '/api/v1/assistant/sessions');
    const hiddenPath = '/api/v1/private/arbitrary-value';
    const hidden = blockedWriteDiagnostic('POST', hiddenPath);
    assert.equal(hidden.route_template, 'unclassified');
    assert.equal(hidden.pathname_sha256, hash(hiddenPath));
    assert.equal(JSON.stringify(hidden).includes('arbitrary-value'), false);
    assert.equal(blockedWriteDiagnostic('DELETE', '/api/v1/vox/calls/private-id/end').route_template, '/api/v1/vox/calls/:call/end');
    const media = { liveSyntheticMicrophone: true, liveRemoteAudioTracks: 1, attemptedRemoteAudioTracks: 1, sentBytes: 1 };
    assert.equal(mediaReady(media), true);
    for (const field of Object.keys(media)) assert.equal(mediaReady({ ...media, [field]: 0 }), false);
    const oldId = '11111111-1111-4111-8111-111111111111', newId = '22222222-2222-4222-8222-222222222222';
    const launches = new Map([[oldId, 1], [newId, 2]]), completed = new Set();
    assert.equal(currentAdmission({ client_call_id: oldId }, 2, launches, completed), false);
    assert.equal(currentAdmission({ client_call_id: newId }, 2, launches, completed), true);
    completed.add(newId);
    assert.equal(currentAdmission({ client_call_id: newId }, 2, launches, completed), false);
    assert.equal(currentAdmission({ client_call_id: 'malformed' }, 2, launches, completed), false);
    (async () => {
      // The first usage request is still pending when the next clock tick
      // reaches the cutoff. Retirement must start without waiting for its HTTP
      // timeout; a late budget error must not replace that first failure.
      let tick, now = 0, failed = null, stopped = 0, budgetReads = 0, rejectBudget;
      const pendingBudget = new Promise((resolve, reject) => { rejectBudget = reject; });
      const timer = startWatchdog({
        guard: async () => { if (now >= 40000) throw qaError('call-wall-clock-stop'); },
        budget: async () => { budgetReads++; return pendingBudget; },
        stop: async () => { stopped++; },
        failed: () => failed !== null, fail: error => { failed = error; },
      }, (callback, delay) => { tick = callback; assert.equal(delay, 750); return 'fake-timer'; });
      assert.equal(timer, 'fake-timer');
      const reading = tick();
      await Promise.resolve();
      assert.equal(budgetReads, 1);
      await tick();
      assert.equal(budgetReads, 1);
      now = 40000;
      await tick();
      assert.equal(stopped, 1);
      assert.equal(failed.qaCode, 'call-wall-clock-stop');
      rejectBudget(qaError('ownedHTTP503'));
      await reading;
      await tick();
      assert.equal(stopped, 1);
      assert.equal(budgetReads, 1);
      assert.equal(failed.qaCode, 'call-wall-clock-stop');
      // A published native AudioSource has no frames until the first reply.
      // The real play wrapper must permit input while its promise is pending,
      // then require actual resolved playback plus received bytes/energy.
      const vm = require('node:vm');
      let release;
      const track = { kind: 'audio', readyState: 'live' };
      class Peer {
        constructor() { this.listeners = new Map(); }
        addEventListener(type, listener) {
          if (!this.listeners.has(type)) this.listeners.set(type, []);
          this.listeners.get(type).push(listener);
        }
        createDataChannel() { return { addEventListener() {} }; }
        emitTrack() { for (const listener of this.listeners.get('track') || []) listener({ track }); }
      }
      class Audio {
        constructor() { this.tagName = 'AUDIO'; this.muted = false; this.paused = true; this.srcObject = { getAudioTracks: () => [track] }; }
        play() { return new Promise(resolve => { release = () => { this.paused = false; resolve(); }; }); }
      }
      const scope = { window: { RTCPeerConnection: Peer }, navigator: { mediaDevices: { getUserMedia() {} } }, HTMLMediaElement: Audio };
      vm.runInNewContext('(' + installSyntheticMic.toString() + ')()', scope);
      const peer = new scope.window.RTCPeerConnection();
      peer.emitTrack();
      const audio = new Audio(), played = audio.play(), state = scope.window.__qaVoice;
      const snapshot = () => ({
        liveSyntheticMicrophone: true, liveRemoteAudioTracks: state.remoteAudioTracks.size,
        attemptedRemoteAudioTracks: state.attemptedAudioElements.size, sentBytes: 1,
        playingRemoteAudioTracks: [...state.playingAudioElements].filter(element => !element.paused).length,
        playReady: state.playbackResolved, receivedBytes: 0, receivedEnergy: 0,
      });
      const baseline = { receivedBytes: 0, receivedEnergy: 0 };
      assert.equal(mediaReady(snapshot()), true);
      assert.equal(state.playbackResolved, 0);
      assert.equal(responseAudioReady(snapshot(), baseline), false);
      release(); await played;
      assert.equal(responseAudioReady(snapshot(), baseline), false);
      assert.equal(responseAudioReady({ ...snapshot(), receivedBytes: 1, receivedEnergy: 0.1 }, baseline), true);
      console.log(JSON.stringify({ offline_self_test: 'pass', deadline_during_slow_usage_regression: 'pass', silent_remote_pending_play_regression: 'pass', provider_calls: 0 }));
    })().catch(error => { console.error(error?.name || 'Error'); process.exitCode = 1; });
  } else {
    let selected;
    try { selected = options(process.argv.slice(2)); }
    catch { console.error(JSON.stringify({ ok: false, boundary: 'explicit-authorization-required' })); process.exit(2); }
    let payload = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => { payload += chunk; if (payload.length > 20000) process.exit(2); });
    process.stdin.on('end', async () => {
      try {
        const secret = JSON.parse(payload); payload = '';
        const result = await run(selected, secret);
        console.log(JSON.stringify(result));
        process.exitCode = result.passed ? 0 : 1;
      } catch (error) {
        console.log(JSON.stringify({ ok: false, first_failure_boundary: 'harness-startup', failure_class: error?.name || 'Error', transcript_or_provider_audio_saved: false }));
        process.exitCode = 1;
      }
    });
  }
}
module.exports = { options, validDecimal, currentAdmission, blockedWriteDiagnostic, timezoneInitialization, mediaReady, responseAudioReady, startWatchdog, run };
