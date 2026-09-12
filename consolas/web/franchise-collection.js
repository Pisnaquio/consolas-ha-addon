(() => {
  /**
   * Franchise Collection Tracker — primera configuración: God of War.
   *
   * Arquitectura genérica (docs/BACKLOG.md): esta página no sabe nada
   * específico de God of War — lee `?id=<franchiseId>` y todo lo demás sale
   * del catálogo (`web/data/franchise-collections.json`) y del estado real
   * de colección, vía `FranchiseCollectionRepository`.
   *
   * Reglas que gobiernan todo lo que sigue:
   * - Registrados siempre antes que deseados/anunciados.
   * - Cobertura de saga, estantería física y crown jewels son tres números
   *   distintos — nunca se mezclan en un solo score.
   * - Ninguna escritura pasa por otro lado que no sea
   *   `FranchiseCollectionRepository` (que a su vez usa `CollectionRepository`).
   * - Texto externo/editorial se escapa igual que en cualquier otra pantalla.
   */

  const repo = window.FranchiseCollectionRepository;
  const root = document.getElementById("franchiseCollectionRoot");

  const OWNERSHIP_OPTIONS = [
    { id: "none", label: "No tengo" },
    { id: "physical", label: "Físico" },
    { id: "digital", label: "Digital" },
    { id: "both", label: "Ambos" }
  ];

  const RELEASE_TYPE_LABELS = {
    original: "Original",
    port: "Port",
    remaster: "Remaster",
    collection: "Compilación",
    "special-edition": "Edición especial"
  };

  const DELIVERY_LABELS = {
    disc: "Disco",
    umd: "UMD",
    digital: "Digital",
    voucher: "Código"
  };

  const CODE_STATUS_LABELS = {
    "not-applicable": "",
    included: "Incluye código",
    "possibly-expired": "Código posiblemente ya usado"
  };

  const CHECKLIST_LABELS = {
    artCard: "Tarjeta de arte",
    artbook: "Artbook",
    collectionBluRay: "Blu-ray de God of War Collection",
    diceSet: "Set de dados",
    disc: "Disco del juego",
    draupnirRing: "Réplica del anillo Draupnir",
    figureOrStatue: "Figura o estatua",
    gameVoucher: "Código de descarga del juego",
    gow3Disc: "Disco de God of War III",
    knowledgeKeepersShrineBox: "Caja santuario del Guardián del Conocimiento",
    mimirHeadStatue: "Estatua de la cabeza de Mímir",
    mjolnirReplica: "Réplica del Mjölnir",
    outerBox: "Caja exterior",
    pandorasBoxReplica: "Réplica de la Caja de Pandora",
    pinSet: "Set de pines",
    postcards: "Postales",
    soundtrack: "Soundtrack",
    soundtrackCds: "Soundtrack en CD",
    steelbook: "Steelbook",
    steelbookDisplayOnly: "Steelbook (solo exhibición, sin disco)",
    vanirTwinsCarvings: "Talladas de los gemelos Vanir",
    vinylRecord: "Vinilo",
    yggdrasilMap: "Mapa de Yggdrasil"
  };

  let model = null;
  let error = "";
  let feedback = "";
  let feedbackTone = "info";
  let busy = false;
  const openChecklists = new Set();

  const filters = {
    ownership: "all", // all | owned | missing
    platform: "all",
    era: "all",
    query: ""
  };

  const escapeHtml = (value = "") =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  function setFeedback(message, tone = "info") {
    feedback = message || "";
    feedbackTone = tone;
  }

  function platformLabel(platformId) {
    return model?.consolesById?.[platformId]?.nombre || platformId || "Sin plataforma";
  }

  async function reload() {
    const franchiseId = new URLSearchParams(window.location.search).get("id") || "god-of-war";
    model = await repo.load(franchiseId);
  }

  async function perform(pending, action, success) {
    if (busy) return;
    busy = true;
    setFeedback(pending);
    render();
    try {
      await action();
      await reload();
      busy = false;
      setFeedback(success, "success");
      render();
    } catch (err) {
      busy = false;
      setFeedback(err.message, "error");
      render();
    }
  }

  // --- Hero: progreso y próxima acción ---------------------------------------

  function progressBar(label, progress) {
    return `<div class="franchise-progress">
      <div class="franchise-progress-head">
        <span>${escapeHtml(label)}</span>
        <strong>${progress.owned ?? progress.covered} / ${progress.total}</strong>
      </div>
      <div class="franchise-progress-track"><span style="width:${progress.percent}%"></span></div>
    </div>`;
  }

  function nextActionCard(nextAction) {
    if (!nextAction) {
      return `<p class="franchise-next-action is-done">No hay una acción obvia siguiente: lo que falta no depende de una plataforma nueva.</p>`;
    }
    if (nextAction.type === "platform") {
      const count = nextAction.newWorkIds.length;
      return `<p class="franchise-next-action">
        <strong>Próxima compra sugerida:</strong> conseguir <strong>${escapeHtml(platformLabel(nextAction.platformId))}</strong>
        habilita ${count} ${count === 1 ? "obra que hoy no podés jugar" : "obras que hoy no podés jugar"}.
      </p>`;
    }
    const release = model.releases.find((item) => item.id === nextAction.releaseId);
    return `<p class="franchise-next-action">
      <strong>Próxima compra sugerida:</strong> ${escapeHtml(release?.title || nextAction.releaseId)}
      (${escapeHtml(platformLabel(release?.platformId))}) — el lanzamiento físico principal que todavía falta.
    </p>`;
  }

  function heroSection() {
    const p = model.progress;
    return `<header class="detail-hero franchise-hero">
      <div>
        <p class="eyebrow">Franchise Collection Tracker</p>
        <h1>${escapeHtml(model.franchise.name)}</h1>
        <p>${escapeHtml(model.franchise.summary || "")}</p>
        ${nextActionCard(model.nextAction)}
      </div>
      <div class="franchise-progress-grid">
        ${progressBar("Cobertura de saga", p.sagaCoverage)}
        ${progressBar("Estantería física", p.physicalShelf)}
        ${progressBar("Crown jewels", p.crownJewels)}
      </div>
    </header>`;
  }

  // --- Filtros -----------------------------------------------------------

  function filterBar() {
    const platforms = [...new Set(model.releases.map((r) => r.platformId))];
    return `<nav class="franchise-filters" aria-label="Filtrar la saga">
      <div class="pill-group">
        <button class="pill${filters.ownership === "all" ? " is-active" : ""}" type="button" data-ownership="all">Todas</button>
        <button class="pill${filters.ownership === "owned" ? " is-active" : ""}" type="button" data-ownership="owned">Tengo</button>
        <button class="pill${filters.ownership === "missing" ? " is-active" : ""}" type="button" data-ownership="missing">Me falta</button>
      </div>
      <select data-filter="platform" aria-label="Plataforma">
        <option value="all">Todas las plataformas</option>
        ${platforms.map((id) => `<option value="${escapeHtml(id)}"${filters.platform === id ? " selected" : ""}>${escapeHtml(platformLabel(id))}</option>`).join("")}
      </select>
      <select data-filter="era" aria-label="Era">
        <option value="all">Todas las eras</option>
        ${(model.franchise.eras || []).map((era) => `<option value="${escapeHtml(era.id)}"${filters.era === era.id ? " selected" : ""}>${escapeHtml(era.label)}</option>`).join("")}
      </select>
      <input type="search" data-filter="query" placeholder="Buscar en la saga…" value="${escapeHtml(filters.query)}" />
    </nav>`;
  }

  function workMatchesFilters(work) {
    if (filters.ownership === "owned" && !work.isCovered) return false;
    if (filters.ownership === "missing" && work.isCovered) return false;
    if (filters.era !== "all" && work.era !== filters.era) return false;
    if (filters.platform !== "all" && !work.releases.some((r) => r.platformId === filters.platform)) return false;
    if (filters.query) {
      const q = filters.query.toLowerCase();
      const haystack = [work.title, ...work.releases.map((r) => r.title)].join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  }

  // --- Cards de obra y lanzamiento --------------------------------------

  function ownershipControls(release) {
    return `<div class="franchise-ownership" role="group" aria-label="Marcar propiedad">
      ${OWNERSHIP_OPTIONS.map(
        (option) =>
          `<button class="btn-link${release.ownershipType === option.id ? " is-on" : ""}" type="button"
             data-release-ownership="${escapeHtml(release.id)}" data-value="${option.id}"${busy ? " disabled" : ""}>
             ${escapeHtml(option.label)}
           </button>`
      ).join("")}
    </div>`;
  }

  function radarSearchHref(release) {
    const params = new URLSearchParams({
      open: "create",
      prefillName: `${release.title} (${platformLabel(release.platformId)})`,
      prefillPlatform: platformLabel(release.platformId)
    });
    return `./chasing-games.html?${params.toString()}`;
  }

  function priceReferenceBlock(release) {
    const range = release.priceRange;
    if (!range || (range.low == null && range.mid == null && range.high == null)) return "";
    const parts = [];
    if (range.low != null) parts.push(`desde USD ${range.low}`);
    if (range.mid != null) parts.push(`típico USD ${range.mid}`);
    if (range.high != null) parts.push(`hasta USD ${range.high}`);
    return `<p class="franchise-release-price">${escapeHtml(parts.join(" · "))}</p>`;
  }

  function releaseChip(release) {
    const delivery = (release.deliveryByComponent || []).map((k) => DELIVERY_LABELS[k] || k).join(" + ");
    const codeNote = CODE_STATUS_LABELS[release.codeStatus] || "";
    const showRadarCta = release.isPrimaryPhysicalTarget && !release.isOwnedPhysical;
    return `<article class="franchise-release${release.isOwned ? " is-owned" : ""}">
      <div class="franchise-release-head">
        ${release.coverUrl ? `<img class="franchise-release-cover" src="${escapeHtml(release.coverUrl)}" alt="" loading="lazy" decoding="async" />` : ""}
        <div>
          <p class="franchise-release-title">${escapeHtml(release.title)}</p>
          <p class="franchise-release-meta">
            ${escapeHtml(platformLabel(release.platformId))} · ${release.year || "s/f"} ·
            ${escapeHtml(RELEASE_TYPE_LABELS[release.releaseType] || release.releaseType)}
            ${delivery ? ` · ${escapeHtml(delivery)}` : ""}
          </p>
          ${priceReferenceBlock(release)}
        </div>
        ${release.physicalStatus === "not-announced" ? `<span class="chip">Sin edición física anunciada</span>` : ""}
      </div>
      ${release.notes ? `<p class="franchise-release-note">${escapeHtml(release.notes)}</p>` : ""}
      ${codeNote ? `<p class="franchise-release-note is-warning">${escapeHtml(codeNote)}</p>` : ""}
      ${release.verificationPending ? `<p class="franchise-release-note is-pending">Detalle en verificación — no tratar como confirmado al 100%.</p>` : ""}
      <div class="franchise-release-actions">
        ${ownershipControls(release)}
        ${showRadarCta ? `<a class="btn-link" href="${escapeHtml(radarSearchHref(release))}">Buscar con Radar</a>` : ""}
      </div>
    </article>`;
  }

  function eraLabel(eraId) {
    return model.franchise.eras?.find((era) => era.id === eraId)?.label || eraId;
  }

  function workCard(work) {
    return `<article class="detail-block franchise-work${work.isCovered ? " is-covered" : ""}">
      <div class="franchise-work-head">
        <div>
          <p class="eyebrow">${escapeHtml(eraLabel(work.era))}${work.year ? ` · ${work.year}` : ""}</p>
          <h2>${escapeHtml(work.title)}</h2>
        </div>
        <span class="chip ${work.isCovered ? "is-owned" : work.workStatus === "announced" ? "is-announced" : "is-missing"}">
          ${work.isCovered ? "Cubierta" : work.workStatus === "announced" ? "Anunciada" : "Sin cubrir"}
        </span>
      </div>
      ${work.notes ? `<p class="muted">${escapeHtml(work.notes)}</p>` : ""}
      <div class="franchise-releases">${work.releases.map(releaseChip).join("") || `<p class="muted">Sin lanzamientos cargados todavía.</p>`}</div>
    </article>`;
  }

  function timelineSortRank(work) {
    // Registrados primero (regla obligatoria de AGENTS.md), lanzados antes
    // que anunciados, y un año real siempre antes que "sin fecha todavía" —
    // año nulo no puede colarse al principio de la línea de tiempo.
    if (work.isCovered) return 0;
    if (work.isDenominator) return 1;
    return 2;
  }

  function timelineSection() {
    const works = model.works
      .filter(workMatchesFilters)
      .sort((a, b) => {
        const rankDiff = timelineSortRank(a) - timelineSortRank(b);
        if (rankDiff !== 0) return rankDiff;
        if (a.year == null && b.year == null) return 0;
        if (a.year == null) return 1;
        if (b.year == null) return -1;
        return a.year - b.year;
      });
    return `<section class="franchise-section">
      <div class="section-head"><h2>La saga</h2><p class="muted">Registrados primero, después lo que falta.</p></div>
      ${works.length ? works.map(workCard).join("") : `<p class="muted">Nada coincide con estos filtros.</p>`}
    </section>`;
  }

  // --- Compilaciones y remasters ------------------------------------------

  function compilationsSection() {
    const items = model.releases.filter((r) => ["collection", "remaster", "port"].includes(r.releaseType));
    if (!items.length) return "";
    return `<section class="franchise-section">
      <div class="section-head"><h2>Compilaciones y remasters</h2><p class="muted">Cubren la saga, no reemplazan la propiedad del original.</p></div>
      <div class="franchise-releases">${items.map(releaseChip).join("")}</div>
    </section>`;
  }

  // --- Crown jewels y hardware --------------------------------------------

  function checklistBlock(item) {
    const keys = item.completenessChecklist;
    if (!keys?.length) return "";
    const done = keys.filter((key) => item.checklist?.[key]).length;
    return `<details class="franchise-checklist" data-checklist-for="${escapeHtml(item.id)}"${openChecklists.has(item.id) ? " open" : ""}>
      <summary>Completitud (${done}/${keys.length})</summary>
      <ul>
        ${keys
          .map(
            (key) => `<li>
              <label>
                <input type="checkbox" data-checklist-item="${escapeHtml(item.id)}" data-checklist-key="${escapeHtml(key)}"
                       ${item.checklist?.[key] ? "checked" : ""}${busy ? " disabled" : ""} />
                <span>${escapeHtml(CHECKLIST_LABELS[key] || key)}</span>
              </label>
            </li>`
          )
          .join("")}
      </ul>
    </details>`;
  }

  function trackedItemCard(item, subtitle) {
    return `<article class="franchise-crown${item.owned ? " is-owned" : ""}">
      <div>
        <p class="franchise-crown-title">${escapeHtml(item.name || item.title)}</p>
        ${subtitle ? `<p class="franchise-release-meta">${escapeHtml(subtitle)}</p>` : ""}
        ${item.notes ? `<p class="franchise-release-note">${escapeHtml(item.notes)}</p>` : ""}
        ${item.verificationPending ? `<p class="franchise-release-note is-pending">Detalle en verificación.</p>` : ""}
        ${checklistBlock(item)}
      </div>
      <button class="btn-link${item.owned ? " is-on" : ""}" type="button" data-tracked-toggle="${escapeHtml(item.id)}"${busy ? " disabled" : ""}>
        ${item.owned ? "La tengo" : "No la tengo"}
      </button>
    </article>`;
  }

  function crownJewelsSection() {
    const editions = model.editions.filter((e) => e.isCrownJewel);
    const crownReleases = model.releases.filter((r) => r.isCrownJewel);
    const hardware = model.hardware;
    if (!editions.length && !crownReleases.length && !hardware.length) return "";
    return `<section class="franchise-section">
      <div class="section-head"><h2>Crown jewels y hardware</h2><p class="muted">Opcional — no cuenta contra la cobertura principal de la saga.</p></div>
      <div class="franchise-crown-grid">
        ${editions.map((e) => trackedItemCard(e, platformLabel(model.releases.find((r) => r.id === e.releaseId)?.platformId))).join("")}
        ${crownReleases.map((r) => trackedItemCard(r, platformLabel(r.platformId))).join("")}
        ${hardware.map((h) => trackedItemCard(h, platformLabel(h.platformId))).join("")}
      </div>
    </section>`;
  }

  // --- Históricos, DLC y extras --------------------------------------------

  function historicalSection() {
    if (!model.historical.length) return "";
    return `<section class="franchise-section">
      <div class="section-head"><h2>Históricos, DLC y extras</h2><p class="muted">Contexto de la saga — no compran ni completan nada.</p></div>
      <div class="franchise-releases">
        ${model.historical
          .map(
            (item) => `<article class="franchise-release is-historical">
              <p class="franchise-release-title">${escapeHtml(item.title)}</p>
              <p class="franchise-release-meta">${escapeHtml(item.platformLabel || "")}${item.year ? ` · ${item.year}` : ""}</p>
              ${item.note ? `<p class="franchise-release-note">${escapeHtml(item.note)}</p>` : ""}
            </article>`
          )
          .join("")}
      </div>
    </section>`;
  }

  // --- Render principal ------------------------------------------------------

  function render() {
    if (error) {
      root.innerHTML = `<section class="detail-block franchise-empty">
        <p class="eyebrow">Franchise Collection Tracker</p>
        <h1>No pudimos cargar esta franquicia</h1>
        <p>${escapeHtml(error)}</p>
        <a class="btn-link" href="./index.html">Volver a la colección</a>
      </section>`;
      return;
    }
    if (!model) {
      root.innerHTML = `<p class="muted">Cargando…</p>`;
      return;
    }

    root.innerHTML = `<div class="back-link"><a href="${window.CollectionRepository?.getHomeHref?.() || "./index.html"}">← Volver a la colección</a></div>
      ${heroSection()}
      ${feedback ? `<p class="chasing-feedback is-${escapeHtml(feedbackTone)}" role="status">${escapeHtml(feedback)}</p>` : ""}
      ${filterBar()}
      ${timelineSection()}
      ${compilationsSection()}
      ${crownJewelsSection()}
      ${historicalSection()}`;

    bindEvents();
  }

  const each = (selector, handler) => root.querySelectorAll(selector).forEach(handler);

  function bindEvents() {
    each("[data-ownership]", (button) =>
      button.addEventListener("click", () => {
        filters.ownership = button.dataset.ownership;
        render();
      })
    );
    root.querySelector('[data-filter="platform"]')?.addEventListener("change", (event) => {
      filters.platform = event.target.value;
      render();
    });
    root.querySelector('[data-filter="era"]')?.addEventListener("change", (event) => {
      filters.era = event.target.value;
      render();
    });
    root.querySelector('[data-filter="query"]')?.addEventListener("input", (event) => {
      filters.query = event.target.value;
      render();
    });

    each("[data-release-ownership]", (button) =>
      button.addEventListener("click", () => {
        const releaseId = button.dataset.releaseOwnership;
        const value = button.dataset.value;
        const release = model.releases.find((item) => item.id === releaseId);
        if (!release) return;
        perform(
          "Guardando…",
          () => repo.setReleaseOwnership(release, value, model.baseGamesByConsole),
          "Actualizado."
        );
      })
    );

    each("[data-tracked-toggle]", (button) =>
      button.addEventListener("click", () => {
        const itemId = button.dataset.trackedToggle;
        const items = [...model.editions, ...model.releases.filter((r) => r.isCrownJewel), ...model.hardware];
        const item = items.find((entry) => entry.id === itemId);
        if (!item) return;
        perform(
          "Guardando…",
          () => repo.setTrackedOwnership(model.franchiseId, itemId, !item.owned),
          item.owned ? "Marcada como no propia." : "Marcada como propia."
        );
      })
    );

    each("[data-checklist-for]", (details) =>
      details.addEventListener("toggle", () => {
        const itemId = details.dataset.checklistFor;
        if (details.open) openChecklists.add(itemId);
        else openChecklists.delete(itemId);
      })
    );

    each("[data-checklist-item]", (checkbox) =>
      checkbox.addEventListener("change", () => {
        const itemId = checkbox.dataset.checklistItem;
        const key = checkbox.dataset.checklistKey;
        const items = [...model.editions, ...model.releases.filter((r) => r.isCrownJewel), ...model.hardware];
        const item = items.find((entry) => entry.id === itemId);
        if (!item) return;
        perform(
          "Guardando…",
          () => repo.setTrackedChecklistItem(model.franchiseId, itemId, item.checklist, key, checkbox.checked),
          "Checklist actualizado."
        );
      })
    );
  }

  async function start() {
    try {
      await reload();
      render();
    } catch (err) {
      error = err.message;
      render();
    }
  }

  start();
})();
