(() => {
  /**
   * Colección temática — una colección dentro de la colección.
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
   *
   * El rediseño del 2026-09 partió de medir la página vieja: 10.342 px de
   * alto y 173 botones para 18 objetos que se pueden tener. La causa no era
   * sólo jerarquía — era duplicación. Una compilación que cubre cinco obras
   * se dibujaba una vez por obra más otra en su propia sección, con su juego
   * de botones cada vez, todos escribiendo el mismo estado.
   *
   * De ahí las dos decisiones estructurales de acá:
   *
   * 1. **Un objeto, un lugar.** Un lanzamiento que cubre una sola obra vive
   *    dentro de esa obra. Uno que cubre varias vive una sola vez, en «Una
   *    compra, varias obras», y las obras lo nombran sin repetir sus
   *    controles. Marcar tenencia sigue estando a un click, pero existe una
   *    sola vez por cosa.
   * 2. **La obra es la fila, el lanzamiento es el detalle.** La pregunta
   *    «¿puedo jugar esta parte?» es sobre la obra; «¿en qué edición?» es el
   *    detalle. Las obras arrancan plegadas y dicen en la propia fila con qué
   *    están cubiertas, así la página se lee sin abrir nada.
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

  const OWNERSHIP_SHORT = {
    none: "",
    physical: "en físico",
    digital: "en digital",
    both: "en físico y digital"
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
  const openWorks = new Set();

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

  /**
   * Un lanzamiento que cubre más de una obra no pertenece a ninguna: es la
   * compilación que las junta. Vive una sola vez, en su propia sección.
   */
  const coversManyWorks = (release) => (release.workIds || []).length > 1;

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

  // --- Hero y los tres números ----------------------------------------------

  function heroSection() {
    return `<header class="detail-hero tc-hero">
      <p class="eyebrow">Colección temática</p>
      <h1>${escapeHtml(model.franchise.name)}</h1>
      <p class="tc-summary">${escapeHtml(model.franchise.summary || "")}</p>
    </header>`;
  }

  /**
   * Los tres números, cada uno con su propia frase.
   *
   * Viven juntos y se leen aparte a propósito: miden preguntas distintas y
   * fusionarlos en un score único destruiría el producto. Por eso cada uno
   * dice, abajo del número, qué está contando — un 2/9 y un 0/9 al lado no se
   * explican solos.
   */
  function metricsBand() {
    const p = model.progress;
    const cards = [
      {
        tone: "play",
        value: `${p.sagaCoverage.covered}/${p.sagaCoverage.total}`,
        percent: p.sagaCoverage.percent,
        label: "Puedo jugar",
        meaning: "Obras de la saga que cubrís de alguna forma: físico, digital o compilación."
      },
      {
        tone: "shelf",
        value: `${p.physicalShelf.owned}/${p.physicalShelf.total}`,
        percent: p.physicalShelf.percent,
        label: "En la estantería",
        meaning: "Sólo los lanzamientos físicos principales. Una compilación cubre la obra pero no llena este número."
      },
      {
        tone: "crown",
        value: `${p.crownJewels.owned}/${p.crownJewels.total}`,
        percent: p.crownJewels.percent,
        label: "Piezas de colección",
        meaning: "Ediciones especiales y hardware. Es opcional: no ensucia los otros dos."
      }
    ];
    return `<section class="tc-metrics" aria-label="Estado de la colección">
      ${cards
        .map(
          (card) => `<article class="tc-metric is-${card.tone}">
            <p class="tc-metric-value">${escapeHtml(card.value)}</p>
            <p class="tc-metric-label">${escapeHtml(card.label)}</p>
            <div class="tc-metric-track"><span style="width:${card.percent}%"></span></div>
            <p class="tc-metric-meaning">${escapeHtml(card.meaning)}</p>
          </article>`
        )
        .join("")}
    </section>`;
  }

  function nextActionBlock() {
    const nextAction = model.nextAction;
    if (!nextAction) {
      return `<section class="tc-next is-done">
        <p class="eyebrow">Próximo paso</p>
        <p>No hay una compra obvia siguiente: lo que falta no depende de una plataforma nueva.</p>
      </section>`;
    }
    if (nextAction.type === "platform") {
      const count = nextAction.newWorkIds.length;
      const names = nextAction.newWorkIds
        .map((id) => model.works.find((work) => work.id === id)?.title)
        .filter(Boolean);
      return `<section class="tc-next">
        <p class="eyebrow">Próximo paso</p>
        <p class="tc-next-headline">Conseguir una <strong>${escapeHtml(platformLabel(nextAction.platformId))}</strong>
          te habilita ${count} ${count === 1 ? "obra" : "obras"} que hoy no podés jugar.</p>
        ${names.length ? `<p class="tc-next-detail">${escapeHtml(names.join(" · "))}</p>` : ""}
      </section>`;
    }
    const release = model.releases.find((item) => item.id === nextAction.releaseId);
    return `<section class="tc-next">
      <p class="eyebrow">Próximo paso</p>
      <p class="tc-next-headline">Te falta <strong>${escapeHtml(release?.title || nextAction.releaseId)}</strong>
        en ${escapeHtml(platformLabel(release?.platformId))}.</p>
      <p class="tc-next-detail">Es el lanzamiento físico principal más antiguo que todavía no está en la estantería.</p>
    </section>`;
  }

  // --- Filtros ---------------------------------------------------------------

  function filterBar() {
    const platforms = [...new Set(model.releases.map((r) => r.platformId))];
    return `<nav class="tc-filters" aria-label="Filtrar la saga">
      <div class="pill-group">
        <button class="pill${filters.ownership === "all" ? " is-active" : ""}" type="button" data-ownership="all">Todas</button>
        <button class="pill${filters.ownership === "owned" ? " is-active" : ""}" type="button" data-ownership="owned">Cubiertas</button>
        <button class="pill${filters.ownership === "missing" ? " is-active" : ""}" type="button" data-ownership="missing">Me faltan</button>
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

  const hasActiveFilters = () =>
    filters.ownership !== "all" || filters.platform !== "all" || filters.era !== "all" || Boolean(filters.query);

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

  // --- Lanzamientos ----------------------------------------------------------

  function ownershipControls(release) {
    return `<div class="tc-ownership" role="group" aria-label="Marcar propiedad de ${escapeHtml(release.title)}">
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
    return `<p class="tc-release-price">${escapeHtml(parts.join(" · "))}</p>`;
  }

  function releaseMeta(release) {
    const delivery = (release.deliveryByComponent || []).map((k) => DELIVERY_LABELS[k] || k).join(" + ");
    return [
      platformLabel(release.platformId),
      release.year || "s/f",
      RELEASE_TYPE_LABELS[release.releaseType] || release.releaseType,
      delivery
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function releaseNotes(release) {
    const codeNote = CODE_STATUS_LABELS[release.codeStatus] || "";
    return `${release.notes ? `<p class="tc-release-note">${escapeHtml(release.notes)}</p>` : ""}
      ${codeNote ? `<p class="tc-release-note is-warning">${escapeHtml(codeNote)}</p>` : ""}
      ${release.verificationPending ? `<p class="tc-release-note is-pending">Detalle en verificación — no tratar como confirmado al 100%.</p>` : ""}`;
  }

  /**
   * El lanzamiento, con su fila de control. `emphasis` marca el que es el
   * objetivo físico principal: es el que llena la estantería, y sin esa
   * distinción un remaster digital se ve igual que el disco original.
   */
  function releaseCard(release, { compact = false } = {}) {
    const showRadarCta = release.isPrimaryPhysicalTarget && !release.isOwnedPhysical;
    return `<article class="tc-release${release.isOwned ? " is-owned" : ""}${
      release.isPrimaryPhysicalTarget ? " is-primary" : ""
    }${compact ? " is-compact" : ""}">
      <div class="tc-release-head">
        ${release.coverUrl ? `<img class="tc-release-cover" src="${escapeHtml(release.coverUrl)}" alt="" loading="lazy" decoding="async" />` : ""}
        <div class="tc-release-id">
          <p class="tc-release-title">${escapeHtml(release.title)}</p>
          <p class="tc-release-meta">${escapeHtml(releaseMeta(release))}</p>
          ${priceReferenceBlock(release)}
        </div>
        <div class="tc-release-flags">
          ${release.isPrimaryPhysicalTarget ? `<span class="chip is-shelf" title="Cuenta para la estantería física">Estantería</span>` : ""}
          ${release.physicalStatus === "not-announced" ? `<span class="chip">Sin edición física</span>` : ""}
        </div>
      </div>
      ${releaseNotes(release)}
      <div class="tc-release-actions">
        ${ownershipControls(release)}
        ${showRadarCta ? `<a class="btn-link tc-radar-cta" href="${escapeHtml(radarSearchHref(release))}">Buscar con Radar</a>` : ""}
      </div>
    </article>`;
  }

  // --- La saga: una fila por obra -------------------------------------------

  function eraLabel(eraId) {
    return model.franchise.eras?.find((era) => era.id === eraId)?.label || eraId;
  }

  /**
   * La línea que hace legible la fila sin abrirla: con qué está cubierta la
   * obra, o qué la cubriría. Nombrar la compilación acá es lo que permite no
   * repetirla como card debajo de cada obra.
   */
  function coverageLine(work) {
    const owned = work.releases.filter((release) => release.isOwned);
    if (owned.length) {
      const names = owned.map(
        (release) => `${release.title} (${platformLabel(release.platformId)})${
          OWNERSHIP_SHORT[release.ownershipType] ? ` ${OWNERSHIP_SHORT[release.ownershipType]}` : ""
        }`
      );
      return `<span class="tc-work-coverage is-covered">La cubrís con ${escapeHtml(names.join(" · "))}</span>`;
    }
    if (work.workStatus === "announced") {
      return `<span class="tc-work-coverage is-future">Todavía no salió. No cuenta contra la cobertura.</span>`;
    }
    const primary = work.releases.find((release) => release.isPrimaryPhysicalTarget);
    const viaCompilation = work.releases.filter(coversManyWorks);
    const parts = [];
    if (primary) parts.push(`${primary.title} en ${platformLabel(primary.platformId)}`);
    if (viaCompilation.length === 1) parts.push(`o en ${viaCompilation[0].title}`);
    else if (viaCompilation.length) parts.push(`o en ${viaCompilation.length} compilaciones, como ${viaCompilation[0].title}`);
    if (!parts.length) return `<span class="tc-work-coverage is-missing">Sin lanzamientos cargados todavía.</span>`;
    return `<span class="tc-work-coverage is-missing">${escapeHtml(parts.join(" "))}</span>`;
  }

  function workStatusChip(work) {
    if (work.isCovered) return `<span class="chip is-owned">Cubierta</span>`;
    if (work.workStatus === "announced") return `<span class="chip is-announced">Anunciada</span>`;
    return `<span class="chip is-missing">Falta</span>`;
  }

  function isWorkOpen(work) {
    // Con un filtro puesto, abrir lo que coincide es la respuesta a la
    // búsqueda: buscar «Saga» y encontrar una fila plegada no sirve de nada.
    return openWorks.has(work.id) || (hasActiveFilters() && Boolean(filters.query));
  }

  function workRow(work) {
    const own = work.releases.filter((release) => !coversManyWorks(release));
    const shared = work.releases.filter(coversManyWorks);
    // Una obra anunciada todavía no tiene ediciones: ofrecerla como plegable
    // sería prometer un detalle que no existe.
    const expandable = own.length > 0 || shared.length > 0;
    const open = expandable && isWorkOpen(work);
    const count = own.length;
    const head = `<span class="tc-work-year">${work.year || "—"}</span>
        <span class="tc-work-id">
          <span class="tc-work-title">${escapeHtml(work.title)}</span>
          ${coverageLine(work)}
        </span>
        <span class="tc-work-flags">
          ${workStatusChip(work)}
          ${
            expandable
              ? `<span class="tc-work-toggle" aria-hidden="true">${open ? "Cerrar" : `${count} ${count === 1 ? "edición" : "ediciones"}`}</span>`
              : ""
          }
        </span>`;
    return `<article class="tc-work${work.isCovered ? " is-covered" : ""}${open ? " is-open" : ""}">
      ${
        expandable
          ? `<button class="tc-work-head" type="button" data-work-toggle="${escapeHtml(work.id)}" aria-expanded="${open}">${head}</button>`
          : `<div class="tc-work-head is-static">${head}</div>`
      }
      ${
        open
          ? `<div class="tc-work-body">
              ${work.notes ? `<p class="tc-work-notes">${escapeHtml(work.notes)}</p>` : ""}
              ${own.length ? own.map((release) => releaseCard(release)).join("") : `<p class="muted">Sin ediciones propias de esta obra.</p>`}
              ${
                shared.length
                  ? `<p class="tc-work-shared">También la cubren ${escapeHtml(
                      shared.map((release) => release.title).join(" · ")
                    )}, más abajo en <a href="#compilaciones">Una compra, varias obras</a>.</p>`
                  : ""
              }
            </div>`
          : ""
      }
    </article>`;
  }

  function sagaSection() {
    const works = model.works.filter(workMatchesFilters);
    if (!works.length) {
      return `<section class="tc-section">
        <div class="tc-section-head"><h2>La saga</h2></div>
        <p class="muted">Nada coincide con estos filtros.</p>
      </section>`;
    }
    const eras = (model.franchise.eras || []).filter((era) => works.some((work) => work.era === era.id));
    return `<section class="tc-section">
      <div class="tc-section-head">
        <h2>La saga</h2>
        <p class="muted">Una fila por obra. Abrila para marcar en qué edición la tenés.</p>
      </div>
      ${eras
        .map((era) => {
          const inEra = works
            .filter((work) => work.era === era.id)
            .sort((a, b) => {
              if (a.year == null && b.year == null) return 0;
              if (a.year == null) return 1;
              if (b.year == null) return -1;
              return a.year - b.year;
            });
          const covered = inEra.filter((work) => work.isCovered).length;
          return `<div class="tc-era">
            <div class="tc-era-head">
              <h3>${escapeHtml(era.label)}</h3>
              <span>${covered}/${inEra.length}</span>
            </div>
            <div class="tc-work-list">${inEra.map(workRow).join("")}</div>
          </div>`;
        })
        .join("")}
    </section>`;
  }

  // --- Una compra, varias obras ---------------------------------------------

  /**
   * Las compilaciones, una sola vez cada una y ordenadas por cuánto cubren.
   *
   * Es la sección que responde «qué me conviene comprar»: un set que cubre
   * cinco obras es una decisión distinta a un remaster que cubre una, y la
   * página vieja las mostraba idénticas y repetidas.
   */
  /**
   * Los filtros también valen acá. Sin esto, buscar «Saga» filtraba la saga
   * pero dejaba las seis compilaciones intactas abajo, y la página mostraba
   * dos respuestas distintas a la misma pregunta.
   */
  function leverageMatchesFilters(release) {
    if (filters.ownership === "owned" && !release.isOwned) return false;
    if (filters.ownership === "missing" && release.isOwned) return false;
    if (filters.platform !== "all" && release.platformId !== filters.platform) return false;
    if (filters.era !== "all") {
      const eras = (release.workIds || []).map((id) => model.works.find((work) => work.id === id)?.era);
      if (!eras.includes(filters.era)) return false;
    }
    if (filters.query && !release.title.toLowerCase().includes(filters.query.toLowerCase())) return false;
    return true;
  }

  function leverageSection() {
    const items = model.releases
      .filter(coversManyWorks)
      .filter(leverageMatchesFilters)
      .sort((a, b) => (b.workIds || []).length - (a.workIds || []).length);
    if (!items.length) return "";
    const workTitle = (id) => model.works.find((work) => work.id === id)?.title || id;
    return `<section class="tc-section" id="compilaciones">
      <div class="tc-section-head">
        <h2>Una compra, varias obras</h2>
        <p class="muted">Compilaciones y sets. Cubren la saga; no reemplazan tener el original en la estantería.</p>
      </div>
      <div class="tc-leverage-grid">
        ${items
          .map((release) => {
            const ids = release.workIds || [];
            const uncovered = ids.filter((id) => !model.works.find((work) => work.id === id)?.isCovered);
            return `<article class="tc-leverage${release.isOwned ? " is-owned" : ""}">
              <div class="tc-leverage-head">
                <span class="tc-leverage-count" title="Obras que cubre">${ids.length}</span>
                <div>
                  <p class="tc-release-title">${escapeHtml(release.title)}</p>
                  <p class="tc-release-meta">${escapeHtml(releaseMeta(release))}</p>
                </div>
              </div>
              ${
                release.isCrownJewel
                  ? `<p class="tc-leverage-crown">También es pieza de colección: abajo se marca aparte, porque tenerla para jugar y tenerla como pieza son dos cosas distintas.</p>`
                  : ""
              }
              <p class="tc-leverage-works">${escapeHtml(ids.map(workTitle).join(" · "))}</p>
              ${
                uncovered.length && !release.isOwned
                  ? `<p class="tc-leverage-gain">Te sumaría ${uncovered.length} ${
                      uncovered.length === 1 ? "obra que hoy no podés jugar" : "obras que hoy no podés jugar"
                    }.</p>`
                  : ""
              }
              ${releaseNotes(release)}
              ${ownershipControls(release)}
            </article>`;
          })
          .join("")}
      </div>
    </section>`;
  }

  // --- Crown jewels y hardware ----------------------------------------------

  function checklistBlock(item) {
    const keys = item.completenessChecklist;
    if (!keys?.length) return "";
    const done = keys.filter((key) => item.checklist?.[key]).length;
    return `<details class="tc-checklist" data-checklist-for="${escapeHtml(item.id)}"${openChecklists.has(item.id) ? " open" : ""}>
      <summary>Completitud ${done}/${keys.length}</summary>
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
    // El control va último y anclado abajo: en una grilla de cards con notas
    // de largos muy distintos, la acción tiene que caer siempre en el mismo
    // lugar, no flotar donde termine el texto de cada una.
    return `<article class="tc-crown${item.owned ? " is-owned" : ""}">
      <div class="tc-crown-head">
        <p class="tc-release-title">${escapeHtml(item.name || item.title)}</p>
        ${subtitle ? `<p class="tc-release-meta">${escapeHtml(subtitle)}</p>` : ""}
      </div>
      ${item.notes ? `<p class="tc-release-note">${escapeHtml(item.notes)}</p>` : ""}
      ${item.verificationPending ? `<p class="tc-release-note is-pending">Detalle en verificación.</p>` : ""}
      ${checklistBlock(item)}
      <button class="btn-link tc-crown-toggle${item.owned ? " is-on" : ""}" type="button" data-tracked-toggle="${escapeHtml(item.id)}"${busy ? " disabled" : ""}>
        ${item.owned ? "La tengo" : "No la tengo"}
      </button>
    </article>`;
  }

  function crownJewelsSection() {
    const editions = model.editions.filter((e) => e.isCrownJewel);
    const crownReleases = model.releases.filter((r) => r.isCrownJewel);
    const hardware = model.hardware;
    if (!editions.length && !crownReleases.length && !hardware.length) return "";
    return `<section class="tc-section tc-section-crown">
      <div class="tc-section-head">
        <h2>Piezas de colección</h2>
        <p class="muted">Ediciones especiales y hardware. Opcional: no cuenta contra la cobertura de la saga.</p>
      </div>
      <div class="tc-crown-grid">
        ${editions.map((e) => trackedItemCard(e, platformLabel(model.releases.find((r) => r.id === e.releaseId)?.platformId))).join("")}
        ${crownReleases.map((r) => trackedItemCard(r, platformLabel(r.platformId))).join("")}
        ${hardware.map((h) => trackedItemCard(h, platformLabel(h.platformId))).join("")}
      </div>
    </section>`;
  }

  // --- Históricos, DLC y extras ---------------------------------------------

  function historicalSection() {
    if (!model.historical.length) return "";
    return `<section class="tc-section">
      <details class="tc-historical">
        <summary>
          <span>Históricos, DLC y extras</span>
          <small>${model.historical.length} · contexto de la saga, no se compran ni completan nada</small>
        </summary>
        <ul>
          ${model.historical
            .map(
              (item) => `<li>
                <strong>${escapeHtml(item.title)}</strong>
                <span>${escapeHtml(item.platformLabel || "")}${item.year ? ` · ${item.year}` : ""}</span>
                ${item.note ? `<p>${escapeHtml(item.note)}</p>` : ""}
              </li>`
            )
            .join("")}
        </ul>
      </details>
    </section>`;
  }

  // --- Render principal ------------------------------------------------------

  function render() {
    if (error) {
      root.innerHTML = `<section class="detail-block tc-empty">
        <p class="eyebrow">Colección temática</p>
        <h1>No pudimos cargar esta colección</h1>
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
      ${metricsBand()}
      ${nextActionBlock()}
      ${feedback ? `<p class="chasing-feedback is-${escapeHtml(feedbackTone)}" role="status">${escapeHtml(feedback)}</p>` : ""}
      ${filterBar()}
      ${sagaSection()}
      ${leverageSection()}
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
      // Escribir en el buscador no puede costar el foco: se vuelve a poner el
      // cursor donde estaba, porque `render()` reemplaza el input entero.
      const input = root.querySelector('[data-filter="query"]');
      if (input) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
    });

    each("[data-work-toggle]", (button) =>
      button.addEventListener("click", () => {
        const workId = button.dataset.workToggle;
        if (openWorks.has(workId)) openWorks.delete(workId);
        else openWorks.add(workId);
        render();
      })
    );

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
