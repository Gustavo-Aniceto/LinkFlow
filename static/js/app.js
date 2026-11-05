/* LinkFlow — comportamento do front-end (sem dependências externas). */
const LinkFlow = (() => {
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = { purple: '#8b5cf6', blue: '#38bdf8', amber: '#fbbf24', green: '#34d399', red: '#f87171', indigo: '#818cf8', gray: '#9a9ab5' };
  const STATUS_COLOR = { found: COLORS.gray, analyzed: COLORS.blue, ready: COLORS.purple, applied: COLORS.indigo, interview: COLORS.amber, approved: COLORS.green, rejected: COLORS.red };

  function toast(msg) {
    const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => t.remove(), 3200);
  }

  /* ---------------- gráficos SVG ---------------- */
  const svgEl = (name, attrs = {}, text) => {
    const e = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const niceMax = (m) => Math.max(4, Math.ceil(m / 4) * 4);

  function weeklyChart(el, data) {
    const W = 480, H = 250, L = 30, R = 12, T = 14, B = 30;
    const names = Object.keys(data.series), cols = [COLORS.purple, COLORS.blue, COLORS.amber];
    const max = niceMax(Math.max(0, ...names.flatMap(n => data.series[n])));
    const x = i => L + (i * (W - L - R)) / (data.labels.length - 1);
    const y = v => T + (H - T - B) * (1 - v / max);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}` });
    for (let g = 0; g <= 4; g++) {
      const v = (max * g) / 4;
      svg.append(svgEl('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), style: 'stroke:var(--border)', 'stroke-width': 1 }),
        svgEl('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v)));
    }
    data.labels.forEach((l, i) => svg.append(svgEl('text', { x: x(i), y: H - 8, 'text-anchor': 'middle' }, l)));
    names.forEach((n, k) => {
      const pts = data.series[n].map((v, i) => [x(i), y(v)]);
      if (k === 0) {
        const area = `M${pts[0][0]},${y(0)} ` + pts.map(p => `L${p[0]},${p[1]}`).join(' ') + ` L${pts.at(-1)[0]},${y(0)} Z`;
        svg.append(svgEl('path', { d: area, fill: cols[0], opacity: 0.12 }));
      }
      svg.append(svgEl('polyline', { points: pts.map(p => p.join(',')).join(' '), fill: 'none', stroke: cols[k], 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      pts.forEach((p, i) => {
        const c = svgEl('circle', { cx: p[0], cy: p[1], r: 4, style: 'fill:var(--panel)', stroke: cols[k], 'stroke-width': 2 });
        c.append(svgEl('title', {}, `${n} — semana de ${data.labels[i]}: ${data.series[n][i]}`));
        svg.append(c);
      });
    });
    el.replaceChildren(svg);
    const lg = $('#legend-weekly');
    if (lg) lg.replaceChildren(...names.map((n, k) => { const s = document.createElement('span'); s.style.setProperty('--c', cols[k]); s.textContent = n; return s; }));
  }

  function funnelChart(el, data) {
    const rowH = 34, W = 440, L = 140, R = 34, H = rowH * data.labels.length + 8;
    const max = niceMax(Math.max(0, ...data.values)) || 4;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}` });
    data.labels.forEach((lab, i) => {
      const yy = 4 + i * rowH, w = ((W - L - R) * data.values[i]) / max;
      svg.append(svgEl('text', { x: L - 10, y: yy + 19, 'text-anchor': 'end' }, lab),
        svgEl('rect', { x: L, y: yy + 5, width: W - L - R, height: 18, rx: 9, style: 'fill:var(--panel2)' }));
      if (data.values[i] > 0) svg.append(svgEl('rect', { x: L, y: yy + 5, width: Math.max(w, 18), height: 18, rx: 9, fill: STATUS_COLOR[data.keys[i]] }));
      svg.append(svgEl('text', { x: W - R + 10, y: yy + 19, style: 'fill:var(--text);font-weight:700' }, data.values[i]));
    });
    el.replaceChildren(svg);
  }

  function dashboardCharts(d) {
    const w = $('#chart-weekly'), f = $('#chart-funnel');
    if (w) weeklyChart(w, d.weekly);
    if (f) funnelChart(f, d.funnel);
  }

  /* ---------------- status via fetch ---------------- */
  async function postStatus(jobId, status) {
    const body = new URLSearchParams({ status, _csrf: csrf() });
    const r = await fetch(`/jobs/${jobId}/status`, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'Não foi possível alterar o status.');
    return j;
  }

  function initStatusSelects() {
    $$('.js-status-select').forEach(sel => {
      const form = sel.closest('form');
      const id = form.action.match(/\/jobs\/(\d+)\/status/)[1];
      sel.dataset.prev = sel.value;
      sel.addEventListener('change', async () => {
        if (form.closest('#board')) return; // o quadro trata sozinho (move o cartão)
        try {
          const r = await postStatus(id, sel.value);
          sel.dataset.prev = sel.value; toast(`Status: ${r.label}`);
          if (location.pathname.startsWith('/jobs/' + id)) location.reload();
        } catch (e) { sel.value = sel.dataset.prev; toast(e.message); }
      });
    });
  }

  /* ---------------- quadro (drag & drop + seletor) ---------------- */
  function initBoard() {
    const board = $('#board'); if (!board) return;
    const refresh = () => $$('.column', board).forEach(c => {
      const n = $$('.kcard', c).length; $('[data-count]', c).textContent = n; $('.js-col-empty', c).hidden = n > 0;
    });
    async function move(card, col) {
      const from = card.parentElement, sel = $('.js-status-select', card), prev = sel.dataset.prev;
      const target = $('[data-drop]', col); target.insertBefore(card, $('.js-col-empty', target));
      sel.value = col.dataset.status; refresh();
      try { const r = await postStatus(card.dataset.job, col.dataset.status); sel.dataset.prev = col.dataset.status; toast(`Movida para “${r.label}”`); }
      catch (e) { from.insertBefore(card, $('.js-col-empty', from)); sel.value = prev; refresh(); toast(e.message); }
    }
    $$('.kcard', board).forEach(card => {
      card.addEventListener('dragstart', e => { card.classList.add('dragging'); e.dataTransfer.setData('text/plain', card.dataset.job); e.dataTransfer.effectAllowed = 'move'; });
      card.addEventListener('dragend', () => card.classList.remove('dragging'));
      $('.js-status-select', card).addEventListener('change', e => {
        const col = $(`.column[data-status="${e.target.value}"]`, board); if (col) move(card, col);
      });
    });
    $$('.column', board).forEach(col => {
      const zone = $('[data-drop]', col);
      zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('over'); });
      zone.addEventListener('dragleave', () => zone.classList.remove('over'));
      zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('over');
        const card = $(`.kcard[data-job="${e.dataTransfer.getData('text/plain')}"]`, board);
        if (card && card.closest('.column') !== col) move(card, col);
      });
    });
  }

  /* ---------------- perfil: blocos dinâmicos ---------------- */
  function initProfile() {
    $$('[data-add]').forEach(btn => btn.addEventListener('click', () => {
      const k = btn.dataset.add, tpl = $('#tpl-' + k), list = $('#list-' + k);
      list.append(tpl.content.cloneNode(true));
      const empty = $(`.js-empty[data-for="${k}"]`); if (empty) empty.hidden = true;
      list.lastElementChild.querySelector('input')?.focus();
    }));
    document.addEventListener('click', e => {
      const rm = e.target.closest('[data-remove]'); if (!rm) return;
      const list = rm.closest('.blocks'); rm.closest('[data-block]').remove();
      const k = list.id.replace('list-', ''); const empty = $(`.js-empty[data-for="${k}"]`);
      if (empty) empty.hidden = list.children.length > 0;
    });
  }

  /* ---------------- geral ---------------- */
  function busyForms() {
    document.querySelectorAll('form[data-busy]').forEach(function (f) {
      f.addEventListener('submit', function () {
        var b = f.querySelector('button.btn-primary');
        if (b) { b.disabled = true; b.textContent = 'Lendo a vaga e montando o currículo…'; }
      });
    });
  }

  function init() {
    busyForms();
    $('#menu-btn')?.addEventListener('click', () => document.body.classList.toggle('menu-open'));
    $('#scrim')?.addEventListener('click', () => document.body.classList.remove('menu-open'));
    document.addEventListener('click', e => { if (e.target.closest('.flash-x')) e.target.closest('.flash').remove(); });
    $$('form[data-confirm]').forEach(f => f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
    $$('form').forEach(f => f.addEventListener('submit', e => {
      const b = e.submitter || $('button[data-loading]', f);
      if (b && b.dataset.loading && !e.defaultPrevented) setTimeout(() => { b.disabled = true; b.textContent = b.dataset.loading; }, 0);
    }));
    const file = $('#resume-file'), dz = $('#dropzone');
    if (file) {
      file.addEventListener('change', () => { $('#file-name').textContent = file.files[0] ? file.files[0].name : 'Máximo 10 MB'; });
      ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, () => dz.classList.add('drag')));
      ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, () => dz.classList.remove('drag')));
    }
    $$('[data-theme-toggle]').forEach(b => b.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('lf-theme', next); } catch (e) { /* ignora */ }
    }));
    initStatusSelects(); initBoard(); initProfile();
  }
  document.addEventListener('DOMContentLoaded', init);
  return { dashboardCharts };
})();
