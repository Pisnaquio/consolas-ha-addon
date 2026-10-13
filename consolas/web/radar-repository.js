(() => {
  /**
   * Capa central de lectura y escritura de las búsquedas de Collection Radar.
   *
   * Las páginas no hablan con `fetch` por su cuenta: el estado de las búsquedas,
   * las fuentes disponibles y sus capabilities salen siempre de acá, igual que
   * `AuctionWatchRepository` centraliza el snapshot de oportunidades.
   *
   * El radar es read-only respecto de la colección. Nada de lo que devuelve esta
   * capa se persiste como estado editable del usuario.
   */
  const API = window.CONSOLAS_API_BASE || "./api";
  const WRITE_HEADER = "X-Consolas-Radar";

  const STATUS_ORDER = ["active", "draft", "paused", "archived"];

  const STATUS_LABELS = {
    active: "Activa",
    draft: "Propuesta",
    paused: "Pausada",
    archived: "Archivada"
  };

  const TYPE_LABELS = {
    chase: "Chase específico",
    console: "Consola completa",
    lot: "Lote",
    upgrade: "Upgrade",
    discovery: "Descubrimiento",
    master: "Master de colección"
  };

  const PRIORITY_LABELS = {
    alta: "Prioridad alta",
    "media-alta": "Prioridad media-alta",
    media: "Prioridad media",
    baja: "Prioridad baja"
  };

  const CONDITION_LABELS = {
    any: "Cualquier condición",
    new: "Nuevo",
    used: "Usado",
    refurbished: "Refurbished"
  };

  const COMPLETENESS_LABELS = {
    any: "Completitud indistinta",
    loose: "Loose",
    boxed: "Con caja",
    cib: "CIB",
    sealed: "Sellado"
  };

  const REQUIREMENT_LABELS = {
    any: "indistinto",
    preferred: "preferido",
    required: "obligatorio"
  };

  /**
   * Por qué se descarta algo. Vive acá y no en una página porque se descarta
   * desde dos lugares — el feed "Para mí" y los resultados de una búsqueda — y
   * el motivo tiene que significar lo mismo en los dos: es lo que después
   * explica por qué esa publicación no vuelve a aparecer.
   */
  const DISMISS_REASONS = [
    { id: "caro", label: "Está caro" },
    { id: "condicion", label: "Por su condición" },
    { id: "region", label: "Región equivocada" },
    { id: "ya-lo-tengo", label: "Ya lo tengo" },
    { id: "no-es-lo-que-busco", label: "No es lo que busco" },
    { id: "dudoso", label: "Me genera dudas" },
    { id: "no-me-interesa", label: "No me interesa" }
  ];

  let model = null;
  let listings = null;
  let runs = null;
  let shipment = null;
  let coverage = null;
  let purchases = null;
  let available = false;

  async function request(path, options = {}) {
    const isWrite = Boolean(options.method && options.method !== "GET");
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(isWrite ? { "Content-Type": "application/json", [WRITE_HEADER]: "1" } : {}),
        ...(options.headers || {})
      }
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "No pudimos completar la acción.");
    return payload;
  }

  async function write(path, body) {
    return request(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  async function load() {
    model = await request("/radar/searches");
    available = true;
    return model;
  }

  function getModel() {
    return model;
  }

  function isAvailable() {
    return available;
  }

  function getSearches() {
    return Array.isArray(model?.items) ? model.items : [];
  }

  function getSources() {
    return Array.isArray(model?.sources) ? model.sources : [];
  }

  function getSource(sourceId) {
    return getSources().find((source) => source.id === sourceId) || null;
  }

  function getSourceLabel(sourceId) {
    return getSource(sourceId)?.label || String(sourceId || "");
  }

  function getEnvironment() {
    return model?.environment || "";
  }

  function getEnvironmentLabel() {
    return model?.source || "eBay USA";
  }

  function isSandbox() {
    return getEnvironment() === "sandbox";
  }

  function getCounts() {
    const counts = model?.counts && typeof model.counts === "object" ? model.counts : {};
    return STATUS_ORDER.reduce((acc, status) => ({ ...acc, [status]: Number(counts[status]) || 0 }), {});
  }

  function getSearchesByStatus(status) {
    if (!status || status === "all") return getSearches();
    return getSearches().filter((item) => item.status === status);
  }

  function getSearch(searchId) {
    return getSearches().find((item) => item.id === searchId) || null;
  }

  function getStatusLabel(status) {
    return STATUS_LABELS[status] || String(status || "");
  }

  function getTypeLabel(type) {
    return TYPE_LABELS[type] || String(type || "");
  }

  /**
   * El tipo dicho como lo lee el dueño: una búsqueda de varios títulos es su
   * lista de deseados de esa consola, no un chase de un juego.
   */
  function getSearchKindLabel(item = {}) {
    if (item.searchType === "chase" && (item.criteria?.titleTargets || []).length > 1) return "Juegos deseados";
    return getTypeLabel(item.searchType);
  }

  function getPriorityLabel(priority) {
    return PRIORITY_LABELS[priority] || String(priority || "");
  }

  function canRun(item = {}) {
    return item.canRun === true;
  }

  /** Previsualizar no exige estar activa: sólo una fuente que pueda correr. */
  function canPreview(item = {}) {
    return item.canPreview === true;
  }

  function isExecutableSource(sourceId) {
    return getSource(sourceId)?.executable === true;
  }

  function getBlockedSources(item = {}) {
    return (item.sources || []).filter((sourceId) => !isExecutableSource(sourceId));
  }

  function formatAmount(value, currency = "USD") {
    if (value === null || value === undefined || value === "") return "";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "";
    return `${currency || "USD"} ${amount.toLocaleString("es-UY", { maximumFractionDigits: 2 })}`;
  }

  /** Resumen legible de los criterios, para que la card explique qué busca. */
  function describeCriteria(criteria = {}) {
    const currency = criteria.currency || "USD";
    const chips = [];
    if (criteria.region) chips.push(`Región ${criteria.region}`);
    if ((criteria.excludeRegions || []).length) {
      chips.push(`Sin ediciones ${criteria.excludeRegions.map((region) => region.toUpperCase()).join(", ")}`);
    }
    if (criteria.condition && criteria.condition !== "any") chips.push(CONDITION_LABELS[criteria.condition]);
    if (criteria.completeness && criteria.completeness !== "any") chips.push(COMPLETENESS_LABELS[criteria.completeness]);
    if (criteria.tested && criteria.tested !== "any") chips.push(`Probado ${REQUIREMENT_LABELS[criteria.tested]}`);
    if (criteria.originalParts && criteria.originalParts !== "any") {
      chips.push(`OEM ${REQUIREMENT_LABELS[criteria.originalParts]}`);
    }
    if (criteria.returnsRequired) chips.push("Con devolución");
    if (criteria.freeShippingOnly) chips.push("Envío gratis");
    const maxItem = formatAmount(criteria.maxItemPrice, currency);
    if (maxItem) chips.push(`Hasta ${maxItem}`);
    const maxTotal = formatAmount(criteria.maxTotalUsa, currency);
    if (maxTotal) chips.push(`Recibido en USA hasta ${maxTotal}`);
    if (criteria.minLotSize) chips.push(`Lotes desde ${criteria.minLotSize} piezas`);
    const perPiece = formatAmount(criteria.maxPricePerPiece, currency);
    if (perPiece) chips.push(`Hasta ${perPiece} por pieza`);
    const queries = (criteria.queries || []).length;
    if (queries) chips.push(`${queries} ${queries === 1 ? "consulta extra" : "consultas extra"}`);
    const targets = (criteria.titleTargets || []).length;
    if (targets) chips.push(`${targets} ${targets === 1 ? "título con precio propio" : "títulos con precio propio"}`);
    if ((criteria.preferTerms || []).length) chips.push(`Prefiere: ${criteria.preferTerms.join(", ")}`);
    if ((criteria.excludeTerms || []).length) chips.push(`Excluye: ${criteria.excludeTerms.join(", ")}`);
    return chips;
  }

  /** Costo puesto en Uruguay. Siempre marcado como estimado: el peso lo es. */
  function formatImported(result = {}) {
    const cost = result.valuation?.cost;
    if (!cost || cost.importedTotal == null) return "";
    return `≈ ${formatAmount(cost.importedTotal, cost.currency)} puesto acá`;
  }

  /** Total recibido en Estados Unidos, sólo cuando el envío está confirmado. */
  function formatTotal(result = {}) {
    const total = Number(result.totalAmount);
    const price = Number(result.priceAmount);
    if (!Number.isFinite(total) || !Number.isFinite(price) || total === price) return "";
    return `${formatAmount(total, result.priceCurrency)} recibido`;
  }

  const BAND_LABELS = {
    ganga: "Ganga real",
    buena: "Buena compra",
    razonable: "Precio razonable",
    premium: "Premium justificable",
    caro: "Caro",
    revisar: "Requiere revisión",
    "sin-referencia": "Sin referencia"
  };

  function getBandLabel(band) {
    return BAND_LABELS[band] || "";
  }

  /**
   * Si la publicación está confirmada como comprable, en palabras.
   *
   * Ninguna fuente automática confirma disponibilidad: lo que trae una corrida
   * es "encontrada", no "comprable". El servidor manda `verification`; una
   * respuesta vieja sin ese campo se lee igual desde `availability` y
   * `requiresVerification`, y lo desconocido nunca se presenta como verificado.
   */
  function getVerification(result = {}) {
    const raw = result.verification || {};
    let state = raw.state;
    if (!state) {
      if (result.verifiedAt && result.requiresVerification === false) state = "verified";
      else if (result.availability === "ended") state = "ended";
      else state = "pending";
    }
    return {
      state,
      reason: raw.reason || "",
      verifiedAt: raw.verifiedAt || result.verifiedAt || null,
      expiresAt: raw.expiresAt || null,
      lastSeenAt: raw.lastSeenAt || result.lastSeenAt || null,
      ttlHours: Number(raw.ttlHours) || 24,
      requiresVerification: !["verified", "available"].includes(state)
    };
  }

  /** "hace 3 h", "hace 2 días": cuándo pasó algo, sin fingir precisión. */
  function formatAgo(value, now = Date.now()) {
    const time = Date.parse(value || "");
    if (!Number.isFinite(time)) return "";
    const minutes = Math.max(0, Math.round((now - time) / 60000));
    if (minutes < 1) return "recién";
    if (minutes < 60) return `hace ${minutes} min`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `hace ${hours} h`;
    return `hace ${Math.round(hours / 24)} días`;
  }

  /** "en 5 h": cuánto le queda a algo que vence. */
  function formatIn(value, now = Date.now()) {
    const time = Date.parse(value || "");
    if (!Number.isFinite(time)) return "";
    const hours = Math.max(0, Math.round((time - now) / 3600000));
    return hours < 1 ? "en menos de una hora" : `en ${hours} h`;
  }

  /** Una línea que dice si se puede decidir sobre esta publicación, y qué falta. */
  function describeVerification(result = {}, now = Date.now()) {
    const verification = getVerification(result);
    const seen = formatAgo(verification.lastSeenAt, now);
    if (verification.state === "verified") {
      const expires = verification.expiresAt ? ` · vence ${formatIn(verification.expiresAt, now)}` : "";
      return {
        ...verification,
        tone: "verified",
        text: `Verificada ${formatAgo(verification.verifiedAt, now) || ""}${expires}.`.replace(" .", ".")
      };
    }
    if (verification.state === "available") {
      return { ...verification, tone: "verified", text: "La fuente confirma que sigue disponible." };
    }
    if (verification.state === "ended") {
      return { ...verification, tone: "ended", text: "La fuente dice que terminó." };
    }
    if (verification.state === "expired") {
      const why =
        verification.reason === "price-changed" ? "cambió de precio después" : `pasaron más de ${verification.ttlHours} h`;
      return {
        ...verification,
        tone: "pending",
        text: `Verificación vencida (${why}): abrila de nuevo y confirmá Buy It Now o Add to Cart.`
      };
    }
    return {
      ...verification,
      tone: "pending",
      text: `Verificación pendiente: encontrada ${seen || "en la última revisión"}, sin confirmar que se pueda comprar. Abrila y confirmá Buy It Now o Add to Cart antes de decidir.`
    };
  }

  /** Por qué el veredicto es el que es: compuertas, faltantes y penalizaciones. */
  function getVerdictNotes(result = {}) {
    return Array.isArray(result.valuation?.verdictNotes) ? result.valuation.verdictNotes : [];
  }

  /**
   * Lo que no cumple la definición de su objetivo va aparte: un lote que no
   * confirma cuántos juegos trae, una copia con caja y sin manual. Qué
   * secciones existen y cómo se llaman lo dice el servidor.
   */
  function sectionOf(result = {}) {
    return result.section || result.valuation?.section || "primary";
  }

  function isSecondaryResult(result = {}) {
    return sectionOf(result) !== "primary";
  }

  // Nombre anterior, de cuando la única sección aparte era la de lotes.
  const isUnconfirmedLot = isSecondaryResult;

  const SECTION_FALLBACK = {
    "unconfirmed-quantity": {
      label: "Cantidad sin confirmar",
      detail: "No dicen cuántos juegos traen, o lo dicen junto con otra cosa: no cumplen la búsqueda hasta confirmarlo."
    }
  };

  function getSectionInfo(section) {
    return model?.sections?.[section] || SECTION_FALLBACK[section] || { label: "Aparte", detail: "" };
  }

  function splitResults(results = []) {
    const primary = [];
    const unconfirmed = [];
    const bySection = new Map();
    for (const result of results || []) {
      if (!isSecondaryResult(result)) {
        primary.push(result);
        continue;
      }
      unconfirmed.push(result);
      const section = sectionOf(result);
      if (!bySection.has(section)) bySection.set(section, { section, ...getSectionInfo(section), items: [] });
      bySection.get(section).items.push(result);
    }
    return { primary, unconfirmed, sections: [...bySection.values()] };
  }

  // --- Objetivos canónicos ----------------------------------------------------
  //
  // Lotes, juegos loose, juegos CIB, consolas loose y consolas CIB. La
  // taxonomía —cuáles son, en qué orden, cómo se llaman— llega del servidor
  // con las búsquedas; esta capa sólo agrupa. Una respuesta sin taxonomía
  // (un servidor anterior) no se agrupa por objetivo y la pantalla vuelve a
  // mostrar las búsquedas como antes.

  function getObjectiveTaxonomy() {
    return Array.isArray(model?.objectives) ? [...model.objectives].sort((a, b) => a.order - b.order) : [];
  }

  function hasObjectiveTaxonomy() {
    return getObjectiveTaxonomy().length > 0;
  }

  function objectiveOfResult(result = {}) {
    return result.objective?.id || result.valuation?.objective?.id || "other";
  }

  const resultKey = (result, search) =>
    String(result.id || result.listingId || `${search?.id || ""}:${result.externalId || result.title}`);

  /**
   * Las publicaciones de un grupo de búsquedas, repartidas por objetivo.
   *
   * Una misma publicación puede llegar por dos búsquedas de la misma
   * plataforma: aparece una vez, con la mejor puntuación y los nombres de las
   * dos. Dentro de cada objetivo, primero lo que cumple y por score; aparte, lo
   * que no. Los cinco canónicos existen siempre —vacíos o no—; el resto, sólo
   * si trae algo.
   */
  function groupResultsByObjective(searches = []) {
    const listings = new Map();
    for (const search of searches) {
      for (const result of search.results || []) {
        const key = resultKey(result, search);
        const current = listings.get(key);
        const score = Number.isFinite(Number(result.score)) ? Number(result.score) : -1;
        if (!current) {
          listings.set(key, { result: { ...result, foundBy: [search.name] }, score });
          continue;
        }
        const foundBy = current.result.foundBy.includes(search.name)
          ? current.result.foundBy
          : [...current.result.foundBy, search.name];
        if (score > current.score) listings.set(key, { result: { ...result, foundBy }, score });
        else current.result.foundBy = foundBy;
      }
    }
    const buckets = new Map();
    for (const { result, score } of listings.values()) {
      const id = objectiveOfResult(result);
      if (!buckets.has(id)) buckets.set(id, []);
      buckets.get(id).push({ result, score });
    }
    return getObjectiveTaxonomy()
      .map((objective) => {
        const items = (buckets.get(objective.id) || [])
          .sort((a, b) => b.score - a.score)
          .map((entry) => entry.result);
        const { primary, unconfirmed, sections } = splitResults(items);
        return {
          ...objective,
          items,
          primary,
          secondary: unconfirmed,
          sections,
          count: items.length,
          best: primary[0] || null
        };
      })
      .filter((objective) => objective.canonical || objective.count);
  }

  /** Si un CIB está declarado por el vendedor o comprobado por el dueño. */
  function describeContents(result = {}, now = Date.now()) {
    const objective = result.objective || result.valuation?.objective || {};
    if (objective.status !== "declared") return null;
    const what = objective.id === "console-cib" ? "caja y contenido" : "caja y manual";
    const checkedAt = result.verification?.contentsVerifiedAt;
    if (checkedAt) {
      return { state: "verified", text: `${what[0].toUpperCase()}${what.slice(1)} comprobados ${formatAgo(checkedAt, now)}.`, what };
    }
    return {
      state: "declared",
      text: `CIB declarado por el vendedor, sin comprobar: mirá ${what} en las fotos antes de pagarlo como CIB.`,
      what
    };
  }

  function titleTargetKey(result = {}) {
    const target = result.titleTarget || {};
    return String(target.gameId || target.query || "").trim().toLowerCase();
  }

  /**
   * Los mejores N para mostrar plegados: en el orden del radar, pero un título
   * por vez antes de repetir copias. El servidor ya diversifica; esto lo
   * garantiza también contra una respuesta que no lo haga.
   */
  function pickTopResults(results = [], count = 3) {
    const { primary } = splitResults(results);
    const picked = [];
    const titles = new Set();
    for (const result of primary) {
      const key = titleTargetKey(result);
      if (key && titles.has(key)) continue;
      if (key) titles.add(key);
      picked.push(result);
      if (picked.length === count) return picked;
    }
    for (const result of primary) {
      if (!picked.includes(result)) picked.push(result);
      if (picked.length === count) break;
    }
    return picked;
  }

  // --- Agrupar búsquedas ------------------------------------------------------
  //
  // Nada de esto sabe qué búsquedas existen: agrupa por lo que cada una
  // declara (`platform`, `searchType`, `priority`, `status`). La clave y el
  // nombre de plataforma los resuelve el servidor, que es quien sabe que «N64»
  // y «Nintendo 64» son la misma; sin ellos se agrupa por el texto tal cual.

  // `objective` es la clave guardada de la vista que agrupa por tipo de
  // búsqueda (consolas, deseados, lotes). Se conserva para no perder la
  // preferencia de nadie; el nombre visible cambió para no confundirla con los
  // cinco objetivos canónicos de cada plataforma.
  const VIEW_MODES = [
    { id: "platform", label: "Por plataforma" },
    { id: "objective", label: "Por tipo de búsqueda" }
  ];

  const OBJECTIVE_GROUPS = [
    { id: "console", label: "Consolas completas", types: ["console"] },
    { id: "wishlist", label: "Juegos deseados", types: ["chase"] },
    { id: "lot", label: "Lotes de juegos", types: ["lot"] },
    { id: "other", label: "Otros tipos", types: [] }
  ];

  // Dentro de un grupo, a igual prioridad: consola, deseados, lotes, el resto.
  const TYPE_ORDER = ["console", "chase", "lot", "upgrade", "discovery", "master"];
  const PRIORITY_ORDER = ["alta", "media-alta", "media", "baja"];

  const GROUP_STATUS_LABELS = {
    active: "Activa",
    partial: "Parcial",
    paused: "Pausada",
    draft: "Propuesta",
    archived: "Archivada",
    empty: "Sin resultados"
  };

  const rankOf = (list, value) => {
    const index = list.indexOf(value);
    return index === -1 ? list.length : index;
  };

  function platformOf(item = {}) {
    const text = String(item.platform || "").trim();
    return {
      key: item.platformKey || text.toLowerCase(),
      label: item.platformLabel || text || "Sin plataforma",
      order: Number.isFinite(Number(item.platformOrder)) ? Number(item.platformOrder) : 999
    };
  }

  function objectiveOf(item = {}) {
    return OBJECTIVE_GROUPS.find((group) => group.types.includes(item.searchType)) || OBJECTIVE_GROUPS.at(-1);
  }

  /** El orden configurado: prioridad, después tipo, después nombre. */
  function compareSearches(a = {}, b = {}) {
    return (
      rankOf(PRIORITY_ORDER, a.priority) - rankOf(PRIORITY_ORDER, b.priority) ||
      rankOf(TYPE_ORDER, a.searchType) - rankOf(TYPE_ORDER, b.searchType) ||
      String(a.name || "").localeCompare(String(b.name || ""), "es")
    );
  }

  function resultCountOf(item = {}) {
    return Number.isFinite(Number(item.resultCount)) ? Number(item.resultCount) : (item.results || []).length;
  }

  /** Lo que el encabezado de un grupo tiene que poder decir de un vistazo. */
  function summarizeGroup(items = []) {
    const active = items.filter((item) => item.status === "active").length;
    // Una publicación que encontraron dos búsquedas del grupo cuenta una vez.
    const unique = new Set(items.flatMap((item) => (item.results || []).map((result) => resultKey(result, item))));
    const resultCount = unique.size || items.reduce((total, item) => total + resultCountOf(item), 0);
    const lastCheckedAt = items
      .map((item) => item.lastCheckedAt)
      .filter(Boolean)
      .sort()
      .at(-1) || null;
    let status;
    if (!active) {
      if (items.some((item) => item.status === "paused")) status = "paused";
      else if (items.some((item) => item.status === "draft")) status = "draft";
      else status = "archived";
    } else if (active < items.length) {
      status = "partial";
    } else {
      status = resultCount ? "active" : "empty";
    }
    return {
      searchCount: items.length,
      activeCount: active,
      resultCount,
      lastCheckedAt,
      errorCount: items.filter((item) => item.lastError).length,
      status,
      statusLabel: GROUP_STATUS_LABELS[status]
    };
  }

  /**
   * Las búsquedas agrupadas por plataforma o por objetivo, ordenadas.
   *
   * `compare` ordena dentro de cada grupo; por defecto, la prioridad
   * configurada. Un grupo vacío no existe: con un filtro de estado, sólo
   * aparecen los grupos que tienen algo en ese estado.
   */
  function groupSearches(items = [], mode = "platform", compare = compareSearches) {
    const groups = new Map();
    for (const item of items) {
      let id;
      let label;
      let order;
      if (mode === "objective") {
        const objective = objectiveOf(item);
        id = `objective:${objective.id}`;
        label = objective.label;
        order = OBJECTIVE_GROUPS.indexOf(objective);
      } else {
        const platform = platformOf(item);
        id = `platform:${platform.key || "none"}`;
        label = platform.label;
        order = platform.key ? platform.order : 1000;
      }
      if (!groups.has(id)) groups.set(id, { id, label, order, mode, items: [] });
      groups.get(id).items.push(item);
    }
    const byPlatform = (a, b) =>
      platformOf(a).order - platformOf(b).order || platformOf(a).label.localeCompare(platformOf(b).label, "es");
    return [...groups.values()]
      .map((group) => ({
        ...group,
        // Por objetivo, dentro del grupo se recorren las plataformas en orden.
        items: [...group.items].sort((a, b) => (mode === "objective" ? byPlatform(a, b) : 0) || compare(a, b)),
        summary: summarizeGroup(group.items)
      }))
      .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label, "es"));
  }

  /** Qué referencia se usó y de dónde salió: nunca un número sin origen. */
  function describeBenchmark(valuation = {}) {
    const benchmark = valuation.benchmark;
    if (!benchmark) return "";
    const parts = [`vs ${formatAmount(benchmark.value, benchmark.currency)} (${benchmark.sourceLabel})`];
    if (benchmark.stale) parts.push("referencia vieja");
    if (benchmark.independent === false) parts.push("no independiente");
    return parts.join(" · ");
  }

  function formatConfidence(confidence) {
    const value = Number(confidence);
    if (!Number.isFinite(value) || value <= 0) return "";
    return `${Math.round(value * 100)}% de confianza`;
  }

  const SLOT_STATE_LABELS = {
    fulfilled: "corrida",
    skipped: "salteada",
    pending: "pendiente"
  };

  const RUN_STATUS_LABELS = {
    running: "en curso",
    completed: "completa",
    degraded: "parcial",
    failed: "fallida"
  };

  /** Estado del scheduler: slots de hoy, próxima corrida e historial. */
  async function loadRuns(limit = 20) {
    runs = await request(`/radar/runs?limit=${encodeURIComponent(limit)}`);
    return runs;
  }

  function getRuns() {
    return Array.isArray(runs?.runs) ? runs.runs : [];
  }

  function getSlots() {
    return Array.isArray(runs?.slots) ? runs.slots : [];
  }

  function getCurrentRun() {
    return runs?.current || null;
  }

  function getNextSlot() {
    return runs?.nextSlot || null;
  }

  function getSlotStateLabel(state) {
    return SLOT_STATE_LABELS[state] || String(state || "");
  }

  function getRunStatusLabel(status) {
    return RUN_STATUS_LABELS[status] || String(status || "");
  }

  /** Hora local del próximo slot, en la zona que declara el backend. */
  function formatSlotTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("es-UY", { weekday: "short", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  async function startRun() {
    return write("/radar/run-now", {});
  }

  /** El Master propone; nunca activa. Todo lo que crea nace como borrador. */
  async function regenerateMaster() {
    return write("/radar/master/regenerate", {});
  }

  /** El feed «Para mí»: hasta diez oportunidades accionables. */
  async function loadFeed(limit = 10) {
    return request(`/radar/feed?limit=${encodeURIComponent(limit)}`);
  }

  /** Qué decidiste sobre una publicación. Nunca escribe estado de colección. */
  async function decide(listingId, decision) {
    return write("/radar/decisions", { listingId, ...decision });
  }

  async function clearDecision(listingId) {
    return request(`/radar/decisions/${encodeURIComponent(listingId)}`, { method: "DELETE", body: "{}" });
  }

  async function loadDecisions() {
    return request("/radar/decisions");
  }

  /** Inventario deduplicado: una publicación, una fila, todas sus búsquedas. */
  async function loadListings(limit = 100) {
    listings = await request(`/radar/listings?limit=${encodeURIComponent(limit)}`);
    return listings;
  }

  function getListings() {
    return Array.isArray(listings?.items) ? listings.items : [];
  }

  async function createSearch(payload) {
    return write("/radar/searches", payload);
  }

  async function updateSearch(searchId, payload) {
    return write(`/radar/searches/${encodeURIComponent(searchId)}`, payload);
  }

  async function setStatus(searchId, status) {
    return write(`/radar/searches/${encodeURIComponent(searchId)}/status`, { status });
  }

  async function duplicateSearch(searchId) {
    return write(`/radar/searches/${encodeURIComponent(searchId)}/duplicate`, {});
  }

  async function runSearch(searchId) {
    return write(`/radar/searches/${encodeURIComponent(searchId)}/run`, {});
  }

  /**
   * Corre la búsqueda y devuelve lo que traería, sin guardar nada.
   *
   * Sirve para mirar una búsqueda en borrador o pausada antes de activarla: el
   * servidor no escribe inventario, historial ni última corrida, así que la
   * respuesta es de sólo lectura y no aparece después en la card.
   */
  async function previewSearch(searchId) {
    return write(`/radar/searches/${encodeURIComponent(searchId)}/preview`, {});
  }

  async function deleteSearch(searchId) {
    return request(`/radar/searches/${encodeURIComponent(searchId)}`, {
      method: "DELETE",
      body: "{}"
    });
  }

  /** Presupuesto mensual (PRD §10.6): monto guardado, gastado/reservado/disponible. */
  async function loadBudget() {
    return request("/radar/budget");
  }

  async function getPreferences() {
    return request("/radar/preferences");
  }

  async function updateBudget(monthlyBudgetUsd, maxActiveSearches) {
    const payload = { monthlyBudgetUsd };
    if (maxActiveSearches !== undefined) payload.maxActiveSearches = maxActiveSearches;
    return write("/radar/preferences", payload);
  }

  /** PRD §10.5, cálculo puro: nada se persiste ni se infiere del título. */
  async function computeLotValuation(payload) {
    return write("/radar/lot-valuation", payload);
  }

  /**
   * Deja evidencia del radar (para presupuesto e historial) y marca la
   * decisión como "purchased". NO escribe la colección — ver
   * `window.RadarPurchase` para la única acción que sí lo hace.
   */
  async function recordPurchase(payload) {
    return write("/radar/purchases", payload);
  }

  /** Historial de compras registradas, lo más reciente primero. */
  async function loadPurchases() {
    purchases = await request("/radar/purchases");
    return purchases;
  }

  function getPurchases() {
    return Array.isArray(purchases?.items) ? purchases.items : [];
  }

  async function deletePurchase(purchaseId) {
    return request(`/radar/purchases/${encodeURIComponent(purchaseId)}`, { method: "DELETE" });
  }

  /** Qué parte de la colección el radar no está mirando. */
  async function loadCoverage() {
    coverage = await request("/radar/coverage");
    return coverage;
  }

  function getCoverage() {
    return coverage;
  }

  /** Lo que espera en la casilla, contra la franquicia de importación. */
  async function loadShipment() {
    shipment = await request("/radar/shipment");
    return shipment;
  }

  function getShipment() {
    return shipment;
  }

  /** El reenvío lo pide el owner: el radar no puede saber cuándo salió. */
  async function closeShipment() {
    const result = await write("/radar/shipment/close", {});
    shipment = result.shipment || null;
    return result;
  }

  /**
   * Carga manual de una fuente de verificación asistida (ShopGoodwill, PRD
   * §12.3): nunca un crawler — el owner trae los datos de una publicación
   * que ya vio, y se evalúa contra los mismos criterios que un resultado
   * automático.
   */
  async function createManualListing(payload) {
    return write("/radar/manual-listings", payload);
  }

  /** Confirma Add to Cart/Buy Now en la publicación real; sin esto, un snippet no alcanza. */
  async function verifyListing(listingId) {
    return write(`/radar/listings/${encodeURIComponent(listingId)}/verify`, {});
  }

  /** El dueño miró las fotos: la caja y el manual (o el contenido) del CIB están. */
  async function verifyListingContents(listingId) {
    return write(`/radar/listings/${encodeURIComponent(listingId)}/verify-contents`, {});
  }

  window.RadarRepository = {
    STATUS_ORDER,
    STATUS_LABELS,
    TYPE_LABELS,
    PRIORITY_LABELS,
    CONDITION_LABELS,
    COMPLETENESS_LABELS,
    REQUIREMENT_LABELS,
    DISMISS_REASONS,
    load,
    getModel,
    isAvailable,
    getSearches,
    getSearch,
    getSearchesByStatus,
    getSources,
    getSource,
    getSourceLabel,
    getEnvironment,
    getEnvironmentLabel,
    isSandbox,
    getCounts,
    getStatusLabel,
    getTypeLabel,
    getSearchKindLabel,
    getPriorityLabel,
    canRun,
    canPreview,
    isExecutableSource,
    getBlockedSources,
    formatAmount,
    formatTotal,
    formatImported,
    formatConfidence,
    getBandLabel,
    getVerification,
    describeVerification,
    getVerdictNotes,
    formatAgo,
    isUnconfirmedLot,
    isSecondaryResult,
    getSectionInfo,
    splitResults,
    getObjectiveTaxonomy,
    hasObjectiveTaxonomy,
    objectiveOfResult,
    groupResultsByObjective,
    describeContents,
    pickTopResults,
    VIEW_MODES,
    OBJECTIVE_GROUPS,
    GROUP_STATUS_LABELS,
    platformOf,
    objectiveOf,
    compareSearches,
    summarizeGroup,
    groupSearches,
    describeBenchmark,
    describeCriteria,
    loadFeed,
    decide,
    clearDecision,
    loadDecisions,
    loadListings,
    getListings,
    loadRuns,
    getRuns,
    getSlots,
    getCurrentRun,
    getNextSlot,
    getSlotStateLabel,
    getRunStatusLabel,
    formatSlotTime,
    startRun,
    regenerateMaster,
    createSearch,
    updateSearch,
    setStatus,
    duplicateSearch,
    runSearch,
    previewSearch,
    deleteSearch,
    loadBudget,
    getPreferences,
    updateBudget,
    computeLotValuation,
    recordPurchase,
    loadCoverage,
    getCoverage,
    loadPurchases,
    getPurchases,
    deletePurchase,
    loadShipment,
    getShipment,
    closeShipment,
    createManualListing,
    verifyListing,
    verifyListingContents
  };
})();
