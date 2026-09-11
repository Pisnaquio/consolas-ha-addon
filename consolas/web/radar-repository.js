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

  let model = null;
  let listings = null;
  let runs = null;
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

  function getPriorityLabel(priority) {
    return PRIORITY_LABELS[priority] || String(priority || "");
  }

  function canRun(item = {}) {
    return item.canRun === true;
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
    if ((criteria.excludeTerms || []).length) chips.push(`Excluye: ${criteria.excludeTerms.join(", ")}`);
    return chips;
  }

  /** Total recibido en Estados Unidos, sólo cuando el envío está confirmado. */
  function formatTotal(result = {}) {
    const total = Number(result.totalAmount);
    const price = Number(result.priceAmount);
    if (!Number.isFinite(total) || !Number.isFinite(price) || total === price) return "";
    return `${formatAmount(total, result.priceCurrency)} recibido`;
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

  async function deleteSearch(searchId) {
    return request(`/radar/searches/${encodeURIComponent(searchId)}`, {
      method: "DELETE",
      body: "{}"
    });
  }

  window.RadarRepository = {
    STATUS_ORDER,
    STATUS_LABELS,
    TYPE_LABELS,
    PRIORITY_LABELS,
    CONDITION_LABELS,
    COMPLETENESS_LABELS,
    REQUIREMENT_LABELS,
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
    getPriorityLabel,
    canRun,
    isExecutableSource,
    getBlockedSources,
    formatAmount,
    formatTotal,
    formatConfidence,
    describeCriteria,
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
    deleteSearch
  };
})();
