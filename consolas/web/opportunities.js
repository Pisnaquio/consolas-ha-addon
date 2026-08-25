const escapeHtml = (text = "") =>
  text
    .toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

let opportunityView = "active";
let opportunityFeedback = null;
let manualRunState = null;
let manualRunRequestedLocally = false;
let manualRunPollTimer = null;
let manualRunSyncPending = false;
let manualRunSyncFailed = false;
let manualRunPollIssue = "";

function getAuctionWatchSyncState() {
  const repo = window.AuctionWatchRepository;
  const source = repo?.getSnapshotSource?.() || "none";
  return repo?.getSyncState?.() || {
    status: source === "server" ? "ready" : source === "none" ? "unavailable" : "degraded",
    source,
    origin: source,
    generatedAt: repo?.getSnapshot?.()?.generatedAt || "",
    runId: repo?.getSnapshot?.()?.runId || "",
    snapshotHash: "",
    stale: false,
    degraded: source !== "server",
    divergence: false
  };
}

function canPersistOpportunityActions() {
  const sync = getAuctionWatchSyncState();
  return sync.origin === "server" && !["stale", "unavailable"].includes(sync.status);
}

function renderOpportunityBadges(item = {}) {
  const repo = window.AuctionWatchRepository;
  const badges = [
    repo?.getSourceLabel?.(item.source) || "Auction Watch",
    item.urgencyLabel || "seguimiento",
    ...(item.dismissed ? ["descartada"] : []),
    ...(item.following ? ["siguiendo"] : []),
    ...(item.watchlist ? ["watchlist"] : []),
    ...(item.matchedKeywords || []).slice(0, 2)
  ];

  return badges
    .filter(Boolean)
    .map((badge) => `<span class="opportunity-badge">${escapeHtml(badge)}</span>`)
    .join("");
}

function compactDescription(item = {}) {
  const title = String(item.title || "").trim();
  const description = String(item.description || item.notes || "").replace(/\s+/g, " ").trim();
  if (!description || description === title) return "";
  return description.length > 180 ? `${description.slice(0, 177).trimEnd()}…` : description;
}

function compactTitle(value = "") {
  const title = String(value || "").replace(/\s+/g, " ").trim();
  if (title.length <= 88) return title;
  const firstSentence = title.split(/[.!?](?=\s|$)/, 1)[0].trim();
  if (firstSentence.length >= 4 && firstSentence.length <= 88) return firstSentence;
  return `${title.slice(0, 85).trimEnd()}…`;
}

function resolveOpportunityImageUrl(item = {}) {
  const candidates = [item.imageUrl, item.image_url, item.image, item.img];
  for (const candidate of candidates) {
    const text = String(candidate || "").trim();
    if (text.startsWith("http://") || text.startsWith("https://")) {
      return text;
    }
  }
  return "";
}

function renderOpportunityImage(item = {}) {
  const url = resolveOpportunityImageUrl(item);
  const title = compactTitle(item.title || "Oportunidad activa");
  if (!url) {
    return `<div class="opportunity-card-media"><span class="opportunity-card-placeholder">Sin foto</span></div>`;
  }
  return `
    <figure class="opportunity-card-media">
      <img class="opportunity-card-image" src="${escapeHtml(url)}" alt="${escapeHtml(title)}" loading="lazy" onerror="this.style.display='none'; this.parentElement.classList.add('has-error');" />
      <span class="opportunity-card-placeholder">Sin foto</span>
    </figure>
  `;
}

function compactGroupLabel(item = {}) {
  const label = String(item.groupLabel || "").replace(/\s+/g, " ").trim();
  if (!label) return "";
  if (label.length <= 54) return label;
  const kind = item.source === "bavastro" ? "Subasta" : "Remate";
  return item.groupId ? `${kind} ${item.groupId}` : "Remate activo";
}

function renderOpportunityCard(item = {}, { featured = false, dismissed = false } = {}) {
  const repo = window.AuctionWatchRepository;
  const primaryUrl = repo?.getPrimaryUrl?.(item) || "";
  const secondaryUrl = repo?.getSecondaryUrl?.(item) || "";
  const primaryLabel = repo?.getPrimaryCtaLabel?.(item) || "Ver detalle";
  const secondaryLabel = repo?.getSecondaryCtaLabel?.(item) || "";
  const consoleId = item.consoleIds?.[0] || "";
  const consoleUrl = consoleId ? window.CollectionRepository?.getConsoleDetailHref?.(consoleId) || "" : "";
  const description = compactDescription(item);
  const title = compactTitle(item.title || "Oportunidad activa");
  const groupLabel = compactGroupLabel(item);
  const opportunityKey = repo?.getOpportunityKey?.(item) || "";
  const canPersist = canPersistOpportunityActions();
  const canDismiss = Boolean(opportunityKey && !dismissed && canPersist);
  const isFollowing = repo?.isOpportunityFollowed?.(item) || item.following === true;
  const actions = [];

  if (primaryUrl) {
    actions.push(
      `<a class="btn-link opportunity-action" href="${escapeHtml(primaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(primaryLabel)}</a>`
    );
  }

  if (secondaryUrl) {
    actions.push(
      `<a class="btn-link opportunity-action" href="${escapeHtml(secondaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(secondaryLabel)}</a>`
    );
  }

  if (canDismiss) {
    actions.push(
      `<button class="btn-link opportunity-action ${isFollowing ? "is-primary" : ""}" type="button" data-opportunity-action="${isFollowing ? "unfollow" : "follow"}" data-opportunity-key="${escapeHtml(opportunityKey)}">${isFollowing ? "Dejar de seguir" : "Seguir"}</button>`
    );
    actions.push(
      `<button class="btn-link opportunity-action" type="button" data-opportunity-action="dismiss" data-opportunity-key="${escapeHtml(opportunityKey)}">Descartar</button>`
    );
  }

  if (opportunityKey && !dismissed && !canPersist) {
    actions.push(
      `<button class="btn-link opportunity-action" type="button" disabled aria-label="${isFollowing ? "Dejar de seguir" : "Seguir"} no disponible sin un snapshot vigente del servidor" title="La app necesita un snapshot vigente del servidor para guardar este cambio.">${isFollowing ? "Dejar de seguir" : "Seguir"}</button>`
    );
    actions.push(
      `<button class="btn-link opportunity-action" type="button" disabled aria-label="Descartar no disponible sin un snapshot vigente del servidor" title="La app necesita un snapshot vigente del servidor para guardar este cambio.">Descartar</button>`
    );
  }

  if (consoleUrl) {
    actions.push(
      `<a class="btn-link opportunity-action" href="${escapeHtml(consoleUrl)}">Ver consola</a>`
    );
  }

  if (dismissed && canPersist) {
    actions.push(
      `<button class="btn-link opportunity-action is-primary" type="button" data-opportunity-action="restore" data-opportunity-key="${escapeHtml(opportunityKey)}">Restaurar</button>`
    );
  }

  if (dismissed && !canPersist) {
    actions.push(
      `<button class="btn-link opportunity-action is-primary" type="button" disabled aria-label="Restaurar no disponible sin conexión con el servidor" title="La app necesita conexión con el servidor para restaurar este descarte.">Restaurar</button>`
    );
  }

  return `
    <article class="${[
      "opportunity-card",
      featured ? "opportunity-card--featured" : "",
      dismissed ? "opportunity-card--dismissed" : ""
    ].filter(Boolean).join(" ")}">
      <div class="opportunity-card-content">
        ${renderOpportunityImage(item)}
        <div class="opportunity-card-body">
          <div class="opportunity-card-head">
            <div>
              <p class="opportunity-kicker">${escapeHtml(repo?.getSourceLabel?.(item.source) || "Auction Watch")}</p>
              <h4 title="${escapeHtml(item.title || "Oportunidad activa")}">${escapeHtml(title)}</h4>
            </div>
            <span class="opportunity-timer">${escapeHtml(item.remainingText || "-")}</span>
          </div>
          <div class="opportunity-badges">
            ${renderOpportunityBadges(item)}
          </div>
          ${description ? `<p class="opportunity-copy">${escapeHtml(description)}</p>` : ""}
          <div class="opportunity-meta">
            ${item.priceLabel ? `<span>${escapeHtml(item.priceLabel)}</span>` : ""}
            ${groupLabel ? `<span title="${escapeHtml(item.groupLabel || "")}">${escapeHtml(groupLabel)}</span>` : ""}
          </div>
        </div>
      </div>
      <div class="card-actions opportunity-card-actions">
        ${actions.join("")}
      </div>
    </article>
  `;
}

function computeUrgentCount(items = []) {
  return (items || []).filter((item) => {
    const label = window.AuctionWatchRepository?.normalizeText?.(item.urgencyLabel || "") || "";
    return label.includes("inminente") || label.includes("hoy") || label.includes("pronto");
  }).length;
}

async function loadAuctionWatchSnapshot() {
  return window.AuctionWatchRepository?.loadSnapshot?.() || null;
}

async function fetchManualRunJson(url, options = {}) {
  const AbortControllerClass = window.AbortController || (
    typeof AbortController === "function" ? AbortController : null
  );
  const controller = AbortControllerClass ? new AbortControllerClass() : null;
  let timer = null;
  try {
    const request = fetch(url, {
      ...options,
      ...(controller ? { signal: controller.signal } : {})
    });
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => {
        controller?.abort();
        reject(new Error("La consulta de estado demoró demasiado."));
      }, 8000);
    });
    const response = await Promise.race([request, timeout]);
    if (!response.ok) throw new Error(`run request unavailable (${response.status})`);
    return await response.json();
  } finally {
    if (timer !== null) window.clearTimeout(timer);
  }
}

function renderSummaryCard(title, value, detail) {
  return `
    <article class="mini-card">
      <h4>${escapeHtml(title)}</h4>
      <strong>${escapeHtml(value)}</strong>
      <p class="muted">${escapeHtml(detail)}</p>
    </article>
  `;
}

function getSyncSourceLabel(sync = {}) {
  const labels = {
    export: "Servidor publicado",
    latest: "Última copia del servidor",
    server: "Servidor",
    runtime: "Runtime local",
    static: "Respaldo del paquete",
    none: "Sin fuente"
  };
  return labels[sync.source] || labels[sync.origin] || "Fuente desconocida";
}

function formatSyncTimestamp(raw = "", fallback = "sin fecha confirmada") {
  const parsed = new Date(raw);
  if (!raw || Number.isNaN(parsed.getTime())) return fallback;
  return new Intl.DateTimeFormat("es-UY", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit"
  }).format(parsed);
}

function renderSyncStatus(snapshot = {}, sync = {}) {
  const status = sync.status || "unavailable";
  const scanStatus = normalizeAuctionWatchScanStatus(sync.scanStatus || snapshot.scanStatus || snapshot.status);
  const labels = {
    ready: "Sincronizado",
    empty: "Sin coincidencias",
    stale: "Desactualizado",
    degraded: "Sincronización parcial",
    unavailable: "Sin conexión de datos"
  };
  const generatedAtLabel = formatSyncTimestamp(
    sync.acceptedAt || sync.generatedAt,
    snapshot.generatedAtLabel || "sin fecha confirmada"
  );
  const runId = sync.runId || snapshot.runId || "sin identificador";
  let message = "El snapshot está confirmado por la app.";

  if (status === "empty") {
    message = "La última corrida está confirmada y no dejó publicaciones activas.";
  } else if (status === "stale") {
    message = "La última publicación está vencida. Las tarjetas pueden no representar el estado actual.";
  } else if (status === "degraded") {
    message = scanStatus === "failed"
      ? "La última corrida falló. Conservamos el inventario publicado como referencia, sin presentarlo como una actualización completa."
      : sync.origin === "server"
        ? "La app conserva el servidor como fuente de verdad, pero detectó una corrida parcial o un respaldo diferente."
        : "Mostramos un respaldo de solo lectura porque el servidor no confirmó un snapshot utilizable.";
  } else if (status === "unavailable") {
    message = "No encontramos un snapshot verificable. No asumimos que la lista esté vacía.";
  } else if (sync.divergence) {
    message = "El servidor está confirmado; existe un respaldo diferente que no reemplaza la fuente de verdad.";
  }

  const attention = ["stale", "degraded", "unavailable"].includes(status) || sync.divergence;
  return `
    <aside class="opportunity-sync-status opportunity-sync-status--${escapeHtml(status)}" ${attention ? 'role="status" aria-live="polite"' : ""}>
      <div class="opportunity-sync-status__head">
        <span class="opportunity-sync-state">${escapeHtml(labels[status] || labels.unavailable)}</span>
        <p>${escapeHtml(message)}</p>
      </div>
      <dl class="opportunity-sync-meta">
        <div><dt>Última sync</dt><dd>${escapeHtml(generatedAtLabel)}</dd></div>
        <div><dt>Fuente</dt><dd>${escapeHtml(getSyncSourceLabel(sync))}</dd></div>
        <div><dt>Corrida</dt><dd title="${escapeHtml(runId)}">${escapeHtml(runId)}</dd></div>
      </dl>
      ${attention ? `<button class="btn-link" type="button" data-opportunity-retry>Verificar de nuevo</button>` : ""}
    </aside>
  `;
}

function renderEmptyState(view, dismissedCount = 0, followingCount = 0, sync = {}) {
  if (["unavailable", "stale"].includes(sync.status) && view === "active") {
    const stale = sync.status === "stale";
    return `
      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>${stale ? "No podemos confirmar oportunidades nuevas" : "No pudimos cargar las oportunidades"}</h2>
          <p class="muted">${
            stale
              ? "El último snapshot está desactualizado. Lo conservamos como referencia, pero no lo tratamos como una lista vigente."
              : "El servidor y los respaldos no entregaron un snapshot verificable. Podés reintentar sin perder descartes ni seguimientos."
          }</p>
        </div>
      </article>
    `;
  }
  if (view === "dismissed") {
    return `
      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>Sin oportunidades descartadas</h2>
          <p class="muted">Los lotes que descartes van a quedar acá por si después querés restaurarlos.</p>
        </div>
      </article>
    `;
  }
  if (view === "following") {
    return `
      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>Sin publicaciones seguidas</h2>
          <p class="muted">Usá Seguir para dejar arriba los lotes que estás evaluando u ofertaste.</p>
        </div>
      </article>
    `;
  }
  if (sync.status === "degraded") {
    const failedScan = normalizeAuctionWatchScanStatus(sync.scanStatus) === "failed";
    return `
      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>${failedScan ? "La corrida fallida no confirmó oportunidades" : "Sin oportunidades confirmadas por el servidor"}</h2>
          <p class="muted">${
            failedScan
              ? "Conservamos este snapshot como referencia, pero no asumimos que el inventario esté vacío."
              : "El respaldo disponible no contiene publicaciones activas. Verificá la sincronización antes de asumir que no hay novedades."
          }</p>
        </div>
      </article>
    `;
  }
  return `
    <article class="detail-block detail-block--wide">
      <div class="section-head">
        <h2>Sin oportunidades activas</h2>
        <p class="muted">
          ${
            followingCount
              ? `No hay publicaciones nuevas por revisar. Tenés ${followingCount} ${followingCount === 1 ? "publicación guardada" : "publicaciones guardadas"} en Siguiendo.`
              : dismissedCount
              ? `Ya revisaste todo. Tenés ${dismissedCount} ${dismissedCount === 1 ? "oportunidad descartada" : "oportunidades descartadas"} en el historial.`
              : `La corrida ${escapeHtml(sync.runId || "confirmada")} no dejó publicaciones abiertas.`
          }
        </p>
        <div class="card-actions opportunity-empty-actions">
          ${followingCount ? `<button class="btn-link btn-primary" type="button" data-opportunity-view="following">Ver siguiendo (${followingCount})</button>` : ""}
          ${dismissedCount ? `<button class="btn-link" type="button" data-opportunity-view="dismissed">Ver descartadas (${dismissedCount})</button>` : ""}
        </div>
      </div>
    </article>
  `;
}

function uniqueOpportunities(items = []) {
  const repo = window.AuctionWatchRepository;
  const seen = new Set();
  return (items || []).filter((item) => {
    const key = repo?.getOpportunityKey?.(item) || item.id || item.lotUrl || "";
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function getPageOpportunities() {
  const repo = window.AuctionWatchRepository;
  const snapshot = repo?.getSnapshot?.();
  const featured = repo?.getFeaturedOpportunity?.() || null;
  const active = uniqueOpportunities([featured, ...(snapshot?.matches || [])].filter(Boolean));
  const dismissed = uniqueOpportunities(repo?.getDismissedOpportunities?.() || []);
  const following = uniqueOpportunities(repo?.getFollowingOpportunities?.() || []);
  return { snapshot, featured, active, following, dismissed };
}

function selectInitialOpportunityView() {
  const { active, following } = getPageOpportunities();
  if (!active.length && following.length) opportunityView = "following";
}

function renderViewSwitch(activeCount, followingCount, dismissedCount) {
  return `
    <div class="opportunity-view-switch" role="group" aria-label="Estado de las oportunidades">
      <button class="brand-pill ${opportunityView === "active" ? "active" : ""}" type="button" data-opportunity-view="active" aria-pressed="${opportunityView === "active"}">
        Activas (${activeCount})
      </button>
      <button class="brand-pill ${opportunityView === "following" ? "active" : ""}" type="button" data-opportunity-view="following" aria-pressed="${opportunityView === "following"}">
        Siguiendo (${followingCount})
      </button>
      <button class="brand-pill ${opportunityView === "dismissed" ? "active" : ""}" type="button" data-opportunity-view="dismissed" aria-pressed="${opportunityView === "dismissed"}">
        Descartadas (${dismissedCount})
      </button>
    </div>
  `;
}

function normalizeAuctionWatchScanStatus(value = "") {
  const normalizeScanStatus = window.AuctionWatchRepository?.normalizeScanStatus;
  if (typeof normalizeScanStatus === "function") return normalizeScanStatus(value);
  const status = String(value || "").trim().toLowerCase();
  return { partial_failure: "partial", failure: "failed" }[status] || status;
}

function getManualRunReceipt(request = {}) {
  const nested = {
    ...(request.snapshot && typeof request.snapshot === "object" ? request.snapshot : {}),
    ...(request.receipt && typeof request.receipt === "object" ? request.receipt : {})
  };
  const scanStatusValue = request.scanStatus || nested.scanStatus || "";
  return {
    runId: String(request.runId || nested.runId || "").trim(),
    snapshotHash: String(request.snapshotHash || nested.snapshotHash || "").trim(),
    scanStatus: normalizeAuctionWatchScanStatus(scanStatusValue),
    snapshotStatus: String(request.snapshotStatus || nested.snapshotStatus || nested.status || "").trim().toLowerCase(),
    emailStatus: String(request.emailStatus || nested.emailStatus || "").trim().toLowerCase(),
    overallStatus: String(request.overallStatus || nested.overallStatus || "").trim().toLowerCase()
  };
}

function normalizeManualRunStatus(value = "") {
  const status = String(value || "").trim().toLowerCase();
  const aliases = {
    queued: "pending",
    in_progress: "running",
    processing: "running",
    delivering: "delivery_pending"
  };
  return aliases[status] || status;
}

function isManualDeliveryPending(request = {}) {
  const lifecycleStatus = normalizeManualRunStatus(request.status);
  const receipt = getManualRunReceipt(request);
  return lifecycleStatus === "delivery_pending" ||
    receipt.overallStatus === "delivery_pending" ||
    receipt.emailStatus === "pending";
}

function getManualRunDisplayStatus(request = {}) {
  const lifecycleStatus = normalizeManualRunStatus(request.status) || "idle";
  if (isManualDeliveryPending(request)) return "delivery_pending";
  if (["pending", "running"].includes(lifecycleStatus)) return lifecycleStatus;
  if (getManualRunReceipt(request).emailStatus === "uncertain") return "email_uncertain";
  return lifecycleStatus;
}

function isManualRunBusy(request = {}) {
  return ["pending", "running", "delivery_pending"].includes(getManualRunDisplayStatus(request));
}

function isRecentRunRequest(request = {}) {
  const raw = request.finishedAt || request.startedAt || request.requestedAt || "";
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) && Date.now() - timestamp < 2 * 60 * 60 * 1000;
}

function isSnapshotConfirmedForRun(request = {}) {
  const receipt = getManualRunReceipt(request);
  const sync = getAuctionWatchSyncState();
  if (sync.origin !== "server" || ["stale", "unavailable"].includes(sync.status)) return false;
  if (receipt.snapshotStatus && ["failed", "unavailable", "not_configured", "skipped"].includes(receipt.snapshotStatus)) {
    return false;
  }
  const hasReceiptIdentity = Boolean(receipt.snapshotHash || receipt.runId);
  if (receipt.snapshotHash && (!sync.snapshotHash || sync.snapshotHash !== receipt.snapshotHash)) return false;
  if (receipt.runId && (!sync.runId || sync.runId !== receipt.runId)) return false;
  if (hasReceiptIdentity) return true;

  const requestedAt = Date.parse(request.startedAt || request.requestedAt || "");
  const generatedAt = Date.parse(sync.generatedAt || "");
  return Number.isFinite(requestedAt) && Number.isFinite(generatedAt) && generatedAt >= requestedAt - 60_000;
}

function hasTerminalSnapshotFailure(request = {}) {
  const status = getManualRunReceipt(request).snapshotStatus;
  return ["failed", "unavailable", "not_configured", "not_published", "publish_failed", "skipped"].includes(status);
}

function renderManualRunControl() {
  const status = getManualRunDisplayStatus(manualRunState || {});
  const busy = isManualRunBusy(manualRunState || {}) || manualRunSyncPending;
  const receipt = getManualRunReceipt(manualRunState || {});
  const emailUncertain = receipt.emailStatus === "uncertain";
  const labels = {
    pending: "Solicitud en cola…",
    running: "Buscando oportunidades…",
    delivery_pending: "Finalizando entrega…",
    completed: "Buscar otra vez",
    email_uncertain: "Buscar otra vez",
    failed: "Reintentar búsqueda"
  };
  let message = "Hace una búsqueda completa y envía el reporte por mail.";
  if (emailUncertain && manualRunSyncFailed) {
    message = "La corrida no pudo publicar el snapshot y el proveedor tampoco confirmó si el mail salió. No lo reenviamos automáticamente para evitar duplicados.";
  } else if (emailUncertain && manualRunSyncPending) {
    message = "La página todavía no confirmó el snapshot nuevo. Seguimos verificando solo esa publicación; el mail quedó sin confirmación y no se reenviará automáticamente.";
  } else if (manualRunSyncFailed) {
    message = "La corrida terminó, pero el servidor informó que no pudo publicar el snapshot. La página conserva la versión anterior.";
  } else if (manualRunSyncPending) {
    message = "La corrida terminó, pero la página todavía no confirmó el snapshot nuevo. Seguimos verificando automáticamente.";
  } else if (status === "pending") {
    message = "La solicitud quedó registrada. Seguimos consultando su estado sin crear otra búsqueda.";
  } else if (status === "running") {
    message = "El buscador está recorriendo los remates. Al terminar verificaremos por separado la publicación de la página y el estado del mail.";
  } else if (status === "delivery_pending") {
    message = isSnapshotConfirmedForRun(manualRunState || {})
      ? "La página ya confirmó esta corrida; la entrega del mail sigue pendiente. Seguimos consultando el mismo resultado sin repetir la búsqueda."
      : "La corrida terminó, pero su publicación o entrega sigue pendiente. Seguimos consultando el mismo resultado sin repetir la búsqueda.";
  } else if (status === "email_uncertain") {
    message = "La búsqueda terminó, pero el proveedor no confirmó si el mail salió. No lo reenviamos automáticamente para evitar duplicados.";
  } else if (status === "completed") {
    const scanPrefix = receipt.scanStatus === "partial" ? "Corrida parcial terminada y página confirmada." : "Corrida terminada y página confirmada.";
    message = `${scanPrefix}${
      ["sent", "success", "sent_via_smtp"].includes(receipt.emailStatus)
        ? " Mail enviado."
        : receipt.emailStatus === "failed"
          ? " El mail no pudo enviarse."
          : ""
    }`;
  } else if (status === "failed") {
    message = `La corrida no terminó correctamente${manualRunState?.detail ? `: ${manualRunState.detail}` : "."}`;
  }
  const visibleMessage = manualRunPollIssue && busy
    ? `${message} ${manualRunPollIssue}`.trim()
    : message;
  const visualStatus = manualRunSyncFailed
    ? "failed"
    : manualRunSyncPending || status === "delivery_pending"
      ? "syncing"
      : status === "email_uncertain"
        ? "failed"
        : status;
  const buttonLabel = emailUncertain
    ? "Buscar otra vez"
    : manualRunSyncFailed
      ? "Reintentar búsqueda"
      : status === "delivery_pending"
        ? labels.delivery_pending
        : manualRunSyncPending
          ? "Sincronizando página…"
          : labels[status] || "Buscar ahora y enviar mail";
  return `
    <div class="opportunity-run-control" data-run-status="${escapeHtml(visualStatus)}">
      <div>
        <strong>Actualizar Auction Watch</strong>
        <span>${escapeHtml(visibleMessage)}</span>
      </div>
      <button class="btn-link opportunity-run-button" type="button" data-auction-run-now ${busy ? "disabled" : ""}>
        ${escapeHtml(buttonLabel)}
      </button>
    </div>
  `;
}

function renderRunIssues(snapshot) {
  const issues = Array.isArray(snapshot?.issues) ? snapshot.issues : [];
  const rawScanStatus = snapshot?.scanStatus || snapshot?.status || "";
  const scanStatus = normalizeAuctionWatchScanStatus(rawScanStatus);
  if (!["partial", "failed"].includes(scanStatus) && !issues.length) return "";

  const details = issues.length
    ? issues
        .map(
          (issue) => `
            <li>
              <strong>${escapeHtml(issue.sourceLabel || "Fuente externa")}</strong>
              <span>${escapeHtml(issue.summary || "La fuente no pudo completar la consulta.")}</span>
            </li>
          `
        )
        .join("")
    : `<li><span>No todas las fuentes pudieron completar la consulta.</span></li>`;

  return `
    <aside class="opportunity-run-issues" role="status" aria-label="Detalle de la última corrida ${scanStatus === "failed" ? "fallida" : "parcial"}">
      <div class="opportunity-run-issues__head">
        <strong>${scanStatus === "failed" ? "La última corrida falló" : "La última corrida quedó parcial"}</strong>
        <span>${
          scanStatus === "failed"
            ? "No todas las fuentes pudieron confirmar un inventario nuevo. Conservamos el estado visible con advertencia."
            : "El resto de las fuentes sí se procesó y las oportunidades disponibles se actualizaron."
        }</span>
      </div>
      <ul>${details}</ul>
    </aside>
  `;
}

function renderFeedback() {
  if (!opportunityFeedback?.message) {
    return `<p class="opportunity-feedback" role="status" aria-live="polite"></p>`;
  }
  return `
    <div class="opportunity-feedback opportunity-feedback--visible" id="opportunityFeedback" role="status" aria-live="polite" tabindex="-1">
      <span id="opportunityFeedbackText"></span>
      ${
        opportunityFeedback.undoKey
          ? `<button class="btn-link" type="button" data-opportunity-action="restore" data-opportunity-key="${escapeHtml(opportunityFeedback.undoKey)}">Deshacer</button>`
          : ""
      }
    </div>
  `;
}

function renderPage({ focusSelector = "" } = {}) {
  const root = document.getElementById("opportunitiesRoot");
  if (!root) return;

  const repo = window.AuctionWatchRepository;
  const { snapshot, featured, active, following, dismissed } = getPageOpportunities();
  const sync = getAuctionWatchSyncState();
  const featuredKey = repo?.getOpportunityKey?.(featured) || featured?.id || "";
  const activeMatches = active.filter((item) => (repo?.getOpportunityKey?.(item) || item.id || "") !== featuredKey);
  const watchlistCount = active.filter((item) => item.watchlist === true).length;
  const urgentCount = computeUrgentCount(active);
  const updatedLabel = snapshot?.generatedAtLabel || "sin dato";
  const opportunitiesHref = window.CollectionRepository?.getHomeHref?.() || "./index.html";
  const showingDismissed = opportunityView === "dismissed";
  const showingFollowing = opportunityView === "following";
  const visibleItems = showingDismissed ? dismissed : showingFollowing ? following : activeMatches;
  const failedScan = normalizeAuctionWatchScanStatus(sync.scanStatus || snapshot?.scanStatus || snapshot?.status) === "failed";
  const inventoryIsReference = ["stale", "unavailable"].includes(sync.status) || failedScan;
  const activeSummaryTitle = inventoryIsReference ? "En snapshot" : "Activas ahora";
  const activeSummaryDetail = inventoryIsReference
    ? "sin vigencia confirmada"
    : "publicaciones nuevas por revisar";

  root.innerHTML = `
    <div class="back-link">
      <a class="btn-link" href="${escapeHtml(opportunitiesHref)}">Volver al inicio</a>
    </div>
    <section class="detail-shell">
      <article class="detail-card">
        <div class="detail-main detail-main--full">
          <p class="eyebrow">Auction Watch</p>
          <h1>Oportunidades</h1>
          <p class="subtitle">Acá quedan las publicaciones activas que encontró el agente en todas las fuentes monitoreadas, con foco en consolas, controles, juegos y hardware relacionado.</p>
          <div class="mini-grid opportunity-summary-grid">
            ${renderSummaryCard(activeSummaryTitle, String(active.length), activeSummaryDetail)}
            ${renderSummaryCard("Siguiendo", String(following.length), "publicaciones guardadas para decidir")}
            ${renderSummaryCard("Cierran pronto", String(urgentCount), "urgencia hoy, pronto o inminente")}
            ${renderSummaryCard("Descartadas", String(dismissed.length), "historial reversible de publicaciones vistas")}
          </div>
          ${renderSyncStatus(snapshot, sync)}
          ${renderManualRunControl()}
          ${renderRunIssues(snapshot)}
          ${renderViewSwitch(active.length, following.length, dismissed.length)}
          ${renderFeedback()}
        </div>
      </article>
    </section>
    <section class="detail-sections">
      ${
        featured && !showingDismissed && !showingFollowing
          ? `
        <article class="detail-block detail-block--wide">
          <div class="section-head">
            <h2>Lote destacado</h2>
            <p class="muted">El agente lo deja arriba para que no se mezcle con el resto.</p>
          </div>
          ${renderOpportunityCard(featured, { featured: true })}
        </article>
      `
          : ""
      }
      ${
        visibleItems.length
          ? `
        <article class="detail-block detail-block--wide">
          <div class="section-head">
            <h2>${showingDismissed ? "Publicaciones descartadas" : showingFollowing ? "Publicaciones que seguís" : "Publicaciones activas"}</h2>
            <p class="muted">${
              showingDismissed
                ? "No aparecen en la portada ni en los próximos mails. Podés restaurarlas cuando quieras."
                : showingFollowing
                  ? "Quedan separadas para que las puedas revisar u ofertar sin perderlas entre el resto."
                  : `Se mantienen visibles mientras la subasta siga abierta. Última actualización: ${escapeHtml(updatedLabel)}.`
            }</p>
          </div>
          <div class="opportunity-list">
            ${visibleItems.map((item) => renderOpportunityCard(item, { dismissed: showingDismissed })).join("")}
          </div>
        </article>
      `
          : featured && !showingDismissed && !showingFollowing
            ? ""
            : renderEmptyState(opportunityView, dismissed.length, following.length, sync)
      }
    </section>
  `;

  window.requestAnimationFrame(() => {
    const feedbackText = document.getElementById("opportunityFeedbackText");
    if (feedbackText && opportunityFeedback?.message) {
      feedbackText.textContent = opportunityFeedback.message;
    }
    if (focusSelector) root.querySelector(focusSelector)?.focus();
  });
}

async function handleOpportunityClick(event) {
  const runNowButton = event.target.closest("[data-auction-run-now]");
  if (runNowButton) {
    if (isManualRunBusy(manualRunState || {}) || manualRunSyncPending) {
      scheduleManualRunPoll();
      return;
    }
    runNowButton.disabled = true;
    runNowButton.textContent = "Creando solicitud…";
    try {
      const payload = await fetchManualRunJson("./api/auction-watch/run-now", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Consolas-Auction-Watch": "1"
        },
        body: "{}"
      });
      manualRunState = payload.request || { status: "pending" };
      manualRunRequestedLocally = true;
      manualRunSyncPending = false;
      manualRunSyncFailed = false;
      manualRunPollIssue = "";
      renderPage({ focusSelector: "[data-auction-run-now]" });
      scheduleManualRunPoll();
    } catch (error) {
      console.error(error);
      manualRunState = { status: "failed", detail: "no se pudo crear la solicitud" };
      manualRunRequestedLocally = true;
      renderPage({ focusSelector: "[data-auction-run-now]" });
    }
    return;
  }

  const retryButton = event.target.closest("[data-opportunity-retry]");
  if (retryButton) {
    retryButton.disabled = true;
    retryButton.textContent = "Reintentando…";
    await loadAuctionWatchSnapshot();
    if (manualRunSyncPending && manualRunState?.status === "completed") {
      manualRunSyncPending = !isSnapshotConfirmedForRun(manualRunState);
    }
    renderPage({
      focusSelector:
        getAuctionWatchSyncState().status === "unavailable"
          ? "[data-opportunity-retry]"
          : "[data-opportunity-view=\"active\"]"
    });
    return;
  }

  const viewButton = event.target.closest("[data-opportunity-view]");
  if (viewButton) {
    const requestedView = viewButton.dataset.opportunityView || "active";
    opportunityView = ["active", "following", "dismissed"].includes(requestedView) ? requestedView : "active";
    opportunityFeedback = null;
    renderPage({ focusSelector: `[data-opportunity-view="${opportunityView}"]` });
    return;
  }

  const actionButton = event.target.closest("[data-opportunity-action]");
  if (!actionButton) return;

  const repo = window.AuctionWatchRepository;
  const key = actionButton.dataset.opportunityKey || "";
  const { active, following, dismissed } = getPageOpportunities();
  const item = [...active, ...following, ...dismissed].find((entry) => repo?.getOpportunityKey?.(entry) === key);
  if (!item) return;

  actionButton.disabled = true;
  const action = actionButton.dataset.opportunityAction;
  actionButton.textContent = action === "restore" ? "Restaurando…" : action === "follow" ? "Guardando…" : action === "unfollow" ? "Quitando…" : "Descartando…";

  try {
    if (action === "restore") {
      await repo.restoreOpportunity(item, { requireServer: true });
      opportunityFeedback = { message: "Descarte deshecho. Volverá a aparecer si el lote sigue activo." };
    } else if (action === "follow") {
      await repo.followOpportunity(item, { requireServer: true });
      opportunityFeedback = { message: "Listo: queda en Siguiendo y arriba de las oportunidades activas." };
    } else if (action === "unfollow") {
      await repo.unfollowOpportunity(item, { requireServer: true });
      opportunityFeedback = { message: "Dejó de estar en seguimiento." };
    } else {
      await repo.dismissOpportunity(item, { requireServer: true });
      opportunityFeedback = {
        message: "Oportunidad descartada. No aparecerá en los próximos mails.",
        undoKey: key
      };
    }
    renderPage({
      focusSelector: opportunityFeedback.undoKey
        ? "#opportunityFeedback [data-opportunity-action=\"restore\"]"
        : "#opportunityFeedback"
    });
  } catch (error) {
    console.error(error);
    const detail = String(error?.message || "").trim();
    opportunityFeedback = {
      message: detail
        ? `No se pudo guardar el cambio: ${detail}`
        : "No se pudo guardar el cambio en el servidor. Probá de nuevo en un momento."
    };
    renderPage({ focusSelector: "#opportunityFeedback" });
  }
}

async function refreshManualRunState() {
  try {
    const payload = await fetchManualRunJson("./api/auction-watch/run-now", { cache: "no-store" });
    const request = payload.request;
    if (!request) throw new Error("La app no devolvió el estado de la solicitud.");
    const requestStatus = getManualRunDisplayStatus(request);
    const alreadyTracking = isManualRunBusy(manualRunState || {});
    const lifecycleStatus = normalizeManualRunStatus(request.status);
    const recentCompletion = requestStatus === "email_uncertain" || (
      ["completed", "failed"].includes(requestStatus) &&
      isRecentRunRequest(request) &&
      ["completed", "failed"].includes(lifecycleStatus)
    );
    if (
      !manualRunRequestedLocally &&
      !alreadyTracking &&
      !manualRunSyncPending &&
      !isManualRunBusy(request) &&
      !recentCompletion
    ) return;
    manualRunState = request;
    manualRunRequestedLocally = true;
    manualRunPollIssue = "";
    if (requestStatus === "delivery_pending") {
      await loadAuctionWatchSnapshot();
      manualRunSyncPending = false;
      manualRunSyncFailed = false;
    } else if (["completed", "failed", "email_uncertain"].includes(requestStatus)) {
      await loadAuctionWatchSnapshot();
      manualRunSyncFailed = hasTerminalSnapshotFailure(request);
      const publishedSnapshot = getManualRunReceipt(request).snapshotStatus === "published";
      manualRunSyncPending = publishedSnapshot && !manualRunSyncFailed && !isSnapshotConfirmedForRun(request);
    } else {
      manualRunSyncPending = false;
      manualRunSyncFailed = false;
    }
    renderPage({ focusSelector: "[data-auction-run-now]" });
    if (isManualRunBusy(request) || manualRunSyncPending) scheduleManualRunPoll();
  } catch (error) {
    console.info("[AuctionWatch] manual run status unavailable", error);
    manualRunPollIssue = "No pudimos leer el estado; vamos a reintentar.";
    if (isManualRunBusy(manualRunState || {}) || manualRunSyncPending) {
      renderPage({ focusSelector: "[data-auction-run-now]" });
      scheduleManualRunPoll();
    }
  }
}

function scheduleManualRunPoll() {
  if (manualRunPollTimer) window.clearTimeout(manualRunPollTimer);
  manualRunPollTimer = window.setTimeout(() => {
    manualRunPollTimer = null;
    refreshManualRunState();
  }, 5000);
}

async function init() {
  await loadAuctionWatchSnapshot();
  await refreshManualRunState();
  selectInitialOpportunityView();
  renderPage();
  document.getElementById("opportunitiesRoot")?.addEventListener("click", handleOpportunityClick);
}

init().catch((error) => {
  console.error(error);
  renderPage();
});
