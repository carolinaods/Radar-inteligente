/* Radar Inteligente — front-end estático. Lê data/*.json; nada é gerado no navegador além de filtros e contagens. */
(() => {
  'use strict';
  const TZ = 'America/Sao_Paulo';
  const LEVELS = ['Crítica', 'Alta', 'Média', 'Informativa'];
  const CAT_LABEL = { sap: 'SAP', reforma_tributaria: 'Reforma Tributária', clientes: 'Clientes', mercado: 'Mercado', seguranca: 'Segurança' };
  const state = { items: [], runs: [], config: { clients: [], sap_modules: [], nature: [], sources: [] }, view: 'cards', levels: new Set(), clients: new Set(), histLimit: 30 };
  const $ = (id) => document.getElementById(id);

  // ---------- segurança: nunca injetar HTML vindo das fontes ----------
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');

  // ---------- datas (padrão brasileiro, fuso de São Paulo) ----------
  const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('pt-BR', { timeZone: TZ }) : null);
  const fmtDateTime = (iso) => (iso ? new Date(iso).toLocaleString('pt-BR', { timeZone: TZ, dateStyle: 'short', timeStyle: 'short' }) : '—');
  const fmtDay = (ymd) => ymd.split('-').reverse().join('/');
  const refDate = (i) => i.published || i.first_seen;

  function nextFriday12() {
    // 12h em Brasília (UTC-3, sem horário de verão) = 15:00 UTC
    const now = new Date();
    for (let d = 0; d < 8; d++) {
      const t = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + d, 15, 0, 0));
      if (t.getUTCDay() === 5 && t > now) return t;
    }
    return null;
  }

  async function getJson(path, fallback) {
    try {
      const r = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) { return fallback; }
  }

  // ---------- filtros ----------
  function readFilters() {
    return {
      q: $('q').value.trim().toLowerCase(),
      cat: $('fCategory').value, mod: $('fModule').value, src: $('fSource').value,
      period: $('fPeriod').value, from: $('dFrom').value, to: $('dTo').value,
      nature: $('fNature').value, action: $('fAction').value,
    };
  }
  function inPeriod(i, f) {
    if (f.period === 'all') return true;
    const t = new Date(refDate(i)).getTime();
    if (f.period === 'custom') {
      if (f.from && t < new Date(f.from + 'T00:00:00-03:00').getTime()) return false;
      if (f.to && t > new Date(f.to + 'T23:59:59-03:00').getTime()) return false;
      return true;
    }
    return t >= Date.now() - Number(f.period) * 864e5;
  }
  function textOf(i) {
    return [i.title, i.summary, i.source_name, (i.clients || []).join(' '), (i.clients_unconfirmed || []).join(' '), (i.tags || []).join(' '), (i.sap_modules || []).join(' ')].join(' ').toLowerCase();
  }
  function passes(i, f, { skipPeriod = false } = {}) {
    if (!skipPeriod && !inPeriod(i, f)) return false;
    if (f.cat && !(i.categories || []).includes(f.cat)) return false;
    if (f.mod && !(i.sap_modules || []).includes(f.mod)) return false;
    if (f.src && i.source_id !== f.src && i.source_name !== f.src) return false;
    if (f.nature && !(i.nature || []).includes(f.nature)) return false;
    if (f.action && i.action_level !== f.action) return false;
    if (state.levels.size && !state.levels.has(i.relevance.level)) return false;
    if (state.clients.size) {
      const all = [...(i.clients || []), ...(i.clients_unconfirmed || [])];
      if (!all.some((c) => state.clients.has(c))) return false;
    }
    if (f.q && !textOf(i).includes(f.q)) return false;
    return true;
  }
  function sorted(list) {
    const by = $('sort').value;
    const d = (i) => new Date(refDate(i)).getTime();
    return [...list].sort(by === 'date' ? (a, b) => d(b) - d(a) : (a, b) => b.relevance.score - a.relevance.score || d(b) - d(a));
  }

  // ---------- componentes ----------
  const dateLine = (i) => (i.published ? `Publicado em ${fmtDate(i.published)}` : 'Data de publicação não confirmada');

  function card(i) {
    const lvl = i.relevance.level;
    const cats = (i.categories || []).map((c) => CAT_LABEL[c] || c);
    const clients = (i.clients || []).map((c) => `<span class="tag client">${esc(c)}</span>`).join('');
    const unc = (i.clients_unconfirmed || []).map((c) => `<span class="tag warn" title="Não foi possível confirmar a identidade da empresa">${esc(c)} · Associação não confirmada</span>`).join('');
    const mods = (i.sap_modules || []).map((m) => `<span class="tag">${esc(m)}</span>`).join('');
    const dl = (i.deadlines || []).length ? `<div class="dl">Prazo(s) citado(s) no texto: ${i.deadlines.slice(0, 3).map(fmtDay).join(', ')} — confirmar na fonte</div>` : '';
    const also = (i.also_in || []).length ? `<div class="meta">Também em: ${i.also_in.map((a) => `<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.source_name)}</a>`).join(' · ')}</div>` : '';
    const upd = i.updated_at ? `<span>Atualizado em ${fmtDate(i.updated_at)}</span>` : '';
    return `<article class="card l-${lvl}">
      <div class="badges"><span class="badge b-${lvl}">${lvl}</span><span class="tag">${esc(cats.join(' / '))}</span>${mods}${clients}${unc}</div>
      <h3><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></h3>
      <div class="meta"><span>${dateLine(i)}</span><span>${esc(i.source_name)}${i.publisher ? ' · ' + esc(i.publisher) : ''}</span><span>${esc(i.content_kind)}</span>${upd}</div>
      ${i.rt_status ? `<div><span class="tag warn">${esc(i.rt_status)}</span></div>` : ''}
      <p class="summary">${i.summary ? esc(i.summary) : '<em>Resumo não disponível na fonte; abra a publicação original.</em>'}</p>
      ${dl}
      <div class="ai"><b>Análise automática (regras) — não é determinação da fonte</b>
        <p><strong>Possível impacto:</strong> ${esc(i.impact_ai)}</p>
        <p><strong>Ação sugerida:</strong> ${esc(i.action_ai)}</p>
        <p><strong>${esc(i.action_level)}</strong> · Confiança da análise: ${esc(i.confidence)}</p>
      </div>
      <details class="why"><summary>Por que ${lvl.toLowerCase()}? (pontuação ${i.relevance.score}/100)</summary><div>${esc(i.relevance.justification)}</div></details>
      ${also}
      <div class="meta"><span>Coletado em ${fmtDateTime(i.first_seen)}</span><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">Abrir publicação original ↗</a></div>
    </article>`;
  }

  function row(i) {
    const lvl = i.relevance.level;
    const who = [...(i.clients || []), ...(i.clients_unconfirmed || []).map((c) => c + ' (não confirmado)')].join(', ');
    return `<div class="li l-${lvl}"><span class="badge b-${lvl}">${lvl}</span>
      <div><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>${who ? `<div class="meta"><span>${esc(who)}</span></div>` : ''}</div>
      <div class="meta"><span>${i.published ? fmtDate(i.published) : 'data n/c'}</span><span>${esc(i.source_name)}</span><span>${i.relevance.score}</span></div></div>`;
  }
  const render1 = (i) => (state.view === 'cards' ? card(i) : row(i));
  const wrap = (list) => `<div class="${state.view === 'cards' ? 'cards' : 'list'}">${list.map(render1).join('')}</div>`;

  function block(id, title, hint, html) {
    return `<section class="blk" id="${id}"><h2>${esc(title)}</h2>${hint ? `<p class="hint">${esc(hint)}</p>` : ''}${html}</section>`;
  }
  const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;

  function topCounts(items, getter, n = 8) {
    const m = new Map();
    items.forEach((i) => getter(i).forEach((k) => m.set(k, (m.get(k) || 0) + 1)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  }
  function bars(title, pairs) {
    if (!pairs.length) return `<div><h4>${esc(title)}</h4><div class="empty">Sem dados no período.</div></div>`;
    const max = pairs[0][1];
    return `<div><h4>${esc(title)}</h4>${pairs.map(([k, v]) => `<div class="bar"><span title="${esc(k)}">${esc(k)}</span><i style="width:${Math.max(4, (v / max) * 100)}%"></i><span>${v}</span></div>`).join('')}</div>`;
  }

  // ---------- render principal ----------
  function render() {
    const f = readFilters();
    const filtered = sorted(state.items.filter((i) => passes(i, f)));
    const periodSet = state.items.filter((i) => passes(i, f, { skipPeriod: false }));

    renderKpis(periodSet);
    $('count').textContent = `${filtered.length} resultado${filtered.length === 1 ? '' : 's'} (de ${state.items.length} no histórico)`;
    renderActive(f);

    const sec = [];
    if (!state.items.length) {
      $('sections').innerHTML = block('sec-historico', 'Histórico', '', empty('Nenhuma notícia coletada ainda. A primeira coleta ocorre ao executar o workflow (manualmente ou na próxima sexta-feira, 12h).')) + statusBlock();
      return;
    }
    const top = (list, n = 6) => list.slice(0, n);
    const crit = filtered.filter((i) => i.relevance.level === 'Crítica');
    sec.push(block('sec-destaques', 'Destaques da semana', 'Maiores pontuações de relevância dentro dos filtros atuais.',
      filtered.length ? wrap(top(sorted(filtered).sort((a, b) => b.relevance.score - a.relevance.score))) : empty('Nenhum resultado para os filtros atuais.')));
    sec.push(block('sec-criticos', 'Alertas críticos', 'Apenas itens com evidência oficial verificável de prazo ou obrigação.',
      crit.length ? wrap(top(crit)) : empty('Nenhum alerta crítico no período e nos filtros atuais.')));
    const rt = filtered.filter((i) => (i.categories || []).includes('reforma_tributaria'));
    sec.push(block('sec-rt', 'Reforma Tributária', 'Cada item indica se é lei/regulamentação, nota técnica, comunicado sujeito a confirmação ou análise de terceiros.',
      rt.length ? wrap(top(rt)) : empty('Sem conteúdo de Reforma Tributária no período e nos filtros atuais.')));
    const sap = filtered.filter((i) => (i.categories || []).includes('sap'));
    sec.push(block('sec-sap', 'SAP em foco', 'Produtos, módulos, notas e soluções.', sap.length ? wrap(top(sap)) : empty('Sem conteúdo SAP no período e nos filtros atuais.')));
    sec.push(block('sec-clientes', 'Radar dos clientes', 'Agrupado por empresa. "Associação não confirmada" = identidade da empresa não verificada.', clientGroups(filtered)));
    sec.push(block('sec-prazos', 'Agenda de prazos', 'Datas extraídas do texto das publicações (não verificadas de forma independente).', agenda(filtered)));
    sec.push(block('sec-tendencias', 'Análises e tendências', 'Contagens calculadas a partir dos registros reais.', `<div class="bars">${bars('Temas recorrentes (tags)', topCounts(filtered, (i) => i.tags || []))}${bars('Módulos SAP', topCounts(filtered, (i) => i.sap_modules || []))}${bars('Natureza da informação', topCounts(filtered, (i) => i.nature || []))}${bars('Clientes mais citados', topCounts(filtered, (i) => i.clients || []))}</div>`));
    const shown = filtered.slice(0, state.histLimit);
    sec.push(block('sec-historico', 'Histórico', 'Arquivo pesquisável de todas as notícias coletadas (use os filtros e a busca).',
      filtered.length ? wrap(shown) + (filtered.length > shown.length ? `<div class="more"><button type="button" id="more">Mostrar mais (${filtered.length - shown.length} restantes)</button></div>` : '') : empty('Nenhum resultado para os filtros atuais.')));
    sec.push(statusBlock());
    $('sections').innerHTML = sec.join('');
    const more = $('more');
    if (more) more.onclick = () => { state.histLimit += 30; render(); };
  }

  function renderKpis(set) {
    const c = (fn) => set.filter(fn).length;
    const k = [
      ['Notícias no período', set.length, ''],
      ['Críticas', c((i) => i.relevance.level === 'Crítica'), 'crit'],
      ['Alta relevância', c((i) => i.relevance.level === 'Alta'), ''],
      ['Atualizações oficiais da Reforma Tributária', c((i) => (i.categories || []).includes('reforma_tributaria') && i.content_kind === 'Conteúdo oficial'), ''],
      ['Notícias de clientes', c((i) => (i.clients || []).length > 0), ''],
      ['Atualizações SAP', c((i) => (i.categories || []).includes('sap')), ''],
      ['Com possível ação necessária', c((i) => ['Exige avaliação imediata', 'Recomenda análise técnica'].includes(i.action_level)), ''],
    ];
    $('kpis').innerHTML = k.map(([l, v, cls]) => `<div class="kpi ${cls}"><b>${v}</b><span>${esc(l)}</span></div>`).join('');
  }

  function clientGroups(list) {
    const by = new Map(); const unc = [];
    list.forEach((i) => {
      (i.clients || []).forEach((c) => { if (!by.has(c)) by.set(c, []); by.get(c).push(i); });
      if ((i.clients_unconfirmed || []).length && !(i.clients || []).length) unc.push(i);
    });
    if (!by.size && !unc.length) return empty('Sem notícias de clientes no período e nos filtros atuais.');
    const groups = [...by.entries()].sort((a, b) => b[1].length - a[1].length);
    let html = groups.map(([name, arr]) => `<details class="client-group"><summary><span>${esc(name)}</span><span>${arr.length}</span></summary><div class="inner">${wrap(arr)}</div></details>`).join('');
    if (unc.length) html += `<details class="client-group"><summary><span>Associação não confirmada</span><span>${unc.length}</span></summary><div class="inner">${wrap(unc)}</div></details>`;
    return html;
  }

  function agenda(list) {
    const today = new Date().toISOString().slice(0, 10);
    const rows = [];
    list.forEach((i) => (i.deadlines || []).forEach((d) => { if (d >= today) rows.push([d, i]); }));
    rows.sort((a, b) => a[0].localeCompare(b[0]));
    if (!rows.length) return empty('Nenhum prazo futuro identificado nas publicações dentro dos filtros atuais.');
    return `<table class="agenda"><thead><tr><th>Data citada</th><th>Publicação</th><th>Fonte</th><th>Relevância</th></tr></thead><tbody>${rows.slice(0, 40).map(([d, i]) => `<tr><td>${fmtDay(d)}</td><td><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></td><td>${esc(i.source_name)}</td><td><span class="badge b-${i.relevance.level}">${i.relevance.level}</span></td></tr>`).join('')}</tbody></table>`;
  }

  function statusBlock() {
    const last = state.runs[state.runs.length - 1];
    const cfgSources = state.config.sources || [];
    const statusById = new Map(((last && last.sources) || []).map((s) => [s.id, s]));
    const rows = cfgSources.map((s) => {
      const st = statusById.get(s.id);
      const status = st ? st.status : (s.enabled ? 'sem' : 'desativada');
      const label = { ok: 'OK', parcial: 'Parcial', erro: 'Erro', manual: 'Manual', desativada: 'Desativada', sem: 'Sem execução' }[status] || status;
      const msg = (st && st.error) || s.note || (s.verified ? '' : 'URL ainda não validada em execução real');
      return `<tr><td>${esc(s.name)}</td><td>${esc(s.kind)}</td><td><span class="st ${status}">${label}</span></td><td>${st ? st.items : '—'}</td><td>${esc(msg)}</td></tr>`;
    }).join('');
    const hist = state.runs.slice(-8).reverse().map((r) => `<tr><td>${fmtDateTime(r.started_at)}</td><td>${esc(r.status)}</td><td>${r.duration_s}s</td><td>${r.sources_consulted}</td><td>${r.items_found}</td><td>${r.new}</td><td>${r.duplicates}</td><td>${r.updated || 0}</td></tr>`).join('');
    return block('sec-status', 'Status da coleta e fontes', 'Fontes indisponíveis ou que exigem acompanhamento manual aparecem aqui. Nada é ocultado.',
      `<table class="src-table"><thead><tr><th>Fonte</th><th>Tipo</th><th>Última execução</th><th>Itens</th><th>Observação</th></tr></thead><tbody>${rows}</tbody></table>
      <h3 style="margin:18px 0 6px;font-size:14px">Últimas execuções</h3>
      ${hist ? `<table class="src-table"><thead><tr><th>Início</th><th>Status</th><th>Duração</th><th>Fontes</th><th>Encontradas</th><th>Novas</th><th>Duplicadas</th><th>Atualizadas</th></tr></thead><tbody>${hist}</tbody></table>` : empty('Nenhuma execução registrada ainda.')}`);
  }

  // ---------- cabeçalho/status ----------
  function renderHeader() {
    const ok = [...state.runs].reverse().find((r) => r.status === 'concluida' || r.status === 'parcial');
    $('lastUpdate').textContent = ok ? fmtDateTime(ok.finished_at) : 'Nenhuma coleta concluída ainda';
    const nf = nextFriday12();
    $('nextUpdate').textContent = nf ? nf.toLocaleString('pt-BR', { timeZone: TZ, dateStyle: 'short', timeStyle: 'short' }) + ' (Brasília)' : '—';
    const last = state.runs[state.runs.length - 1];
    const map = { concluida: ['Concluída', 'ok'], parcial: ['Parcial', 'partial'], falha: ['Com falha', 'fail'] };
    const [txt, cls] = last ? (map[last.status] || [last.status, '']) : ['Sem coleta ainda', ''];
    $('runStatus').textContent = txt; $('runStatus').className = 'pill ' + cls;
    const b = $('banner');
    if (!last) { b.hidden = false; b.textContent = 'Nenhuma coleta foi executada ainda. Rode o workflow "Coleta semanal" no GitHub para preencher o radar com notícias reais.'; }
    else if (last.status === 'falha') { b.hidden = false; b.className = 'banner warn'; b.textContent = 'A última coleta falhou; exibindo a última base válida. Veja detalhes em "Status da coleta e fontes".'; }
    else if (last.status === 'parcial') { b.hidden = false; b.className = 'banner warn'; b.textContent = 'A última coleta foi parcial: algumas fontes falharam ou exigem acompanhamento manual. Veja "Status da coleta e fontes".'; }
    else b.hidden = true;
  }

  // ---------- controles ----------
  function fillSelect(id, values) { const s = $(id); values.forEach((v) => { const o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); }); }

  function renderClientList() {
    const q = $('clientSearch').value.toLowerCase();
    $('clientList').innerHTML = state.config.clients.filter((c) => c.name.toLowerCase().includes(q)).map((c) => `<label class="chk"><input type="checkbox" data-c="${esc(c.name)}" ${state.clients.has(c.name) ? 'checked' : ''}> ${esc(c.name)}</label>`).join('');
    $('clientAll').checked = state.clients.size === 0;
    $('clientSummary').textContent = state.clients.size === 0 ? 'Todos os clientes' : state.clients.size === 1 ? [...state.clients][0] : `${state.clients.size} clientes selecionados`;
  }

  function renderActive(f) {
    const tags = [];
    const add = (label, clear) => tags.push({ label, clear });
    if (f.q) add(`Busca: ${f.q}`, () => { $('q').value = ''; });
    if (f.cat) add(`Categoria: ${CAT_LABEL[f.cat]}`, () => { $('fCategory').value = ''; });
    if (f.mod) add(`Módulo: ${f.mod}`, () => { $('fModule').value = ''; });
    if (f.src) add(`Fonte: ${$('fSource').selectedOptions[0].textContent}`, () => { $('fSource').value = ''; });
    if (f.nature) add(`Natureza: ${f.nature}`, () => { $('fNature').value = ''; });
    if (f.action) add(`Ação: ${f.action}`, () => { $('fAction').value = ''; });
    if (f.period !== '30') add(`Período: ${$('fPeriod').selectedOptions[0].textContent}`, () => { $('fPeriod').value = '30'; $('customDates').hidden = true; });
    state.levels.forEach((l) => add(`Relevância: ${l}`, () => state.levels.delete(l)));
    state.clients.forEach((c) => add(`Cliente: ${c}`, () => { state.clients.delete(c); renderClientList(); }));
    const box = $('active'); box.innerHTML = '';
    tags.forEach((t) => {
      const el = document.createElement('span'); el.className = 'tag-active'; el.textContent = t.label;
      const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', 'Remover filtro'); b.textContent = '×';
      b.onclick = () => { t.clear(); syncChips(); update(); };
      el.appendChild(b); box.appendChild(el);
    });
  }

  function syncChips() { document.querySelectorAll('#relChips .chip').forEach((b) => b.setAttribute('aria-pressed', String(state.levels.has(b.dataset.l)))); }
  function update() { state.histLimit = 30; render(); }

  function bind() {
    ['q', 'fCategory', 'fModule', 'fSource', 'fNature', 'fAction', 'sort', 'dFrom', 'dTo'].forEach((id) => $(id).addEventListener(id === 'q' ? 'input' : 'change', update));
    $('fPeriod').addEventListener('change', () => { $('customDates').hidden = $('fPeriod').value !== 'custom'; update(); });
    $('relChips').innerHTML = LEVELS.map((l) => `<button type="button" class="chip" data-l="${l}" aria-pressed="false">${l}</button>`).join('');
    $('relChips').addEventListener('click', (e) => { const b = e.target.closest('.chip'); if (!b) return; state.levels.has(b.dataset.l) ? state.levels.delete(b.dataset.l) : state.levels.add(b.dataset.l); syncChips(); update(); });
    $('clientSearch').addEventListener('input', renderClientList);
    $('clientAll').addEventListener('change', () => { state.clients.clear(); renderClientList(); update(); });
    $('clientList').addEventListener('change', (e) => { const c = e.target.dataset.c; if (!c) return; e.target.checked ? state.clients.add(c) : state.clients.delete(c); renderClientList(); update(); });
    $('viewCards').onclick = () => { state.view = 'cards'; $('viewCards').classList.add('on'); $('viewList').classList.remove('on'); render(); };
    $('viewList').onclick = () => { state.view = 'list'; $('viewList').classList.add('on'); $('viewCards').classList.remove('on'); render(); };
    $('clear').onclick = () => {
      ['q', 'dFrom', 'dTo'].forEach((id) => { $(id).value = ''; });
      ['fCategory', 'fModule', 'fSource', 'fNature', 'fAction'].forEach((id) => { $(id).value = ''; });
      $('fPeriod').value = '30'; $('customDates').hidden = true; $('sort').value = 'relevance';
      state.levels.clear(); state.clients.clear(); syncChips(); renderClientList(); update();
    };
    document.addEventListener('click', (e) => { const ms = $('clientMs'); if (ms.open && !ms.contains(e.target)) ms.open = false; });
  }

  async function init() {
    const [news, runs, config] = await Promise.all([getJson('data/news.json', { items: [] }), getJson('data/runs.json', { runs: [] }), getJson('data/config.json', state.config)]);
    state.items = news.items || []; state.runs = runs.runs || []; state.config = config;
    fillSelect('fModule', config.sap_modules || []);
    fillSelect('fNature', config.nature || []);
    const sel = $('fSource');
    const seen = new Map(); state.items.forEach((i) => seen.set(i.source_id, i.source_name));
    [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1])).forEach(([id, name]) => { const o = document.createElement('option'); o.value = id; o.textContent = name; sel.appendChild(o); });
    bind(); renderClientList(); renderHeader(); render();
    window.__radar = { state, passes, readFilters }; // facilita testes automatizados
  }
  init();
})();
