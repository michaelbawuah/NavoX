/* Read-only first-time entry QA. Login is the only permitted server write.
 * Run on the Mac with Playwright available via NODE_PATH or NAVOX_PLAYWRIGHT_MODULE.
 * Credentials are read only at runtime; neither credentials nor storage state are written.
 */
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const assert = require("node:assert/strict");

function playwright() {
  for (const name of [process.env.NAVOX_PLAYWRIGHT_MODULE, "playwright", "@playwright/test"].filter(Boolean)) {
    try { return require(name); } catch (error) {
      if (error.code !== "MODULE_NOT_FOUND") throw error;
    }
  }
  throw Error("Playwright is unavailable; provide NAVOX_PLAYWRIGHT_MODULE or NODE_PATH.");
}

const base = (process.env.NAVOX_QA_BASE_URL || "https://vox-test.66-175-235-169.nip.io").replace(/\/$/, "");
const api = (process.env.NAVOX_QA_API_URL || `${base}/api/v1`).replace(/\/$/, "");
const credentialPath = process.env.NAVOX_QA_CREDENTIALS;
const output = process.env.NAVOX_QA_OUTPUT_DIR || "/tmp/navox-five-step-check";
const report = { release: process.env.NAVOX_QA_RELEASE || null, passed: false, target: base,
  mode: "read-only entry QA", output_dir: output, steps: [], blocked_requests: [], providers: {}, screenshots: [] };
const normalize = value => String(value).replace(/\s+/g, " ").trim();
const digest = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const safePath = raw => new URL(raw).pathname.replace(/[a-f0-9]{8}-[a-f0-9-]{27,}/gi, ":id");
let browser, apiContext;

async function main() {
  const { chromium, request } = playwright();
  assert(credentialPath, "Set NAVOX_QA_CREDENTIALS to a private QA credential JSON file.");
  const privateValue = JSON.parse(fs.readFileSync(credentialPath, "utf8"));
  const candidates = [privateValue, privateValue.credentials, privateValue.account, privateValue.user].filter(Boolean);
  const credentials = candidates.find(item => typeof item.email === "string" && typeof item.password === "string");
  assert(credentials, "Credential JSON needs email and password (top-level, credentials, account, or user).");
  apiContext = await request.newContext({ ignoreHTTPSErrors: false, extraHTTPHeaders: { Origin: new URL(base).origin } });
  const login = await apiContext.post(`${api}/auth/login`, { data: { email: credentials.email, password: credentials.password } });
  assert.equal(login.status(), 200, `QA login failed with HTTP ${login.status()}.`);
  const account = await login.json();
  // /auth/login returns AccountResponse directly, not { account: ... }.
  assert.equal(typeof account.id, "string", "Login response has no account ID.");
  assert.equal(typeof account.workspace?.id, "string", "Login response has no workspace ID.");
  assert.equal(account.workspace.workspace_type, "personal", "Fresh-account QA requires a personal workspace.");
  assert.equal(typeof account.timezone, "string", "Login response has no saved timezone.");
  new Intl.DateTimeFormat("en-US", { timeZone: account.timezone }).format();
  const accountKey = `${account.workspace.id}:${account.id}`;
  const headers = { "X-NavoX-Account": accountKey };
  const browserZone = account.timezone === "Pacific/Honolulu" ? "Asia/Tokyo" : "Pacific/Honolulu";
  report.saved_timezone = account.timezone;
  report.browser_timezone = browserZone;
  assert.notEqual(browserZone, account.timezone);
  async function read(endpoint) {
    const response = await apiContext.get(`${api}${endpoint}`, { headers });
    assert.equal(response.status(), 200, `${endpoint} returned HTTP ${response.status()}.`);
    return response.json();
  }
  const restored = await read("/auth/me");
  assert.equal(restored.id, account.id, "The login cookie did not restore the same account.");
  assert.equal(restored.workspace?.id, account.workspace.id, "The login cookie did not restore the same workspace.");
  assert.equal(restored.timezone, account.timezone, "Saved timezone changed immediately after login.");
  report.auth = { login_status: login.status(), response_shape: "AccountResponse", cookie_session_verified: true };
  async function snapshot() {
    const [profile, timetable, tasks, plan, preferences, connections, calendars, outlook] = await Promise.all([
      read("/student/profile"), read("/student/timetable?days=7"), read("/student/planning/tasks"),
      read("/student/planning/plan"), read("/student/planning/preferences"), read("/connections"),
      read("/student/calendars"), read("/outlook/status"),
    ]);
    return { profile, timetable: { courses: timetable.courses, fixed_blocks: timetable.fixed_blocks,
      breaks: timetable.breaks, exceptions: timetable.exceptions }, tasks, plan, preferences, connections,
      calendars, outlook: { configured: outlook.configured, connections: outlook.connections } };
  }
  const initial = await snapshot();
  assert(Array.isArray(initial.connections), "/api/v1/connections must return a connection list.");
  assert.equal(initial.tasks.tasks.length, 0, "This account already has tasks; fresh-account QA stopped.");
  assert.equal(initial.timetable.courses.length, 0, "This account already has courses; fresh-account QA stopped.");
  assert.equal(initial.timetable.fixed_blocks.length, 0, "This account already has shifts; fresh-account QA stopped.");
  assert.equal(initial.plan.blocks.length, 0, "This account already has study blocks; fresh-account QA stopped.");
  assert.equal(initial.connections.length, 0, "This account has connections; fresh-account QA stopped.");
  assert.equal(initial.calendars.grants.length, 0, "This account has calendar grants; fresh-account QA stopped.");
  assert.equal(initial.outlook.connections.length, 0, "This account has Outlook connections; fresh-account QA stopped.");
  report.initial_state = { empty_classes: true, empty_tasks: true, empty_plan: true, no_connections: true };
  const catalog = await read("/connectors");
  assert(Array.isArray(catalog), "/api/v1/connectors must return a connector catalog list.");
  report.api_routes_verified = ["/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/connections", "/api/v1/connectors"];
  report.providers = {
    calendar: initial.calendars.providers.map(row => ({ id: row.id, availability: row.availability,
      configured: row.availability === "available", consent_completed: false })),
    outlook: { configured: initial.outlook.configured, consent_completed: false,
      qualification: initial.outlook.configured ? "available; consent not attempted" : "unconfigured; consent cannot be qualified" },
    google: { availability: catalog.find(row => row.id === "google-workspace")?.availability ?? "missing",
      configured: catalog.find(row => row.id === "google-workspace")?.availability === "available",
      consent_completed: false },
  };
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  browser = await chromium.launch({ headless: true });
  const allowedOrigins = new Set([new URL(base).origin, new URL(api).origin]);
  async function guard(context) {
    await context.route("**/*", async route => {
      const req = route.request(), url = new URL(req.url());
      if (!allowedOrigins.has(url.origin) || !["GET", "HEAD", "OPTIONS"].includes(req.method())) {
        report.blocked_requests.push({ method: req.method(), path: safePath(url.href),
          reason: allowedOrigins.has(url.origin) ? "read-only guard" : "external origin guard" });
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    if (typeof context.routeWebSocket === "function") {
      await context.routeWebSocket(/.*/, socket => {
        report.blocked_requests.push({ method: "WEBSOCKET", path: safePath(socket.url()), reason: "read-only guard" });
        socket.close();
      });
    }
  }
  const browserOptions = { viewport: { width: 1440, height: 1000 }, timezoneId: browserZone,
    locale: "en-US", serviceWorkers: "block", reducedMotion: "reduce" };
  // The cinematic Enter NavoX gate is anonymous-only. Verify it in a separate
  // cookie-free context, then reuse the already verified QA login for setup.
  const anonymous = await browser.newContext(browserOptions);
  await guard(anonymous);
  try {
    const landing = await anonymous.newPage();
    landing.setDefaultTimeout(15000);
    await landing.goto(`${base}/`, { waitUntil: "domcontentloaded" });
    const enter = landing.getByRole("button", { name: /^Enter NavoX/ }).first();
    await enter.waitFor({ state: "visible" });
    assert.equal(await landing.getByRole("region", { name: "Set up NavoX", exact: true }).count(), 0);
    await enter.click();
    const dialog = landing.getByRole("dialog");
    await dialog.getByRole("group", { name: "Account access", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Sign in", exact: true }).first().click();
    await dialog.getByRole("heading", { name: "Welcome back", exact: true }).waitFor();
    await dialog.getByLabel("Email address", { exact: true }).waitFor();
    await dialog.getByLabel("Password", { exact: true }).waitFor();
    const file = path.join(output, "00-enter-navox-gate.png");
    await landing.screenshot({ path: file, fullPage: true });
    report.screenshots.push(file);
    report.welcome_gate = { passed: true, entry: "Enter NavoX", login_form_opened: true, credentials_entered: false };
  } catch (error) {
    report.welcome_gate = { passed: false, error: String(error.message).slice(0, 1400) };
  } finally {
    await anonymous.close();
  }
  const context = await browser.newContext({ ...browserOptions, storageState: await apiContext.storageState() });
  await guard(context);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  async function screenshot(name) {
    const file = path.join(output, `${name}.png`);
    await page.screenshot({ path: file, fullPage: true });
    report.screenshots.push(file);
  }
  async function clock() {
    const time = page.locator("header time[datetime]").first();
    await time.waitFor({ state: "visible" });
    await page.waitForFunction(zone => {
      const element = document.querySelector("header time[datetime]");
      const instant = element?.getAttribute("datetime");
      if (!instant) return false;
      const date = new Date(instant);
      const expected = new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "short", month: "short", day: "numeric" }).format(date)
        + " · " + new Intl.DateTimeFormat("en-US", { timeZone: zone, hour: "numeric", minute: "2-digit" }).format(date);
      return element.textContent.replace(/\s+/g, " ").trim() === expected.replace(/\s+/g, " ").trim();
    }, account.timezone);
    return { saved_zone_verified: true, displayed: normalize(await time.textContent()) };
  }
  async function home() {
    await page.goto(`${base}/`, { waitUntil: "domcontentloaded" });
    // A logged-in page skips the anonymous gate. Never treat an auth dialog as
    // first-time setup, and never submit a second login from the guarded browser.
    await page.getByRole("region", { name: "Set up NavoX", exact: true }).waitFor();
    const progress = page.getByRole("progressbar", { name: "Setup progress" });
    assert.equal(await progress.getAttribute("value"), "0", "Fresh setup should have zero completed steps.");
  }
  function entry(title) {
    return page.getByRole("region", { name: "Set up NavoX", exact: true }).locator("li").filter({ has: page.getByText(title, { exact: true }) });
  }
  async function step(name, action) {
    try { report.steps.push({ name, status: "passed", ...await action() }); }
    catch (error) { report.steps.push({ name, status: "failed", error: String(error.message).slice(0, 1400) }); }
  }
  await step("Fresh Today", async () => {
    await home(); const zone = await clock(); await screenshot("01-today-fresh"); return zone;
  });
  await step("Add your classes", async () => {
    await home(); await entry("Add your classes").getByRole("link", { name: "Add classes", exact: true }).click();
    await page.waitForURL(url => url.pathname === "/classes");
    await page.getByRole("heading", { name: "Let’s add your classes", exact: true }).waitFor();
    for (const name of ["Say them to Vox", "Import a file", "Snap a photo", "Type them in"]) {
      assert.equal(await page.getByRole("button", { name: new RegExp(name) }).count(), 1, `Missing class entry: ${name}`);
    }
    const zone = await clock(); await screenshot("02-classes-fresh");
    await page.getByRole("button", { name: /Type them in/ }).click();
    await page.getByLabel("Course name", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Course name", { exact: true }).inputValue(), "");
    return { route: "/classes", manual_editor_opened: true, saved: false, ...zone };
  });
  await step("Connect your calendar", async () => {
    await home(); await entry("Connect your calendar").getByRole("link", { name: "Connect", exact: true }).click();
    await page.waitForURL(url => url.pathname === "/connections");
    await page.getByRole("heading", { name: "Find time without sharing event details" }).waitFor();
    const check = page.getByRole("button", { name: "Check calendar access", exact: true });
    await check.click();
    await page.getByText("Calendar access checked. No calendar events were read.", { exact: true }).waitFor();
    const zone = await clock(); await screenshot("03-calendar-entry");
    return { route: "/connections", provider_status_checked_by_get: true, consent_completed: false, ...zone };
  });
  await step("Connect your email", async () => {
    await home(); await entry("Connect your email").getByRole("link", { name: "Connect", exact: true }).click();
    await page.waitForURL(url => url.pathname === "/connections");
    const outlook = page.getByRole("region", { name: "Outlook connection", exact: true });
    await outlook.waitFor();
    if (initial.outlook.configured === false) {
      await outlook.getByText(/Outlook setup is pending for this release/).waitFor();
      assert.equal(await outlook.getByRole("button", { name: "Connect Outlook", exact: true }).count(), 0);
    } else await outlook.getByRole("button", { name: "Connect Outlook", exact: true }).waitFor();
    const zone = await clock(); await screenshot("04-email-entry");
    await page.goto(`${base}/inbox`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Never miss an email that needs you" }).waitFor();
    await page.getByRole("link", { name: "Connect Gmail", exact: true }).waitFor();
    await page.getByRole("link", { name: "Connect Outlook", exact: true }).waitFor();
    await page.getByRole("region", { name: "Example emails, not your inbox", exact: true }).waitFor();
    await screenshot("05-inbox-fresh");
    return { route: "/connections", inbox_welcome_verified: true, consent_completed: false,
      outlook_configured: initial.outlook.configured, ...zone };
  });
  await step("Add one thing that’s due", async () => {
    await home(); await entry("Add one thing that’s due").getByRole("link", { name: "Add task", exact: true }).click();
    await page.waitForURL(url => url.pathname === "/planner");
    await page.getByRole("heading", { name: "What’s on your mind?", exact: true }).waitFor();
    await page.getByRole("heading", { name: "Add something to work on", exact: true }).waitFor();
    const field = page.locator('input[maxlength="200"]').first();
    await field.waitFor(); assert.equal(await field.inputValue(), "");
    const zone = await clock(); await screenshot("06-new-task-entry");
    return { route: "/planner#add-task", task_editor_opened: true, saved: false, ...zone };
  });
  await step("Try Vox", async () => {
    await home();
    const button = entry("Try Vox").getByRole("button", { name: "Try Vox", exact: true });
    await button.waitFor();
    if (await button.isDisabled()) return { status: "unavailable", launcher_visible: true,
      live_admission: "not attempted; launcher is disabled", standby_verified: false };
    // This launcher attribute is rendered only after Vox verifies its owner;
    // a visible setup button alone does not prove that async check completed.
    await page.locator('[data-vox-launcher="floating"]').waitFor({ state: "visible" });
    const before = report.blocked_requests.length;
    await button.click();
    await page.getByRole("dialog", { name: "NavoX Vox", exact: true }).waitFor();
    await screenshot("07-vox-guarded-entry");
    return { launcher_visible: true, overlay_opened: true, live_admission: "intentionally blocked by read-only guard",
      standby_verified: false, qualification: "entry UI only; blocked-start overlay is not successful standby",
      blocked_attempts: report.blocked_requests.slice(before), ...await clock() };
  });
  await home();
  const final = await snapshot();
  report.saved_state_unchanged = digest(initial) === digest(final);
  assert.equal(report.saved_state_unchanged, true, "Saved profile, classes, tasks, plan, routine or connections changed.");
  await screenshot("08-today-preserved");
  report.passed = report.welcome_gate?.passed === true && report.steps.every(item => item.status === "passed");
  report.live_provider_qualification = "Separate authenticated-provider QA required; this run verifies entry routing only.";
}

main().catch(error => { report.passed = false; report.error = String(error.message).slice(0, 1400); })
  .finally(async () => {
    if (browser) await browser.close().catch(() => {});
    if (apiContext) await apiContext.dispose().catch(() => {});
    fs.mkdirSync(output, { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(output, "five-step-entry-checks.json"), JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    process.exitCode = report.passed ? 0 : 1;
  });
