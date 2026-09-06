const listEl = document.getElementById("list");
const daySelect = document.getElementById("day-select");
const metaEl = document.getElementById("meta");
const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refresh-btn");

const fmtNum = (n, d = 2) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString("es-ES", {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function changeClass(pct) {
  if (pct === null || pct === undefined) return "flat";
  if (pct > 0.05) return "up";
  if (pct < -0.05) return "down";
  return "flat";
}

function arrow(pct) {
  if (pct === null || pct === undefined) return "•";
  if (pct > 0.05) return "▲";
  if (pct < -0.05) return "▼";
  return "→";
}

// Las cinco conclusiones posibles del analisis, cada una con su color.
const STATUS = {
  ok:           { label: "Explicado",          cls: "st-ok" },
  no_cause:     { label: "Sin causa clara",    cls: "st-nocause" },
  no_news:      { label: "Sin noticias",       cls: "st-nonews" },
  not_relevant: { label: "Movimiento pequeño", cls: "st-flat" },
  unavailable:  { label: "No disponible",      cls: "st-none" },
};

function statusBadge(status) {
  const info = STATUS[status] || STATUS.unavailable;
  return `<span class="tag ${info.cls}">${info.label}</span>`;
}

function summaryBlock(item) {
  const status = item.explanation_status;
  if (!item.summary) {
    return `<p class="summary muted">${statusBadge("unavailable")} Sin explicación disponible.</p>`;
  }
  const explicado = status === "ok" || status === "no_cause";
  const provider = item.provider && explicado
    ? ` <span class="tag">${esc(item.provider)}</span>` : "";
  return `<p class="summary${explicado ? "" : " muted"}">
    ${statusBadge(status)}${provider} ${esc(item.summary)}
  </p>`;
}

function newsBlock(item) {
  if (!item.news || item.news.length === 0) return "";
  const rows = item.news.map((n) => {
    const pub = n.publisher ? ` <span class="pub">— ${esc(n.publisher)}</span>` : "";
    return n.url
      ? `<li><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>${pub}</li>`
      : `<li>${esc(n.title)}${pub}</li>`;
  }).join("");
  return `<details><summary>Titulares usados (${item.news.length})</summary><ul>${rows}</ul></details>`;
}

function card(item) {
  const cls = changeClass(item.change_pct);
  const pct = item.change_pct === null || item.change_pct === undefined
    ? "—"
    : `${item.change_pct > 0 ? "+" : ""}${fmtNum(item.change_pct)}%`;
  const stale = item.asof && item.asof !== item.day
    ? `<span class="tag">cierre del ${esc(item.asof)}</span>` : "";
  const renombrado = item.alias
    ? `<span class="code">${esc(item.ticker)}</span>` : "";
  return `
    <article class="card" data-status="${esc(item.explanation_status || "unavailable")}">
      <div class="card-head">
        <div class="ident">
          <span class="ticker">${esc(item.label || item.ticker)}</span>
          ${renombrado}
          ${item.name ? `<span class="name">${esc(item.name)}</span>` : ""}
          ${stale}
        </div>
        <div class="numbers">
          <span class="price">${fmtNum(item.price)} ${esc(item.currency || "")}</span>
          <span class="change ${cls}">${arrow(item.change_pct)} ${pct}</span>
        </div>
      </div>
      ${summaryBlock(item)}
      ${newsBlock(item)}
    </article>`;
}

async function loadDays() {
  try {
    const res = await fetch("/api/days");
    const data = await res.json();
    daySelect.innerHTML = data.days.map((d) => `<option value="${d}">${d}</option>`).join("");
    daySelect.disabled = data.days.length === 0;
  } catch (err) {
    daySelect.disabled = true;
  }
}

async function loadPortfolio(day) {
  listEl.innerHTML = '<p class="empty">Cargando…</p>';
  try {
    const url = day ? `/api/portfolio?day=${encodeURIComponent(day)}` : "/api/portfolio";
    const res = await fetch(url);
    const data = await res.json();

    if (!data.day || data.items.length === 0) {
      listEl.innerHTML = '<p class="empty">Todavía no hay datos. Pulsa «Actualizar ahora» para lanzar el primer análisis.</p>';
      metaEl.textContent = "";
      return;
    }
    if (daySelect.value !== data.day) daySelect.value = data.day;
    listEl.innerHTML = data.items.map(card).join("");

    const run = data.last_run;
    const when = run && run.finished_at ? new Date(run.finished_at).toLocaleString("es-ES") : "—";
    metaEl.textContent = `Datos del ${data.day} · última actualización: ${when}`;
  } catch (err) {
    listEl.innerHTML = '<p class="empty">No se han podido cargar los datos.</p>';
  }
}

async function refresh() {
  refreshBtn.disabled = true;
  statusEl.textContent = "Analizando… puede tardar un par de minutos.";
  try {
    const res = await fetch("/api/refresh", { method: "POST" });
    if (res.status === 409) {
      statusEl.textContent = "Ya hay un análisis en marcha.";
    } else if (!res.ok) {
      statusEl.textContent = "No se ha podido lanzar el análisis.";
    } else {
      // El job arranca justo despues de responder: damos margen antes de mirar.
      setTimeout(pollHealth, 3000);
      return;
    }
  } catch (err) {
    statusEl.textContent = "Error de red al lanzar el análisis.";
  }
  refreshBtn.disabled = false;
}

async function pollHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.job_running) {
      setTimeout(pollHealth, 5000);
      return;
    }
  } catch (err) {
    // si health falla, dejamos de esperar y recargamos igualmente
  }
  statusEl.textContent = "";
  refreshBtn.disabled = false;
  await loadDays();
  await loadPortfolio(daySelect.value || null);
}

// --- gestion de la cartera --------------------------------------------------

const tickerList = document.getElementById("ticker-list");
const tickerCount = document.getElementById("ticker-count");
const addForm = document.getElementById("add-form");
const tickerInput = document.getElementById("ticker-input");
const addBtn = document.getElementById("add-btn");
const addMsg = document.getElementById("add-msg");
const resultsEl = document.getElementById("results");

function showMsg(text, kind) {
  addMsg.textContent = text;
  addMsg.className = `add-msg ${kind}`;
  addMsg.hidden = !text;
}

let misValores = [];

function tickerRow(t) {
  const codigo = t.alias ? `<span class="code">${esc(t.ticker)}</span>` : "";
  const tema = t.topic
    ? `<span class="t-topic">noticias de ${esc(t.topic)}${t.topic_manual ? " (a mano)" : ""}</span>`
    : '<span class="t-topic t-topic-off">noticias por su propio nombre</span>';
  return `
    <li data-row="${esc(t.ticker)}">
      <span class="t-main">
        <span class="t-title">
          <strong>${esc(t.label || t.ticker)}</strong>
          ${codigo}
          ${t.name ? `<span class="t-name">${esc(t.name)}</span>` : ""}
        </span>
        ${tema}
      </span>
      <span class="t-actions">
        <button type="button" data-rename="${esc(t.ticker)}">Renombrar</button>
        <button type="button" data-topic="${esc(t.ticker)}">Tema</button>
        <button type="button" data-ticker="${esc(t.ticker)}">Quitar</button>
      </span>
    </li>`;
}

function renderTickers() {
  tickerCount.textContent = `${misValores.length} valores`;
  tickerList.innerHTML = misValores.map(tickerRow).join("")
    || '<li><span class="t-name">La cartera está vacía. Busca un valor arriba para empezar.</span></li>';
}

async function loadTickers() {
  try {
    const res = await fetch("/api/tickers");
    const data = await res.json();
    misValores = data.tickers;
    renderTickers();
  } catch (err) {
    tickerList.innerHTML = '<li><span class="t-name">No se ha podido cargar la cartera.</span></li>';
  }
}

function startEdit(ticker, campo) {
  const item = misValores.find((t) => t.ticker === ticker);
  const row = tickerList.querySelector(`li[data-row="${CSS.escape(ticker)}"]`);
  if (!item || !row) return;
  const esTema = campo === "topic";
  const valor = esTema ? (item.topic_manual || "") : (item.label || ticker);
  const pista = esTema
    ? `Tema de noticias de ${esc(ticker)}. Vacío = ${esc(item.topic || "su propio nombre")}`
    : `Etiqueta para ${esc(ticker)}`;
  row.innerHTML = `
    <span class="t-main">
      <input class="t-input" type="text" maxlength="${esTema ? 60 : 30}" value="${esc(valor)}"
             placeholder="${esc(esTema ? item.topic || "" : ticker)}" aria-label="${pista}">
      <span class="t-name">${pista}</span>
    </span>
    <span class="t-actions">
      <button type="button" data-save="${esc(ticker)}" data-field="${esc(campo)}">Guardar</button>
      <button type="button" data-cancel="1">Cancelar</button>
    </span>`;
  const input = row.querySelector("input");
  input.focus();
  input.select();
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); saveEdit(ticker, campo, input.value); }
    if (event.key === "Escape") renderTickers();
  });
}

async function saveEdit(ticker, campo, valor) {
  try {
    const res = await fetch(`/api/tickers/${encodeURIComponent(ticker)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(campo === "topic" ? { topic: valor } : { name: valor }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showMsg(data.detail || "No se ha podido guardar.", "error");
      renderTickers();
      return;
    }
    const data = await res.json();
    await loadTickers();
    await loadPortfolio(daySelect.value || null);
    if (campo === "topic") {
      showMsg(data.topic
        ? `${ticker}: se buscarán noticias de ${data.topic}.`
        : `${ticker}: se buscarán noticias por su propio nombre.`, "ok");
    } else {
      showMsg(data.alias
        ? `${ticker} se muestra ahora como «${data.label}».`
        : `${ticker} vuelve a mostrarse con su código.`, "ok");
    }
  } catch (err) {
    showMsg("Error de red al guardar.", "error");
    renderTickers();
  }
}

// --- buscador ---------------------------------------------------------------

let searchTimer = null;
let searchSeq = 0;

function changeBadge(pct) {
  if (pct === null || pct === undefined) return "";
  const signo = pct > 0 ? "+" : "";
  return `<span class="change ${changeClass(pct)}">${arrow(pct)} ${signo}${fmtNum(pct)}%</span>`;
}

function renderResults(query, results) {
  if (results.length === 0) {
    resultsEl.innerHTML = `
      <li>
        <span class="r-main">
          <span class="r-empty">
            Ningún valor con datos de precio para «${esc(query)}».
            Prueba con el nombre completo, otro mercado o el ISIN.
          </span>
        </span>
      </li>`;
  } else {
    resultsEl.innerHTML = results.map((r) => `
      <li>
        <span class="r-main">
          <span class="r-name"><span class="r-sym">${esc(r.symbol)}</span> · ${esc(r.name)}</span>
          <span class="r-meta">${esc(r.type)}${r.exchange ? ` · ${esc(r.exchange)}` : ""}${r.currency ? ` · <strong>${esc(r.currency)}</strong>` : ""}</span>
        </span>
        <span class="r-change">
          ${changeBadge(r.change_pct)}
          <span class="r-price">${fmtNum(r.price)}${r.currency ? ` ${esc(r.currency)}` : ""}</span>
        </span>
        ${r.in_portfolio
          ? '<span class="r-in">Ya en cartera</span>'
          : `<button type="button" data-add="${esc(r.symbol)}">Añadir</button>`}
      </li>`).join("");
  }
  resultsEl.hidden = false;
}

function clearResults() {
  resultsEl.innerHTML = "";
  resultsEl.hidden = true;
}

async function runSearch(query) {
  const seq = ++searchSeq;
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    if (seq !== searchSeq) return;  // llegó tarde: hay una búsqueda más nueva
    renderResults(query, data.results || []);
  } catch (err) {
    if (seq === searchSeq) showMsg("No se ha podido buscar ahora mismo.", "error");
  }
}

function onSearchInput() {
  const value = tickerInput.value.trim();
  showMsg("", "");
  clearTimeout(searchTimer);
  if (value.length < 2) {
    clearResults();
    return;
  }
  searchTimer = setTimeout(() => runSearch(value), 350);
}

function onSearchSubmit(event) {
  event.preventDefault();
  clearTimeout(searchTimer);
  const value = tickerInput.value.trim();
  if (value.length < 2) return;
  // Enter añade el primer resultado disponible; si no hay, busca.
  const first = resultsEl.querySelector("button[data-add]");
  if (first && !resultsEl.hidden) {
    addTicker(first.dataset.add);
  } else {
    runSearch(value);
  }
}

async function addTicker(symbol) {
  addBtn.disabled = true;
  showMsg(`Añadiendo ${symbol}…`, "warn");
  try {
    const res = await fetch("/api/tickers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: symbol }),
    });
    const data = await res.json();
    if (!res.ok) {
      showMsg(data.detail || "No se ha podido añadir.", "error");
    } else {
      tickerInput.value = "";
      clearResults();
      await loadTickers();
      if (data.warning) {
        showMsg(data.warning, "warn");
      } else {
        showMsg(`${data.ticker} añadido${data.name ? ` (${data.name})` : ""}. Buscando su precio…`, "ok");
        setTimeout(pollHealth, 3000);
      }
    }
  } catch (err) {
    showMsg("Error de red al añadir el valor.", "error");
  }
  addBtn.disabled = false;
}

async function removeTicker(ticker) {
  showMsg("", "");
  try {
    const res = await fetch(`/api/tickers/${encodeURIComponent(ticker)}`, { method: "DELETE" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showMsg(data.detail || "No se ha podido quitar.", "error");
      return;
    }
    await loadTickers();
    await loadPortfolio(daySelect.value || null);
    showMsg(`${ticker} eliminado de la cartera. Su histórico se conserva.`, "ok");
  } catch (err) {
    showMsg("Error de red al quitar el valor.", "error");
  }
}

tickerList.addEventListener("click", (event) => {
  const quitar = event.target.closest("button[data-ticker]");
  if (quitar) { removeTicker(quitar.dataset.ticker); return; }
  const renombrar = event.target.closest("button[data-rename]");
  if (renombrar) { startEdit(renombrar.dataset.rename, "name"); return; }
  const tema = event.target.closest("button[data-topic]");
  if (tema) { startEdit(tema.dataset.topic, "topic"); return; }
  const guardar = event.target.closest("button[data-save]");
  if (guardar) {
    const input = guardar.closest("li").querySelector("input");
    saveEdit(guardar.dataset.save, guardar.dataset.field, input ? input.value : "");
    return;
  }
  if (event.target.closest("button[data-cancel]")) renderTickers();
});
resultsEl.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-add]");
  if (btn) addTicker(btn.dataset.add);
});
tickerInput.addEventListener("input", onSearchInput);
addForm.addEventListener("submit", onSearchSubmit);
daySelect.addEventListener("change", () => loadPortfolio(daySelect.value));
refreshBtn.addEventListener("click", refresh);

(async function init() {
  await loadTickers();
  await loadDays();
  await loadPortfolio(daySelect.value || null);
})();
