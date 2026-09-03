const escapeHtml = (text = "") =>
  String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const params = new URLSearchParams(window.location.search);
const storedOpportunity = window.history?.state?.auctionWatchOpportunity || {};
let opportunity = {
  source: String(params.get("source") || storedOpportunity.source || "").trim().toLowerCase(),
  lotId: String(params.get("lot") || storedOpportunity.lotId || "").trim(),
  groupId: String(params.get("group") || storedOpportunity.groupId || "").trim(),
  title: String(params.get("title") || storedOpportunity.title || "Oportunidad activa").trim(),
  lotUrl: String(params.get("lotUrl") || storedOpportunity.lotUrl || "").trim()
};

function safePublicUrl(raw = "") {
  try {
    const parsed = new URL(raw);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch (_error) {
    return "";
  }
}

function rememberOpportunity() {
  if (!window.history?.replaceState) return;
  window.history.replaceState(
    { ...(window.history.state || {}), auctionWatchOpportunity: opportunity },
    document.title,
    "./auction-watch-action.html"
  );
}

function focusMainHeading() {
  window.requestAnimationFrame(() => {
    const heading = document.querySelector("#auctionWatchActionRoot h1");
    if (!heading) return;
    heading.setAttribute("tabindex", "-1");
    heading.focus();
  });
}

function renderLoading() {
  document.getElementById("auctionWatchActionRoot").innerHTML = `
    <section class="detail-shell opportunity-action-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>Verificando oportunidad…</h1>
          <p class="subtitle">Estamos confirmando la identidad del lote antes de mostrar la acción.</p>
        </div>
      </article>
    </section>
  `;
}

function findOpportunityByKey(items = [], key = "") {
  const repo = window.AuctionWatchRepository;
  return (items || []).find((item) => repo?.getOpportunityKey?.(item) === key) || null;
}

async function loadCanonicalOpportunity() {
  const repo = window.AuctionWatchRepository;
  const requestedKey = repo?.getOpportunityKey?.(opportunity) || "";
  if (!requestedKey) return { state: "invalid", item: null };

  await repo.loadSnapshot();
  const sync = repo.getSyncState?.() || {
    status: repo.getSnapshotSource?.() === "server" ? "ready" : "unavailable",
    origin: repo.getSnapshotSource?.() || "none"
  };
  if (sync.status === "unavailable") return { state: "sync_unavailable", item: null };
  if (sync.status === "stale") return { state: "sync_stale", item: null };
  if (sync.origin !== "server") return { state: "sync_degraded", item: null };

  const snapshot = repo.getSnapshot();
  const active = [repo.getFeaturedOpportunity(), ...(snapshot?.matches || [])].filter(Boolean);
  const activeItem = findOpportunityByKey(active, requestedKey);
  const dismissedItem = findOpportunityByKey(repo.getDismissedOpportunities(), requestedKey);
  const canonical = activeItem || dismissedItem;
  if (!canonical) {
    return { state: sync.status === "degraded" ? "sync_degraded" : "unavailable", item: null };
  }

  opportunity = {
    source: String(canonical.source || canonical.sourceId || "").trim().toLowerCase(),
    lotId: String(canonical.lotId || "").trim(),
    groupId: String(canonical.groupId || "").trim(),
    title: String(canonical.title || "Oportunidad activa").trim(),
    lotUrl: repo.safePublicUrl(canonical.lotUrl)
  };
  rememberOpportunity();
  return { state: dismissedItem ? "dismissed" : "active", item: canonical };
}

function renderUnavailable(title, message, { retry = false } = {}) {
  const root = document.getElementById("auctionWatchActionRoot");
  root.innerHTML = `
    <section class="detail-shell opportunity-action-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>${escapeHtml(title)}</h1>
          <p class="subtitle">${escapeHtml(message)}</p>
          <div class="card-actions">
            ${retry ? `<button class="btn-link" id="retryVerificationButton" type="button">Verificar de nuevo</button>` : ""}
            <a class="btn-link btn-primary" href="./opportunities.html">Ver oportunidades</a>
          </div>
        </div>
      </article>
    </section>
  `;
  document.getElementById("retryVerificationButton")?.addEventListener("click", init);
  focusMainHeading();
}

function renderConfirmation(message = "") {
  const root = document.getElementById("auctionWatchActionRoot");
  const repo = window.AuctionWatchRepository;
  const valid = Boolean(repo?.getOpportunityKey?.(opportunity));
  const publicUrl = safePublicUrl(opportunity.lotUrl);

  root.innerHTML = `
    <section class="detail-shell opportunity-action-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>${valid ? "Descartar oportunidad" : "Enlace incompleto"}</h1>
          <p class="subtitle">
            ${
              valid
                ? "Dejará de aparecer en los próximos correos y en las oportunidades activas. No modifica el remate ni tu colección."
                : "No pudimos identificar el lote que querés descartar."
            }
          </p>
          ${
            valid
              ? `
            <article class="opportunity-card opportunity-card--confirmation">
              <p class="opportunity-kicker">${escapeHtml(repo.getSourceLabel(opportunity.source))}</p>
              <h4>${escapeHtml(opportunity.title)}</h4>
              <div class="opportunity-meta">
                <span>Lote ${escapeHtml(opportunity.lotId)}</span>
              </div>
              <div class="card-actions">
                ${publicUrl ? `<a class="btn-link" href="${escapeHtml(publicUrl)}" target="_blank" rel="noreferrer noopener">Ver publicación</a>` : ""}
                <button class="btn-link btn-primary" id="confirmDismissButton" type="button">Confirmar descarte</button>
              </div>
            </article>
          `
              : ""
          }
          <p class="opportunity-feedback" id="actionFeedback" role="status" aria-live="polite">${escapeHtml(message)}</p>
          <div class="card-actions">
            <a class="btn-link" href="./opportunities.html">Volver a oportunidades</a>
          </div>
        </div>
      </article>
    </section>
  `;

  document.getElementById("confirmDismissButton")?.addEventListener("click", handleDismiss);
  focusMainHeading();
}

async function handleDismiss(event) {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Descartando…";
  try {
    await window.AuctionWatchRepository.loadDismissals();
    await window.AuctionWatchRepository.dismissOpportunity(opportunity, { requireServer: true });
    renderSuccess();
  } catch (error) {
    console.error(error);
    button.disabled = false;
    button.textContent = "Confirmar descarte";
    document.getElementById("actionFeedback").textContent =
      "No se pudo guardar el descarte. Probá de nuevo desde la app.";
  }
}

function renderSuccess() {
  const root = document.getElementById("auctionWatchActionRoot");
  root.innerHTML = `
    <section class="detail-shell opportunity-action-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>Oportunidad descartada</h1>
          <p class="subtitle">No volverá a aparecer en los próximos correos mientras conserve esta identidad de lote.</p>
          <p class="opportunity-feedback opportunity-feedback--visible" role="status">${escapeHtml(opportunity.title)}</p>
          <div class="card-actions">
            <button class="btn-link" id="undoDismissButton" type="button">Deshacer</button>
            <a class="btn-link btn-primary" href="./opportunities.html">Ver oportunidades activas</a>
          </div>
          <p class="opportunity-feedback" id="undoFeedback" role="status" aria-live="polite"></p>
        </div>
      </article>
    </section>
  `;
  document.getElementById("undoDismissButton")?.addEventListener("click", handleUndo);
  focusMainHeading();
}

function renderRestored() {
  const root = document.getElementById("auctionWatchActionRoot");
  const publicUrl = safePublicUrl(opportunity.lotUrl);
  root.innerHTML = `
    <section class="detail-shell opportunity-action-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>Descarte deshecho</h1>
          <p class="subtitle">La oportunidad volverá a aparecer si el lote sigue activo en una próxima actualización.</p>
          <div class="card-actions">
            ${publicUrl ? `<a class="btn-link" href="${escapeHtml(publicUrl)}" target="_blank" rel="noreferrer noopener">Ver publicación</a>` : ""}
            <a class="btn-link btn-primary" href="./opportunities.html">Ver oportunidades</a>
          </div>
        </div>
      </article>
    </section>
  `;
  focusMainHeading();
}

async function handleUndo(event) {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Restaurando…";
  try {
    await window.AuctionWatchRepository.restoreOpportunity(opportunity, { requireServer: true });
    renderRestored();
  } catch (error) {
    console.error(error);
    button.disabled = false;
    button.textContent = "Deshacer";
    const feedback = document.getElementById("undoFeedback");
    if (feedback) feedback.textContent = "No se pudo deshacer el descarte. Probá de nuevo en un momento.";
  }
}

async function init() {
  rememberOpportunity();
  renderLoading();
  try {
    const result = await loadCanonicalOpportunity();
    if (result.state === "active") {
      renderConfirmation();
      return;
    }
    if (result.state === "dismissed") {
      renderSuccess();
      return;
    }
    if (result.state === "invalid") {
      renderUnavailable("Enlace incompleto", "No pudimos identificar el lote que querés descartar.");
      return;
    }
    if (result.state === "sync_stale") {
      renderUnavailable(
        "Snapshot desactualizado",
        "La app todavía no confirmó una corrida vigente. No vamos a asumir que este lote terminó ni a descartarlo con datos viejos.",
        { retry: true }
      );
      return;
    }
    if (result.state === "sync_degraded") {
      renderUnavailable(
        "Sincronización pendiente",
        "Hay un respaldo disponible, pero las acciones del mail requieren el snapshot confirmado por el servidor.",
        { retry: true }
      );
      return;
    }
    if (result.state === "sync_unavailable") {
      renderUnavailable(
        "No pudimos verificar el lote",
        "El servidor no entregó un snapshot utilizable. Probá de nuevo cuando la app recupere la conexión.",
        { retry: true }
      );
      return;
    }
    renderUnavailable(
      "Oportunidad no disponible",
      "El lote ya no está entre las oportunidades monitoreadas. No hace falta descartarlo de los próximos correos."
    );
  } catch (error) {
    console.error(error);
    renderUnavailable(
      "No pudimos verificar el lote",
      "La app no respondió a tiempo. Probá de nuevo cuando estés conectado a la misma red."
    );
  }
}

init();
