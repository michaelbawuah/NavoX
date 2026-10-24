"""Build an offline comparison index from committed QA images and reports."""
from pathlib import Path
import html
import json
import sys

root = Path(sys.argv[1])
release = sys.argv[2]
def report(name):
    path = root / name
    return json.loads(path.read_text()) if path.exists() else None

groups = [
    ("today-morning", "Today at 9 AM", "reference-today.png", "today-09am-{device}-{theme}.png", "Real saved synthetic classes, tasks, shift and goals. The client and timetable display clock is controlled to 9 AM in America/New_York."),
    ("today-afternoon", "Today at 3 PM", "reference-today.png", "today-03pm-{device}-{theme}.png", "The same saved data at a controlled 3 PM local display clock; past entries and the next event change with time."),
    ("today-night", "Today at 3 AM", "reference-night.png", "today-03am-{device}-{theme}.png", "Controlled 3 AM local display clock. Sleep and wake information follows saved routine and alarm intent. The full day starts collapsed; native ringing is later work."),
    ("today-first", "First-time Today", "reference-welcome.png", "today-first-{device}-{theme}.png", "Untouched fresh QA account. Setup completion is based on actual saved state; provider consent is not fabricated."),
    ("classes", "Classes with a saved schedule", "reference-classes.png", "classes-saved-{device}-{theme}.png", "Three saved synthetic courses and a recurring shift. The mobile grid opens around today and scrolls sideways; time labels remain visible."),
    ("classes-first", "First-time Classes", "reference-classes-first.png", "classes-first-{device}-{theme}.png", "Four real entry paths: Vox, schedule file, reviewed photo extraction and manual entry. The faded sample week is explicitly a preview."),
    ("planner", "Planner with saved work", "reference-planner.png", "planner-saved-{device}-{theme}.png", "Saved tasks and accepted study blocks. The written 90-minute maximum takes precedence over the reference's two-hour sample blocks."),
    ("planner-first", "First-time Planner", "reference-planner-first.png", "planner-first-{device}-{theme}.png", "Task capture and saved-routine setup. No completion, capacity or focus-time history is invented for a new account."),
    ("inbox-first", "Inbox before connection", "reference-inbox-first.png", "inbox-first-{device}-{theme}.png", "The real fresh account has no connected mailbox. Gmail and Outlook entry paths are shown; provider credentials and live-account qualification remain separate gates."),
    ("inbox-connected", "Connected Inbox: reviewed draft", "reference-inbox.png", "inbox-route-fixtures/{device}-{theme}-saved-draft-QA-FIXTURE-full.png", "Clearly labelled browser route fixtures: two synthetic messages and one saved review. This verifies UI behavior only. No real account, provider read, OAuth consent or email send occurred in this fixture run."),
    ("inbox-meeting", "Connected Inbox: source-linked clash review", "reference-inbox.png", "inbox-route-fixtures/{device}-{theme}-meeting-clash-QA-FIXTURE-full.png", "The exact opened email is reviewed before checking a proposed time. The class conflict, source coverage and two alternatives are explicitly simulated contract responses, not real connected-calendar evidence."),
    ("settings", "Settings groundwork", "reference-settings.png", "settings-saved-{device}-{theme}.png", "The shared shell, school/term/timezone, appearance and routine/alarm controls are present. The source spec allows the full memory/privacy/history redesign to finish in milestone 5; visual parity with that panel is not claimed."),
]

checks = []
for name, label in [
    ("deployment-checks.json", "Isolated deployment and migration"),
    ("engineering-checks.json", "Automated tests and static checks"),
    ("browser-checks.json", "Live saved/first-time browser layouts"),
    ("today-controlled-clock-checks.json", "Controlled-clock Today states"),
    ("five-step-entry-checks.json", "Fresh-account setup destinations"),
    ("vox-capture-browser.json", "Live Vox review and navigation"),
    ("linux-postgres-qualification.json", "Linux and PostgreSQL qualification"),
    ("linux-vox-qualification.json", "Final-image Vox qualification"),
    ("inbox-route-fixtures/fixture-scope-and-results.json", "Connected Inbox browser fixtures"),
]:
    value = report(name)
    if not value:
        checks.append((label, "Not recorded", name, ""))
        continue
    passed = value.get("passed") is True or value.get("status") == "passed"
    detail = value.get("summary") or (str(len(value["screens"])) + " screen captures" if isinstance(value.get("screens"), list) else "See scoped report")
    checked_release = value.get("release", "unrecorded")
    status = "Passed" if passed else "Needs attention"
    if checked_release != release:
        status += " - earlier code checkpoint"
    checks.append((label, status, name, f"{detail}; code {checked_release}"))

def esc(value):
    return html.escape(str(value), quote=True)

cards = []
for anchor, title, reference, pattern, note in groups:
    initial = pattern.format(device="desktop", theme="dark")
    exists = (root / reference).exists() and (root / initial).exists()
    if not exists:
        cards.append(f'<section id="{anchor}"><h2>{esc(title)}</h2><p>{esc(note)}</p><p class="pending">This comparison is not recorded yet.</p></section>')
        continue
    cards.append(f'''<section id="{anchor}"><h2>{esc(title)}</h2><p class="note">{esc(note)}</p>
      <div class="pair"><figure><figcaption>Supplied design</figcaption><a href="{esc(reference)}"><img loading="lazy" src="{esc(reference)}" alt="Supplied {esc(title)} reference"></a></figure>
      <figure class="current"><figcaption>Implementation <span class="variant">desktop / dark</span></figcaption><a data-shot-link="{esc(pattern)}" href="{esc(initial)}"><img data-shot-pattern="{esc(pattern)}" loading="lazy" src="{esc(initial)}" alt="Implemented {esc(title)}"></a></figure></div></section>''')

rows = "".join(f'<tr><th>{esc(label)}</th><td>{esc(status)}</td><td><a href="{esc(path)}">Scoped report</a><small>{esc(detail)}</small></td></tr>' for label, status, path, detail in checks)
nav = "".join(f'<a href="#{anchor}">{esc(title)}</a>' for anchor, title, *_ in groups)
document = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NavoX milestone 2 - reference review</title>
<style>
:root{color-scheme:dark;font:15px/1.55 Inter,system-ui,sans-serif;background:#0b0d10;color:#f2f4f7}*{box-sizing:border-box}body{margin:0}main{max-width:1660px;margin:auto;padding:32px}h1{font-size:32px;line-height:1.2;margin:12px 0}h2{font-size:22px;margin:0 0 8px}p{max-width:1050px}a{color:#4fd1b5;text-underline-offset:3px}.kicker{color:#b69cff;letter-spacing:.08em;font-size:12px;text-transform:uppercase}.muted,.note,figcaption,small{color:#a3acb8}code{font:13px ui-monospace,monospace;overflow-wrap:anywhere}.controls{display:flex;flex-wrap:wrap;gap:18px;align-items:center;border:1px solid #262b33;background:#14171c;border-radius:12px;padding:16px;margin:24px 0}label{display:flex;gap:8px;align-items:center}select{background:#1b1f26;color:#f2f4f7;border:1px solid #455060;border-radius:8px;padding:10px;font:inherit}.nav{display:flex;flex-wrap:wrap;gap:8px 18px;margin:24px 0 32px}.nav a{font-size:13px}section{border-top:1px solid #262b33;padding:28px 0 36px;scroll-margin-top:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}figure{margin:0;min-width:0;background:#14171c;border:1px solid #262b33;border-radius:12px;padding:10px;overflow:hidden}figcaption{font-size:13px;margin-bottom:10px}.variant{float:right;color:#4fd1b5}img{display:block;width:100%;height:auto;border-radius:6px}.current[data-mobile=true] img{max-width:375px;margin:0 auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #262b33;padding:12px;text-align:left;vertical-align:top}td small{display:block;margin-top:5px;max-width:700px;overflow-wrap:anywhere}.pending{color:#f5b14c}video{width:100%;max-width:375px;border:1px solid #262b33;border-radius:12px}footer{padding:24px 0;color:#a3acb8;font-size:13px}@media(max-width:760px){main{padding:18px}h1{font-size:26px}.pair{grid-template-columns:1fr}table,tbody,tr,th,td{display:block}td,th{padding:7px 0;border:0}tr{padding:10px 0;border-bottom:1px solid #262b33}.variant{float:none;margin-left:8px}}
</style></head><body><main><header><div class="kicker">24 October 2026 / milestone 2</div><h1>NavoX - implementation beside the designs</h1>
<p>These comparisons use the supplied 19-page redesign specification. Names, courses and events in the implementation are clearly synthetic QA data. Automated results show what was checked; they do not constitute physical-phone or real-student acceptance.</p>
<p class="muted">Runtime code: <code>__RELEASE__</code> · <a href="https://vox-test.66-175-235-169.nip.io/">Open the isolated test site</a> · <a href="https://github.com/michaelbawuah/NavoX/pull/57">Draft PR #57</a></p></header>
<div class="controls"><strong>Compare a view</strong><label>Size <select id="device"><option value="desktop">Desktop · 1440 px</option><option value="mobile">Mobile browser · 375 px</option></select></label><label>Appearance <select id="theme"><option value="dark">Dark</option><option value="light">Light</option></select></label><span class="muted">The supplied reference stays unchanged. Select any image to open it at full resolution.</span></div>
<nav class="nav" aria-label="Comparison sections">__NAV__</nav>
<section id="checks"><h2>Evidence and limits</h2><table><thead><tr><th>Check</th><th>Result</th><th>Details</th></tr></thead><tbody>__ROWS__</tbody></table><p>The broad 247-test backend run retains its earlier revision. Only the listed Vox files changed afterward, and the final image passed its separate 45-test Vox run. <a href="backend-qualification-scope.json">See the exact qualification scope</a>.</p><p>Genuine Google and Microsoft consent, mailbox/calendar reads and exact send-receipt qualification remain pending account configuration. The connected Inbox images are visibly marked fixtures. Actual closed-phone ringing/snooze and a physical-phone acceptance recording remain later work. Full memory/history/privacy controls follow the specification's milestone 5 allowance.</p></section>
__CARDS__
<section id="vox"><h2>Vox stays connected through navigation</h2><p>A real provider call with typed QA requests and a synthetic microphone. The recording covers exact task review, class handoff, client-side navigation and hangup. It is browser emulation and has no microphone speech acceptance claim.</p><video controls preload="metadata" src="vox-review-navigation-mobile-emulation.webm"></video><p><a href="vox-capture-browser.json">Call evidence and cleanup checks</a></p></section>
<footer>Source image provenance: <a href="reference-screens.json">reference-screens.json</a>. Study streaks reflect recorded completed study blocks; elapsed focus, freezes and rewards are Part 2 work. Public production and main were not changed for this review.</footer></main>
<script>
function update(){const device=document.getElementById('device').value;const theme=document.getElementById('theme').value;document.querySelectorAll('[data-shot-pattern]').forEach(image=>{image.src=image.dataset.shotPattern.replaceAll('{device}',device).replaceAll('{theme}',theme);image.closest('figure').dataset.mobile=String(device==='mobile')});document.querySelectorAll('[data-shot-link]').forEach(link=>{link.href=link.dataset.shotLink.replaceAll('{device}',device).replaceAll('{theme}',theme)});document.querySelectorAll('.variant').forEach(label=>{label.textContent=device+' / '+theme})}document.getElementById('device').addEventListener('change',update);document.getElementById('theme').addEventListener('change',update);update();
</script></body></html>'''
document = document.replace("__RELEASE__", esc(release)).replace("__NAV__", nav).replace("__ROWS__", rows).replace("__CARDS__", "\n".join(cards))
(root / "index.html").write_text(document)
print(json.dumps({"index": str(root / "index.html"), "comparisons": len(cards), "release": release}))
