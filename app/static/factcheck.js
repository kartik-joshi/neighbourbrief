// "Check my numbers" in the browser, for the static public site (no server). A port of
// app/verify/factcheck.py working on a neighbourhood's published numbers (data.json); kept in step
// with the Python version by scripts/js_parity_check.py.
window.NBFC = (function () {
  const CATEGORY_WORDS = {
    'Assault': /assault/,
    'Break and Enter': /break[- ]?ins?|break and enters?|burglar\w*/,
    'Auto Theft': /stolen (?:vehicles?|cars?)|auto thefts?|car thefts?|vehicle thefts?|cars? stolen/,
    'Theft from Motor Vehicle': /thefts? from (?:motor )?vehicles?|thefts? from cars?|car break[- ]?ins?/,
    'Robbery': /robber\w*/,
    'Theft Over': /thefts? over/,
    'Bike Theft': /bike thefts?|bicycle thefts?|stolen bikes?/,
    'Shooting': /shootings?/,
    'Homicide': /homicides?|murders?/,
    'Collisions': /collisions?|crash\w*|(?:traffic|car|road) accidents?|road safety|pedestrians?|cyclists?/,
  };
  const UP = /\b(up|increase[sd]?|increasing|rise|rose|risen|higher|more|jump\w*|climb\w*)\b/i;
  const DOWN = /\b(down|decrease[sd]?|decreasing|drop\w*|fell|fallen|fall|lower|fewer|less|declin\w*)\b/i;
  const MONTHS = '(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\.?';
  const SKIP = [
    ['dollar amount', /\$\s?\d[\d,]*(?:\.\d+)?/g],
    ['phone', /\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b/g],
    ['time', /\b\d{1,2}(?::\d{2})?\s?(?:a\.?m\.?|p\.?m\.?)/gi],
    ['date', new RegExp('\\b' + MONTHS + '\\s+\\d{1,2}(?:st|nd|rd|th)?\\b|\\b\\d{1,2}(?:st|nd|rd|th)?\\s+' + MONTHS, 'gi')],
    ['address', /\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'.-]*\s){1,3}(?:Street|St|Avenue|Ave|Av|Road|Rd|Drive|Dr|Boulevard|Blvd|Crescent|Cres|Court|Ct|Lane|Way|Place|Pl|Circle|Trail|Square)\b\.?/g],
    ['year', /\b(?:19|20)\d{2}\b/g],
  ];
  const NUM = /\d+(?:[.,]\d+)*/g;

  function canonical(raw) {
    const parts = raw.split(/[.,]/), seps = raw.match(/[.,]/g) || [];
    let num;
    if (!seps.length) num = raw;
    else if (parts.slice(1).every(p => p.length === 3) && parts[0].length <= 3) num = parts.join('');
    else if (seps.length === 1) num = parts[0] + '.' + parts[1];
    else return null;
    const n = Number(num);
    if (!isFinite(n)) return null;
    return Number.isInteger(n) ? String(n) : String(n);
  }

  function factIndex(d) {
    const p = d.period, year = p.month_start.slice(0, 4), prevYear = String(Number(year) - 1), out = [];
    const r = d.road_safety;
    if (r) {
      const names = {total: 'collisions', injury: 'collisions with injuries', pedestrian: 'collisions involving a pedestrian',
                     cyclist: 'collisions involving a cyclist', fatal: 'fatal collisions'};
      for (const [k, label] of Object.entries(names)) {
        out.push({value: String(r.month[k]), key: 'Collisions', kind: 'month', text: `Traffic ${label}: ${p.month_label}`});
        out.push({value: String(r.ytd[k]), key: 'Collisions', kind: 'ytd', text: `Traffic ${label}: year to date ${year}`});
        out.push({value: String(r.ytd_prev[k]), key: 'Collisions', kind: 'prev', text: `Traffic ${label}: same period ${prevYear}`});
      }
      if (r.pct_change !== null && r.pct_change !== undefined) {
        const pc = r.pct_change;
        out.push({value: String(Math.abs(pc)), key: 'Collisions', kind: 'pct', sign: Math.sign(pc),
                  text: `Traffic collisions: ${pc > 0 ? 'up' : pc < 0 ? 'down' : 'no change'} ${Math.abs(pc)}% year to date vs ${prevYear}`});
      }
    }
    let tm = 0, ty = 0;
    for (const l of d.reported_crime) {
      tm += l.month; ty += l.ytd;
      out.push({value: String(l.month), key: l.key, kind: 'month', text: `${l.label}: reports in ${p.month_label}`});
      out.push({value: String(l.ytd), key: l.key, kind: 'ytd', text: `${l.label}: year to date ${year}`});
      if (l.ytd_prev !== null && l.ytd_prev !== undefined) out.push({value: String(l.ytd_prev), key: l.key, kind: 'prev', text: `${l.label}: same period ${prevYear}`});
      if (l.pct_change !== null && l.pct_change !== undefined) {
        const pc = l.pct_change;
        out.push({value: String(Math.abs(pc)), key: l.key, kind: 'pct', sign: Math.sign(pc),
                  text: `${l.label}: ${pc > 0 ? 'up' : pc < 0 ? 'down' : 'no change'} ${Math.abs(pc)}% year to date vs ${prevYear}`});
      }
    }
    out.push({value: String(tm), key: null, kind: 'total', text: `All categories: reports in ${p.month_label}`});
    out.push({value: String(ty), key: null, kind: 'total', text: `All categories: year to date ${year}`});
    return out;
  }

  function sentences(text) {
    const spans = []; let start = 0;
    const re = /[.!?](?=\s+[A-Z0-9"“(]|\s*$)|\n+/g; let m;
    while ((m = re.exec(text))) { spans.push([start, m.index + m[0].length]); start = m.index + m[0].length; if (!m[0].length) re.lastIndex++; }
    if (start < text.length) spans.push([start, text.length]);
    return spans;
  }

  function categoriesIn(sentence) {
    const s = sentence.toLowerCase(), found = new Set(Object.keys(CATEGORY_WORDS).filter(k => CATEGORY_WORDS[k].test(s)));
    if (found.has('Theft from Motor Vehicle') && /car break[- ]?ins?/.test(s)) found.delete('Break and Enter');
    return found;
  }

  function label(d, key) {
    if (key === 'Collisions') return 'traffic collisions';
    const l = d.reported_crime.find(x => x.key === key);
    return l ? l.label : key;
  }

  function check(text, d) {
    const index = factIndex(d), spans = [];
    for (const [kind, re] of SKIP) { re.lastIndex = 0; let m; while ((m = re.exec(text))) spans.push([m.index, m.index + m[0].length, kind]); }
    const sents = sentences(text), items = [];
    NUM.lastIndex = 0; let m;
    while ((m = NUM.exec(text))) {
      const value = canonical(m[0]);
      if (value === null) continue;
      const start = m.index, end = start + m[0].length;
      const span = spans.find(([s, e]) => s <= start && end <= e);
      if (span && items.length && items[items.length - 1]._span === span) continue;
      const [s0, s1] = sents.find(([a, b]) => a <= start && start < b) || [0, text.length];
      const sentence = text.slice(s0, s1);
      const isPct = /^\s?(%|percent|per cent)/i.test(text.slice(end, end + 9));
      const item = {start, end, text: m[0], value, sentence: sentence.trim()};
      if (span && !isPct) {
        const k = span[2];
        Object.assign(item, {verdict: 'skipped', _span: span, start: span[0], end: span[1], text: text.slice(span[0], span[1]),
                             detail: `Looks like ${'aeiou'.includes(k[0]) ? 'an' : 'a'} ${k}, not a statistic.`});
        items.push(item); continue;
      }
      const cats = categoriesIn(sentence);
      let matches = index.filter(f => f.value === value && (f.kind === 'pct') === isPct);
      if (!matches.length) {
        const close = index.filter(f => f.value === value);
        let hint = '';
        if (close.length && isPct) hint = " It matches a count, but it's written as a percentage.";
        else if (close.length) hint = " It matches a percentage, but it's written as a count.";
        Object.assign(item, {verdict: 'not_found', detail: `Not in the TPS data for ${d.neighbourhood.name}, ${d.period.month_label}.` + hint});
        items.push(item); continue;
      }
      if (cats.size) {
        const inCat = matches.filter(f => cats.has(f.key));
        if (!inCat.length) {
          Object.assign(item, {verdict: 'wrong_category', facts: matches.map(f => f.text),
                               detail: `${value} is the number for ${matches[0].text}, but this sentence is about ${[...cats].sort().map(c => label(d, c)).join(', ')}.`});
          items.push(item); continue;
        }
        matches = inCat;
      }
      const pctMatch = matches.find(f => f.kind === 'pct');
      if (pctMatch) {
        const up = UP.test(sentence), down = DOWN.test(sentence);
        if ((up && !down && pctMatch.sign < 0) || (down && !up && pctMatch.sign > 0)) {
          Object.assign(item, {verdict: 'direction', facts: [pctMatch.text],
                               detail: `The data says ${pctMatch.text}, but this sentence says the opposite direction.`});
          items.push(item); continue;
        }
      }
      Object.assign(item, {verdict: 'match', detail: matches.slice(0, 3).map(f => f.text).join(' or '), facts: matches.map(f => f.text)});
      items.push(item);
    }
    items.forEach(i => delete i._span);
    const counts = {match: 0, wrong_category: 0, direction: 0, not_found: 0, skipped: 0};
    items.forEach(i => counts[i.verdict]++);
    return {items, counts, ok: counts.wrong_category + counts.direction + counts.not_found === 0};
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }

  function render(text, result) {
    const cls = {match: 'fc-ok', wrong_category: 'fc-warn', direction: 'fc-warn', not_found: 'fc-bad', skipped: 'fc-skip'};
    let out = '', pos = 0;
    result.items.forEach((it, i) => {
      out += esc(text.slice(pos, it.start)) + `<mark class="${cls[it.verdict]}" title="${esc(it.detail)}">${esc(text.slice(it.start, it.end))}</mark>`;
      pos = it.end;
    });
    return (out + esc(text.slice(pos))).replace(/\n/g, '<br>');
  }

  function init() {
    const form = document.getElementById('jsfc-form');
    const data = document.getElementById('jsfc-data');
    if (!form || !data) return;
    const d = JSON.parse(data.textContent);
    form.addEventListener('submit', e => {
      e.preventDefault();
      const text = document.getElementById('jsfc-text').value.slice(0, 6000);
      const r = check(text, d), bad = r.counts.wrong_category + r.counts.direction + r.counts.not_found;
      const icon = {match: '✅', wrong_category: '⚠️', direction: '⚠️', not_found: '❌'};
      const word = {match: 'matches the data', wrong_category: 'wrong category', direction: 'wrong direction', not_found: 'not in the data'};
      document.getElementById('jsfc-out').innerHTML =
        `<p class="font-bold text-ink" role="status">${!r.items.length ? 'No numbers to check in this text'
          : (r.counts.skipped === r.items.length ? 'No statistics to check: only dates, times, phone numbers or addresses'
          : (r.ok ? '✅ Every number checks out' : '⚠️ ' + bad + ' number' + (bad !== 1 ? 's' : '') + ' to fix'))}</p>` +
        `<div class="mt-2 card leading-8">${render(text, r)}</div>` +
        '<ul class="mt-2 grid gap-2">' + r.items.filter(i => i.verdict !== 'skipped').map(i =>
          `<li class="card py-2 text-sm"><span aria-hidden="true">${icon[i.verdict]}</span> <strong>${esc(i.text)}</strong> <span class="text-slate-600">${word[i.verdict]}</span><br>${esc(i.detail)}</li>`).join('') + '</ul>';
    });
  }
  document.addEventListener('DOMContentLoaded', init);
  return {check, render, canonical};
})();
