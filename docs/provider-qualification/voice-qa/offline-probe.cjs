/* Verify synthetic microphone lifecycle locally; no NavoX/provider surface. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const { chromium } = require('/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const { installSyntheticMic } = require('./synthetic-mic.cjs');
function wav(samples, audible) {
  const result = Buffer.alloc(44 + samples * 2);
  result.write('RIFF', 0); result.writeUInt32LE(result.length - 8, 4); result.write('WAVEfmt ', 8);
  result.writeUInt32LE(16, 16); result.writeUInt16LE(1, 20); result.writeUInt16LE(1, 22);
  result.writeUInt32LE(16000, 24); result.writeUInt32LE(32000, 28); result.writeUInt16LE(2, 32); result.writeUInt16LE(16, 34);
  result.write('data', 36); result.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) result.writeInt16LE(audible ? Math.round(6000 * Math.sin(i * 2 * Math.PI * 440 / 16000)) : 0, 44 + i * 2);
  return result;
}
(async () => {
  let server, browser;
  const file = '/tmp/navox-offline-voice-silence-' + process.pid + '.wav';
  const result = { scope: 'offline-synthetic-input-only', provider_calls: 0, native_app_calls: 0, typed_input: false, physical_microphone: false, passed: false };
  try {
    fs.writeFileSync(file, wav(16000, false));
    server = http.createServer((req, res) => { res.writeHead(200, { 'content-type': 'text/html' }); res.end('<!doctype html><title>Offline synthetic capture</title>'); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ executablePath: '/workspace/scratch/6240f0071ccf/browser-qa/chromium', headless: true, args: [
      '--no-sandbox', '--disable-dev-shm-usage', '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream', '--use-file-for-fake-audio-capture=' + file,
      '--autoplay-policy=no-user-gesture-required',
    ] });
    const context = await browser.newContext({ permissions: ['microphone'], serviceWorkers: 'block' });
    let external = 0;
    await context.route('**/*', route => {
      if (new URL(route.request().url()).hostname !== '127.0.0.1') { external++; return route.abort(); }
      return route.continue();
    });
    await context.addInitScript(installSyntheticMic);
    const page = await context.newPage();
    await page.goto('http://127.0.0.1:' + server.address().port);
    const probe = await page.evaluate(async encoded => {
      const observations = [];
      for (let index = 0; index < 3; index++) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        const initiallyDisabled = stream.getAudioTracks()[0].enabled === false;
        // This local lifecycle probe has no application publication to enable it.
        stream.getAudioTracks()[0].enabled = true;
        const context = new AudioContext(); await context.resume();
        const source = context.createMediaStreamSource(stream), analyser = context.createAnalyser();
        source.connect(analyser);
        const playing = window.__qaSpeakFixture(encoded);
        await new Promise(resolve => setTimeout(resolve, 200));
        const samples = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
        await playing;
        stream.getTracks().forEach(track => track.stop());
        observations.push({ initiallyDisabled, nonzeroSignal: rms > 0.01, trackEnded: stream.getAudioTracks()[0].readyState === 'ended' });
        await context.close();
      }
      return { observations, created: window.__qaVoice.contextsCreated, closed: window.__qaVoice.contextsClosed, plays: window.__qaVoice.fixturePlays, rtcPeers: window.__qaVoice.peers.length };
    }, wav(16000, true).toString('base64'));
    assert.equal(external, 0);
    assert.equal(probe.created, 3); assert.equal(probe.closed, 3); assert.equal(probe.plays, 3); assert.equal(probe.rtcPeers, 0);
    assert(probe.observations.every(item => item.initiallyDisabled && item.nonzeroSignal && item.trackEnded));
    result.passed = true; result.synthetic_tracks = 3; result.contexts_closed = 3; result.external_requests = 0;
  } catch (error) { result.failure_class = error?.name || 'Error'; }
  finally {
    if (browser) await browser.close().catch(() => {});
    if (server) await new Promise(resolve => server.close(resolve));
    fs.rmSync(file, { force: true });
    fs.writeFileSync(__dirname + '/offline-probe-result.json', JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    if (!result.passed) process.exitCode = 1;
  }
})();
