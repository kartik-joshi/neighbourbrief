// NeighbourBrief TO: small vanilla JS helpers for the three screens.
window.NB = (function () {
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function initNewForm() {
    const input = $('#hood-input');
    const opts = $all('#hood-list option');
    const box = $('#hood-info');
    const err = $('#hood-error');
    let last = '';

    function idFor(name) {
      const n = name.trim().toLowerCase();
      const o = opts.find(o => o.value.toLowerCase() === n);
      return o ? o.dataset.id : null;
    }

    async function lookup() {
      const id = idFor(input.value);
      if (!id || id === last) return;
      last = id;
      err.classList.add('hidden');
      try {
        const reuseId = $('#draft-form').dataset.reuse || '';
        const r = await fetch('/api/neighbourhood/' + encodeURIComponent(id) + (reuseId ? '?reuse=' + encodeURIComponent(reuseId) : ''));
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'Could not load this neighbourhood');
        $('#hi-division').textContent = d.division || 'n/a';
        $('#hi-fresh').textContent = d.freshness || 'n/a';
        if ($('#hi-profile')) $('#hi-profile').href = '/neighbourhoods/' + encodeURIComponent(d.id);
        const nco = d.nco;
        let html = '';
        if (nco && nco.has_nco) {
          html = '<p class="text-slate-600">Neighbourhood Community Officers</p><p>' +
            nco.officers.map(o => '<strong>' + esc(o.name) + '</strong>').join(' · ') + '</p>';
        } else if (nco) {
          html = '<p class="text-slate-600">No NCO listed. The bulletin will show the division contact.</p><p><strong>' +
            esc(nco.division.name) + '</strong> · ' + esc(nco.division.phone) + '</p>';
        } else {
          html = '<p class="text-slate-600">No NCO information found for this neighbourhood.</p>';
        }
        $('#hi-nco').innerHTML = html;
        const so = $('#signoff');
        if (so) {
          so.length = 1;
          ((nco && nco.officers) || []).forEach(o => so.add(new Option(o.name, o.name)));
          $('#signoff-box').classList.toggle('hidden', so.length < 2);
        }
        box.classList.remove('hidden');
        showMap(d);  // after the box is visible, so the map knows its size
        // suggested languages: reset chips to the suggestions
        const sel = $('#tip_id');
        if (sel) {  // suggest tips that match this month's categories first
          const cats = new Set(d.categories || []);
          const opts = [...sel.options].slice(1);
          opts.forEach(o => { o.textContent = o.textContent.replace(/^★ /, ''); });
          const match = opts.filter(o => (o.dataset.cats || '').split('|').some(c => cats.has(c)));
          match.reverse().forEach(o => { o.textContent = '★ ' + o.textContent; sel.insertBefore(o, sel.options[1]); });
        }
        existing = d.existing || [];
        checkDuplicate();
        const rs = $('#residents');
        if (rs) {  // what residents asked for (feedback on bulletins and profiles), last 60 days
          const s = d.residents || {count: 0};
          rs.classList.toggle('hidden', !s.count);
          if (s.count) {
            $('#rs-topics').textContent = s.topics.map(t => t[0] + ' (' + t[1] + ')').join(', ') || 'no topic picked';
            $('#rs-count').textContent = s.count + (s.count === 1 ? ' answer' : ' answers');
            $('#rs-comments').innerHTML = s.comments.map(c => '<li><span class="text-slate-500">' + esc(c.date) + '</span> ' +
              '<span lang="' + esc(c.lang) + '" dir="auto">' + esc(c.comment) + '</span></li>').join('');
          }
        }
        const prev = d.previous;
        const fu = $('#followup-box');
        if (fu) {  // "You said, we did": follow up on the last bulletin's priority
          const has = !!(prev && prev.priority);
          fu.classList.toggle('hidden', !has);
          $('#prev_priority').value = has ? prev.priority : '';
          $('#fu-prev').textContent = has ? prev.priority : '';
          $('#fu-month').textContent = has ? prev.month : '';
        }
        const reuse = $('#reuse');
        if (prev && (prev.priority || prev.action_taken || prev.meeting_note)) {
          $('#reuse-month').textContent = prev.month;
          reuse.classList.remove('hidden');
          $('#reuse-btn').onclick = () => {
            $('#priority').value = prev.priority || '';
            $('#action_taken').value = prev.action_taken || '';
            $('#meeting_note').value = prev.meeting_note || '';
            if ($('#signoff') && prev.signoff) $('#signoff').value = prev.signoff;
            if ($('#tip_id')) { $('#tip_id').value = prev.tip_id || ''; $('#tip_custom').value = prev.tip_id ? '' : (prev.tip || ''); }
            const pl = new Set(prev.languages || []);
            $all('#lang-chips input[type=checkbox]').forEach(cb => { if (!cb.disabled) cb.checked = pl.has(cb.value); });
            $('#reuse-btn').textContent = 'Reused ✓ Edit anything that changed.';
          };
          if (reuseId && prev.id === reuseId) { $('#reuse-btn').click(); $('#draft-form').dataset.reuse = ''; }
        } else {
          reuse.classList.add('hidden');
        }
        const langs = new Set(d.languages || []);
        const form = $('#draft-form');
        if (form.dataset.keepLangs) { form.dataset.keepLangs = ''; }  // came back with an error: keep the officer's choice
        else $all('#lang-chips input[type=checkbox]').forEach(cb => { if (!cb.disabled) cb.checked = langs.has(cb.value); });
        const notes = (d.language_notes || []).map(l => l.language + ' ' + l.share + '%').join(', ')
          + ((d.other_languages || []).length ? '. Also spoken here (no translation yet): ' +
             d.other_languages.map(l => l.language + ' ' + l.share + '%').join(', ') : '');
        $('#lang-note').textContent = notes ? 'Suggested for ' + d.name + ' (2021 Census mother tongue): ' + notes + '.'
                                            : 'No language suggestions for ' + d.name + '. Choose any languages you need.';
      } catch (e) {
        box.classList.add('hidden');
        err.textContent = e.message;
        err.classList.remove('hidden');
      }
    }
    let existing = [];
    function checkDuplicate() {
      const box = $('#dup-warning');
      const month = $('#month') ? $('#month').value : '';
      const same = existing.filter(x => !month || x.month === month);
      if (!same.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
      box.innerHTML = '<strong>You already have ' + (same.length === 1 ? 'a bulletin' : same.length + ' bulletins') +
        ' for ' + esc(input.value) + ', ' + esc(same[0].month_label) + '.</strong> ' +
        same.map(x => '<a class="underline font-semibold" href="/publish/' + encodeURIComponent(x.id) + '">Open the ' + esc(x.status) + ' one</a>').join(' · ') +
        '<br><span class="text-amber-900">You can still draft another one below.</span>';
      box.classList.remove('hidden');
    }
    if ($('#month')) $('#month').addEventListener('change', checkDuplicate);
    input.addEventListener('change', lookup);
    input.addEventListener('input', () => { if (idFor(input.value)) lookup(); });
    if (input.value) lookup();

    $('#draft-form').addEventListener('submit', () => {
      const b = $('#draft-btn');
      b.disabled = true; b.textContent = 'Drafting…';
      $('#draft-wait').classList.remove('hidden');
      const t0 = Date.now();
      setInterval(() => {
        const s = Math.round((Date.now() - t0) / 1000);
        $('#draft-elapsed').textContent = '(' + s + ' s)';
        if (s >= 90) $('#draft-slow').classList.remove('hidden');
      }, 1000);
    });
  }

  // Locator map: boundary + community service pins only (never incident locations).
  let map = null;
  function showMap(d) {
    const box = $('#hood-map-box');
    if (!box) return;
    const m = d.map;
    if (!m) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    const el = $('#hood-map');
    const pins = m.services || [];
    $('#hood-map-pins').textContent = pins.length ? 'Numbered pins: ' + pins.map((s, i) => (i + 1) + ' ' + s.name.split(':')[0].replace(/…$/, '')).join(', ') + '.' : '';
    el.setAttribute('aria-label', 'Map of ' + d.name + ' boundary' + (pins.length ? ' with ' + pins.length + ' community services' : ''));
    if (map) { map.remove(); map = null; }
    if (m.tiles && window.L) {
      el.innerHTML = '';
      map = L.map(el, {scrollWheelZoom: false, attributionControl: true});
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18, referrerPolicy: 'strict-origin-when-cross-origin',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
      const shape = L.geoJSON(m.geometry, {style: {color: '#0c2242', weight: 3, fillColor: '#1f4e8c', fillOpacity: 0.08}}).addTo(map);
      pins.forEach((s, i) => L.marker([s.lat, s.lon], {title: s.name, alt: s.name,
        icon: L.divIcon({className: '', html: '<span class="map-pin">' + (i + 1) + '</span>', iconSize: [24, 24], iconAnchor: [12, 12]})})
        .bindPopup('<strong>' + esc(s.name) + '</strong><br>' + esc(s.category) + '<br>' + esc(s.address)).addTo(map));
      map.invalidateSize();
      map.fitBounds(shape.getBounds(), {padding: [10, 10]});
    } else if (m.outline) {
      el.innerHTML = '<svg viewBox="' + esc(m.outline.viewBox) + '" aria-hidden="true" preserveAspectRatio="xMidYMid meet">' +
        '<path d="' + esc(m.outline.d) + '" fill="#dbe6f3" stroke="#0c2242" stroke-width="2" fill-rule="evenodd"/>' +
        (m.outline.points || []).map((p, i) => '<g><circle cx="' + p[0] + '" cy="' + p[1] + '" r="7" fill="#1f4e8c" stroke="#fff" stroke-width="1.5"/>' +
          '<text x="' + p[0] + '" y="' + (p[1] + 3.5) + '" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">' + (i + 1) + '</text></g>').join('') +
        '</svg>';
    }
  }

  // "Use my location": the browser finds the neighbourhood itself; the position is never sent anywhere.
  function pointIn(ring, x, y) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function findHood(fc, lon, lat) {
    for (const f of fc.features) {
      const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
      if (polys.some(p => pointIn(p[0], lon, lat) && !p.slice(1).some(h => pointIn(h, lon, lat)))) return f.properties;
    }
    return null;
  }
  function initLocate() {
    $all('[data-locate]').forEach(btn => {
      const note = document.getElementById(btn.getAttribute('aria-describedby'));
      const say = t => { if (note) note.textContent = t; };
      if (!navigator.geolocation) { btn.hidden = true; return; }
      btn.addEventListener('click', () => {
        say('Finding your neighbourhood…');
        navigator.geolocation.getCurrentPosition(async pos => {
          try {
            const r = await fetch('/api/boundaries.geojson');
            const fc = await r.json();
            if (!r.ok) throw new Error(fc.error || 'Boundaries unavailable');
            const hit = findHood(fc, pos.coords.longitude, pos.coords.latitude);
            if (!hit) { say("That location isn't inside a City of Toronto neighbourhood."); return; }
            const input = document.getElementById(btn.dataset.locate);
            const listed = !input.list || [...input.list.options].some(o => o.value.toLowerCase() === hit.name.toLowerCase());
            if (!listed) { say('You are in ' + hit.name + ', which is not saved for offline use.'); return; }
            say('You are in ' + hit.name + '.');
            if (btn.dataset.go) { location.href = btn.dataset.go + encodeURIComponent(hit.name); return; }
            input.value = hit.name;
            input.dispatchEvent(new Event('change'));
          } catch (e) { say('Could not find your neighbourhood: ' + e.message); }
        }, () => say('Location is off or was not allowed. Type your neighbourhood instead.'), {timeout: 10000, maximumAge: 0});
      });
    });
  }

  function initReview(id, running) {
    const form = $('#translate-form');
    const box = $('#progress');
    form.addEventListener('submit', () => {
      box.classList.remove('hidden');
      form.querySelector('button[type=submit]').disabled = true;
    });
    if (!running && !/translating=1/.test(location.search)) return;
    box.classList.remove('hidden');
    const icons = { pending: '○', running: '', done: '✅', failed: '⚠️' };
    async function poll() {
      try {
        const r = await fetch('/api/bulletins/' + encodeURIComponent(id) + '/progress');
        const d = await r.json();
        $('#pg-engine').textContent = d.engine || '';
        $('#pg-langs').innerHTML = d.langs.map(l =>
          '<li class="rounded-lg border border-slate-200 bg-white px-3 py-2 flex items-center gap-2">' +
          (l.status === 'running' ? '<span class="spinner"></span>' : '<span>' + (icons[l.status] || '') + '</span>') +
          '<span class="font-semibold">' + esc(l.name) + '</span>' +
          '<span class="ms-auto text-slate-500">' + (l.seconds != null ? esc(l.seconds) + ' s' : esc(l.status)) + '</span></li>').join('');
        if (d.status === 'done') { location.href = '/publish/' + encodeURIComponent(id); return; }
        if (d.status === 'stalled') {
          $('#pg-langs').insertAdjacentHTML('afterend', '<p class="mt-3 text-sm text-red-800">The translation stopped (the app may have restarted). Press “Translate &amp; publish” again.</p>');
          return;
        }
      } catch (e) { /* keep polling */ }
      setTimeout(poll, 1500);
    }
    poll();
  }

  function initPublish() {
    const tabs = $all('[role=tab]');
    function show(code, focus) {
      tabs.forEach(t => {
        const on = t.dataset.tab === code;
        t.classList.toggle('tab-active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;  // roving tabindex (WAI-ARIA tabs pattern)
        if (on && focus) t.focus();
      });
      $all('[data-panel]').forEach(p => p.classList.toggle('hidden', p.dataset.panel !== code));
      const u = new URL(location.href); u.searchParams.set('lang', code); history.replaceState(null, '', u);
    }
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => show(t.dataset.tab));
      t.addEventListener('keydown', e => {
        const keys = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
        if (!(e.key in keys)) return;
        e.preventDefault();
        const j = (keys[e.key] + tabs.length) % tabs.length;
        show(tabs[j].dataset.tab, true);
      });
    });
    $all('[data-print]').forEach(b => b.addEventListener('click', () => window.print()));
    $all('[data-copy]').forEach(b => b.addEventListener('click', async () => {
      const code = b.dataset.copy;
      const ta = $('#' + CSS.escape(b.dataset.copySrc || ('wa-' + code)));
      const text = ta.value;
      try { await navigator.clipboard.writeText(text); }
      catch (e) { ta.classList.remove('sr-only'); ta.select(); document.execCommand('copy'); ta.classList.add('sr-only'); }
      const ok = $('[data-copied="' + code + '"]'); ok.classList.remove('hidden'); setTimeout(() => ok.classList.add('hidden'), 2000);
    }));
    const cb = $('#manual-ok');
    if (cb) cb.addEventListener('change', () => { $('#publish-btn').disabled = !cb.checked; });
  }

  // Print safety net: if a (long) bulletin is taller than one Letter page, scale it down to fit.
  const PAGE_PX = (11 * 96) - (28 / 25.4 * 96) - 12;  // Letter height minus 2 × 14 mm margins, with headroom
  const PRINT_W = (8.5 * 96) - (28 / 25.4 * 96);      // Letter width minus the same margins
  function fitForPrint() {
    const printing = window.matchMedia('print').matches && !document.body.classList.contains('large-print');
    $all('.bulletin').forEach(b => {
      b.style.zoom = '';
      if (!printing || !b.offsetParent) return;
      // Measure at the printed width: on a wide screen the bulletin is wider, so shorter, than on paper
      // (it once looked like 848 px and printed on two pages).
      const w = b.style.width;
      b.style.width = PRINT_W + 'px';
      const h = b.getBoundingClientRect().height;
      b.style.width = w;
      if (h > PAGE_PX) b.style.zoom = String(Math.max(0.7, PAGE_PX / h));
    });
  }
  window.addEventListener('beforeprint', fitForPrint);
  window.addEventListener('afterprint', () => $all('.bulletin').forEach(b => { b.style.zoom = ''; }));
  window.matchMedia('print').addEventListener('change', fitForPrint);

  function initHistory() {
    const q = $('#h-search');
    if (!q) return;
    const items = $all('#h-list > li');
    let status = 'all';
    function apply() {
      const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      items.forEach(li => {
        const ok = (status === 'all' || li.dataset.status === status) && words.every(w => li.dataset.search.includes(w));
        li.classList.toggle('hidden', !ok);
        if (ok) shown++;
      });
      $('#h-count').textContent = shown === items.length ? '' : shown + ' of ' + items.length + ' shown';
      $('#h-empty').classList.toggle('hidden', shown > 0);
    }
    q.addEventListener('input', apply);
    $all('[data-filter]').forEach(b => b.addEventListener('click', () => {
      status = b.dataset.filter;
      $all('[data-filter]').forEach(x => { const on = x === b; x.classList.toggle('chip-filter-on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      apply();
    }));
    $all('form[data-confirm]').forEach(f => f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
  }

  // Listen: read the bulletin aloud with the device's own voice for that language (no server, no
  // recording). Hidden when the device has no voice for the language, rather than reading it wrong.
  const VOICE_LANGS = {'zh-Hant': ['zh-HK', 'zh-TW', 'yue'], 'zh-Hans': ['zh-CN', 'cmn'], 'fil': ['fil', 'tl'],
                       'pt-pt': ['pt-PT', 'pt'], 'pa': ['pa-IN', 'pa'], 'en': ['en-CA', 'en']};
  function voiceFor(code) {
    const want = (VOICE_LANGS[code] || [code]).map(x => x.toLowerCase());
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    for (const w of want) {
      const v = voices.find(v => v.lang.toLowerCase().replace('_', '-') === w) ||
                voices.find(v => v.lang.toLowerCase().replace('_', '-').startsWith(w + '-'));
      if (v) return v;
    }
    return null;
  }
  function bulletinText(code) {
    const root = document.querySelector('[data-panel="' + code + '"] .bulletin') || document.querySelector('.bulletin');
    if (!root) return '';
    return ['.b-headline', '.b-summary', '.b-line-text', '.b-officer p', '.b-closing']
      .flatMap(sel => [...root.querySelectorAll(sel)].map(e => e.innerText.trim())).filter(Boolean)
      .map(t => /[.!?؟۔।。]$/.test(t) ? t : t + '.').join(' ');
  }
  function initListen() {
    const buttons = $all('[data-listen]');
    if (!buttons.length || !window.speechSynthesis) return;
    function setup() {
      buttons.forEach(btn => {
        const code = btn.dataset.listen;
        btn.hidden = !voiceFor(code);
        if (btn.dataset.ready) return;
        btn.dataset.ready = '1';
        btn.addEventListener('click', () => {
          if (speechSynthesis.speaking) { speechSynthesis.cancel(); btn.textContent = '🔊 Listen'; return; }
          const u = new SpeechSynthesisUtterance(bulletinText(code));
          u.voice = voiceFor(code); u.lang = u.voice ? u.voice.lang : code; u.rate = 0.95;
          u.onend = u.onerror = () => { btn.textContent = '🔊 Listen'; };
          speechSynthesis.speak(u);
          btn.textContent = '■ Stop';
        });
      });
    }
    setup();
    speechSynthesis.addEventListener('voiceschanged', setup);
  }
  document.addEventListener('DOMContentLoaded', initListen);

  // Image to share: a 1080×1080 card of the bulletin's key lines in this language, drawn in the
  // browser (nothing is uploaded), with the "checked", AI-translation and not-affiliated notices.
  function wrapLines(ctx, text, maxW) {
    const out = []; let line = '';
    const tokens = /\s/.test(text) ? text.split(/(\s+)/) : [...text];
    for (const tok of tokens) {
      const pieces = ctx.measureText(tok).width > maxW ? [...tok] : [tok];
      for (const p of pieces) {
        const test = line + p;
        if (ctx.measureText(test).width > maxW && line.trim()) { out.push(line.trim()); line = p.trimStart(); }
        else line = test;
      }
    }
    if (line.trim()) out.push(line.trim());
    return out;
  }
  function drawCard(btn) {
    const code = btn.dataset.card, rtl = !!btn.dataset.rtl;
    const b = document.querySelector('[data-panel="' + code + '"] .bulletin');
    if (!b) return null;
    const cs = getComputedStyle(b), font = cs.fontFamily;
    const ratio = Math.min(2.1, Math.max(1.35, (parseFloat(cs.lineHeight) / parseFloat(cs.fontSize)) || 1.45));  // Urdu needs ~2
    const W = 1080, H = 1080, P = 72, maxW = W - 2 * P;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    ctx.direction = rtl ? 'rtl' : 'ltr'; ctx.textAlign = rtl ? 'right' : 'left'; ctx.textBaseline = 'top';
    const x = rtl ? W - P : P;
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#0c2242'; ctx.fillRect(0, 0, W, 250);
    ctx.fillStyle = '#f2b705'; ctx.fillRect(0, 250, W, 8);
    let y = 60;
    const text = (t, size, weight, color, lh, maxLines) => {
      ctx.font = weight + ' ' + size + 'px ' + font; ctx.fillStyle = color;
      const lines = wrapLines(ctx, t, maxW).slice(0, maxLines || 99);
      lines.forEach(l => { ctx.fillText(l, x, y); y += size * lh; });
    };
    text(btn.dataset.label, 34, '600', '#d9e4f5', 1.4, 1);
    text((b.querySelector('.b-title') || {}).innerText || '', 54, '700', '#ffffff', 1.3, 2);
    y = Math.max(y, 290);
    text((b.querySelector('.b-headline') || {}).innerText || '', 44, '700', '#0c2242', Math.max(1.35, ratio * 0.9), 3);
    y += 14;
    const lines = [...b.querySelectorAll('.b-line-text')].map(e => e.innerText.trim()).filter(Boolean);
    for (const l of lines) {
      if (y > 760) break;
      ctx.font = '400 34px ' + font;
      const wrapped = wrapLines(ctx, l, maxW - 40).slice(0, 3);
      ctx.fillStyle = '#1f4e8c'; ctx.fillText('•', x, y);
      ctx.fillStyle = '#0f172a';
      wrapped.forEach(w => { ctx.fillText(w, rtl ? x - 40 : x + 40, y); y += 34 * ratio; });
      y += 10;
    }
    ctx.fillStyle = '#eef3fb'; ctx.fillRect(0, 860, W, 220);
    y = 885;
    text('✓ ' + btn.dataset.verified, 28, '700', '#0b5a37', 1.35, 2);
    if (btn.dataset.notice) text(btn.dataset.notice, 24, '400', '#334155', 1.35, 2);
    ctx.direction = 'ltr'; ctx.textAlign = 'left';
    ctx.font = '400 22px "Noto Sans", system-ui, sans-serif'; ctx.fillStyle = '#475569';
    ctx.fillText('Source: Toronto Police Service open data. Not affiliated with or endorsed by TPS.', P, H - 50);
    return c;
  }
  function initCards() {
    $all('[data-card]').forEach(btn => btn.addEventListener('click', async () => {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      const c = drawCard(btn);
      if (!c) return;
      c.toBlob(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob); a.download = btn.dataset.file;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      }, 'image/png');
    }));
  }
  document.addEventListener('DOMContentLoaded', initCards);

  return { initNewForm, initLocate, showMap, initListen, drawCard, initReview, initPublish, initHistory, fitForPrint };
})();
