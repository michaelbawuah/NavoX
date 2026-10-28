/* NavoX landing page behavior. Needs THREE (r128) and NOA loaded first. */
(function () {
  const root = document.documentElement;
  /* Reduce Motion makes the page calmer, never frozen. Touch screens get scroll-driven motion instead of hover. */
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const touch = window.matchMedia('(hover: none), (pointer: coarse)').matches;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  /* Theme + vibe */
  const VIBES = {
    midnight: ['#4FD1B5', '#0F7A66'], lavender: ['#B69CFF', '#5B3FC4'],
    sunset: ['#FF8A5B', '#B4441A'], matcha: ['#B5E550', '#4D7A0E']
  };
  function applyVibe(name) {
    const v = VIBES[name] || VIBES.midnight;
    root.style.setProperty('--accent', v[0]);
    root.style.setProperty('--accent-deep', v[1]);
    document.querySelectorAll('.vibe').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.vibe === name)));
    store.set('navox-vibe', name);
    orbColor();
  }
  function isDark() {
    const t = root.getAttribute('data-theme');
    return t ? t === 'dark' : !window.matchMedia('(prefers-color-scheme: light)').matches;
  }
  const savedTheme = store.get('navox-theme');
  if (savedTheme === 'light' || savedTheme === 'dark') root.setAttribute('data-theme', savedTheme);
  document.getElementById('theme').addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    store.set('navox-theme', next);
    orbColor();
  });
  document.querySelectorAll('.vibe').forEach((b) => b.addEventListener('click', () => applyVibe(b.dataset.vibe)));

  /* Noa helpers */
  function noa(el, look, mood) { el.innerHTML = NOA[look + '-' + mood] || NOA['girl-idle']; }

  let pointer = { x: 0, y: 0 };
  var Week = null;
  /* Hero: the student's week in 3D, played by a Vox call */
  const script = [
    { who: 'you', text: 'when can I meet Prof. Lee tomorrow?', mood: 'listening', sticker: 'all ears', phase: 'listen' },
    {
      who: 'noa',
      text: '3:30 to 4:30 is free tomorrow, and it’s during Prof. Lee’s office hours. want me to add it?',
      mood: 'speaking',
      sticker: 'ok so',
      phase: 'offer'
    },
    { who: 'you', text: 'yeah', mood: 'listening', sticker: 'mm-hm', phase: 'listen' },
    { who: 'noa', text: 'bet. it’s on your calendar. want me to draft an email to Prof. Lee too?', mood: 'happy', sticker: 'W', phase: 'drop' }
  ];
  const chat = document.getElementById('chat');
  const heroNoa = document.getElementById('noaHero');
  const heroSticker = document.getElementById('heroSticker');
  let step = -1, demoTimer = null;
  function showStep(i) {
    const s = script[i];
    noa(heroNoa, 'girl', s.mood);
    heroSticker.textContent = s.sticker;
    if (Week) Week.phase(s.phase);
    const shown = script.slice(0, i + 1).slice(-3);
    chat.innerHTML = '';
    shown.forEach((m, k) => {
      const b = document.createElement('div');
      b.className = 'bubble ' + m.who + (k < shown.length - 1 ? ' old' : '');
      b.textContent = m.text;
      chat.appendChild(b);
    });
  }
  function playDemo() {
    clearInterval(demoTimer);
    if (Week) Week.phase('reset');
    step = 0; showStep(0);
    demoTimer = setInterval(() => {
      step += 1;
      if (step < script.length) showStep(step);
      else if (step > script.length + 1) { if (Week) Week.phase('reset'); step = 0; showStep(0); }
      else { noa(heroNoa, 'girl', 'idle'); if (Week) Week.phase('idle'); }
    }, 2800);
  }
  const callDemoButton = document.getElementById('replay');
  const callDemoDialog = document.getElementById('callDemoDialog');
  const callDemoVideo = document.getElementById('callDemoVideo');
  const callDemoStatus = document.getElementById('callDemoStatus');
  function closeCallDemo() { if (callDemoDialog.open) callDemoDialog.close(); }
  callDemoButton.addEventListener('click', () => {
    if (callDemoDialog.open) return;
    callDemoStatus.textContent = '';
    callDemoDialog.showModal();
    document.body.classList.add('call-demo-open');
    callDemoVideo.muted = false;
    callDemoVideo.currentTime = 0;
    const playback = callDemoVideo.play();
    if (playback) playback.catch(() => { callDemoStatus.textContent = 'Press Play to start the demo with sound.'; });
  });
  document.getElementById('closeCallDemo').addEventListener('click', closeCallDemo);
  callDemoDialog.addEventListener('click', (event) => {
    const bounds = callDemoDialog.getBoundingClientRect();
    if (event.target === callDemoDialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) closeCallDemo();
  });
  callDemoDialog.addEventListener('cancel', () => { callDemoVideo.pause(); });
  callDemoDialog.addEventListener('close', () => {
    callDemoVideo.pause();
    document.body.classList.remove('call-demo-open');
    callDemoButton.focus({ preventScroll: true });
  });
  callDemoVideo.addEventListener('error', () => { callDemoStatus.textContent = 'The video could not load. Please close the demo and try again.'; });

  function orbColor() { if (Week) Week.recolor(); }

  Week = (function buildWeek() {
    const canvas = document.getElementById('week3d');
    const box = document.getElementById('weekBox');
    const tip = document.getElementById('weekTip');
    if (!window.THREE || !canvas) return null;
    let renderer;
    try {
      const options = { alpha: true, antialias: true };
      const context = canvas.getContext('webgl2', options) || canvas.getContext('webgl', options);
      if (!context) return null;
      renderer = new THREE.WebGLRenderer({ canvas, context, ...options });
    } catch (e) { return null; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
    const hemi = new THREE.HemisphereLight(0xffffff, 0x3a4250, 0.8);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xffffff, 0.75);
    sun.position.set(-5, 11, 7);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    Object.assign(sun.shadow.camera, { left: -7, right: 7, top: 7, bottom: -7 });
    scene.add(sun);
    const world = new THREE.Group();
    scene.add(world);

    const H0 = 8, H1 = 22, COL = 1.12, ROW = 0.42;
    const X = (d) => (d - 3) * COL;
    const Z = (h) => (h - H0) * ROW - ((H1 - H0) * ROW) / 2;
    const COURSE = { math: '#B69CFF', cs: '#7AA2F7', ece: '#2FB89A', sig: '#FF8A9B', shift: '#F5B14C' };
    const LABEL = {
      math: 'MATH 2940 · Linear Algebra', cs: 'CS 2110 · Data Structures', ece: 'ECE 2300 · Digital Logic',
      sig: 'ECE 2720 · Signals', shift: 'Shift · Service Center', lab: 'ECE 2300 lab'
    };
    const m = (h, min) => h + min / 60;
    const ev = (d, a, b, k, kind) => ({ d, a, b, k, kind: kind || k });
    const EVENTS = [
      ev(0, m(9, 5), m(9, 55), 'math'), ev(0, m(10, 10), 11, 'cs'), ev(0, m(14, 55), m(16, 10), 'sig'),
      ev(1, 12.5, 13.75, 'ece'),
      ev(2, m(9, 5), m(9, 55), 'math'), ev(2, m(10, 10), 11, 'cs'), ev(2, m(14, 55), m(16, 10), 'sig'), ev(2, 19.5, 22, 'ece', 'lab'),
      ev(3, m(9, 5), m(9, 55), 'math'), ev(3, m(10, 10), 11, 'cs'), ev(3, 12.5, 13.75, 'ece'), ev(3, 17, 20, 'shift'),
      ev(4, m(9, 5), m(9, 55), 'math'), ev(4, m(10, 10), 11, 'cs'),
      ev(5, 12, 16, 'shift')
    ];
    const SLOT = { d: 4, a: 15.5, b: 16.5 };
    const DAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const fmt = (h) => { const hh = Math.floor(h), mm = Math.round((h - hh) * 60); return ((hh + 11) % 12 + 1) + ':' + String(mm).padStart(2, '0'); };

    /* Hour plates */
    const plateGeo = new THREE.BoxGeometry(1.0, 0.08, ROW - 0.06);
    const plateEdges = new THREE.EdgesGeometry(plateGeo);
    const plates = [];
    const voice = new THREE.Vector2(X(-1.4), Z(H1) + 0.8);
    for (let d = 0; d < 7; d++) {
      for (let r = 0; r < H1 - H0; r++) {
        const mat = new THREE.MeshStandardMaterial({ roughness: 0.85, metalness: 0 });
        const mesh = new THREE.Mesh(plateGeo, mat);
        mesh.position.set(X(d), 0, Z(H0 + r + 0.5));
        mesh.receiveShadow = true;
        const line = new THREE.LineSegments(plateEdges, new THREE.LineBasicMaterial({ transparent: true }));
        mesh.add(line);
        world.add(mesh);
        const busy = EVENTS.some((e) => e.d === d && e.a < H0 + r + 1 && e.b > H0 + r);
        const inSlot = d === SLOT.d && SLOT.a < H0 + r + 1 && SLOT.b > H0 + r;
        plates.push({ mesh, mat, line, d, r, busy, inSlot, dist: Math.hypot(mesh.position.x - voice.x, mesh.position.z - voice.y) });
      }
    }

    /* Classes, lab and shifts as raised blocks */
    const bricks = [];
    function brick(e, color) {
      const h = e.kind === 'shift' ? 0.34 : 0.5;
      const len = (e.b - e.a) * ROW - 0.05;
      const geo = new THREE.BoxGeometry(0.94, h, len);
      const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(X(e.d), h / 2 + 0.04, Z((e.a + e.b) / 2));
      mesh.castShadow = true; mesh.receiveShadow = true;
      const line = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ transparent: true }));
      mesh.add(line);
      mesh.userData = { label: (LABEL[e.kind] || LABEL[e.k]) + ' · ' + DAY[e.d] + ' ' + fmt(e.a) + '–' + fmt(e.b), line, h };
      world.add(mesh);
      bricks.push(mesh);
      return mesh;
    }
    EVENTS.forEach((e) => brick(e, COURSE[e.k]));

    /* The new meeting: ghost outline + the block that drops in */
    const slotLen = (SLOT.b - SLOT.a) * ROW - 0.05;
    const ghostGeo = new THREE.BoxGeometry(0.94, 0.5, slotLen);
    const ghostMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0 });
    const ghost = new THREE.LineSegments(new THREE.EdgesGeometry(ghostGeo), ghostMat);
    ghost.position.set(X(SLOT.d), 0.29, Z((SLOT.a + SLOT.b) / 2));
    world.add(ghost);
    const meet = brick({ d: SLOT.d, a: SLOT.a, b: SLOT.b, k: 'ece', kind: 'meet' }, '#ffffff');
    meet.userData.label = 'Prof. Lee · office hours · Fri 3:30–4:30 · added by you';
    meet.material.emissiveIntensity = 0.35;
    meet.visible = false;

    /* Burst when it lands */
    const bitGeo = new THREE.BoxGeometry(0.07, 0.07, 0.07);
    const bits = [];
    for (let i = 0; i < 26; i++) {
      const b = new THREE.Mesh(bitGeo, new THREE.MeshBasicMaterial({ transparent: true }));
      b.visible = false; world.add(b); bits.push(b);
    }

    /* Day and hour labels drawn on the board */
    const labelMeshes = [];
    function textPlane(text, w, h, size, weight, color) {
      const c = document.createElement('canvas');
      c.width = 256; c.height = 128;
      const g = c.getContext('2d');
      g.fillStyle = color; g.font = weight + ' ' + size + 'px "Bricolage Grotesque", "Instrument Sans", sans-serif';
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, 128, 64);
      const tex = new THREE.CanvasTexture(c);
      tex.anisotropy = 4;
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
      mesh.rotation.x = -Math.PI / 2;
      return mesh;
    }
    function drawLabels(ink, accent) {
      labelMeshes.forEach((l) => { world.remove(l); l.material.map.dispose(); l.material.dispose(); l.geometry.dispose(); });
      labelMeshes.length = 0;
      DAY.forEach((d, i) => {
        const special = i === 3 ? 'today' : i === 4 ? 'tomorrow' : '';
        const p = textPlane(d, 1.1, 0.55, 64, 800, i === 4 ? accent : ink);
        p.position.set(X(i), 0.01, Z(H0) - 0.42); world.add(p); labelMeshes.push(p);
        if (special) {
          const s = textPlane(special, 1.1, 0.55, 40, 600, i === 4 ? accent : ink);
          s.position.set(X(i), 0.01, Z(H0) - 0.86); world.add(s); labelMeshes.push(s);
        }
      });
      [[8, '8a'], [12, '12p'], [16, '4p'], [20, '8p']].forEach(([h, t]) => {
        const p = textPlane(t, 1.2, 0.6, 60, 700, ink);
        p.position.set(X(0) - 1.0, 0.01, Z(h) + 0.02); world.add(p); labelMeshes.push(p);
      });
    }

    /* Colors follow theme and vibe */
    const tmp = new THREE.Color(), accentC = new THREE.Color(), plateC = new THREE.Color();
    let dark = true;
    function recolor() {
      dark = isDark();
      const css = getComputedStyle(root);
      const accent = css.getPropertyValue('--accent').trim() || '#4FD1B5';
      accentC.set(accent);
      plateC.set(dark ? '#161920' : '#FFFFFF');
      const edge = dark ? '#2C323D' : '#0B0D10';
      plates.forEach((p) => { p.mat.color.copy(plateC); p.line.material.color.set(edge); p.line.material.opacity = dark ? 0.9 : 1; });
      bricks.forEach((b) => { b.userData.line.material.color.set(dark ? '#05060A' : '#0B0D10'); });
      meet.material.color.copy(accentC); meet.material.emissive = accentC.clone();
      ghostMat.color.copy(accentC);
      bits.forEach((b, i) => b.material.color.set([accent, '#B69CFF', '#FFD27A', '#FF8A9B', '#FFFFFF'][i % 5]));
      hemi.intensity = dark ? 0.7 : 0.95;
      sun.intensity = dark ? 0.8 : 0.6;
      drawLabels(dark ? '#A3ACB8' : '#0B0D10', dark ? accent : (css.getPropertyValue('--accent-deep').trim() || '#0F7A66'));
    }

    /* Camera: slow drift, mouse parallax, drag to turn */
    const target = new THREE.Vector3(0.35, 0, 0.2);
    let az = 0.55, azDrag = 0, dragging = false, dragX = 0, scrollTurn = 0;
    /* On phones the week turns as you scroll past it */
    window.addEventListener('scroll', () => {
      const r = canvas.getBoundingClientRect(), vh = window.innerHeight;
      const p = Math.max(-1, Math.min(1, (r.top + r.height / 2 - vh / 2) / vh));
      scrollTurn = touch ? p * (calm ? 0.25 : 0.6) : 0;
    }, { passive: true });
    function placeCamera(t) {
      const narrow = canvas.clientWidth < 440;
      const radius = narrow ? 18.5 : 16;
      const a = az + azDrag + pointer.x * 0.14 + (calm ? 0 : Math.sin(t * 0.00015) * 0.08) + scrollTurn;
      const polar = 0.92 + pointer.y * 0.06;
      camera.position.set(target.x + radius * Math.sin(polar) * Math.sin(a), radius * Math.cos(polar), target.z + radius * Math.sin(polar) * Math.cos(a));
      camera.lookAt(target);
    }
    canvas.addEventListener('pointerdown', (e) => { dragging = true; dragX = e.clientX; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener('pointerup', () => { dragging = false; });
    canvas.addEventListener('pointercancel', () => { dragging = false; });

    /* Hover a block to see what it is */
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    canvas.addEventListener('pointermove', (e) => {
      const r = canvas.getBoundingClientRect();
      if (dragging) { azDrag = Math.max(-1.1, Math.min(1.1, azDrag + (e.clientX - dragX) * 0.006)); dragX = e.clientX; tip.hidden = true; return; }
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObjects(bricks.filter((b) => b.visible), false)[0];
      if (hit) {
        tip.textContent = hit.object.userData.label;
        const br = box.getBoundingClientRect();
        tip.style.left = (e.clientX - br.left) + 'px'; tip.style.top = (e.clientY - br.top) + 'px';
        tip.hidden = false;
      } else tip.hidden = true;
    });
    canvas.addEventListener('pointerleave', () => { tip.hidden = true; });

    /* Phases driven by the call */
    let wave = 0, waveTarget = 0, offer = 0, offerTarget = 0, dropAt = -1, landed = false;
    function phase(p) {
      if (p === 'listen') { waveTarget = 1; }
      else if (p === 'offer') { waveTarget = 0.25; offerTarget = 1; }
      else if (p === 'drop') { waveTarget = 0; offerTarget = 1; if (!meet.visible) { dropAt = performance.now(); meet.visible = true; landed = false; } }
      else if (p === 'idle') { waveTarget = 0; offerTarget = 0; }
      else if (p === 'reset') { waveTarget = 0; offerTarget = 0; meet.visible = false; dropAt = -1; landed = false; bits.forEach((b) => { b.visible = false; }); }
    }
    function bounce(x) {
      const n = 7.5625, d = 2.75;
      if (x < 1 / d) return n * x * x;
      if (x < 2 / d) return n * (x -= 1.5 / d) * x + 0.75;
      if (x < 2.5 / d) return n * (x -= 2.25 / d) * x + 0.9375;
      return n * (x -= 2.625 / d) * x + 0.984375;
    }
    function burst() {
      bits.forEach((b) => {
        b.visible = true;
        b.position.set(meet.position.x, 0.55, meet.position.z);
        b.userData = { vx: (Math.random() - 0.5) * 0.09, vy: 0.06 + Math.random() * 0.07, vz: (Math.random() - 0.5) * 0.09, life: 1 };
        b.material.opacity = 1;
      });
    }

    function resize() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix();
    }

    function render(now) {
      const t = now / 1000;
      wave += (waveTarget - wave) * 0.06;
      offer += (offerTarget - offer) * 0.08;
      const pulse = 0.55 + 0.45 * Math.sin(t * 5);
      plates.forEach((p) => {
        let k = 0;
        if (!p.busy) {
          const s = Math.sin(t * 6.2 - p.dist * 1.25);
          k = wave * Math.pow(Math.max(0, s), 3) * (0.55 + 0.45 * Math.sin(t * 2.3 + p.d));
        }
        const slotGlow = p.inSlot ? offer * (meet.visible ? 0.35 : pulse) : 0;
        p.mesh.scale.y = 1 + k * 7;
        p.mesh.position.y = 0.04 * k * 7;
        tmp.copy(plateC).lerp(accentC, Math.min(1, k * 1.6 + slotGlow * 0.9));
        p.mat.color.copy(tmp);
      });
      ghostMat.opacity = meet.visible ? 0 : offer * pulse;
      if (meet.visible) {
        const h = meet.userData.h, rest = h / 2 + 0.04;
        if (landed) { meet.position.y = rest; }
        else {
          const x = Math.min(1, (now - dropAt) / 900);
          meet.position.y = rest + (1 - (calm ? 1 - Math.pow(1 - x, 3) : bounce(x))) * (calm ? 1.2 : 3.4);
          if (x >= 1) { landed = true; burst(); }
        }
      }
      bits.forEach((b) => {
        if (!b.visible) return;
        const u = b.userData;
        u.vy -= 0.004; b.position.x += u.vx; b.position.y += u.vy; b.position.z += u.vz;
        b.rotation.x += 0.2; b.rotation.y += 0.15; u.life -= 0.016;
        b.material.opacity = Math.max(0, u.life);
        if (u.life <= 0 || b.position.y < 0) b.visible = false;
      });
      placeCamera(now);
      renderer.render(scene, camera);
    }

    resize();
    window.addEventListener('resize', resize);
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', recolor);
    let visible = true;
    new IntersectionObserver((es) => { visible = es[0].isIntersecting; }).observe(canvas);
    (function loop(now) { if (visible) render(now); requestAnimationFrame(loop); })(performance.now());
    return { phase, recolor, ready: () => { recolor(); } };
  })();
  if (Week) Week.ready();
  playDemo();

  /* Mouse position feeds the week camera */
  window.addEventListener('pointermove', (e) => {
    const r = document.getElementById('stage').getBoundingClientRect();
    pointer.x = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / (r.width / 2)));
    pointer.y = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / (r.height / 2)));
  }, { passive: true });
  /* Marquee */
  const saysA = [
    'what’s next?',
    'when am I free tomorrow?',
    'i’m cooked',
    'quiz me on chapter 4',
    'move my study block to tonight',
    'explain this slide like I’m tired',
    'wake me up at 7',
    'did Prof. Lee reply?'
  ];
  const saysB = [
    'plan my week',
    'how long is my walk to Phillips?',
    'I didn’t finish the lab report',
    'draft an email saying I’ll miss lab',
    'what did I say about my internship?',
    'forget that',
    'lock in for 25 minutes',
    'hey NavoX'
  ];
  function fill(id, list) {
    const el = document.getElementById(id);
    list.concat(list).forEach((s, i) => {
      const sp = document.createElement('span'); sp.textContent = '“' + s + '”';
      if (i >= list.length) sp.setAttribute('aria-hidden', 'true');
      el.appendChild(sp);
    });
  }
  fill('trackA', saysA); fill('trackB', saysB);

  /* You say / Noa does */
  const examples = [
    {
      say: 'i’m cooked',
      reply: 'real. 3 things due this week. smallest first: lab report outline, 25 minutes, right now. i’ll lock in with you.',
      chips: ['lock in · 25 min', 'show my week']
    },
    {
      say: 'when am I free tomorrow?',
      reply: '2:00 to 4:45 is open, after ECE 2300. also free 7 to 9 tonight. want me to put the lab report in one?',
      chips: ['yes, add it', 'other times']
    },
    {
      say: 'explain slide 14',
      reply: 'opening it on the board. i’ll draw the circuit step by step. stop me anytime with a question.',
      chips: ['open board', 'quiz me after']
    },
    {
      say: 'i missed class today',
      reply: 'got you. here’s what was covered from the slides, the one thing to do before Thursday, and a draft note to your TA.',
      chips: ['see recap', 'review draft']
    }
  ];
  const tabs = document.getElementById('tabs');
  const reply = document.getElementById('reply');
  function pick(i) {
    const ex = examples[i];
    tabs.querySelectorAll('.tab').forEach((t, k) => t.setAttribute('aria-selected', String(k === i)));
    document.getElementById('sayQ').textContent = ex.say;
    document.getElementById('replyText').textContent = ex.reply;
    const chips = document.getElementById('replyChips'); chips.innerHTML = '';
    ex.chips.forEach((c) => { const s = document.createElement('span'); s.className = 'chip'; s.textContent = c; chips.appendChild(s); });
    reply.classList.remove('flip'); void reply.offsetWidth; reply.classList.add('flip');
  }
  examples.forEach((ex, i) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'tab'; b.setAttribute('role', 'tab'); b.textContent = '“' + ex.say + '”';
    b.addEventListener('click', () => pick(i));
    tabs.appendChild(b);
  });
  pick(0);

  /* Feature bars + tilt cards */
  const bb = document.getElementById('bigBars');
  [20, 40, 64, 30, 78, 52, 90, 60, 84, 36, 70, 44, 28, 16].forEach((h, i) => {
    const s = document.createElement('span'); s.style.height = h + 'px'; s.style.animationDelay = ((i * 0.13) % 1.1).toFixed(2) + 's'; bb.appendChild(s);
  });
  if (!touch) {
    document.querySelectorAll('[data-tilt]').forEach((card) => {
      card.addEventListener('pointermove', (e) => {
        const r = card.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        card.style.transform = 'rotateY(' + ((x - 0.5) * 16).toFixed(2) + 'deg) rotateX(' + ((0.5 - y) * 14).toFixed(2) + 'deg) translateZ(10px)';
        card.style.setProperty('--gx', (x * 100).toFixed(1) + '%'); card.style.setProperty('--gy', (y * 100).toFixed(1) + '%'); card.style.setProperty('--glare', '1');
      });
      card.addEventListener('pointerleave', () => { card.style.transform = ''; card.style.setProperty('--glare', '0'); });
    });
  }

  if (touch) {
    /* No hover on phones: cards lean as they scroll through the screen, and glow when tapped */
    const cards = [...document.querySelectorAll('[data-tilt]')];
    const lean = () => {
      const vh = window.innerHeight;
      cards.forEach((card, i) => {
        const r = card.getBoundingClientRect();
        if (r.bottom < 0 || r.top > vh) return;
        const p = Math.max(-1, Math.min(1, (r.top + r.height / 2 - vh / 2) / (vh / 2)));
        const k = calm ? 0.35 : 1;
        card.style.transform = 'rotateX(' + (p * 10 * k).toFixed(2) + 'deg) rotateY(' + ((i % 2 ? -1 : 1) * p * 6 * k).toFixed(2) + 'deg)';
        card.style.setProperty('--gy', ((1 - p) * 50).toFixed(1) + '%');
        card.style.setProperty('--glare', (0.6 * (1 - Math.abs(p))).toFixed(2));
      });
    };
    window.addEventListener('scroll', lean, { passive: true }); lean();
  }

  /* Meet Noa */
  const MOODS = {
    idle: 'Just vibing. Blinks, floats, and keeps an eye on your next class.',
    listening: 'Leans in, nods, and shows your words live as you talk.',
    thinking: 'Checking your calendar or working out the answer.',
    speaking: 'Talking you through it. You can cut in anytime.',
    happy: 'You finished something. Noa noticed.',
    focus: 'Locked in. Noa studies with you and stays quiet.',
    sleepy: 'It’s 2am. Shorter answers, gentle nudges toward sleep.'
  };
  let look = 'girl', mood = 'idle';
  const meet = document.getElementById('noaMeet');
  function renderMeet() {
    noa(meet, look, mood);
    meet.classList.remove('pop'); void meet.offsetWidth; meet.classList.add('pop');
    document.getElementById('moodNote').textContent = MOODS[mood];
    document.querySelectorAll('#looks button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === look)));
    document.querySelectorAll('#moods button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === mood)));
  }
  function seg(id, values, onPick) {
    const el = document.getElementById(id);
    values.forEach((v) => {
      const b = document.createElement('button'); b.type = 'button'; b.dataset.v = v; b.textContent = v;
      b.addEventListener('click', () => onPick(v)); el.appendChild(b);
    });
  }
  seg('looks', ['girl', 'boy'], (v) => { look = v; renderMeet(); });
  seg('moods', Object.keys(MOODS), (v) => { mood = v; renderMeet(); });
  renderMeet();
  noa(document.getElementById('noaCta'), 'boy', 'happy');

  /* 3D carousel */
  const SCREENS = [
    ['assets/img/today.png', 'Today: your next class, when to leave, and your day in order.'],
    ['assets/img/listening.png', 'Vox listening: live captions show it heard you right.'],
    ['assets/img/actions.png', 'One tap to add it. Nothing happens until you say yes.'],
    ['assets/img/focus.png', 'Lock in for 25 minutes. Noa studies with you.'],
    ['assets/img/late.png', 'At 2 am it gets calmer and nudges you to sleep.'],
    ['assets/img/rewards.png', 'Streak rewards: real work unlocks outfits for Noa.'],
    ['assets/img/dressed.png', 'Wear your unlocks. Pick your vibe.']
  ];
  const ringEl = document.getElementById('ring');
  const car = document.getElementById('carousel');
  const step3d = 360 / SCREENS.length;
  let angle = 0, active = 0, radius = 420;
  SCREENS.forEach((s, i) => {
    const d = document.createElement('div'); d.className = 'phone';
    const img = document.createElement('img'); img.src = s[0]; img.alt = s[1]; img.loading = 'lazy';
    d.appendChild(img); ringEl.appendChild(d);
  });
  function layout() {
    const w = ringEl.offsetWidth || 250;
    radius = Math.round((w / 2) / Math.tan(Math.PI / SCREENS.length) + 40);
    ringEl.querySelectorAll('.phone').forEach((p, i) => { p.style.transform = 'rotateY(' + (i * step3d) + 'deg) translateZ(' + radius + 'px)'; });
    spin();
  }
  function spin() {
    ringEl.style.transform = 'translateZ(' + (-radius) + 'px) rotateY(' + angle + 'deg)';
    active = ((Math.round(-angle / step3d) % SCREENS.length) + SCREENS.length) % SCREENS.length;
    document.getElementById('carCap').textContent = SCREENS[active][1];
  }
  function go(d) { angle -= d * step3d; spin(); }
  document.getElementById('prev').addEventListener('click', () => { go(-1); hold(); });
  document.getElementById('next').addEventListener('click', () => { go(1); hold(); });
  let held = false, auto = null;
  function hold() { held = true; clearTimeout(auto); auto = setTimeout(() => { held = false; }, 8000); }
  setInterval(() => { if (!held) go(1); }, calm ? 5000 : 3200);
  let dragX = null, startAngle = 0;
  car.addEventListener('pointerdown', (e) => { dragX = e.clientX; startAngle = angle; ringEl.classList.add('dragging'); car.setPointerCapture(e.pointerId); hold(); });
  car.addEventListener('pointermove', (e) => { if (dragX === null) return; angle = startAngle + (e.clientX - dragX) * 0.35; spin(); });
  function endDrag() { if (dragX === null) return; dragX = null; ringEl.classList.remove('dragging'); angle = Math.round(angle / step3d) * step3d; spin(); }
  car.addEventListener('pointerup', endDrag); car.addEventListener('pointercancel', endDrag);
  window.addEventListener('resize', layout); layout();

  /* Laptop straightens as it scrolls into view */
  const laptop = document.getElementById('laptop');
  function onScroll() {
    const r = laptop.getBoundingClientRect(), vh = window.innerHeight;
    const p = Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.75)));
    laptop.style.setProperty('--lx', (24 * (1 - p)).toFixed(2) + 'deg');
    laptop.style.setProperty('--ls', (0.92 + 0.08 * p).toFixed(3));
  }
  window.addEventListener('scroll', onScroll, { passive: true }); onScroll();

  /* Streak rewards with confetti */
  const REWARDS = [[3, 'study glasses'], [7, 'cozy beanie'], [14, 'lavender hoodie'], [30, 'mystery drop']];
  const unlocks = document.getElementById('unlocks');
  REWARDS.forEach((r) => { const d = document.createElement('div'); d.className = 'unlock'; d.innerHTML = '<b>' + r[0] + '</b><span>' + r[1] + '</span>'; unlocks.appendChild(d); });
  let streak = 2;
  const cv = document.getElementById('confetti'), cx = cv.getContext('2d');
  let bits = [];
  function burst() {
    const r = cv.getBoundingClientRect(); cv.width = r.width; cv.height = r.height;
    const accent = getComputedStyle(root).getPropertyValue('--accent').trim();
    const colors = [accent, '#B69CFF', '#FFD27A', '#FF8A9B', '#FFFFFF'];
    for (let i = 0; i < 90; i++) bits.push({ x: r.width * 0.3 + Math.random() * r.width * 0.2, y: r.height * 0.5, vx: (Math.random() - 0.5) * 9, vy: -Math.random() * 11 - 4, w: 6 + Math.random() * 6, h: 8 + Math.random() * 8, a: Math.random() * 6, c: colors[i % colors.length], life: 1 });
    if (bits.length === 90) requestAnimationFrame(tick);
  }
  function tick() {
    cx.clearRect(0, 0, cv.width, cv.height);
    bits.forEach((b) => { b.vy += 0.32; b.x += b.vx; b.y += b.vy; b.a += 0.15; b.life -= 0.012; cx.save(); cx.globalAlpha = Math.max(0, b.life); cx.translate(b.x, b.y); cx.rotate(b.a); cx.fillStyle = b.c; cx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h); cx.restore(); });
    bits = bits.filter((b) => b.life > 0 && b.y < cv.height + 40);
    if (bits.length) requestAnimationFrame(tick); else cx.clearRect(0, 0, cv.width, cv.height);
  }
  function renderStreak(celebrate) {
    document.getElementById('streakNum').textContent = streak;
    unlocks.querySelectorAll('.unlock').forEach((d, i) => d.classList.toggle('on', streak >= REWARDS[i][0]));
    if (celebrate) burst();
  }
  document.getElementById('addDay').addEventListener('click', () => {
    streak += streak < 7 ? 1 : streak < 14 ? 7 : 16;
    if (streak > 30) streak = 30;
    renderStreak(REWARDS.some((r) => r[0] === streak));
  });
  document.getElementById('resetDay').addEventListener('click', () => { streak = 2; renderStreak(false); });
  renderStreak(false);

  /* Day */
  const DAY = [
    ['7:45', 'wake up', '“good morning. coffee first, CS at 10:10.”'],
    ['9:55', 'between classes', '“ECE 2300 in 15 min, Phillips 101. leave by 12:20.”'],
    ['1:50', 'free window', '“2:00 to 4:45 is open. drop the lab report there?”'],
    ['3:10', 'stuck on a problem', '“let me draw it. stop me anytime.”'],
    ['9:30', 'winding down', '“3 things done today. tomorrow starts at 10:10.”', true],
    ['2:07', 'still up', '“it’s 2am fr. one more thing then sleep, deal?”', true]
  ];
  const dayEl = document.getElementById('day');
  DAY.forEach((d) => {
    const m = document.createElement('div'); m.className = 'moment' + (d[3] ? ' night' : '');
    m.innerHTML = '<time>' + d[0] + '</time><small>' + d[1] + '</small><span>' + d[2] + '</span>';
    dayEl.appendChild(m);
  });

  /* Join */
  document.getElementById('joinForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const email = document.getElementById('email');
    const note = document.getElementById('joinNote');
    if (!email.value || !email.checkValidity()) { note.textContent = 'Enter your school email, like you@school.edu.'; email.focus(); return; }
    /* Set data-endpoint on the form once the waitlist API exists. Success shows only when the server confirms it saved. */
    const endpoint = e.target.dataset.endpoint;
    if (!endpoint) { note.textContent = 'Early-access sign-ups open soon. Check back here.'; return; }
    const btn = e.target.querySelector('button');
    btn.disabled = true; note.textContent = 'Adding you…';
    fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.value.trim(), source: 'landing' }) })
      .then((r) => {
        if (r.status === 201 || r.status === 200) { note.textContent = 'You’re on the list. We’ll email you when your school opens.'; e.target.reset(); }
        else if (r.status === 409) { note.textContent = 'You’re already on the list.'; }
        else if (r.status === 429) { note.textContent = 'Too many tries. Give it a minute.'; }
        else { note.textContent = 'That didn’t go through. Try again in a minute.'; }
      })
      .catch(() => { note.textContent = 'That didn’t go through. Check your connection and try again.'; })
      .finally(() => { btn.disabled = false; });
  });

  applyVibe(store.get('navox-vibe') || 'midnight');
})();
