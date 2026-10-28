#!/usr/bin/env node
/*
  Build the NavoX landing page into deployable files.

  Usage:
    node build.js OUT_DIR [BASE]

    BASE is the URL path the site is served from. Use "" (default) when the
    site is the root of its own domain, or "/landing/" when it lives inside
    the Next.js app's public/landing folder.

  Inputs (next to this script):
    src.html               page markup, styles and script in one file
    noa.js                 `const NOA = {...}` with the 14 Noa SVGs
    img/*.png              app screenshots used on the page
    og.png                 share image (optional)
    vendor/three.min.js    Three.js r128 (MIT, license alongside)

  Needs only Node 18+. No packages.
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HERE = __dirname;
const SITE_URL = 'https://navox.net/';

const read = (name) => fs.readFileSync(path.join(HERE, name), 'utf8');
function write(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text, 'utf8');
}
function copy(from, to) {
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.copyFileSync(from, to);
}

function main() {
  const out = process.argv[2];
  const base = process.argv[3] || '';
  if (!out) { console.error('usage: node build.js OUT_DIR [BASE]'); process.exit(1); }
  const a = base + 'assets/';
  const src = read('src.html');

  const css = src.match(/<style>\n([\s\S]*?)<\/style>/)[1];
  const fonts = src.match(/<link rel="stylesheet" href="(https:\/\/fonts\.googleapis\.com[^"]+)">/)[1];
  let body = src.slice(src.indexOf('</style>') + '</style>'.length, src.indexOf('<script src="https://cdnjs')).trim();
  let js = src.match(/<script>\n\/\*NOA_DATA\*\/\n([\s\S]*?)<\/script>/)[1];

  body = body.split('src="img/').join('src="' + a + 'img/');
  for (const attribute of ['src', 'poster', 'href']) {
    body = body.split(attribute + '="demo/').join(attribute + '="' + a + 'demo/');
  }
  js = js.split("'img/").join("'" + a + 'img/');

  const ogImage = SITE_URL + base.replace(/^\//, '') + 'og.png';
  const head = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NavoX · your semester, on speaking terms</title>
<meta name="description" content="NavoX is the voice-first personal assistant for college students. Talk to Vox and it plans your week, finds time, drafts the email to your professor, and teaches you on a whiteboard. Nothing happens without your yes.">
<meta name="theme-color" content="#08090C" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)">
<link rel="icon" href="${base}favicon.svg" type="image/svg+xml">
<link rel="canonical" href="${SITE_URL}">
<meta property="og:type" content="website">
<meta property="og:title" content="NavoX · your semester, on speaking terms">
<meta property="og:description" content="The voice-first assistant for college students. Every action waits for your yes.">
<meta property="og:url" content="${SITE_URL}">
<meta property="og:image" content="${ogImage}">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="${fonts}">
<link rel="stylesheet" href="${a}landing.css">
<script>
  /* Apply the saved theme before first paint so there is no flash */
  try {
    var t = localStorage.getItem('navox-theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
</script>
</head>
<body>
`;
  const tail = `
<script src="${a}vendor/three.min.js" defer></script>
<script src="${a}noa-data.js" defer></script>
<script src="${a}landing.js" defer></script>
</body>
</html>
`;
  write(path.join(out, 'index.html'), head + body + '\n' + tail);

  const cssNote = '/* NavoX landing page styles. Tokens live on :root; '
    + 'light mode via prefers-color-scheme or [data-theme]. */\n';
  const jsNote = '/* NavoX landing page behavior. Needs THREE (r128) and NOA loaded first. */\n';
  const noaNote = '/* Noa mascot SVGs, keyed look-mood '
    + '(girl|boy x idle|listening|thinking|speaking|happy|focus|sleepy). '
    + 'Original NavoX character. */\n';
  write(path.join(out, 'assets', 'landing.css'), cssNote + css);
  write(path.join(out, 'assets', 'landing.js'), jsNote + js);
  write(path.join(out, 'assets', 'noa-data.js'), noaNote + read('noa.js').trim() + '\n');

  for (const f of fs.readdirSync(path.join(HERE, 'img'))) {
    copy(path.join(HERE, 'img', f), path.join(out, 'assets', 'img', f));
  }
  for (const f of fs.readdirSync(path.join(HERE, 'demo'))) {
    copy(path.join(HERE, 'demo', f), path.join(out, 'assets', 'demo', f));
  }
  for (const f of ['three.min.js', 'THREE-LICENSE.txt']) {
    copy(path.join(HERE, 'vendor', f), path.join(out, 'assets', 'vendor', f));
  }

  // Each Noa pose as its own SVG file, for reuse in the app
  const sandbox = {};
  vm.runInNewContext(read('noa.js') + ';this.NOA = NOA;', sandbox);
  const xmlns = '<svg xmlns="http://www.w3.org/2000/svg" ';
  for (const [name, svg] of Object.entries(sandbox.NOA)) {
    const file = svg.slice(0, 200).includes('xmlns=') ? svg : svg.replace('<svg ', xmlns);
    write(path.join(out, 'assets', 'noa', name + '.svg'), file);
  }

  write(path.join(out, 'favicon.svg'), `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="16" fill="#08090C"/>
  <g fill="#4FD1B5">
    <rect x="15" y="24" width="8" height="16" rx="4"/>
    <rect x="28" y="14" width="8" height="36" rx="4"/>
    <rect x="41" y="24" width="8" height="16" rx="4"/>
  </g>
</svg>
`);
  write(path.join(out, 'robots.txt'), 'User-agent: *\nAllow: /\n');
  if (fs.existsSync(path.join(HERE, 'og.png'))) copy(path.join(HERE, 'og.png'), path.join(out, 'og.png'));
  console.log('built', out, 'base=' + JSON.stringify(base));
}

main();
