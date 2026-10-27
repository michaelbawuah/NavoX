const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../site/assets/landing.js'), 'utf8');
const handlerSource = source.slice(source.indexOf('  /* Join */'), source.indexOf('  applyVibe(store.get'));

function fixture({ endpoint, valid = true, status = 201, offline = false } = {}) {
  let submit;
  const email = { value: 'student@example.edu', checkValidity: () => valid, focus() {} };
  const note = { textContent: '' };
  const button = { disabled: false };
  let calls = 0;
  let resets = 0;
  const form = {
    dataset: { endpoint },
    addEventListener: (_, callback) => { submit = callback; },
    querySelector: () => button,
    reset: () => { resets++; }
  };
  const elements = { joinForm: form, email, joinNote: note };
  vm.runInNewContext(handlerSource, {
    document: { getElementById: (id) => elements[id] },
    fetch: async (url, options) => {
      calls++;
      assert.equal(url, endpoint);
      assert.equal(options.method, 'POST');
      assert.deepEqual(JSON.parse(options.body), { email: 'student@example.edu', source: 'landing' });
      if (offline) throw new Error('offline');
      return { status };
    }
  });
  return {
    async send() {
      submit({ preventDefault() {}, target: form });
      await new Promise(resolve => setImmediate(resolve));
      return { text: note.textContent, disabled: button.disabled, calls, resets };
    }
  };
}

test('no endpoint never reports success or sends email', async () => {
  const result = await fixture().send();
  assert.match(result.text, /sign-ups open soon/);
  assert.equal(result.calls, 0);
  assert.equal(result.resets, 0);
});

test('invalid email never calls the server', async () => {
  const result = await fixture({ valid: false, endpoint: '/v1/waitlist' }).send();
  assert.match(result.text, /Enter your school email/);
  assert.equal(result.calls, 0);
});

for (const [status, text, resets] of [
  [201, /You’re on the list/, 1], [200, /You’re on the list/, 1],
  [409, /already on the list/, 0], [429, /Too many tries/, 0],
  [422, /didn’t go through/, 0], [503, /didn’t go through/, 0]
]) {
  test(`server ${status} maps to an honest status and unlocks submit`, async () => {
    const result = await fixture({ endpoint: '/v1/waitlist', status }).send();
    assert.match(result.text, text);
    assert.equal(result.resets, resets);
    assert.equal(result.calls, 1);
    assert.equal(result.disabled, false);
  });
}

test('network failure keeps the address available for retry', async () => {
  const result = await fixture({ endpoint: '/v1/waitlist', offline: true }).send();
  assert.match(result.text, /Check your connection/);
  assert.equal(result.resets, 0);
  assert.equal(result.disabled, false);
});
