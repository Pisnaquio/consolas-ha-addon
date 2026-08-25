const fallbackImage = "./assets/photos/console-placeholder.svg";
const fallbackGameImage = "./assets/photos/game-placeholder.svg";
const formatPrice = (value, currency = "USD") => {
  if (!value || value <= 0) return "Sin dato";
  const normalizedCurrency = /^[A-Z]{3}$/.test(String(currency).toUpperCase())
    ? String(currency).toUpperCase()
    : "USD";
  return new Intl.NumberFormat("es-UY", {
    style: "currency",
    currency: normalizedCurrency,
    maximumFractionDigits: 0
  }).format(value);
};

const yesNo = (value) => {
  if (value === true) return "Si";
  if (value === false) return "No";
  return "N/D";
};

const escapeHtml = (text = "") =>
  text
    .toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const appState = {
  root: null,
  id: null,
  item: null,
  baseGames: [],
  baseAccessories: [],
  allStatuses: [],
  accessoryCatalogByConsole: {},
  draft: null,
  saveMessage: "",
  accessoryFilters: {
    q: "",
    view: "all",
    type: "all",
    layout: "compact"
  },
  gameFilters: {
    q: "",
    status: "all",
    priority: "all",
    franchise: "all",
    genre: "all",
    ownership: "all",
    list: "all",
    sort: "default"
  }
};

const PHYSICAL_ONLY_GAME_CONSOLES = new Set([
  "snes",
  "ps1",
  "gba-sp",
  "n64",
  "genesis",
  "dreamcast",
  "nes-clonica",
  "atari",
  "gb-original",
  "gb-color",
  "ds-lite",
  "gamecube",
  "wii"
]);

function getPageMode() {
  return document.body?.dataset?.view || "detail";
}

function buildConsoleHref(page = "detail", consoleId = "") {
  const targetId = consoleId || appState.id || "";
  if (page === "games") return window.CollectionRepository.getConsoleGamesHref(targetId);
  if (page === "accessories") return window.CollectionRepository.getConsoleAccessoriesHref(targetId);
  return window.CollectionRepository.getConsoleDetailHref(targetId);
}

function buildHomeHref() {
  return window.CollectionRepository.getHomeHref();
}

function readOverrides() {
  return window.CollectionRepository.readOverrides();
}

function readAdditions() {
  return window.CollectionRepository.readAdditionsArray();
}

function readDetailEdits() {
  return window.CollectionRepository.readDetailEdits();
}

function mergeWithAdditions(items) {
  return window.CollectionRepository.mergeWithAdditions(items);
}

function getPriceModel(item) {
  return {
    priceChart: Number(item.precioPriceChart) || null,
    ebaySold: Number(item.precioEbaySold) || null,
    gameStop: Number(item.precioGameStop) || null,
    cib: Number(item.precioCIB) || null,
    target: Number(item.precioObjetivoCompra) || null
  };
}

function renderPriceReference(prices, item) {
  const targetBlock = item.tengo
    ? ""
    : `
      <article class="price-box target">
        <small>Objetivo de compra</small>
        <strong>${formatPrice(prices.target)}</strong>
      </article>
    `;

  return `
    <section class="price-model" aria-label="Referencias de precio">
      <p class="price-model-title">Referencias de precio (USD)</p>
      <div class="price-grid">
        <article class="price-box market">
          <small>Mercado: eBay sold</small>
          <strong>${formatPrice(prices.ebaySold)}</strong>
        </article>
        <article class="price-box market">
          <small>Mercado: PriceChart</small>
          <strong>${formatPrice(prices.priceChart)}</strong>
        </article>
        <article class="price-box retail">
          <small>Techo: GameStop</small>
          <strong>${formatPrice(prices.gameStop)}</strong>
        </article>
        <article class="price-box premium">
          <small>Premium: CIB</small>
          <strong>${formatPrice(prices.cib)}</strong>
        </article>
        ${targetBlock}
      </div>
    </section>
  `;
}

function applyItemOverride(item) {
  const override = readOverrides()[item.id];
  if (!override) return item;
  const tengo = typeof override.tengo === "boolean" ? override.tengo : item.tengo;
  return {
    ...item,
    tengo,
    categoria: tengo ? "coleccion" : "wishlist"
  };
}

function applyDetailEdits(item) {
  const edits = readDetailEdits()[item.id];
  return window.CollectionRepository.applyItemDetailEdits(item, edits);
}

function saveDetailEdits(partial) {
  window.DataStore?.updateDetailEdit?.(appState.id, partial || {});
}

function createDraftFromItem(item) {
  return {
    estado: item.estado || "",
    funcionando: item.funcionando === true ? "true" : item.funcionando === false ? "false" : "",
    formaObtencion: item.formaObtencion || "",
    ubicacionMapa: item.ubicacionMapa || "",
    precioPagado: item.precioPagado ?? "",
    accesoriosCatalogoNotas: item.accesoriosCatalogoNotas || ""
  };
}

function markPendingSave() {
  appState.saveMessage = "Cambios sin guardar";
  const status = document.getElementById("saveIndicator");
  if (status) status.textContent = appState.saveMessage;
}

function saveDraftFields() {
  const payload = {
    estado: appState.draft.estado,
    funcionando: appState.draft.funcionando === "true" ? true : appState.draft.funcionando === "false" ? false : null,
    formaObtencion: appState.draft.formaObtencion,
    ubicacionMapa: appState.draft.ubicacionMapa,
    accesoriosCatalogoNotas: appState.draft.accesoriosCatalogoNotas
  };

  if (appState.item.tengo) {
    const raw = appState.draft.precioPagado;
    const numeric = raw === "" ? null : Number(raw);
    payload.precioPagado = Number.isFinite(numeric) ? numeric : null;
  }

  saveDetailEdits(payload);
  appState.item = applyDetailEdits(appState.item);
  appState.draft = createDraftFromItem(appState.item);
  appState.saveMessage = "Guardado";
  render();
}

function updateGamesCatalog(nextGames) {
  window.CollectionRepository.persistGameEntityState(appState.id, nextGames, appState.baseGames || []);
  appState.item = {
    ...appState.item,
    juegosCatalogo: composeGamesForConsole(appState.baseGames || [], nextGames)
  };
  appState.saveMessage = "Guardado";
  render();
}

function updateAccessoriesCatalog(nextAccessories) {
  window.CollectionRepository.persistAccessoryEntityState(appState.id, nextAccessories, appState.baseAccessories || []);
  appState.item = {
    ...appState.item,
    accesoriosItems: composeAccessoriesForConsole(appState.baseAccessories || [], nextAccessories)
  };
  appState.saveMessage = "Guardado";
  render();
}

function removePhotoAt(index) {
  const list = [...(appState.item.fotosPropias || [])];
  list.splice(index, 1);
  saveDetailEdits({ fotosPropias: list });
  appState.item = applyDetailEdits(appState.item);
  appState.saveMessage = "Guardado";
  render();
}

function removeGameAt(index) {
  const list = [...(appState.item.juegos || [])];
  list.splice(index, 1);
  saveDetailEdits({ juegos: list });
  appState.item = applyDetailEdits(appState.item);
  appState.saveMessage = "Guardado";
  render();
}

function removeOpportunityAt(index) {
  const list = [...(appState.item.oportunidades || [])];
  list.splice(index, 1);
  saveDetailEdits({ oportunidades: list });
  appState.item = applyDetailEdits(appState.item);
  appState.saveMessage = "Guardado";
  render();
}

function renderEditableList(items, emptyText, removeClass) {
  if (!items?.length) return `<li>${emptyText}</li>`;
  return items
    .map(
      (value, idx) => `
      <li class="editable-row">
        <span>${escapeHtml(value)}</span>
        <button class="btn-link ${removeClass}" data-index="${idx}" type="button">Quitar</button>
      </li>
    `
    )
    .join("");
}

function getConsoleGalleryImages(item = {}) {
  const seen = new Set();
  return [
    ...(Array.isArray(item.fotos) ? item.fotos : []),
    ...(Array.isArray(item.fotosPropias) ? item.fotosPropias : [])
  ].filter((src) => {
    const value = typeof src === "string" ? src.trim() : "";
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function renderPhotoGallery(item, galleryImages = getConsoleGalleryImages(item)) {
  const own = Array.isArray(item.fotosPropias) ? item.fotosPropias : [];
  if (!own.length) return "<p class='muted'>Todavia no cargaste fotos propias.</p>";
  return `
    <div class="own-photo-grid">
      ${own
        .map((src, idx) => {
          const galleryIndex = galleryImages.indexOf(src);
          return `
        <article class="own-photo-card">
          <button
            class="own-photo-open js-open-console-photo"
            data-gallery-index="${galleryIndex}"
            type="button"
            aria-label="Abrir foto propia ${idx + 1} de ${escapeHtml(item.nombre || "la consola")} en grande"
          >
            <img src="${escapeHtml(src)}" alt="Foto propia ${idx + 1} de ${escapeHtml(item.nombre || "la consola")}" loading="lazy" />
            <span class="own-photo-open-label" aria-hidden="true">Ver en grande</span>
          </button>
          <button class="btn-link js-remove-photo" data-index="${idx}" type="button">Quitar</button>
        </article>
      `;
        })
        .join("")}
    </div>
  `;
}

function renderConsolePhotoDialog(item, galleryImages = getConsoleGalleryImages(item)) {
  if (!galleryImages.length) return "";
  const consoleName = item.nombre || "la consola";
  const hasMultiplePhotos = galleryImages.length > 1;

  return `
    <dialog id="consolePhotoDialog" class="console-photo-dialog" aria-labelledby="consolePhotoDialogTitle">
      <div class="console-photo-dialog-shell">
        <header class="console-photo-dialog-head">
          <div>
            <p class="eyebrow">Galería de consola</p>
            <h3 id="consolePhotoDialogTitle" aria-live="polite">Foto 1 de ${galleryImages.length}</h3>
          </div>
          <button id="closeConsolePhotoDialogBtn" class="console-photo-close" type="button" aria-label="Cerrar foto ampliada">×</button>
        </header>
        <div class="console-photo-stage">
          <button
            id="previousConsolePhotoBtn"
            class="console-photo-nav console-photo-nav--previous"
            type="button"
            aria-label="Ver foto anterior"
            ${hasMultiplePhotos ? "" : "hidden"}
          >←</button>
          <figure class="console-photo-figure">
            <img
              id="consolePhotoDialogImage"
              src="${escapeHtml(galleryImages[0])}"
              alt="Foto 1 de ${galleryImages.length} de ${escapeHtml(consoleName)}"
            />
            <figcaption id="consolePhotoDialogCaption">${escapeHtml(consoleName)} · Foto 1 de ${galleryImages.length}</figcaption>
          </figure>
          <button
            id="nextConsolePhotoBtn"
            class="console-photo-nav console-photo-nav--next"
            type="button"
            aria-label="Ver foto siguiente"
            ${hasMultiplePhotos ? "" : "hidden"}
          >→</button>
        </div>
      </div>
    </dialog>
  `;
}

function renderOpportunities(item) {
  if (!item.oportunidades?.length) return "<p class='muted'>Todavia no hay oportunidades registradas.</p>";

  return `
    <div class="mini-grid">
      ${item.oportunidades
        .map(
          (op, idx) => `
        <article class="mini-card">
          <h4>${escapeHtml(op.titulo || "Oportunidad")}</h4>
          <p class="muted">Fuente: ${escapeHtml(op.fuente || "N/D")} ${op.fecha ? `• ${escapeHtml(op.fecha)}` : ""}</p>
          <p>${escapeHtml(op.nota || "Sin nota")}</p>
          <p class="muted">Precio visto: ${formatPrice(op.precioVisto)} • Objetivo: ${formatPrice(op.precioObjetivo)}</p>
          ${
            op.url
              ? `<a class="btn-link" href="${escapeHtml(op.url)}" target="_blank" rel="noreferrer noopener">Abrir enlace</a>`
              : ""
          }
          <button class="btn-link js-remove-opportunity" data-index="${idx}" type="button">Quitar</button>
        </article>
      `
        )
        .join("")}
    </div>
  `;
}

async function loadAuctionWatchSnapshot() {
  return window.AuctionWatchRepository?.loadSnapshot?.() || null;
}

function renderAutomaticOpportunityBadges(item = {}) {
  const repo = window.AuctionWatchRepository;
  const badges = [
    repo?.getSourceLabel?.(item.source) || "Auction Watch",
    item.urgencyLabel || "seguimiento",
    ...(item.watchlist ? ["watchlist"] : []),
    ...(item.matchedKeywords || []).slice(0, 2)
  ];

  return badges
    .filter(Boolean)
    .map((badge) => `<span class="opportunity-badge">${escapeHtml(badge)}</span>`)
    .join("");
}

function renderAutomaticOpportunityCard(item = {}, highlighted = false) {
  const repo = window.AuctionWatchRepository;
  const primaryUrl = repo?.getPrimaryUrl?.(item) || "";
  const secondaryUrl = repo?.getSecondaryUrl?.(item) || "";
  const primaryLabel = repo?.getPrimaryCtaLabel?.(item) || "Ver detalle";
  const secondaryLabel = repo?.getSecondaryCtaLabel?.(item) || "";
  const description = item.description || item.notes || "Oportunidad detectada por el monitoreo activo.";

  return `
    <article class="mini-card opportunity-card ${highlighted ? "opportunity-card--featured" : ""}">
      <div class="opportunity-card-head">
        <div>
          <p class="opportunity-kicker">${escapeHtml(repo?.getSourceLabel?.(item.source) || "Auction Watch")} · solo lectura</p>
          <h4>${escapeHtml(item.title || "Oportunidad activa")}</h4>
        </div>
        <span class="opportunity-timer">${escapeHtml(item.remainingText || "-")}</span>
      </div>
      <div class="opportunity-badges">
        ${renderAutomaticOpportunityBadges(item)}
      </div>
      <p class="opportunity-copy">${escapeHtml(description)}</p>
      <div class="opportunity-meta">
        ${item.priceLabel ? `<span>${escapeHtml(item.priceLabel)}</span>` : ""}
        ${item.groupLabel ? `<span>${escapeHtml(item.groupLabel)}</span>` : ""}
      </div>
      <div class="card-actions">
        ${primaryUrl ? `<a class="btn-link" href="${escapeHtml(primaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(primaryLabel)}</a>` : ""}
        ${secondaryUrl ? `<a class="btn-link" href="${escapeHtml(secondaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(secondaryLabel)}</a>` : ""}
      </div>
    </article>
  `;
}

function renderAutomaticOpportunities(consoleId = "") {
  const repo = window.AuctionWatchRepository;
  const sync = repo?.getSyncState?.() || {
    status: repo?.getSnapshotSource?.() === "server" ? "ready" : "unavailable"
  };
  const items = repo?.getConsoleOpportunities?.(consoleId) || [];
  if (!items.length) {
    return sync.status === "empty" || sync.status === "ready"
      ? "<p class='muted'>No hay oportunidades automáticas activas para esta consola en la corrida confirmada.</p>"
      : "<p class='muted'>Auction Watch no tiene un snapshot vigente confirmado para esta consola. Revisá la página de Oportunidades.</p>";
  }

  const syncNotice = ["stale", "degraded", "unavailable"].includes(sync.status)
    ? `<p class="muted">Estas oportunidades provienen de un snapshot ${sync.status === "stale" ? "desactualizado" : "sin sincronización completa"}. Verificá Auction Watch antes de decidir.</p>`
    : "";

  return `
    ${syncNotice}
    <div class="mini-grid">
      ${items.map((item) => renderAutomaticOpportunityCard(item, item.watchlist === true)).join("")}
    </div>
  `;
}

function refreshAutomaticOpportunitiesBlock() {
  const target = document.getElementById("auctionWatchConsoleOpportunities");
  if (!target || !appState.item?.id) return;
  target.innerHTML = renderAutomaticOpportunities(appState.item.id);
}

function buildStatusOptions(selectedStatus) {
  const unique = [...new Set([...(appState.allStatuses || []), selectedStatus].filter(Boolean))];
  return unique
    .map((status) => {
      const selected = status === selectedStatus ? "selected" : "";
      return `<option value="${escapeHtml(status)}" ${selected}>${escapeHtml(status)}</option>`;
    })
    .join("");
}

function buildFunctioningOptions(value) {
  const selected = value === true ? "true" : value === false ? "false" : "";
  return [
    ["", "Sin confirmar"],
    ["true", "Sí, funciona"],
    ["false", "No, requiere revisión"]
  ]
    .map(([optionValue, label]) => `<option value="${optionValue}" ${optionValue === selected ? "selected" : ""}>${label}</option>`)
    .join("");
}

function functioningLabel(value) {
  if (value === "true" || value === true) return "Sí";
  if (value === "false" || value === false) return "No";
  return "Sin confirmar";
}

function accessoryPlaceholderImage(accessory = {}) {
  const title = (accessory.nombre || "Accesorio").slice(0, 28);
  const type = getAccessoryTypeLabel(accessory.tipo || "otro").toUpperCase().slice(0, 18);
  const accent =
    accessory.tipo === "control"
      ? "#5ad1ff"
      : accessory.tipo === "vr"
        ? "#8c96ff"
        : accessory.tipo === "audio"
          ? "#7ee0a8"
          : accessory.tipo === "video"
            ? "#f3c56d"
            : "#7fcfff";
  const svg = `
  <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 480 320'>
    <defs>
      <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
        <stop offset='0%' stop-color='#16385a'/>
        <stop offset='100%' stop-color='#08182d'/>
      </linearGradient>
      <linearGradient id='panel' x1='0' y1='0' x2='1' y2='1'>
        <stop offset='0%' stop-color='rgba(255,255,255,0.18)'/>
        <stop offset='100%' stop-color='rgba(255,255,255,0.05)'/>
      </linearGradient>
    </defs>
    <rect width='480' height='320' rx='26' fill='url(#bg)'/>
    <rect x='28' y='28' width='424' height='264' rx='22' fill='url(#panel)' stroke='rgba(146,201,244,0.34)'/>
    <rect x='52' y='54' width='376' height='120' rx='18' fill='rgba(3,13,24,0.28)' stroke='rgba(255,255,255,0.10)'/>
    <circle cx='98' cy='114' r='28' fill='${accent}' opacity='0.92'/>
    <circle cx='382' cy='114' r='16' fill='${accent}' opacity='0.45'/>
    <rect x='146' y='92' width='170' height='16' rx='8' fill='rgba(255,255,255,0.92)'/>
    <rect x='146' y='122' width='132' height='12' rx='6' fill='rgba(194,220,244,0.72)'/>
    <rect x='52' y='206' width='126' height='54' rx='14' fill='rgba(255,255,255,0.08)'/>
    <text x='70' y='239' font-size='22' fill='${accent}' font-family='Arial, sans-serif'>PS4</text>
    <text x='240' y='238' font-size='22' text-anchor='middle' fill='rgba(236,245,255,0.95)' font-family='Arial, sans-serif'>${title}</text>
    <text x='240' y='268' font-size='16' text-anchor='middle' fill='rgba(179,208,235,0.85)' font-family='Arial, sans-serif'>${type}</text>
  </svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function normalizeAccessoryName(name = "") {
  return String(name || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function getAccessoryAliasKeys(accessory = {}) {
  const keys = new Set();
  const id = String(accessory?.id || "").trim().toLowerCase();
  const name = normalizeAccessoryName(accessory?.nombre || "");

  if (id) keys.add(id);
  if (name) keys.add(name);

  if (id === "dualshock-4" || name.includes("dualshock 4") || name.includes("dual shock 4") || name.includes("ds4")) {
    keys.add("dualshock-4");
    keys.add("dualshock 4");
    keys.add("dual shock 4");
    keys.add("ds4");
    keys.add("control dualshock 4");
    keys.add("controller dualshock 4");
  }

  if (
    id === "charging-dock" ||
    name.includes("charging station") ||
    name.includes("charging dock") ||
    name.includes("base de carga") ||
    name.includes("cargador dualshock") ||
    name.includes("dock dualshock")
  ) {
    keys.add("charging-dock");
    keys.add("charging station dualshock 4");
    keys.add("charging dock dualshock 4");
    keys.add("base de carga dualshock 4");
    keys.add("base de carga");
    keys.add("dock dualshock 4");
  }

  if (id === "psvr-headset" || name.includes("playstation vr") || name.includes("ps vr") || name.includes("psvr")) {
    keys.add("psvr-headset");
    keys.add("playstation vr");
    keys.add("ps vr");
    keys.add("psvr");
  }

  return keys;
}

function accessoryMatchesReference(reference = {}, candidate = {}) {
  const referenceKeys = getAccessoryAliasKeys(reference);
  const candidateKeys = getAccessoryAliasKeys(candidate);
  for (const key of referenceKeys) {
    if (candidateKeys.has(key)) return true;
  }
  return false;
}

function getAccessoryCanonicalGroup(accessory = {}) {
  const keys = getAccessoryAliasKeys(accessory);
  if (keys.has("dualshock-4") || keys.has("dualshock 4") || keys.has("dual shock 4") || keys.has("ds4")) {
    return "dualshock-4";
  }
  if (keys.has("charging-dock") || keys.has("base de carga") || keys.has("charging station dualshock 4")) {
    return "charging-dock";
  }
  if (keys.has("psvr-headset") || keys.has("playstation vr") || keys.has("ps vr") || keys.has("psvr")) {
    return "psvr-headset";
  }
  return "";
}

function isAccessoryGenericDuplicate(accessory = {}, canonicalGroup = "") {
  const normalizedName = normalizeAccessoryName(accessory?.nombre || "");
  if (!canonicalGroup || !normalizedName) return false;

  const genericNamesByGroup = {
    "dualshock-4": new Set([
      "dualshock 4",
      "dual shock 4",
      "ds4",
      "control dualshock 4",
      "controller dualshock 4",
      "joystick ps4",
      "control ps4"
    ]),
    "charging-dock": new Set([
      "base de carga",
      "base de carga para controles",
      "base de carga dualshock 4",
      "charging dock",
      "charging station",
      "charging station dualshock 4",
      "charging dock dualshock 4",
      "dock dualshock 4",
      "cargador dualshock 4"
    ]),
    "psvr-headset": new Set([
      "playstation vr",
      "ps vr",
      "psvr",
      "ps vr headset",
      "playstation vr headset"
    ])
  };

  return genericNamesByGroup[canonicalGroup]?.has(normalizedName) === true;
}

function scoreAccessoryForDedup(accessory = {}) {
  let score = 0;
  if ((accessory.sourceType || "catalog") === "catalog") score += 100;
  if (accessoryIsOwned(accessory)) score += 20;
  if (accessory.esencial === true) score += 10;
  if (accessory.image && !String(accessory.image).startsWith("data:image/svg+xml")) score += 5;
  if (accessory.notas) score += 2;
  return score;
}

function dedupeAccessoryList(accessories = []) {
  const filtered = [];
  const seenGenericByGroup = new Map();

  accessories.forEach((accessory) => {
    const canonicalGroup = getAccessoryCanonicalGroup(accessory);
    const isGeneric = isAccessoryGenericDuplicate(accessory, canonicalGroup);

    if (!canonicalGroup || !isGeneric) {
      filtered.push(accessory);
      return;
    }

    const existingIndex = seenGenericByGroup.get(canonicalGroup);
    if (existingIndex === undefined) {
      seenGenericByGroup.set(canonicalGroup, filtered.length);
      filtered.push(accessory);
      return;
    }

    const existing = filtered[existingIndex];
    if (scoreAccessoryForDedup(accessory) > scoreAccessoryForDedup(existing)) {
      filtered[existingIndex] = accessory;
    }
  });

  return filtered;
}

function normalizeAccessoryState(accessory = {}, index = 0) {
  const cantidad = Number(accessory.cantidad);
  const safeQty = Number.isFinite(cantidad) && cantidad > 0 ? Math.round(cantidad) : 0;
  return {
    ...accessory,
    __index: index,
    tipo: accessory.tipo || "otro",
    cantidad: safeQty,
    tengo: accessory.tengo === true || safeQty > 0,
    esencial: accessory.esencial === true,
    funcionando:
      accessory.funcionando === true ? true : accessory.funcionando === false ? false : null,
    original:
      accessory.original === "original" || accessory.original === "third-party" || accessory.original === "mixto"
        ? accessory.original
        : "",
    image: accessory.image || "",
    estado: accessory.estado || "",
    notas: accessory.notas || "",
    sourceType: accessory.sourceType || "catalog",
    orden: Number(accessory.orden) || (index + 1) * 10
  };
}

function accessoryIsOwned(accessory = {}) {
  return accessory.tengo === true || Number(accessory.cantidad) > 0;
}

function getAccessoryTypeLabel(type = "") {
  const labels = {
    control: "Control",
    energia: "Energía",
    video: "Video",
    audio: "Audio",
    carga: "Carga",
    soporte: "Soporte",
    sensor: "Sensor",
    vr: "VR",
    memoria: "Memoria",
    red: "Red",
    otro: "Otro"
  };
  return labels[type] || "Otro";
}

function getAccessoryOriginalLabel(value = "") {
  if (value === "original") return "Original";
  if (value === "third-party") return "Third-party";
  if (value === "mixto") return "Mixto";
  return "Sin dato";
}

function getAccessoryFunctioningLabel(value) {
  if (value === true) return "Sí";
  if (value === false) return "No";
  return "Sin probar";
}

function buildAccessoryFunctioningOptions(value) {
  const selected = value === true ? "true" : value === false ? "false" : "";
  return [
    ["", "Sin probar"],
    ["true", "Sí"],
    ["false", "No"]
  ]
    .map(([optionValue, label]) => `<option value="${optionValue}" ${optionValue === selected ? "selected" : ""}>${label}</option>`)
    .join("");
}

function buildAccessoryOriginalOptions(value = "") {
  return [
    ["", "Sin dato"],
    ["original", "Original"],
    ["third-party", "Third-party"],
    ["mixto", "Mixto"]
  ]
    .map(([optionValue, label]) => `<option value="${optionValue}" ${optionValue === value ? "selected" : ""}>${label}</option>`)
    .join("");
}

function buildAccessoryStateOptions(value = "") {
  return [
    ["", "Sin dato"],
    ["excelente", "Excelente"],
    ["muy bueno", "Muy bueno"],
    ["bueno", "Bueno"],
    ["detalles", "Con detalles"],
    ["revisar", "Revisar"]
  ]
    .map(([optionValue, label]) => `<option value="${optionValue}" ${optionValue === value ? "selected" : ""}>${label}</option>`)
    .join("");
}

function reconcileAccessoriesCatalog(baseAccessories = [], currentAccessories = [], legacyAccessories = []) {
  const normalizedBase = Array.isArray(baseAccessories) ? baseAccessories : [];
  const normalizedCurrent = Array.isArray(currentAccessories) ? currentAccessories : [];
  const normalizedLegacy = Array.isArray(legacyAccessories) ? legacyAccessories : [];

  const byId = new Map();
  const byName = new Map();
  normalizedCurrent.forEach((accessory) => {
    if (accessory?.id) byId.set(String(accessory.id), accessory);
    const key = normalizeAccessoryName(accessory?.nombre || "");
    if (key) byName.set(key, accessory);
  });

  const legacyUsed = new Set();
  const merged = normalizedBase.map((base, index) => {
    const nonManualMatch = normalizedCurrent.find(
      (accessory) => (accessory.sourceType || "catalog") !== "manual" && accessoryMatchesReference(base, accessory)
    );
    const existing =
      byId.get(String(base.id)) ||
      byName.get(normalizeAccessoryName(base.nombre)) ||
      nonManualMatch;
    let legacyMatch = null;
    if (!existing) {
      legacyMatch = normalizedLegacy.find((entry, legacyIndex) => {
        if (legacyUsed.has(legacyIndex)) return false;
        const normalizedEntry = normalizeAccessoryName(entry);
        const baseName = normalizeAccessoryName(base.nombre);
        const idName = normalizeAccessoryName(base.id);
        return normalizedEntry.includes(baseName) || baseName.includes(normalizedEntry) || (idName && normalizedEntry.includes(idName));
      });
      if (legacyMatch) legacyUsed.add(normalizedLegacy.indexOf(legacyMatch));
    }

    return normalizeAccessoryState(
      {
        ...base,
        ...(existing || {}),
        image: (existing?.sourceType || "catalog") === "manual" ? existing?.image || base.image || "" : base.image || existing?.image || "",
        tengo: existing ? existing.tengo : Boolean(legacyMatch),
        cantidad:
          existing && existing.cantidad !== undefined
            ? existing.cantidad
            : legacyMatch
              ? Math.max(1, Number((String(legacyMatch).match(/\d+/) || [1])[0]))
              : 0,
        notas:
          existing?.notas ||
          ""
      },
      index
    );
  });

  const extras = normalizedCurrent
    .filter((accessory) => {
      if ((accessory.sourceType || "catalog") !== "manual") return false;
      const exactBaseMatch = normalizedBase.some(
        (base) =>
          String(base.id) === String(accessory.id) || normalizeAccessoryName(base.nombre) === normalizeAccessoryName(accessory.nombre)
      );
      return !exactBaseMatch;
    })
    .map((accessory, index) => normalizeAccessoryState({ ...accessory, sourceType: accessory.sourceType || "manual" }, merged.length + index));

  const adoptedLegacyExtras = normalizedLegacy
    .filter((entry, index) => !legacyUsed.has(index))
    .map((entry, index) =>
      normalizeAccessoryState(
        {
          id: `legacy-${normalizeAccessoryName(entry).replaceAll(" ", "-") || `item-${index + 1}`}`,
          nombre: entry,
          tipo: "otro",
          esencial: false,
          tengo: true,
          cantidad: Math.max(1, Number((String(entry).match(/\d+/) || [1])[0])),
          sourceType: "manual",
          orden: 900 + index * 10
        },
        merged.length + extras.length + index
      )
    );

  return dedupeAccessoryList([...merged, ...extras, ...adoptedLegacyExtras]).sort((a, b) => {
    const ownedGap = Number(accessoryIsOwned(b)) - Number(accessoryIsOwned(a));
    if (ownedGap !== 0) return ownedGap;
    const essentialGap = Number(b.esencial === true) - Number(a.esencial === true);
    if (essentialGap !== 0) return essentialGap;
    const orderGap = (a.orden || 0) - (b.orden || 0);
    if (orderGap !== 0) return orderGap;
    return (a.nombre || "").localeCompare(b.nombre || "");
  });
}

function composeAccessoriesForConsole(baseAccessories = [], legacyCurrent = [], legacyAccessories = []) {
  const entityState = window.CollectionRepository.getConsoleEntityState(appState.id);
  if (window.CollectionRepository.hasAccessoryEntityState(entityState)) {
    return dedupeAccessoryList(window.CollectionRepository.composeAccessoriesFromEntity(baseAccessories, entityState)).sort((a, b) => {
      const ownedGap = Number(accessoryIsOwned(b)) - Number(accessoryIsOwned(a));
      if (ownedGap !== 0) return ownedGap;
      const essentialGap = Number(b.esencial === true) - Number(a.esencial === true);
      if (essentialGap !== 0) return essentialGap;
      const orderGap = (a.orden || 0) - (b.orden || 0);
      if (orderGap !== 0) return orderGap;
      return (a.nombre || "").localeCompare(b.nombre || "");
    });
  }
  return reconcileAccessoriesCatalog(baseAccessories, legacyCurrent, legacyAccessories);
}

function getAccessoryCatalogList(item = {}) {
  return Array.isArray(item.accesoriosItems) ? item.accesoriosItems.map((entry, index) => normalizeAccessoryState(entry, index)) : [];
}

function getAccessoryMetrics(item = {}) {
  const list = getAccessoryCatalogList(item);
  const owned = list.filter((entry) => accessoryIsOwned(entry)).length;
  const missing = list.filter((entry) => !accessoryIsOwned(entry)).length;
  const essentials = list.filter((entry) => entry.esencial === true).length;
  const essentialMissing = list.filter((entry) => entry.esencial === true && !accessoryIsOwned(entry)).length;
  const ready = list.filter((entry) => accessoryIsOwned(entry) && entry.funcionando === true).length;
  return {
    total: list.length,
    owned,
    missing,
    essentials,
    essentialMissing,
    ready
  };
}

function normalizePriority(priority = "") {
  return priority.toLowerCase().replaceAll(" ", "-");
}

function normalizeFilterValue(value = "") {
  return value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
}

function sanitizePriority(priority = "") {
  const normalized = normalizePriority(priority || "media");
  if (["alta", "media-alta", "media", "baja"].includes(normalized)) return normalized;
  return "media";
}

function priorityRank(priority = "") {
  const ranked = {
    alta: 4,
    "media-alta": 3,
    media: 2,
    baja: 1
  };
  return ranked[sanitizePriority(priority)] || 2;
}

function normalizeOwnershipType(raw = "", loTengo = false) {
  return window.CollectionRepository.normalizeOwnershipType(raw, loTengo);
}

function gameIsOwned(game = {}) {
  return window.CollectionRepository.gameIsOwned(game);
}

function isNonGameEntry(game = {}) {
  return window.CollectionRepository.isNonGameEntry(game);
}

function isPhysicalOnlyGameConsole(consoleId = "") {
  return PHYSICAL_ONLY_GAME_CONSOLES.has(consoleId);
}

function getOwnershipLabel(ownershipType = "none") {
  if (ownershipType === "physical") return "Fisico";
  if (ownershipType === "digital") return "Digital";
  if (ownershipType === "both") return "Ambos";
  return "";
}

function getOwnershipBadgeClass(ownershipType = "none") {
  if (ownershipType === "physical") return "ownership-physical";
  if (ownershipType === "digital") return "ownership-digital";
  if (ownershipType === "both") return "ownership-both";
  return "ownership-none";
}

function normalizeGameState(game = {}, index = 0) {
  const ownershipType = normalizeOwnershipType(game.ownershipType, game.loTengo);
  const owned = ownershipType !== "none";
  const keepInWishlist = game.keepInWishlist !== undefined ? Boolean(game.keepInWishlist) : !owned;
  const standby = game.standby === true;
  return {
    ...game,
    __index: index,
    ownershipType,
    loTengo: owned,
    keepInWishlist,
    standby,
    prioridad: sanitizePriority(game.prioridad || "media"),
    priceRange: mergePriceRange(game.priceRange || {}, game)
  };
}

function belongsToRegistered(game = {}) {
  return gameIsOwned(game);
}

function belongsToWishlist(game = {}) {
  if (window.CollectionRepository?.gameBelongsToWishlist) {
    return window.CollectionRepository.gameBelongsToWishlist(game);
  }
  const loQuiero = game.loQuiero === true;
  const keepInWishlist = game.keepInWishlist === true;
  return loQuiero || keepInWishlist;
}

function gameImagePlaceholder(game, consoleName = "") {
  const title = (game.nombre || "GBA").slice(0, 26);
  const label = (game.franquicia || consoleName || "Consola").slice(0, 30);
  const consoleLabel = (consoleName || "Console").toUpperCase().slice(0, 24);
  const svg = `
  <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 360 520'>
    <defs>
      <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
        <stop offset='0%' stop-color='#fdf3da'/>
        <stop offset='100%' stop-color='#e8f0fb'/>
      </linearGradient>
    </defs>
    <rect x='0' y='0' width='360' height='520' fill='url(#g)'/>
    <rect x='26' y='26' width='308' height='468' rx='18' fill='rgba(255,255,255,0.72)' stroke='#d9ceb9'/>
    <rect x='60' y='80' width='240' height='180' rx='12' fill='#f3f6fb' stroke='#d4dde8'/>
    <text x='180' y='132' font-size='16' text-anchor='middle' fill='#445769' font-family='Arial'>${consoleLabel}</text>
    <text x='180' y='166' font-size='18' text-anchor='middle' fill='#445769' font-family='Arial'>COLLECTION</text>
    <text x='180' y='320' font-size='20' text-anchor='middle' fill='#1e2b36' font-family='Arial'>${title}</text>
    <text x='180' y='356' font-size='16' text-anchor='middle' fill='#617181' font-family='Arial'>${label}</text>
  </svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function normalizeGameName(name = "") {
  return name
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function inferManualGameFromInput(name = "", games = []) {
  const clean = (name || "").trim();
  const normalized = normalizeGameName(clean);
  if (!normalized) {
    return {
      detectedName: "",
      detectedFranchise: "",
      detectedGenre: "",
      detectedCover: "",
      confidence: "none",
      reason: "Esperando nombre o imagen."
    };
  }

  const exact = games.find((g) => normalizeGameName(g.nombre) === normalized);
  if (exact) {
    return {
      detectedName: exact.nombre || clean,
      detectedFranchise: exact.franquicia || "",
      detectedGenre: exact.genero || "",
      detectedCover: exact.coverImage || exact.coverUrl || "",
      confidence: "high",
      reason: "Coincidencia exacta por nombre en catálogo de esta consola."
    };
  }

  const byContains = games.find((g) => normalizeGameName(g.nombre).includes(normalized) || normalized.includes(normalizeGameName(g.nombre)));
  if (byContains) {
    return {
      detectedName: byContains.nombre || clean,
      detectedFranchise: byContains.franquicia || "",
      detectedGenre: byContains.genero || "",
      detectedCover: byContains.coverImage || byContains.coverUrl || "",
      confidence: "medium",
      reason: "Coincidencia parcial por nombre."
    };
  }

  return {
    detectedName: clean,
    detectedFranchise: "",
    detectedGenre: "",
    detectedCover: "",
    confidence: "low",
    reason: "Sin match claro en catálogo. Se guarda como manual."
  };
}

function mergePriceGuide(baseGuide = {}, source = {}) {
  const sourceGuide = source.priceGuide || source.precio || {};
  return {
    priceCharting:
      sourceGuide.priceCharting !== undefined ? sourceGuide.priceCharting : baseGuide.priceCharting ?? null,
    ebaySold: sourceGuide.ebaySold !== undefined ? sourceGuide.ebaySold : baseGuide.ebaySold ?? null,
    cib: sourceGuide.cib !== undefined ? sourceGuide.cib : baseGuide.cib ?? null,
    targetBuy: sourceGuide.targetBuy !== undefined ? sourceGuide.targetBuy : baseGuide.targetBuy ?? null,
    notes: sourceGuide.notes ?? baseGuide.notes ?? ""
  };
}

function mergePriceRange(baseRange = {}, source = {}) {
  const sourceRange = source.priceRange || {};
  return {
    low: sourceRange.low !== undefined ? sourceRange.low : baseRange.low ?? null,
    mid: sourceRange.mid !== undefined ? sourceRange.mid : baseRange.mid ?? null,
    high: sourceRange.high !== undefined ? sourceRange.high : baseRange.high ?? null,
    notes: sourceRange.notes ?? baseRange.notes ?? ""
  };
}

function mergeVariants(baseVariants, sourceVariants) {
  if (Array.isArray(sourceVariants)) return sourceVariants;
  if (Array.isArray(baseVariants)) return baseVariants;
  return [];
}

function mergeOptionalList(baseList, sourceList) {
  if (Array.isArray(sourceList)) return sourceList;
  if (Array.isArray(baseList)) return baseList;
  return [];
}

function isPlaceholderCover(value = "") {
  const cover = String(value || "");
  return (
    !cover ||
    cover.includes("game-placeholder.svg") ||
    cover.includes("-fallback.svg") ||
    cover.includes("-owned.svg")
  );
}

function resolveCoverField(source, base) {
  const sourceCover = source?.coverImage || source?.coverUrl || "";
  const baseCover = base?.coverImage || base?.coverUrl || "";
  if (sourceCover) return sourceCover;
  if (baseCover) return baseCover;
  return "";
}

function isLowQualityAutoCover(game = {}) {
  const source = String(game?.imageSource || "").toLowerCase();
  const status = String(game?.imageStatus || "").toLowerCase();
  const cover = String(game?.coverImage || game?.coverUrl || "");

  if (status === "placeholder") return true;
  if (cover.includes("-owned.svg")) return true;
  if (source === "catalog-generated" || source === "catalog-fallback-local" || source === "placeholder") return true;
  if (cover.endsWith(".svg") && !source.includes("manual")) return true;
  return false;
}

function isUserManualCover(game = {}) {
  const source = String(game?.imageSource || "").toLowerCase();
  const status = String(game?.imageStatus || "").toLowerCase();
  const cover = String(game?.coverImage || game?.coverUrl || "");
  const generatedPlaceholderLike =
    isPlaceholderCover(cover) ||
    cover.includes("/generated/") ||
    cover.includes("cover-pending") ||
    cover.includes("catalog-placeholder");

  if (generatedPlaceholderLike) return false;
  return status === "manual" || source === "manual-upload" || source === "user-upload";
}

function resolveImageMetaField(source, base, field, fallback = "") {
  if (source && source[field] !== undefined && source[field] !== null && source[field] !== "") return source[field];
  if (base && base[field] !== undefined && base[field] !== null && base[field] !== "") return base[field];
  return fallback;
}

function getFilteredGames(games, view = "all") {
  const { q, status, priority, franchise, genre, ownership, sort } = appState.gameFilters;
  const query = normalizeFilterValue(q);

  let list = games.filter((game) => {
    const owned = gameIsOwned(game);
    const keepInWishlist = game.keepInWishlist === true;
    const isStandby = game.standby === true;

    if (view === "wishlist" && !belongsToWishlist(game)) return false;
    if (view === "registered" && !belongsToRegistered(game)) return false;

    const haystack = normalizeFilterValue(
      [game.nombre, game.franquicia, game.genero, game.prioridad, game.notas, game.condicion].join(" ")
    );
    const matchQ = !query || haystack.includes(query);

    const matchStatus =
      status === "all" ||
      (status === "standby" && isStandby) ||
      (status === "want" && game.loQuiero === true) ||
      (status === "have" && owned) ||
      (status === "to-buy" && (game.loQuiero === true || keepInWishlist) && !owned && !isStandby);

    const matchPriority = priority === "all" || normalizeFilterValue(game.prioridad) === priority;
    const matchFranchise = franchise === "all" || normalizeFilterValue(game.franquicia) === franchise;
    const matchGenre = genre === "all" || normalizeFilterValue(game.genero) === genre;
    const matchOwnership =
      ownership === "all" ||
      (ownership === "owned" && owned) ||
      (ownership === "none" && !owned) ||
      normalizeOwnershipType(game.ownershipType, game.loTengo) === ownership;

    return matchQ && matchStatus && matchPriority && matchFranchise && matchGenre && matchOwnership;
  });

  if (sort === "name-asc") {
    list = [...list].sort((a, b) => (a.nombre || "").localeCompare(b.nombre || ""));
  } else if (sort === "name-desc") {
    list = [...list].sort((a, b) => (b.nombre || "").localeCompare(a.nombre || ""));
  } else if (sort === "priority-desc") {
    list = [...list].sort((a, b) => {
      const byPriority = priorityRank(b.prioridad) - priorityRank(a.prioridad);
      if (byPriority !== 0) return byPriority;
      return (a.nombre || "").localeCompare(b.nombre || "");
    });
  } else if (sort === "price-low") {
    list = [...list].sort((a, b) => {
      const aLow = Number(a?.priceRange?.low);
      const bLow = Number(b?.priceRange?.low);
      const aVal = Number.isFinite(aLow) ? aLow : Number.POSITIVE_INFINITY;
      const bVal = Number.isFinite(bLow) ? bLow : Number.POSITIVE_INFINITY;
      if (aVal !== bVal) return aVal - bVal;
      return (a.nombre || "").localeCompare(b.nombre || "");
    });
  } else {
    list = [...list].sort((a, b) => (a.__index ?? 0) - (b.__index ?? 0));
  }

  return list;
}

function reconcileGamesCatalog(baseGames = [], currentGames = []) {
  if (!Array.isArray(baseGames) || !baseGames.length) return currentGames || [];

  const mapByName = new Map();
  const mapById = new Map();
  const consumedCurrentKeys = new Set();
  const consumedCurrentIds = new Set();
  (currentGames || []).forEach((game) => {
    mapByName.set(normalizeGameName(game.nombre), game);
    if (game?.id) mapById.set(String(game.id), game);
  });

  return baseGames.map((base) => {
    const key = normalizeGameName(base.nombre);
    let source = base?.id ? mapById.get(String(base.id)) : undefined;
    if (!source) source = mapByName.get(key);
    if (source) consumedCurrentKeys.add(key);
    if (source?.id) consumedCurrentIds.add(String(source.id));

    if (!source) {
      const mergedCover = resolveCoverField(undefined, base);
      const finalCover = mergedCover || fallbackGameImage;
      const ownershipType = normalizeOwnershipType(base.ownershipType, base.loTengo);
      const owned = ownershipType !== "none";
      return {
        ...base,
        sourceType: base.sourceType || "catalog",
        ownershipType,
        loTengo: owned,
        keepInWishlist: base.keepInWishlist !== undefined ? Boolean(base.keepInWishlist) : !owned,
        standby: base.standby === true,
        prioridad: sanitizePriority(base.prioridad || "media"),
        coverImage: finalCover,
        coverUrl: finalCover || "",
        imageSource: resolveImageMetaField(undefined, base, "imageSource", mergedCover ? "existing" : "placeholder"),
        imageStatus: resolveImageMetaField(undefined, base, "imageStatus", mergedCover ? "found" : "placeholder"),
        imageSearchName: resolveImageMetaField(undefined, base, "imageSearchName", base?.nombre || ""),
        priceGuide: mergePriceGuide(base.priceGuide || {}),
        priceRange: mergePriceRange(base.priceRange || {})
      };
    }

    const sourceCover = source?.coverImage || source?.coverUrl || "";
    const sourceMarkedManual = isUserManualCover(source);
    const sourceAutoLowQuality = isLowQualityAutoCover(source);
    const sourceForCover =
      sourceMarkedManual || (!isPlaceholderCover(sourceCover) && !sourceAutoLowQuality) ? source : undefined;
    const mergedCover = resolveCoverField(sourceForCover, base);
    const finalCover = mergedCover || fallbackGameImage;
    const ownershipType = normalizeOwnershipType(source.ownershipType ?? base.ownershipType, source.loTengo ?? base.loTengo);
    const owned = ownershipType !== "none";
    const preserveSourceMeta = sourceMarkedManual || !isPlaceholderCover(source?.coverImage || source?.coverUrl);
    return {
      ...base,
      sourceType: source.sourceType || base.sourceType || "catalog",
      loQuiero: source.loQuiero ?? base.loQuiero,
      loTengo: owned,
      ownershipType,
      keepInWishlist:
        source.keepInWishlist !== undefined
          ? Boolean(source.keepInWishlist)
          : base.keepInWishlist !== undefined
            ? Boolean(base.keepInWishlist)
            : !owned,
      standby: source.standby !== undefined ? source.standby === true : base.standby === true,
      prioridad: sanitizePriority(source.prioridad ?? base.prioridad ?? "media"),
      status: source.status ?? base.status,
      condicion: source.condicion ?? base.condicion,
      notas: source.notas ?? base.notas,
      coverImage: finalCover,
      coverUrl: finalCover || "",
      imageSource: preserveSourceMeta
        ? source.imageSource || "manual-upload"
        : resolveImageMetaField(source, base, "imageSource", mergedCover ? "existing" : "placeholder"),
      imageStatus: preserveSourceMeta
        ? source.imageStatus || "manual"
        : resolveImageMetaField(source, base, "imageStatus", mergedCover ? "found" : "placeholder"),
      imageSearchName: resolveImageMetaField(source, base, "imageSearchName", source?.nombre || base?.nombre || ""),
      variants: mergeVariants(base.variants, source.variants),
      editions: mergeOptionalList(base.editions, source.editions),
      priceGuide: mergePriceGuide(base.priceGuide || {}, source),
      priceRange: mergePriceRange(base.priceRange || {}, source)
    };
  }).concat(
    (currentGames || [])
      .filter((game) => {
        const key = normalizeGameName(game.nombre || "");
        if (!key) return false;
        const id = String(game.id || "");
        const consumedById = id ? consumedCurrentIds.has(id) : false;
        return !consumedCurrentKeys.has(key) && !consumedById;
      })
      .map((game) => ({
        ...game,
        sourceType: game.sourceType || "manual",
        coverImage: game.coverImage || game.coverUrl || fallbackGameImage,
        coverUrl: game.coverUrl || game.coverImage || fallbackGameImage,
        imageSource:
          game.imageSource ||
          (isPlaceholderCover(game.coverImage || game.coverUrl || "") ? "placeholder" : "manual-upload"),
        imageStatus:
          game.imageStatus || (isPlaceholderCover(game.coverImage || game.coverUrl || "") ? "placeholder" : "manual"),
        imageSearchName: game.imageSearchName || game.nombre || ""
      }))
  );
}

function composeGamesForConsole(baseGames = [], legacyCurrent = []) {
  const entityState = window.CollectionRepository.getConsoleEntityState(appState.id);
  if (window.CollectionRepository.hasGameEntityState(entityState)) {
    return window.CollectionRepository.composeGamesFromEntity(baseGames, entityState);
  }
  return reconcileGamesCatalog(baseGames, legacyCurrent);
}

function renderGbaGamesSection(item) {
  const games = Array.isArray(item.juegosCatalogo) ? item.juegosCatalogo : [];
  const normalizedGames = games.map((game, index) => normalizeGameState(game, index));
  const normalizedMainGames = normalizedGames.filter((game) => !isNonGameEntry(game));
  const normalizedExtras = normalizedGames.filter((game) => isNonGameEntry(game));
  const physicalOnlyConsole = isPhysicalOnlyGameConsole(appState.id);
  const filteredWishlist = appState.gameFilters.list === "registered" ? [] : getFilteredGames(normalizedGames, "wishlist");
  const filteredRegistered = appState.gameFilters.list === "wishlist" ? [] : getFilteredGames(normalizedGames, "registered");
  const ownedCount = normalizedMainGames.filter((game) => gameIsOwned(game)).length;
  const physicalCount = normalizedMainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "physical").length;
  const digitalCount = normalizedMainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "digital").length;
  const bothCount = normalizedMainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "both").length;
  const extrasOwnedCount = normalizedExtras.filter((game) => gameIsOwned(game)).length;

  const franchises = [...new Set(normalizedGames.map((g) => g.franquicia).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b))
    .map(
      (name) =>
        `<option value="${normalizeFilterValue(name)}" ${
          appState.gameFilters.franchise === normalizeFilterValue(name) ? "selected" : ""
        }>${escapeHtml(name)}</option>`
    )
    .join("");

  const genres = [...new Set(normalizedGames.map((g) => g.genero).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b))
    .map(
      (name) =>
        `<option value="${normalizeFilterValue(name)}" ${
          appState.gameFilters.genre === normalizeFilterValue(name) ? "selected" : ""
        }>${escapeHtml(name)}</option>`
    )
    .join("");

  const renderCards = (list, sectionName) =>
    list
      .map((game) => {
        const originalIdx = game.__index;
        const ownershipType = normalizeOwnershipType(game.ownershipType, game.loTengo);
        const ownershipLabel = getOwnershipLabel(ownershipType);
        const isOwned = gameIsOwned(game);
        const keepInWishlist = game.keepInWishlist !== false;
        const isStandby = game.standby === true;
        const wantActive = game.loQuiero === true && !isStandby;
        const showKeepBadge = isOwned && keepInWishlist;
        const showOwnedOutBadge = isOwned && !keepInWishlist;
        const priorityValue = sanitizePriority(game.prioridad || "media");
        const priorityClass = `priority-${priorityValue}`;
        const ownedClass = isOwned ? "is-owned" : "";
        const pokemonClass = (game.franquicia || "").toLowerCase().includes("pokemon") ? "is-pokemon" : "";
        const status = (game.status || "").toLowerCase();
        const priceGuide = game.priceGuide || game.precio || {};
        const priceRange = game.priceRange || {};
        const lowValue = priceRange.low ?? priceGuide.targetBuy ?? null;
        const midValue = priceRange.mid ?? priceGuide.priceCharting ?? null;
        const highValue = priceRange.high ?? priceGuide.cib ?? null;
        const rangeNotes = priceRange.notes || "";
        const cover =
          game.coverImage ||
          game.coverUrl ||
          fallbackGameImage ||
          gameImagePlaceholder(game, appState.item?.nombre || "") ||
          fallbackGameImage;
        const variants = Array.isArray(game.variants) ? game.variants : [];
        const editions = Array.isArray(game.editions) ? game.editions : [];
        const optionItems = [...variants, ...editions];
        const variantsBlock = optionItems.length
          ? `
            <div class="gba-variants">
              <p class="gba-variants-title">Hacks / variantes / ediciones</p>
              <div class="gba-variants-list">
                ${optionItems
                  .map(
                    (variant) => `
                  <span class="gba-variant-pill">
                    ${escapeHtml(variant.nombre || "Variante")}
                    ${variant.region ? ` • ${escapeHtml(variant.region)}` : ""}
                    ${variant.loTengo ? " • Tengo" : variant.loQuiero ? " • Quiero" : ""}
                  </span>
                `
                  )
                  .join("")}
              </div>
            </div>
          `
          : "";

        const ownershipControl = physicalOnlyConsole
          ? `
            <label class="mini-field">
              Tenencia
              <select class="js-game-ownership" data-index="${originalIdx}">
                <option value="none" ${ownershipType === "none" ? "selected" : ""}>No lo tengo</option>
                <option value="physical" ${ownershipType !== "none" ? "selected" : ""}>Fisico</option>
              </select>
            </label>
          `
          : `
            <label class="mini-field">
              Formato
              <select class="js-game-ownership" data-index="${originalIdx}">
                <option value="none" ${ownershipType === "none" ? "selected" : ""}>No lo tengo</option>
                <option value="physical" ${ownershipType === "physical" ? "selected" : ""}>Fisico</option>
                <option value="digital" ${ownershipType === "digital" ? "selected" : ""}>Digital</option>
                <option value="both" ${ownershipType === "both" ? "selected" : ""}>Ambos</option>
              </select>
            </label>
          `;

        return `
          <article class="gba-game-card ${ownedClass} ${pokemonClass}">
            <div class="gba-game-layout">
            <div class="gba-game-cover">
                <img src="${cover}" alt="Portada de ${escapeHtml(game.nombre || "Juego")}" onerror="this.onerror=null;this.src='${fallbackGameImage}'" />
            </div>
              <div class="gba-game-main">
                <div class="gba-game-head">
                  <div>
                    <h3 class="gba-game-title">${escapeHtml(game.nombre || "Juego")}</h3>
                    <p class="gba-game-meta">${escapeHtml(game.franquicia || "Franquicia N/D")} • ${escapeHtml(
          game.genero || "Genero N/D"
        )}</p>
                  </div>
                  <div class="gba-badges">
                    <span class="gba-badge ${priorityClass}">Prioridad: ${escapeHtml(priorityValue)}</span>
                    ${(game.franquicia || "").toLowerCase().includes("pokemon") ? '<span class="gba-badge pokemon">Pokemon</span>' : ""}
                    ${status ? `<span class="gba-badge status-${escapeHtml(status)}">Status: ${escapeHtml(status)}</span>` : ""}
                    ${wantActive ? '<span class="gba-badge want">Lo quiero</span>' : ""}
                    ${isStandby ? '<span class="gba-badge standby">Standby</span>' : ""}
                    ${isOwned && ownershipLabel ? `<span class="gba-badge ${getOwnershipBadgeClass(ownershipType)}">${escapeHtml(ownershipLabel)}</span>` : ""}
                    ${game.region ? `<span class="gba-badge">${escapeHtml(game.region)}</span>` : ""}
                    ${showKeepBadge ? '<span class="gba-badge keep-wishlist">En deseados</span>' : ""}
                    ${showOwnedOutBadge ? '<span class="gba-badge keep-off">Solo registrado</span>' : ""}
                    ${sectionName === "registered" ? '<span class="gba-badge registered">Registrado</span>' : ""}
                    ${game.sourceType === "manual" ? '<span class="gba-badge manual">Manual</span>' : ""}
                  </div>
                </div>

                <div class="gba-game-controls">
                  <button class="btn-link js-gba-toggle-want ${wantActive ? "active" : ""}" data-index="${originalIdx}" type="button">Quiero</button>
                  <button class="btn-link js-game-standby ${isStandby ? "active standby" : ""}" data-index="${originalIdx}" type="button">Standby</button>

                  ${ownershipControl}

                  <label class="mini-field">
                    Prioridad
                    <select class="js-game-priority" data-index="${originalIdx}">
                      <option value="alta" ${priorityValue === "alta" ? "selected" : ""}>Alta</option>
                      <option value="media-alta" ${priorityValue === "media-alta" ? "selected" : ""}>Media/Alta</option>
                      <option value="media" ${priorityValue === "media" ? "selected" : ""}>Media</option>
                      <option value="baja" ${priorityValue === "baja" ? "selected" : ""}>Baja</option>
                    </select>
                  </label>
                </div>

                <label class="keep-toggle">
                  <input class="js-game-keep-wishlist" data-index="${originalIdx}" type="checkbox" ${keepInWishlist ? "checked" : ""} />
                  Mantener en deseados aunque ya lo tenga
                </label>

                <div class="gba-game-price-grid">
                  <article class="gba-price-item low"><small>Low</small><strong>${formatPrice(lowValue)}</strong></article>
                  <article class="gba-price-item mid"><small>Mid</small><strong>${formatPrice(midValue)}</strong></article>
                  <article class="gba-price-item high"><small>High</small><strong>${formatPrice(highValue)}</strong></article>
                </div>

                ${rangeNotes || priceGuide.notes ? `<p class="gba-game-note">${escapeHtml(rangeNotes || priceGuide.notes)}</p>` : ""}
                ${variantsBlock}

                <details class="game-edit-panel">
                  <summary>Editar estado y notas</summary>
                  <div class="gba-game-fields">
                    <label>
                      Condicion
                      <input class="js-gba-condition" data-index="${originalIdx}" type="text" placeholder="Ej: caja original, suelto, etc." value="${escapeHtml(
          game.condicion || ""
        )}" />
                    </label>
                    <label>
                      Region
                      <input class="js-game-region" data-index="${originalIdx}" type="text" placeholder="Ej: NTSC, PAL" value="${escapeHtml(
          game.region || ""
        )}" />
                    </label>
                    <label>
                      Notas personales
                      <textarea class="js-gba-notes" data-index="${originalIdx}" rows="2" placeholder="Detalle corto">${escapeHtml(
          game.notas || ""
        )}</textarea>
                    </label>
                    <label>
                      Cambiar portada manual
                      <input class="js-game-cover-file" data-index="${originalIdx}" type="file" accept="image/*" />
                    </label>
                  </div>
                </details>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

  const filteredWishlistMain = filteredWishlist.filter((game) => !isNonGameEntry(game));
  const filteredWishlistExtras = filteredWishlist.filter((game) => isNonGameEntry(game));
  const filteredRegisteredMain = filteredRegistered.filter((game) => !isNonGameEntry(game));
  const filteredRegisteredExtras = filteredRegistered.filter((game) => isNonGameEntry(game));

  const cardsWishlistMain = renderCards(filteredWishlistMain, "wishlist");
  const cardsWishlistExtras = renderCards(filteredWishlistExtras, "wishlist");
  const cardsRegisteredMain = renderCards(filteredRegisteredMain, "registered");
  const cardsRegisteredExtras = renderCards(filteredRegisteredExtras, "registered");
  const totalVisible =
    filteredWishlistMain.length +
    filteredWishlistExtras.length +
    filteredRegisteredMain.length +
    filteredRegisteredExtras.length;

  const manualGameFormBlock = `
    <div class="manual-game-launcher">
      <button id="openManualGameModalBtn" class="btn-link" type="button">Agregar juego manual (no listado)</button>
    </div>

    <dialog id="manualGameDialog" class="manual-game-dialog">
      <form method="dialog" class="manual-game-dialog-shell">
        <header class="manual-game-dialog-head">
          <div>
            <h3>Agregar juego manual</h3>
            <p>Subí una imagen y/o escribí nombre. Intentamos autocompletar y luego ajustás lo mínimo.</p>
          </div>
          <button id="closeManualGameModalBtn" class="btn-link" type="button">Cerrar</button>
        </header>

        <section class="manual-step-grid">
          <label class="manual-step-field">
            Nombre del juego
            <input id="manualGameName" type="text" placeholder="Ej: Strawberry Shortcake" />
          </label>
          <label class="manual-step-field">
            Portada / foto
            <input id="manualGameCoverFile" type="file" accept="image/*" />
          </label>
          <p id="manualDetectHint" class="manual-detect-hint">Identificación automática: pendiente.</p>
          <p id="manualGameError" class="manual-inline-error" role="alert" hidden></p>
        </section>

        <section class="manual-preview-panel">
          <article class="gba-game-card manual-preview-card">
            <div class="gba-game-layout">
              <div class="gba-game-cover">
                <img id="manualGameCoverPreview" src="${fallbackGameImage}" alt="Preview portada manual" />
              </div>
              <div class="gba-game-main">
                <div class="gba-game-head">
                  <div>
                    <h3 id="manualPreviewTitle" class="gba-game-title">Juego manual</h3>
                    <p id="manualPreviewMeta" class="gba-game-meta">Franquicia N/D • Genero N/D</p>
                  </div>
                  <div class="gba-badges">
                    <span id="manualPreviewPriority" class="gba-badge priority-media">Prioridad: media</span>
                    <span id="manualPreviewOwnership" class="gba-badge ownership-none">No lo tengo</span>
                    <span id="manualPreviewWish" class="gba-badge want">Lo quiero</span>
                    <span class="gba-badge manual">Manual</span>
                  </div>
                </div>
              </div>
            </div>
          </article>
        </section>

        <section class="manual-quick-fields">
          <label>
            Tenencia
            <select id="manualGameOwnership">
              <option value="none">No lo tengo</option>
              <option value="physical">Fisico</option>
              ${physicalOnlyConsole ? "" : '<option value="digital">Digital</option><option value="both">Ambos</option>'}
            </select>
          </label>
          <label>
            Estado
            <select id="manualGameState">
              <option value="want" selected>Quiero</option>
              <option value="none">No quiero</option>
              <option value="standby">Standby</option>
            </select>
          </label>
          <label>
            Prioridad
            <select id="manualGamePriority">
              <option value="alta">Alta</option>
              <option value="media-alta">Media/Alta</option>
              <option value="media" selected>Media</option>
              <option value="baja">Baja</option>
            </select>
          </label>
          <label class="manual-check">
            <input id="manualGameKeepWishlist" type="checkbox" />
            Mantener en deseados
          </label>
        </section>

        <details class="manual-advanced">
          <summary>Campos avanzados</summary>
          <div class="manual-form-grid">
            <label>
              Genero
              <input id="manualGameGenre" type="text" placeholder="Opcional" />
            </label>
            <label>
              Franquicia
              <input id="manualGameFranchise" type="text" placeholder="Opcional" />
            </label>
            <label>
              Condicion
              <input id="manualGameCondition" type="text" placeholder="Opcional" />
            </label>
            <label>
              Region
              <input id="manualGameRegion" type="text" placeholder="Ej: NTSC, PAL" />
            </label>
            <label class="manual-notes">
              Notas
              <textarea id="manualGameNotes" rows="2" placeholder="Opcional"></textarea>
            </label>
          </div>
          <div class="manual-price-grid">
            <label>Low<input id="manualGamePriceLow" type="number" min="0" step="1" placeholder="USD" /></label>
            <label>Mid<input id="manualGamePriceMid" type="number" min="0" step="1" placeholder="USD" /></label>
            <label>High<input id="manualGamePriceHigh" type="number" min="0" step="1" placeholder="USD" /></label>
            <label class="manual-price-notes">Notas precio<textarea id="manualGamePriceNotes" rows="2" placeholder="Opcional"></textarea></label>
          </div>
        </details>

        <footer class="manual-dialog-actions">
          <button id="addManualGameBtn" class="btn-link btn-primary" type="button" disabled>Guardar juego manual</button>
        </footer>
      </form>
    </dialog>
  `;

  return `
    <article class="detail-block detail-block--games">
      <div class="section-head">
        <h2>Gestion de juegos</h2>
        <p class="muted">Deseados y registrados con el mismo formato visual, filtros y acciones rápidas.</p>
      </div>
      <div class="games-filter-shell">
        <div class="games-filterbar">
          <label class="filter-search">
            Buscar
            <input id="gameSearchInput" type="search" placeholder="Nombre, franquicia o notas..." value="${escapeHtml(
              appState.gameFilters.q
            )}" />
          </label>
          <label>
            Vista
            <select id="gameListFilter">
              <option value="all" ${appState.gameFilters.list === "all" ? "selected" : ""}>Deseados + Registrados</option>
              <option value="wishlist" ${appState.gameFilters.list === "wishlist" ? "selected" : ""}>Solo deseados</option>
              <option value="registered" ${appState.gameFilters.list === "registered" ? "selected" : ""}>Solo registrados</option>
            </select>
          </label>
          <label>
            Estado
            <select id="gameStatusFilter">
              <option value="all" ${appState.gameFilters.status === "all" ? "selected" : ""}>Todos</option>
              <option value="to-buy" ${appState.gameFilters.status === "to-buy" ? "selected" : ""}>Quiero comprar</option>
              <option value="want" ${appState.gameFilters.status === "want" ? "selected" : ""}>Lo quiero</option>
              <option value="standby" ${appState.gameFilters.status === "standby" ? "selected" : ""}>Standby</option>
              <option value="have" ${appState.gameFilters.status === "have" ? "selected" : ""}>Lo tengo</option>
            </select>
          </label>
          <label>
            Prioridad
            <select id="gamePriorityFilter">
              <option value="all" ${appState.gameFilters.priority === "all" ? "selected" : ""}>Todas</option>
              <option value="alta" ${appState.gameFilters.priority === "alta" ? "selected" : ""}>Alta</option>
              <option value="media-alta" ${appState.gameFilters.priority === "media-alta" ? "selected" : ""}>Media/Alta</option>
              <option value="media" ${appState.gameFilters.priority === "media" ? "selected" : ""}>Media</option>
              <option value="baja" ${appState.gameFilters.priority === "baja" ? "selected" : ""}>Baja</option>
            </select>
          </label>
          <label>
            Franquicia
            <select id="gameFranchiseFilter">
              <option value="all" ${appState.gameFilters.franchise === "all" ? "selected" : ""}>Todas</option>
              ${franchises}
            </select>
          </label>
          <label>
            Genero
            <select id="gameGenreFilter">
              <option value="all" ${appState.gameFilters.genre === "all" ? "selected" : ""}>Todos</option>
              ${genres}
            </select>
          </label>
          <label>
            Posesion
            <select id="gameOwnershipFilter">
              <option value="all" ${appState.gameFilters.ownership === "all" ? "selected" : ""}>Cualquiera</option>
              <option value="none" ${appState.gameFilters.ownership === "none" ? "selected" : ""}>No lo tengo</option>
              <option value="owned" ${appState.gameFilters.ownership === "owned" ? "selected" : ""}>Tengo alguno</option>
              <option value="physical" ${appState.gameFilters.ownership === "physical" ? "selected" : ""}>Fisico</option>
              <option value="digital" ${appState.gameFilters.ownership === "digital" ? "selected" : ""}>Digital</option>
              <option value="both" ${appState.gameFilters.ownership === "both" ? "selected" : ""}>Ambos</option>
            </select>
          </label>
          <label>
            Orden
            <select id="gameSortFilter">
              <option value="default" ${appState.gameFilters.sort === "default" ? "selected" : ""}>Orden base</option>
              <option value="priority-desc" ${appState.gameFilters.sort === "priority-desc" ? "selected" : ""}>Prioridad alta primero</option>
              <option value="name-asc" ${appState.gameFilters.sort === "name-asc" ? "selected" : ""}>Nombre A-Z</option>
              <option value="name-desc" ${appState.gameFilters.sort === "name-desc" ? "selected" : ""}>Nombre Z-A</option>
              <option value="price-low" ${appState.gameFilters.sort === "price-low" ? "selected" : ""}>Low mas barato</option>
            </select>
          </label>
        </div>
      </div>

      <p class="games-count">${totalVisible} item(s) visibles de ${normalizedGames.length} (${normalizedMainGames.length} juegos + ${normalizedExtras.length} extras)</p>
      <div class="games-stats-strip">
        <article class="games-stat-item">
          <small>Juegos tengo</small>
          <strong>${ownedCount}</strong>
        </article>
        <article class="games-stat-item">
          <small>Fisicos</small>
          <strong>${physicalCount}</strong>
        </article>
        <article class="games-stat-item">
          <small>Digitales</small>
          <strong>${digitalCount}</strong>
        </article>
        <article class="games-stat-item">
          <small>Ambos</small>
          <strong>${bothCount}</strong>
        </article>
        <article class="games-stat-item">
          <small>Extras tengo</small>
          <strong>${extrasOwnedCount}</strong>
        </article>
      </div>
      ${manualGameFormBlock}

      ${
        appState.gameFilters.list !== "wishlist"
          ? `
        <details class="games-subsection games-subsection-collapsible" open>
          <summary class="games-subtitle-row">
            <h3 class="games-subtitle">Registrados / ya tengo</h3>
            <span class="games-subtitle-count">${filteredRegisteredMain.length}</span>
          </summary>
          ${
            filteredRegisteredMain.length
              ? `<div class="gba-games-grid">${cardsRegisteredMain}</div>`
              : `<p class="muted">Todavia no hay juegos registrados con estos filtros.</p>`
          }
        </details>
        <details class="games-subsection games-subsection-collapsible games-subsection-extras">
          <summary class="games-subtitle-row">
            <h3 class="games-subtitle">Registrados • demos / extras / apps</h3>
            <span class="games-subtitle-count">${filteredRegisteredExtras.length}</span>
          </summary>
          ${
            filteredRegisteredExtras.length
              ? `<div class="gba-games-grid">${cardsRegisteredExtras}</div>`
              : `<p class="muted">No hay demos/extras registrados con estos filtros.</p>`
          }
        </details>
      `
          : ""
      }

      ${
        appState.gameFilters.list !== "registered"
          ? `
        <details class="games-subsection games-subsection-collapsible" open>
          <summary class="games-subtitle-row">
            <h3 class="games-subtitle">Deseados / recomendados</h3>
            <span class="games-subtitle-count">${filteredWishlistMain.length}</span>
          </summary>
          ${
            filteredWishlistMain.length
              ? `<div class="gba-games-grid">${cardsWishlistMain}</div>`
              : `<p class="muted">No hay juegos visibles en deseados con estos filtros.</p>`
          }
        </details>
        <details class="games-subsection games-subsection-collapsible games-subsection-extras">
          <summary class="games-subtitle-row">
            <h3 class="games-subtitle">Deseados • demos / extras / apps</h3>
            <span class="games-subtitle-count">${filteredWishlistExtras.length}</span>
          </summary>
          ${
            filteredWishlistExtras.length
              ? `<div class="gba-games-grid">${cardsWishlistExtras}</div>`
              : `<p class="muted">No hay demos/extras en deseados con estos filtros.</p>`
          }
        </details>
      `
          : ""
      }
    </article>
  `;
}

function getConsoleGameMetrics(item) {
  const games = Array.isArray(item.juegosCatalogo) ? item.juegosCatalogo : [];
  const normalizedGames = games.map((game, index) => normalizeGameState(game, index));
  const mainGames = normalizedGames.filter((game) => !isNonGameEntry(game));
  const registered = mainGames.filter((game) => belongsToRegistered(game)).length;
  const wanted = mainGames.filter((game) => belongsToWishlist(game)).length;
  const physical = mainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "physical").length;
  const digital = mainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "digital").length;
  const both = mainGames.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "both").length;
  const progress = mainGames.length ? Math.round((registered / mainGames.length) * 100) : 0;

  return {
    total: mainGames.length,
    registered,
    wanted,
    physical,
    digital,
    both,
    progress,
    extras: normalizedGames.length - mainGames.length
  };
}

function getConsoleStatusMeta(item) {
  if (item.funcionando === true) return { label: "Lista para jugar", tone: "ready" };
  if (item.funcionando === false) return { label: "Requiere revision", tone: "critical" };
  return { label: "Estado pendiente", tone: "unknown" };
}

function renderDetailSubnav(item) {
  return `
    <div class="detail-subnav">
      <a class="btn-link" href="${buildConsoleHref("detail", item.id)}">Resumen</a>
      <a class="btn-link" href="${buildConsoleHref("games", item.id)}">Biblioteca</a>
      <a class="btn-link" href="${buildConsoleHref("accessories", item.id)}">Accesorios</a>
    </div>
  `;
}

function renderAccessoryPreview(item) {
  const list = getAccessoryCatalogList(item);
  const metrics = getAccessoryMetrics(item);
  const featured = list.filter((entry) => accessoryIsOwned(entry)).slice(0, 4);
  return `
    <article class="detail-block">
      <div class="section-head">
        <h2>Accesorios</h2>
        <p class="muted">Inventario controlado por catálogo y estado real.</p>
      </div>
      <div class="preview-metrics">
        <article><small>Tengo</small><strong>${metrics.owned}</strong></article>
        <article><small>Esenciales faltan</small><strong>${metrics.essentialMissing}</strong></article>
        <article><small>Listos</small><strong>${metrics.ready}</strong></article>
      </div>
      ${
        featured.length
          ? `
        <div class="preview-pill-list">
          ${featured
            .map(
              (entry) =>
                `<span class="preview-pill">${escapeHtml(entry.nombre)}${entry.cantidad > 1 ? ` • ${entry.cantidad}` : ""}</span>`
            )
            .join("")}
        </div>
      `
          : `<p class="muted">Todavía no marcaste accesorios registrados en esta consola.</p>`
      }
      <div class="card-actions">
        <a class="btn-link btn-primary" href="${buildConsoleHref("accessories", item.id)}">Gestionar accesorios</a>
      </div>
    </article>
  `;
}

function renderGamesPreview(item) {
  const metrics = getConsoleGameMetrics(item);
  return `
    <article class="detail-block">
      <div class="section-head">
        <h2>Biblioteca</h2>
        <p class="muted">Juegos registrados y deseados en una vista dedicada.</p>
      </div>
      <div class="preview-metrics">
        <article><small>Registrados</small><strong>${metrics.registered}</strong></article>
        <article><small>Deseados</small><strong>${metrics.wanted}</strong></article>
        <article><small>Progreso</small><strong>${metrics.progress}%</strong></article>
      </div>
      <div class="info-pills">
        <span class="info-pill">Total catálogo: ${metrics.total}</span>
        <span class="info-pill">Físico: ${metrics.physical}</span>
        <span class="info-pill">Digital: ${metrics.digital}</span>
        <span class="info-pill">Ambos: ${metrics.both}</span>
      </div>
      <div class="card-actions">
        <a class="btn-link btn-primary" href="${buildConsoleHref("games", item.id)}">Abrir biblioteca</a>
      </div>
    </article>
  `;
}

function renderAccessoriesPage(item) {
  const list = getAccessoryCatalogList(item);
  const metrics = getAccessoryMetrics(item);
  const query = normalizeFilterValue(appState.accessoryFilters.q);
  const subtitle = "Catálogo base corto y confiable, más variantes manuales tuyas cuando haga falta.";
  const filtered = list.filter((entry) => {
    const haystack = normalizeFilterValue([entry.nombre, entry.tipo, entry.estado, entry.notas].join(" "));
    const owned = accessoryIsOwned(entry);
    const matchesQuery = !query || haystack.includes(query);
    const matchesView =
      appState.accessoryFilters.view === "all" ||
      (appState.accessoryFilters.view === "owned" && owned) ||
      (appState.accessoryFilters.view === "missing" && !owned) ||
      (appState.accessoryFilters.view === "essential" && entry.esencial === true);
    const matchesType = appState.accessoryFilters.type === "all" || entry.tipo === appState.accessoryFilters.type;
    return matchesQuery && matchesView && matchesType;
  });
  const types = [...new Set(list.map((entry) => entry.tipo).filter(Boolean))].sort();
  const manualAccessoryBlock = `
    <div class="manual-game-launcher manual-accessory-launcher">
      <button id="openManualAccessoryModalBtn" class="btn-link" type="button">Agregar accesorio manual</button>
      <p class="muted">Para variantes propias o piezas no listadas, sin romper el catálogo base.</p>
    </div>

    <dialog id="manualAccessoryDialog" class="manual-game-dialog">
      <form method="dialog" class="manual-game-dialog-shell">
        <header class="manual-game-dialog-head">
          <div>
            <h3>Agregar accesorio manual</h3>
            <p>Ej: DualShock 4 Star Wars, edición azul, volante oficial o una variante puntual de tu colección.</p>
          </div>
          <button id="closeManualAccessoryModalBtn" class="btn-link" type="button">Cerrar</button>
        </header>

        <section class="manual-step-grid">
          <label class="manual-step-field">
            Nombre del accesorio
            <input id="manualAccessoryName" type="text" placeholder="Ej: DualShock 4 Star Wars" />
          </label>
          <label class="manual-step-field">
            Foto / imagen
            <input id="manualAccessoryImageFile" type="file" accept="image/*" />
          </label>
          <p class="manual-detect-hint">Se guarda como item manual del usuario y convive con el catálogo base.</p>
          <p id="manualAccessoryError" class="manual-inline-error" role="alert" hidden></p>
        </section>

        <section class="manual-preview-panel">
          <article class="accessory-card is-owned manual-accessory-preview">
            <div class="accessory-card-media">
              <img id="manualAccessoryPreviewImage" src="${accessoryPlaceholderImage({ nombre: "Accesorio manual", tipo: "otro" })}" alt="Preview accesorio manual" />
            </div>
            <div class="accessory-card-head">
              <div>
                <h3 id="manualAccessoryPreviewTitle">Accesorio manual</h3>
                <p id="manualAccessoryPreviewMeta">Otro</p>
              </div>
              <span id="manualAccessoryPreviewStatus" class="status-pill ready">Tengo</span>
            </div>
          </article>
        </section>

        <section class="manual-quick-fields">
          <label>
            Tipo
            <select id="manualAccessoryType">
              <option value="control">Control</option>
              <option value="audio">Audio</option>
              <option value="sensor">Sensor</option>
              <option value="vr">VR</option>
              <option value="soporte">Soporte</option>
              <option value="carga">Carga</option>
              <option value="otro" selected>Otro</option>
            </select>
          </label>
          <label>
            Origen
            <select id="manualAccessoryOriginal">
              <option value="original" selected>Original</option>
              <option value="third-party">Third-party</option>
              <option value="mixto">Mixto</option>
            </select>
          </label>
          <label>
            Cantidad
            <input id="manualAccessoryQty" type="number" min="0" step="1" value="1" />
          </label>
          <label class="manual-check">
            <input id="manualAccessoryOwned" type="checkbox" checked />
            Lo tengo
          </label>
        </section>

        <details class="manual-advanced">
          <summary>Campos avanzados</summary>
          <div class="manual-form-grid">
            <label>
              Estado
              <select id="manualAccessoryState">
                <option value="">Sin dato</option>
                <option value="excelente">Excelente</option>
                <option value="muy bueno">Muy bueno</option>
                <option value="bueno">Bueno</option>
                <option value="detalles">Con detalles</option>
                <option value="revisar">Revisar</option>
              </select>
            </label>
            <label>
              Funciona
              <select id="manualAccessoryFunctioning">
                <option value="">Sin probar</option>
                <option value="true">Sí</option>
                <option value="false">No</option>
              </select>
            </label>
            <label class="manual-notes">
              Notas
              <textarea id="manualAccessoryNotes" rows="2" placeholder="Color, edición, detalle de bundle, etc."></textarea>
            </label>
          </div>
        </details>

        <footer class="manual-dialog-actions">
          <button id="addManualAccessoryBtn" class="btn-link btn-primary" type="button" disabled>Guardar accesorio manual</button>
        </footer>
      </form>
    </dialog>
  `;

  return `
    <a href="${buildConsoleHref("detail", item.id)}" class="btn-link back-link">← Volver al resumen de consola</a>
    ${renderDetailSubnav(item)}
    <section class="detail-hero detail-shell">
      <div class="detail-main detail-card detail-main--full">
        <p class="eyebrow">Accesorios de consola</p>
        <h1>${escapeHtml(item.nombre)}</h1>
        <p class="subtitle">${escapeHtml(subtitle)}</p>
        <div class="detail-status-line">
          <span>${metrics.owned}/${metrics.total} items registrados</span>
          <span>${metrics.essentialMissing} esenciales pendientes</span>
          <span>${metrics.ready} marcados como funcionales</span>
        </div>
        <div class="detail-quick-metrics">
          <article><small>Tengo</small><strong>${metrics.owned}</strong></article>
          <article><small>Faltan</small><strong>${metrics.missing}</strong></article>
          <article><small>Esenciales</small><strong>${metrics.essentials}</strong></article>
          <article><small>Esenciales faltan</small><strong>${metrics.essentialMissing}</strong></article>
        </div>
      </div>
    </section>

    <section class="detail-toolbar">
      <span id="saveIndicator" class="save-indicator">${escapeHtml(appState.saveMessage || "Cambios guardados automáticamente")}</span>
    </section>

    <section class="detail-sections">
      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>Inventario de accesorios</h2>
          <p class="muted">Primero catálogo controlado, después estado real. Nada de texto suelto como fuente principal.</p>
        </div>
        ${manualAccessoryBlock}
        <div class="accessory-filterbar">
          <label>
            Buscar
            <input id="accessorySearchInput" type="search" placeholder="Nombre, tipo o nota..." value="${escapeHtml(appState.accessoryFilters.q)}" />
          </label>
          <label>
            Vista
            <select id="accessoryViewFilter">
              <option value="all" ${appState.accessoryFilters.view === "all" ? "selected" : ""}>Todos</option>
              <option value="owned" ${appState.accessoryFilters.view === "owned" ? "selected" : ""}>Tengo</option>
              <option value="missing" ${appState.accessoryFilters.view === "missing" ? "selected" : ""}>Me faltan</option>
              <option value="essential" ${appState.accessoryFilters.view === "essential" ? "selected" : ""}>Solo esenciales</option>
            </select>
          </label>
          <label>
            Tipo
            <select id="accessoryTypeFilter">
              <option value="all" ${appState.accessoryFilters.type === "all" ? "selected" : ""}>Todos</option>
              ${types
                .map(
                  (type) =>
                    `<option value="${type}" ${appState.accessoryFilters.type === type ? "selected" : ""}>${escapeHtml(
                      getAccessoryTypeLabel(type)
                    )}</option>`
                )
                .join("")}
            </select>
          </label>
          <label>
            Formato
            <select id="accessoryLayoutFilter">
              <option value="compact" ${appState.accessoryFilters.layout === "compact" ? "selected" : ""}>Lista compacta</option>
              <option value="visual" ${appState.accessoryFilters.layout === "visual" ? "selected" : ""}>Vista visual</option>
            </select>
          </label>
        </div>
        <div class="accessory-grid accessory-grid--${escapeHtml(appState.accessoryFilters.layout || "compact")}">
          ${
            filtered.length
              ? filtered
                  .map((entry) => {
                    const owned = accessoryIsOwned(entry);
                    const image = entry.image || accessoryPlaceholderImage(entry);
                    return `
                      <article class="accessory-card ${owned ? "is-owned" : "is-missing"} ${entry.esencial ? "is-essential" : ""}">
                        <div class="accessory-card-media">
                          <img src="${image}" alt="Imagen de ${escapeHtml(entry.nombre)}" loading="lazy" onerror="this.onerror=null;this.src='${accessoryPlaceholderImage(
                            entry
                          )}'" />
                        </div>
                        <div class="accessory-card-head">
                          <div>
                            <h3>${escapeHtml(entry.nombre)}</h3>
                            <p>${escapeHtml(getAccessoryTypeLabel(entry.tipo))}${entry.esencial ? " • esencial" : ""}</p>
                          </div>
                          <span class="status-pill ${owned ? "ready" : "unknown"}">${owned ? "Tengo" : "No tengo"}</span>
                        </div>
                        <div class="accessory-card-controls">
                          <label class="keep-toggle">
                            <input class="js-accessory-owned" data-index="${entry.__index}" type="checkbox" ${owned ? "checked" : ""} />
                            Registrado
                          </label>
                          <label>
                            Cantidad
                            <input class="js-accessory-qty" data-index="${entry.__index}" type="number" min="0" step="1" value="${entry.cantidad}" />
                          </label>
                          <label>
                            Funciona
                            <select class="js-accessory-functioning" data-index="${entry.__index}">
                              ${buildAccessoryFunctioningOptions(entry.funcionando)}
                            </select>
                          </label>
                          <label>
                            Origen
                            <select class="js-accessory-original" data-index="${entry.__index}">
                              ${buildAccessoryOriginalOptions(entry.original)}
                            </select>
                          </label>
                          <label>
                            Estado
                            <select class="js-accessory-state" data-index="${entry.__index}">
                              ${buildAccessoryStateOptions(entry.estado)}
                            </select>
                          </label>
                        </div>
                        <label>
                          Notas
                          <textarea class="js-accessory-notes" data-index="${entry.__index}" rows="2" placeholder="Detalle corto">${escapeHtml(
                            entry.notas || ""
                          )}</textarea>
                        </label>
                      </article>
                    `;
                  })
                  .join("")
              : `<p class="muted">No hay accesorios visibles con estos filtros.</p>`
          }
        </div>
      </article>

      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>Notas generales</h2>
          <p class="muted">Queda como contexto global de la consola, no como fuente principal del inventario.</p>
        </div>
        <label>
          Nota sobre accesorios de esta consola
          <textarea id="accesoriosNotaInput" rows="4" placeholder="Ej: un control necesita goma nueva o revisar batería...">${escapeHtml(
            appState.draft?.accesoriosCatalogoNotas || ""
          )}</textarea>
        </label>
      </article>
    </section>
  `;
}

function renderGamesPage(item) {
  const gameMetrics = getConsoleGameMetrics(item);
  return `
    <a href="${buildConsoleHref("detail", item.id)}" class="btn-link back-link">← Volver al resumen de consola</a>
    ${renderDetailSubnav(item)}
    <section class="detail-hero detail-shell">
      <div class="detail-main detail-card detail-main--full">
        <p class="eyebrow">Biblioteca por consola</p>
        <h1>${escapeHtml(item.nombre)}</h1>
        <p class="subtitle">Gestión completa de juegos fuera del resumen principal de consola.</p>
        <div class="detail-status-line">
          <span>${gameMetrics.registered}/${gameMetrics.total} registrados</span>
          <span>${gameMetrics.wanted} deseados</span>
          <span>${gameMetrics.progress}% de cobertura</span>
        </div>
      </div>
    </section>
    <section class="detail-sections">
      ${renderGbaGamesSection(item)}
    </section>
  `;
}

function renderPage(item) {
  const mode = getPageMode();
  if (mode === "games") return renderGamesPage(item);
  if (mode === "accessories") return renderAccessoriesPage(item);
  return renderDetail(item);
}

function isRenderableConsoleImage(value = "") {
  const src = String(value || "").trim();
  if (!src || src.includes("/runtime/media/")) return false;
  return /^(https?:|data:|blob:|\/?\.?\/?(assets|media)\/)/i.test(src);
}

function getConsoleMainImage(item = {}) {
  const candidates = [
    ...(Array.isArray(item.fotos) ? item.fotos : []),
    ...(Array.isArray(item.fotosPropias) ? item.fotosPropias : [])
  ];
  return candidates.find(isRenderableConsoleImage) || fallbackImage;
}


function renderDetail(item) {
  const prices = getPriceModel(item);
  const draft = appState.draft || createDraftFromItem(item);
  const mainImage = getConsoleMainImage(item);
  const galleryImages = getConsoleGalleryImages(item);
  const mainGalleryIndex = galleryImages.indexOf(mainImage);
  const gameMetrics = getConsoleGameMetrics(item);
  const statusMeta = getConsoleStatusMeta(item);

  return `
    <a href="${buildHomeHref()}" class="btn-link back-link">← Volver al panel</a>
    ${renderDetailSubnav(item)}
    <section class="detail-hero detail-shell">
      <div class="detail-media detail-card">
        ${
          mainGalleryIndex >= 0
            ? `
          <button
            class="detail-media-zoom js-open-console-photo"
            data-gallery-index="${mainGalleryIndex}"
            type="button"
            aria-label="Abrir imagen de ${escapeHtml(item.nombre)} en grande"
          >
            <img src="${escapeHtml(mainImage)}" alt="Imagen de ${escapeHtml(item.nombre)}" onerror="this.onerror=null;this.src='${fallbackImage}'" />
            <span class="detail-media-zoom-label" aria-hidden="true">Ver en grande</span>
          </button>
        `
            : `<img src="${escapeHtml(mainImage)}" alt="Imagen de ${escapeHtml(item.nombre)}" onerror="this.onerror=null;this.src='${fallbackImage}'" />`
        }
      </div>
      <div class="detail-main detail-card">
        <p class="eyebrow">${item.categoria === "coleccion" ? "Mi colección" : "Wishlist"}</p>
        <h1>${escapeHtml(item.nombre)}</h1>
        <p class="subtitle">${escapeHtml(item.fabricante || "Marca N/D")} • ${escapeHtml(item.generacion || "Gen. N/D")} • ${escapeHtml(item.anioLanzamiento || "Año N/D")}</p>
        <div class="detail-status-line">
          <span class="status-pill ${statusMeta.tone}">${escapeHtml(statusMeta.label)}</span>
          <span>${item.tengo ? "Registrada en coleccion" : "En seguimiento"}</span>
          <span>${gameMetrics.registered}/${gameMetrics.total} juegos registrados</span>
        </div>
        <div class="chips">
          <span class="chip">Tengo: ${yesNo(item.tengo)}</span>
          <span class="chip">Estado: ${escapeHtml(item.estado || "N/D")}</span>
          <span class="chip">Funcionando: ${yesNo(item.funcionando)}</span>
        </div>
        <p>${escapeHtml(item.notas || "Sin notas todavía.")}</p>
        <div class="detail-quick-metrics">
          <article>
            <small>Registrados</small>
            <strong>${gameMetrics.registered}</strong>
          </article>
          <article>
            <small>Deseados</small>
            <strong>${gameMetrics.wanted}</strong>
          </article>
          <article>
            <small>Biblioteca</small>
            <strong>${gameMetrics.progress}%</strong>
          </article>
          <article>
            <small>Formato</small>
            <strong>${gameMetrics.physical}/${gameMetrics.digital}/${gameMetrics.both}</strong>
          </article>
        </div>
        <div class="price-model--large">${renderPriceReference(prices, item)}</div>
        ${item.tengo ? `<div class="price-row"><span>Pagado: ${formatPrice(item.precioPagado, item.monedaPago)}</span></div>` : ""}
        <div class="card-actions">
          <a class="btn-link" href="${buildConsoleHref("games", item.id)}">Abrir biblioteca</a>
          <a class="btn-link" href="${buildConsoleHref("accessories", item.id)}">Abrir accesorios</a>
          <a class="btn-link" href="${item.priceChartingUrl || "https://www.pricecharting.com"}" target="_blank" rel="noreferrer noopener">Ver referencia en PriceCharting</a>
        </div>
      </div>
    </section>

    <section class="detail-toolbar">
      <button id="saveDetailBtn" class="btn-link btn-primary" type="button">Guardar cambios</button>
      <span id="saveIndicator" class="save-indicator">${escapeHtml(appState.saveMessage || "Sin cambios pendientes")}</span>
    </section>

    <section class="detail-sections">
      <article class="detail-block">
        <div class="section-head">
          <h2>Gestion de consola</h2>
          <p class="muted">Resumen + edición progresiva.</p>
        </div>
        <div class="info-pills">
          <span class="info-pill">Estado: ${escapeHtml(draft.estado || "N/D")}</span>
          ${item.tengo ? `<span class="info-pill">Lista para jugar: ${functioningLabel(draft.funcionando)}</span>` : ""}
          <span class="info-pill">Obtención: ${escapeHtml(draft.formaObtencion || "Sin dato")}</span>
          <span class="info-pill">Mapa: ${escapeHtml(draft.ubicacionMapa || "Sin dato")}</span>
          ${item.tengo ? `<span class="info-pill">Pagado: ${formatPrice(draft.precioPagado, item.monedaPago)}</span>` : ""}
        </div>
        ${
          item.tengo
            ? `
          <div class="detail-inline-control">
            <div>
              <strong>Estado operativo</strong>
              <p>Marcá si esta consola ya está lista para jugar para limpiar alertas del panel.</p>
            </div>
            <label>
              <span>Lista para jugar</span>
              <select id="funcionandoSelect">
                ${buildFunctioningOptions(draft.funcionando)}
              </select>
            </label>
          </div>
        `
            : ""
        }
        <details class="edit-panel">
          <summary>Editar datos de consola</summary>
          <div class="detail-form-grid">
            <label>
              Estado
              <select id="estadoSelect">
                ${buildStatusOptions(draft.estado)}
              </select>
            </label>
            <label>
              Forma en que la obtuve
              <input id="obtencionInput" type="text" placeholder="Ej: feria, remate, regalo..." value="${escapeHtml(
                draft.formaObtencion || ""
              )}" />
            </label>
            <label>
              Direccion / mapa (texto o link)
              <input id="mapaInput" type="text" placeholder="Ej: https://maps.google.com/..." value="${escapeHtml(
                draft.ubicacionMapa || ""
              )}" />
            </label>
            ${
              item.tengo
                ? `
              <label>
                Precio pagado (${escapeHtml(item.monedaPago || "USD")})
                <input id="precioPagadoInput" type="number" min="0" step="1" value="${escapeHtml(
                  draft.precioPagado
                )}" placeholder="Ej: 120" />
              </label>
            `
                : ""
            }
          </div>
        </details>
      </article>

      <article class="detail-block">
        <div class="section-head">
          <h2>Fotos propias</h2>
          <p class="muted">Galería de consola y accesorios. Tocá una foto para verla en grande.</p>
        </div>
        <label class="upload-control">
          <span>Subir fotos</span>
          <input id="photoInput" type="file" accept="image/*" multiple />
        </label>
        <div class="spacer-10"></div>
        ${renderPhotoGallery(item, galleryImages)}
      </article>

      ${renderAccessoryPreview(item)}

      ${renderGamesPreview(item)}

      <article class="detail-block detail-block--wide">
        <div class="section-head">
          <h2>Oportunidades</h2>
          <p class="muted">Historial visual de oportunidades y seguimiento.</p>
        </div>
        <div class="opportunity-cluster">
          <div class="action-queue-head">
            <strong>Detectadas por auction-watch</strong>
            <span>solo lectura</span>
          </div>
          <div id="auctionWatchConsoleOpportunities">
            ${renderAutomaticOpportunities(item.id)}
          </div>
        </div>
        <div class="opportunity-cluster">
          <div class="action-queue-head">
            <strong>Guardadas por vos</strong>
            <span>persistidas en tu colección</span>
          </div>
          <details class="edit-panel" open>
            <summary>Nueva oportunidad</summary>
            <div class="detail-form-grid">
              <label>
                Titulo
                <input id="oppTitulo" type="text" placeholder="Ej: Lote GBA SP en remate" />
              </label>
              <label>
                Fuente
                <input id="oppFuente" type="text" placeholder="Ej: MercadoLibre / Facebook" />
              </label>
              <label>
                URL (opcional)
                <input id="oppUrl" type="url" placeholder="https://..." />
              </label>
              <label>
                Precio visto (USD)
                <input id="oppPrecioVisto" type="number" min="0" step="1" />
              </label>
              <label>
                Precio objetivo (USD)
                <input id="oppPrecioObjetivo" type="number" min="0" step="1" />
              </label>
              <label>
                Nota
                <input id="oppNota" type="text" placeholder="Detalle corto" />
              </label>
            </div>
            <div class="card-actions">
              <button id="addOpportunityBtn" class="btn-link" type="button">Agregar oportunidad</button>
            </div>
          </details>
          <div class="spacer-10"></div>
          ${renderOpportunities(item)}
        </div>
      </article>
    </section>
    ${renderConsolePhotoDialog(item, galleryImages)}
  `;
}

function bindGbaGameEvents() {
  const gameSearchInput = document.getElementById("gameSearchInput");
  if (gameSearchInput) {
    gameSearchInput.addEventListener("input", (event) => {
      appState.gameFilters.q = event.target.value;
      render();
    });
  }

  const gameStatusFilter = document.getElementById("gameStatusFilter");
  if (gameStatusFilter) {
    gameStatusFilter.addEventListener("change", (event) => {
      appState.gameFilters.status = event.target.value;
      render();
    });
  }

  const gamePriorityFilter = document.getElementById("gamePriorityFilter");
  if (gamePriorityFilter) {
    gamePriorityFilter.addEventListener("change", (event) => {
      appState.gameFilters.priority = event.target.value;
      render();
    });
  }

  const gameFranchiseFilter = document.getElementById("gameFranchiseFilter");
  if (gameFranchiseFilter) {
    gameFranchiseFilter.addEventListener("change", (event) => {
      appState.gameFilters.franchise = event.target.value;
      render();
    });
  }

  const gameGenreFilter = document.getElementById("gameGenreFilter");
  if (gameGenreFilter) {
    gameGenreFilter.addEventListener("change", (event) => {
      appState.gameFilters.genre = event.target.value;
      render();
    });
  }

  const gameOwnershipFilter = document.getElementById("gameOwnershipFilter");
  if (gameOwnershipFilter) {
    gameOwnershipFilter.addEventListener("change", (event) => {
      appState.gameFilters.ownership = event.target.value;
      render();
    });
  }

  const gameListFilter = document.getElementById("gameListFilter");
  if (gameListFilter) {
    gameListFilter.addEventListener("change", (event) => {
      appState.gameFilters.list = event.target.value;
      render();
    });
  }

  const gameSortFilter = document.getElementById("gameSortFilter");
  if (gameSortFilter) {
    gameSortFilter.addEventListener("change", (event) => {
      appState.gameFilters.sort = event.target.value;
      render();
    });
  }

  document.querySelectorAll(".js-gba-toggle-want").forEach((button) => {
    button.addEventListener("click", () => {
      const idx = Number(button.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      const current = next[idx] || {};
      const nextWant = current.loQuiero !== true;
      next[idx] = {
        ...current,
        loQuiero: nextWant,
        standby: false,
        keepInWishlist: nextWant ? true : false
      };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-standby").forEach((button) => {
    button.addEventListener("click", () => {
      const idx = Number(button.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      const nextStandby = next[idx].standby !== true;
      next[idx] = {
        ...next[idx],
        standby: nextStandby,
        loQuiero: nextStandby ? false : next[idx].loQuiero
      };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-ownership").forEach((select) => {
    select.addEventListener("change", () => {
      const idx = Number(select.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      const chosen = normalizeOwnershipType(select.value, false);
      const owned = chosen !== "none";
      const currentKeep = next[idx].keepInWishlist;
      next[idx] = {
        ...next[idx],
        ownershipType: chosen,
        loTengo: owned,
        keepInWishlist: currentKeep !== undefined ? Boolean(currentKeep) : !owned
      };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-priority").forEach((select) => {
    select.addEventListener("change", () => {
      const idx = Number(select.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = { ...next[idx], prioridad: sanitizePriority(select.value) };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-keep-wishlist").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = { ...next[idx], keepInWishlist: input.checked };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-gba-condition").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = { ...next[idx], condicion: input.value.trim() };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-region").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = { ...next[idx], region: input.value.trim() };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-gba-notes").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = { ...next[idx], notas: input.value.trim() };
      updateGamesCatalog(next);
    });
  });

  document.querySelectorAll(".js-game-cover-file").forEach((input) => {
    input.addEventListener("change", async () => {
      const idx = Number(input.dataset.index);
      const file = input.files?.[0];
      if (!file) return;
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const next = [...(appState.item.juegosCatalogo || [])];
      next[idx] = {
        ...next[idx],
        coverImage: encoded,
        coverUrl: encoded,
        imageSource: "manual-upload",
        imageStatus: "manual",
        sourceType: next[idx]?.sourceType || "manual"
      };
      updateGamesCatalog(next);
    });
  });

  const manualDialog = document.getElementById("manualGameDialog");
  const openManualBtn = document.getElementById("openManualGameModalBtn");
  const closeManualBtn = document.getElementById("closeManualGameModalBtn");
  const manualCoverInput = document.getElementById("manualGameCoverFile");
  const manualCoverPreview = document.getElementById("manualGameCoverPreview");
  const manualNameInput = document.getElementById("manualGameName");
  const manualFranchiseInput = document.getElementById("manualGameFranchise");
  const manualGenreInput = document.getElementById("manualGameGenre");
  const manualPriorityInput = document.getElementById("manualGamePriority");
  const manualOwnershipInput = document.getElementById("manualGameOwnership");
  const manualStateInput = document.getElementById("manualGameState");
  const manualKeepInput = document.getElementById("manualGameKeepWishlist");
  const manualDetectHint = document.getElementById("manualDetectHint");
  const manualGameError = document.getElementById("manualGameError");
  const previewTitle = document.getElementById("manualPreviewTitle");
  const previewMeta = document.getElementById("manualPreviewMeta");
  const previewPriority = document.getElementById("manualPreviewPriority");
  const previewOwnership = document.getElementById("manualPreviewOwnership");
  const previewWish = document.getElementById("manualPreviewWish");
  const addManualGameBtn = document.getElementById("addManualGameBtn");

  const openManualDialog = () => {
    if (!manualDialog) return;
    if (typeof manualDialog.showModal === "function") {
      manualDialog.showModal();
      return;
    }
    manualDialog.setAttribute("open", "open");
    manualDialog.classList.add("is-open-fallback");
  };

  const closeManualDialog = () => {
    if (!manualDialog) return;
    if (typeof manualDialog.close === "function") {
      manualDialog.close();
      return;
    }
    manualDialog.removeAttribute("open");
    manualDialog.classList.remove("is-open-fallback");
  };

  const refreshManualPreview = (detected = null) => {
    if (!previewTitle || !previewMeta || !previewPriority || !previewOwnership || !previewWish) return;
    const currentName = manualNameInput?.value?.trim() || detected?.detectedName || "Juego manual";
    const currentFranchise = manualFranchiseInput?.value?.trim() || detected?.detectedFranchise || "Franquicia N/D";
    const currentGenre = manualGenreInput?.value?.trim() || detected?.detectedGenre || "Genero N/D";
    const currentPriority = sanitizePriority(manualPriorityInput?.value || "media");
    const currentOwnership = normalizeOwnershipType(manualOwnershipInput?.value || "none", false);
    const currentState = manualStateInput?.value || "want";

    previewTitle.textContent = currentName;
    previewMeta.textContent = `${currentFranchise} • ${currentGenre}`;
    previewPriority.textContent = `Prioridad: ${currentPriority}`;
    previewPriority.className = `gba-badge priority-${currentPriority}`;
    previewOwnership.textContent = getOwnershipLabel(currentOwnership) || "No lo tengo";
    previewOwnership.className = `gba-badge ${getOwnershipBadgeClass(currentOwnership)}`;
    previewWish.textContent = currentState === "want" ? "Lo quiero" : currentState === "standby" ? "Standby" : "No quiero";
    previewWish.className = `gba-badge ${currentState === "standby" ? "standby" : currentState === "want" ? "want" : "keep-off"}`;
  };

  const runManualAutodetect = () => {
    const detected = inferManualGameFromInput(manualNameInput?.value || "", appState.item?.juegosCatalogo || []);
    if (manualDetectHint) {
      const label =
        detected.confidence === "high" ? "alta" : detected.confidence === "medium" ? "media" : detected.confidence === "low" ? "baja" : "nula";
      manualDetectHint.textContent = `Identificación automática (${label}): ${detected.reason}`;
    }
    if (manualNameInput && !manualNameInput.value.trim() && detected.detectedName) {
      manualNameInput.value = detected.detectedName;
    }
    if (manualFranchiseInput && !manualFranchiseInput.value.trim() && detected.detectedFranchise) {
      manualFranchiseInput.value = detected.detectedFranchise;
    }
    if (manualGenreInput && !manualGenreInput.value.trim() && detected.detectedGenre) {
      manualGenreInput.value = detected.detectedGenre;
    }
    if (manualCoverPreview && (!manualCoverInput?.files?.[0]) && detected.detectedCover) {
      manualCoverPreview.src = detected.detectedCover;
    }
    refreshManualPreview(detected);
  };

  const clearManualError = () => {
    if (!manualGameError) return;
    manualGameError.hidden = true;
    manualGameError.textContent = "";
  };

  const setManualError = (message) => {
    if (!manualGameError) return;
    manualGameError.hidden = false;
    manualGameError.textContent = message;
  };

  const updateManualFormValidity = () => {
    const hasName = Boolean(manualNameInput?.value?.trim());
    if (addManualGameBtn) addManualGameBtn.disabled = !hasName;
    if (hasName) clearManualError();
  };

  if (openManualBtn && manualDialog) {
    openManualBtn.addEventListener("click", () => {
      openManualDialog();
      runManualAutodetect();
      updateManualFormValidity();
    });
  }
  if (closeManualBtn && manualDialog) {
    closeManualBtn.addEventListener("click", () => {
      closeManualDialog();
    });
  }

  if (manualNameInput) {
    manualNameInput.addEventListener("input", () => {
      runManualAutodetect();
      updateManualFormValidity();
    });
  }
  if (manualFranchiseInput) manualFranchiseInput.addEventListener("input", () => refreshManualPreview());
  if (manualGenreInput) manualGenreInput.addEventListener("input", () => refreshManualPreview());
  if (manualPriorityInput) manualPriorityInput.addEventListener("change", () => refreshManualPreview());
  if (manualOwnershipInput) manualOwnershipInput.addEventListener("change", () => refreshManualPreview());
  if (manualStateInput) manualStateInput.addEventListener("change", () => refreshManualPreview());
  if (manualKeepInput) manualKeepInput.addEventListener("change", () => refreshManualPreview());

  if (manualCoverInput && manualCoverPreview) {
    manualCoverInput.addEventListener("change", async () => {
      const file = manualCoverInput.files?.[0];
      if (!file) {
        runManualAutodetect();
        return;
      }
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      if (manualNameInput && !manualNameInput.value.trim()) {
        const filename = file.name.replace(/\.[^/.]+$/, "").replace(/[_-]+/g, " ").trim();
        if (filename) manualNameInput.value = filename;
      }
      manualCoverPreview.src = encoded;
      runManualAutodetect();
      updateManualFormValidity();
    });
  }

  updateManualFormValidity();
  if (addManualGameBtn) {
    addManualGameBtn.addEventListener("click", async () => {
      const nameInput = document.getElementById("manualGameName");
      const franchiseInput = document.getElementById("manualGameFranchise");
      const genreInput = document.getElementById("manualGameGenre");
      const priorityInput = document.getElementById("manualGamePriority");
      const stateInput = document.getElementById("manualGameState");
      const ownershipInput = document.getElementById("manualGameOwnership");
      const keepInput = document.getElementById("manualGameKeepWishlist");
      const conditionInput = document.getElementById("manualGameCondition");
      const regionInput = document.getElementById("manualGameRegion");
      const notesInput = document.getElementById("manualGameNotes");
      const priceLowInput = document.getElementById("manualGamePriceLow");
      const priceMidInput = document.getElementById("manualGamePriceMid");
      const priceHighInput = document.getElementById("manualGamePriceHigh");
      const priceNotesInput = document.getElementById("manualGamePriceNotes");
      const coverInput = document.getElementById("manualGameCoverFile");

      const nombre = nameInput?.value?.trim() || "";
      if (!nombre) {
        setManualError("El nombre del juego es obligatorio para guardar.");
        nameInput?.focus();
        updateManualFormValidity();
        return;
      }
      clearManualError();

      const sourceState = stateInput?.value || "want";
      const loQuiero = sourceState === "want";
      const standby = sourceState === "standby";
      const ownershipType = normalizeOwnershipType(ownershipInput?.value || "none", false);
      const owned = ownershipType !== "none";

      let manualCover = fallbackGameImage;
      if (coverInput?.files?.[0]) {
        manualCover = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(coverInput.files[0]);
        });
      } else if (manualCoverPreview?.src && manualCoverPreview.src !== fallbackGameImage) {
        manualCover = manualCoverPreview.src;
      }

      const keepInWishlist = keepInput?.checked === true || loQuiero === true || (!owned && sourceState !== "none");

      const next = [...(appState.item.juegosCatalogo || [])];
      const existingIdx = next.findIndex((game) => normalizeGameName(game.nombre) === normalizeGameName(nombre));
      const shouldEditExisting =
        existingIdx >= 0
          ? window.confirm("Ya existe un juego con ese nombre. ¿Querés editar ese juego existente?")
          : false;
      const payload = {
        id:
          shouldEditExisting
            ? next[existingIdx].id
            : `manual-${normalizeGameName(nombre).replaceAll(" ", "-") || "juego"}-${Date.now().toString(36).slice(-5)}`,
        nombre,
        franquicia: franchiseInput?.value?.trim() || "N/D",
        genero: genreInput?.value?.trim() || "N/D",
        prioridad: sanitizePriority(priorityInput?.value || "media"),
        loQuiero,
        standby,
        loTengo: owned,
        ownershipType,
        keepInWishlist,
        condicion: conditionInput?.value?.trim() || "",
        region: regionInput?.value?.trim() || "",
        notas: notesInput?.value?.trim() || "",
        sourceType: "manual",
        coverImage: manualCover || fallbackGameImage,
        coverUrl: manualCover || fallbackGameImage,
        imageSource: coverInput?.files?.[0] ? "manual-upload" : manualCover !== fallbackGameImage ? "autodetect" : "placeholder",
        imageStatus: coverInput?.files?.[0] ? "manual" : manualCover !== fallbackGameImage ? "found" : "placeholder",
        imageSearchName: nombre,
        priceRange: {
          low: priceLowInput?.value ? Number(priceLowInput.value) : null,
          mid: priceMidInput?.value ? Number(priceMidInput.value) : null,
          high: priceHighInput?.value ? Number(priceHighInput.value) : null,
          notes: priceNotesInput?.value?.trim() || ""
        },
        priceGuide: {
          priceCharting: null,
          ebaySold: null,
          cib: null,
          targetBuy: null,
          notes: ""
        }
      };

      if (shouldEditExisting && existingIdx >= 0) {
        next[existingIdx] = {
          ...next[existingIdx],
          ...payload
        };
      } else {
        next.push(payload);
      }

      updateGamesCatalog(next);

      if (nameInput) nameInput.value = "";
      if (franchiseInput) franchiseInput.value = "";
      if (genreInput) genreInput.value = "";
      if (priorityInput) priorityInput.value = "media";
      if (stateInput) stateInput.value = "want";
      if (ownershipInput) ownershipInput.value = "none";
      if (keepInput) keepInput.checked = false;
      if (conditionInput) conditionInput.value = "";
      if (notesInput) notesInput.value = "";
      if (priceLowInput) priceLowInput.value = "";
      if (priceMidInput) priceMidInput.value = "";
      if (priceHighInput) priceHighInput.value = "";
      if (priceNotesInput) priceNotesInput.value = "";
      if (coverInput) coverInput.value = "";
      if (manualCoverPreview) manualCoverPreview.src = fallbackGameImage;
      clearManualError();
      updateManualFormValidity();
      closeManualDialog();
    });
  }
}

function bindAccessoryEvents() {
  const accessorySearchInput = document.getElementById("accessorySearchInput");
  if (accessorySearchInput) {
    accessorySearchInput.addEventListener("input", (event) => {
      appState.accessoryFilters.q = event.target.value;
      render();
    });
  }

  const accessoryViewFilter = document.getElementById("accessoryViewFilter");
  if (accessoryViewFilter) {
    accessoryViewFilter.addEventListener("change", (event) => {
      appState.accessoryFilters.view = event.target.value;
      render();
    });
  }

  const accessoryTypeFilter = document.getElementById("accessoryTypeFilter");
  if (accessoryTypeFilter) {
    accessoryTypeFilter.addEventListener("change", (event) => {
      appState.accessoryFilters.type = event.target.value;
      render();
    });
  }

  const accessoryLayoutFilter = document.getElementById("accessoryLayoutFilter");
  if (accessoryLayoutFilter) {
    accessoryLayoutFilter.addEventListener("change", (event) => {
      appState.accessoryFilters.layout = event.target.value;
      render();
    });
  }

  const manualDialog = document.getElementById("manualAccessoryDialog");
  const openManualBtn = document.getElementById("openManualAccessoryModalBtn");
  const closeManualBtn = document.getElementById("closeManualAccessoryModalBtn");
  const addManualBtn = document.getElementById("addManualAccessoryBtn");
  const manualNameInput = document.getElementById("manualAccessoryName");
  const manualImageInput = document.getElementById("manualAccessoryImageFile");
  const manualTypeInput = document.getElementById("manualAccessoryType");
  const manualOriginalInput = document.getElementById("manualAccessoryOriginal");
  const manualQtyInput = document.getElementById("manualAccessoryQty");
  const manualOwnedInput = document.getElementById("manualAccessoryOwned");
  const manualStateInput = document.getElementById("manualAccessoryState");
  const manualFunctioningInput = document.getElementById("manualAccessoryFunctioning");
  const manualNotesInput = document.getElementById("manualAccessoryNotes");
  const manualError = document.getElementById("manualAccessoryError");
  const previewTitle = document.getElementById("manualAccessoryPreviewTitle");
  const previewMeta = document.getElementById("manualAccessoryPreviewMeta");
  const previewStatus = document.getElementById("manualAccessoryPreviewStatus");
  const previewImage = document.getElementById("manualAccessoryPreviewImage");

  const openManualDialog = () => {
    if (!manualDialog) return;
    if (typeof manualDialog.showModal === "function") {
      manualDialog.showModal();
      return;
    }
    manualDialog.setAttribute("open", "true");
    manualDialog.classList.add("is-open-fallback");
  };

  const closeManualDialog = () => {
    if (!manualDialog) return;
    if (typeof manualDialog.close === "function") {
      manualDialog.close();
      return;
    }
    manualDialog.removeAttribute("open");
    manualDialog.classList.remove("is-open-fallback");
  };

  const clearManualError = () => {
    if (!manualError) return;
    manualError.hidden = true;
    manualError.textContent = "";
  };

  const setManualError = (message) => {
    if (!manualError) return;
    manualError.hidden = false;
    manualError.textContent = message;
  };

  const refreshManualAccessoryPreview = () => {
    if (!previewTitle || !previewMeta || !previewStatus || !previewImage) return;
    const nombre = manualNameInput?.value?.trim() || "Accesorio manual";
    const tipo = manualTypeInput?.value || "otro";
    const owned = manualOwnedInput?.checked !== false && Number(manualQtyInput?.value || 1) > 0;
    previewTitle.textContent = nombre;
    previewMeta.textContent = getAccessoryTypeLabel(tipo);
    previewStatus.textContent = owned ? "Tengo" : "No tengo";
    previewStatus.className = `status-pill ${owned ? "ready" : "unknown"}`;
    if (!manualImageInput?.files?.[0] && (!previewImage.getAttribute("src") || previewImage.dataset.manual !== "true")) {
      previewImage.src = accessoryPlaceholderImage({ nombre, tipo });
      previewImage.dataset.manual = "false";
    }
  };

  const updateManualAccessoryValidity = () => {
    const hasName = Boolean(manualNameInput?.value?.trim());
    if (addManualBtn) addManualBtn.disabled = !hasName;
    if (hasName) clearManualError();
  };

  if (openManualBtn) {
    openManualBtn.addEventListener("click", () => {
      openManualDialog();
      refreshManualAccessoryPreview();
      updateManualAccessoryValidity();
    });
  }
  if (closeManualBtn) {
    closeManualBtn.addEventListener("click", () => {
      closeManualDialog();
    });
  }
  if (manualNameInput) {
    manualNameInput.addEventListener("input", () => {
      refreshManualAccessoryPreview();
      updateManualAccessoryValidity();
    });
  }
  if (manualTypeInput) manualTypeInput.addEventListener("change", refreshManualAccessoryPreview);
  if (manualQtyInput) {
    manualQtyInput.addEventListener("input", () => {
      const qty = Number(manualQtyInput.value);
      if (manualOwnedInput) manualOwnedInput.checked = Number.isFinite(qty) && qty > 0;
      refreshManualAccessoryPreview();
    });
  }
  if (manualOwnedInput) {
    manualOwnedInput.addEventListener("change", () => {
      if (manualQtyInput && !manualOwnedInput.checked) manualQtyInput.value = "0";
      if (manualQtyInput && manualOwnedInput.checked && Number(manualQtyInput.value) <= 0) manualQtyInput.value = "1";
      refreshManualAccessoryPreview();
    });
  }
  if (manualImageInput && previewImage) {
    manualImageInput.addEventListener("change", async () => {
      const file = manualImageInput.files?.[0];
      if (!file) {
        previewImage.dataset.manual = "false";
        refreshManualAccessoryPreview();
        return;
      }
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      previewImage.src = encoded;
      previewImage.dataset.manual = "true";
      if (manualNameInput && !manualNameInput.value.trim()) {
        const filename = file.name.replace(/\.[^/.]+$/, "").replace(/[_-]+/g, " ").trim();
        if (filename) manualNameInput.value = filename;
      }
      refreshManualAccessoryPreview();
      updateManualAccessoryValidity();
    });
  }

  updateManualAccessoryValidity();
  if (addManualBtn) {
    addManualBtn.addEventListener("click", async () => {
      const nombre = manualNameInput?.value?.trim() || "";
      if (!nombre) {
        setManualError("El nombre del accesorio es obligatorio para guardar.");
        manualNameInput?.focus();
        updateManualAccessoryValidity();
        return;
      }
      clearManualError();

      let image = "";
      if (manualImageInput?.files?.[0]) {
        image = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(manualImageInput.files[0]);
        });
      } else if (previewImage?.src && !previewImage.src.startsWith("data:image/svg+xml")) {
        image = previewImage.src;
      }

      const qty = Number(manualQtyInput?.value);
      const cantidad = Number.isFinite(qty) && qty > 0 ? Math.round(qty) : 0;
      const tengo = manualOwnedInput?.checked === true && cantidad > 0;
      const tipo = manualTypeInput?.value || "otro";
      const next = [...getAccessoryCatalogList(appState.item)];
      const existingIdx = next.findIndex(
        (entry) => (entry.sourceType || "catalog") === "manual" && normalizeAccessoryName(entry.nombre) === normalizeAccessoryName(nombre)
      );
      const shouldEditExisting =
        existingIdx >= 0 ? window.confirm("Ya existe un accesorio manual con ese nombre. ¿Querés actualizarlo?") : false;

      const payload = {
        id:
          shouldEditExisting
            ? next[existingIdx].id
            : `manual-${normalizeAccessoryName(nombre).replaceAll(" ", "-") || "accesorio"}-${Date.now().toString(36).slice(-5)}`,
        nombre,
        tipo,
        image: image || "",
        tengo,
        cantidad,
        funcionando:
          manualFunctioningInput?.value === "true" ? true : manualFunctioningInput?.value === "false" ? false : null,
        original: manualOriginalInput?.value || "original",
        estado: manualStateInput?.value || "",
        notas: manualNotesInput?.value?.trim() || "",
        esencial: false,
        sourceType: "manual",
        orden: shouldEditExisting ? next[existingIdx].orden : 900 + next.filter((entry) => entry.sourceType === "manual").length * 10
      };

      if (shouldEditExisting && existingIdx >= 0) {
        next[existingIdx] = {
          ...next[existingIdx],
          ...payload
        };
      } else {
        next.push(payload);
      }

      updateAccessoriesCatalog(next);

      if (manualNameInput) manualNameInput.value = "";
      if (manualImageInput) manualImageInput.value = "";
      if (manualTypeInput) manualTypeInput.value = "otro";
      if (manualOriginalInput) manualOriginalInput.value = "original";
      if (manualQtyInput) manualQtyInput.value = "1";
      if (manualOwnedInput) manualOwnedInput.checked = true;
      if (manualStateInput) manualStateInput.value = "";
      if (manualFunctioningInput) manualFunctioningInput.value = "";
      if (manualNotesInput) manualNotesInput.value = "";
      if (previewImage) {
        previewImage.src = accessoryPlaceholderImage({ nombre: "Accesorio manual", tipo: "otro" });
        previewImage.dataset.manual = "false";
      }
      refreshManualAccessoryPreview();
      updateManualAccessoryValidity();
      closeManualDialog();
    });
  }

  document.querySelectorAll(".js-accessory-owned").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const next = [...getAccessoryCatalogList(appState.item)];
      const current = next[idx] || {};
      next[idx] = {
        ...current,
        tengo: input.checked,
        cantidad: input.checked ? Math.max(1, Number(current.cantidad) || 1) : 0
      };
      updateAccessoriesCatalog(next);
    });
  });

  document.querySelectorAll(".js-accessory-qty").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.index);
      const qty = Number(input.value);
      const next = [...getAccessoryCatalogList(appState.item)];
      next[idx] = {
        ...next[idx],
        cantidad: Number.isFinite(qty) && qty > 0 ? Math.round(qty) : 0,
        tengo: Number.isFinite(qty) && qty > 0
      };
      updateAccessoriesCatalog(next);
    });
  });

  document.querySelectorAll(".js-accessory-functioning").forEach((select) => {
    select.addEventListener("change", () => {
      const idx = Number(select.dataset.index);
      const next = [...getAccessoryCatalogList(appState.item)];
      next[idx] = {
        ...next[idx],
        funcionando: select.value === "true" ? true : select.value === "false" ? false : null
      };
      updateAccessoriesCatalog(next);
    });
  });

  document.querySelectorAll(".js-accessory-original").forEach((select) => {
    select.addEventListener("change", () => {
      const idx = Number(select.dataset.index);
      const next = [...getAccessoryCatalogList(appState.item)];
      next[idx] = { ...next[idx], original: select.value || "" };
      updateAccessoriesCatalog(next);
    });
  });

  document.querySelectorAll(".js-accessory-state").forEach((select) => {
    select.addEventListener("change", () => {
      const idx = Number(select.dataset.index);
      const next = [...getAccessoryCatalogList(appState.item)];
      next[idx] = { ...next[idx], estado: select.value || "" };
      updateAccessoriesCatalog(next);
    });
  });

  document.querySelectorAll(".js-accessory-notes").forEach((textarea) => {
    textarea.addEventListener("change", () => {
      const idx = Number(textarea.dataset.index);
      const next = [...getAccessoryCatalogList(appState.item)];
      next[idx] = { ...next[idx], notas: textarea.value.trim() };
      updateAccessoriesCatalog(next);
    });
  });
}

function bindDetailEvents() {
  const saveDetailBtn = document.getElementById("saveDetailBtn");
  if (saveDetailBtn) {
    saveDetailBtn.addEventListener("click", () => {
      saveDraftFields();
    });
  }

  const estadoSelect = document.getElementById("estadoSelect");
  if (estadoSelect) {
    estadoSelect.addEventListener("input", (event) => {
      appState.draft.estado = event.target.value;
      markPendingSave();
    });
  }

  const funcionandoSelect = document.getElementById("funcionandoSelect");
  if (funcionandoSelect) {
    funcionandoSelect.addEventListener("change", (event) => {
      appState.draft.funcionando = event.target.value;
      markPendingSave();
    });
  }

  const obtencionInput = document.getElementById("obtencionInput");
  if (obtencionInput) {
    obtencionInput.addEventListener("input", (event) => {
      appState.draft.formaObtencion = event.target.value;
      markPendingSave();
    });
  }

  const mapaInput = document.getElementById("mapaInput");
  if (mapaInput) {
    mapaInput.addEventListener("input", (event) => {
      appState.draft.ubicacionMapa = event.target.value;
      markPendingSave();
    });
  }

  const precioPagadoInput = document.getElementById("precioPagadoInput");
  if (precioPagadoInput) {
    precioPagadoInput.addEventListener("input", (event) => {
      appState.draft.precioPagado = event.target.value;
      markPendingSave();
    });
  }

  const accesoriosNotaInput = document.getElementById("accesoriosNotaInput");
  if (accesoriosNotaInput) {
    accesoriosNotaInput.addEventListener("input", (event) => {
      appState.draft.accesoriosCatalogoNotas = event.target.value;
      markPendingSave();
    });
    if (getPageMode() === "accessories") {
      accesoriosNotaInput.addEventListener("change", () => {
        saveDraftFields();
      });
    }
  }

  const addOpportunityBtn = document.getElementById("addOpportunityBtn");
  if (addOpportunityBtn) {
    addOpportunityBtn.addEventListener("click", () => {
      const titulo = document.getElementById("oppTitulo").value.trim();
      const fuente = document.getElementById("oppFuente").value.trim();
      const url = document.getElementById("oppUrl").value.trim();
      const nota = document.getElementById("oppNota").value.trim();
      const precioVistoRaw = document.getElementById("oppPrecioVisto").value;
      const precioObjetivoRaw = document.getElementById("oppPrecioObjetivo").value;

      const next = {
        titulo: titulo || "Oportunidad",
        fuente: fuente || "N/D",
        url: url || null,
        nota: nota || "",
        fecha: new Date().toISOString().slice(0, 10),
        precioVisto: precioVistoRaw ? Number(precioVistoRaw) : null,
        precioObjetivo: precioObjetivoRaw ? Number(precioObjetivoRaw) : null
      };

      const current = [...(appState.item.oportunidades || [])];
      current.push(next);
      saveDetailEdits({ oportunidades: current });
      appState.item = applyDetailEdits(appState.item);
      appState.saveMessage = "Guardado";
      render();
    });
  }

  document.querySelectorAll(".js-remove-opportunity").forEach((button) => {
    button.addEventListener("click", () => {
      removeOpportunityAt(Number(button.dataset.index));
    });
  });

  const photoInput = document.getElementById("photoInput");
  if (photoInput) {
    photoInput.addEventListener("change", async (event) => {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;

      const encoded = await Promise.all(
        files.map(
          (file) =>
            new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(reader.result);
              reader.onerror = reject;
              reader.readAsDataURL(file);
            })
        )
      );

      const current = [...(appState.item.fotosPropias || [])];
      saveDetailEdits({ fotosPropias: [...current, ...encoded] });
      appState.item = applyDetailEdits(appState.item);
      appState.saveMessage = "Guardado";
      render();
    });
  }

  document.querySelectorAll(".js-remove-photo").forEach((button) => {
    button.addEventListener("click", () => {
      removePhotoAt(Number(button.dataset.index));
    });
  });

  const photoDialog = document.getElementById("consolePhotoDialog");
  const photoDialogImage = document.getElementById("consolePhotoDialogImage");
  const photoDialogTitle = document.getElementById("consolePhotoDialogTitle");
  const photoDialogCaption = document.getElementById("consolePhotoDialogCaption");
  const previousPhotoBtn = document.getElementById("previousConsolePhotoBtn");
  const nextPhotoBtn = document.getElementById("nextConsolePhotoBtn");
  const closePhotoBtn = document.getElementById("closeConsolePhotoDialogBtn");
  const galleryImages = getConsoleGalleryImages(appState.item);
  let activePhotoIndex = 0;
  let photoDialogTrigger = null;

  const updatePhotoDialog = (nextIndex) => {
    if (!galleryImages.length || !photoDialogImage || !photoDialogTitle || !photoDialogCaption) return;
    activePhotoIndex = (nextIndex + galleryImages.length) % galleryImages.length;
    const position = activePhotoIndex + 1;
    const consoleName = appState.item?.nombre || "la consola";
    photoDialogImage.onerror = () => {
      photoDialogImage.onerror = null;
      photoDialogImage.src = fallbackImage;
      photoDialogImage.alt = `No se pudo cargar la foto ${position} de ${consoleName}`;
      photoDialogCaption.textContent = `${consoleName} · No se pudo cargar esta foto`;
    };
    photoDialogImage.src = galleryImages[activePhotoIndex];
    photoDialogImage.alt = `Foto ${position} de ${galleryImages.length} de ${consoleName}`;
    photoDialogTitle.textContent = `Foto ${position} de ${galleryImages.length}`;
    photoDialogCaption.textContent = `${consoleName} · Foto ${position} de ${galleryImages.length}`;
  };

  const closePhotoDialog = () => {
    if (!photoDialog) return;
    if (typeof photoDialog.close === "function" && photoDialog.open) {
      photoDialog.close();
      return;
    }
    photoDialog.removeAttribute("open");
    photoDialog.classList.remove("is-open-fallback");
    photoDialogTrigger?.focus();
  };

  document.querySelectorAll(".js-open-console-photo").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.galleryIndex);
      if (!photoDialog || !galleryImages[index]) return;
      photoDialogTrigger = button;
      updatePhotoDialog(index);
      if (typeof photoDialog.showModal === "function") {
        if (!photoDialog.open) photoDialog.showModal();
        return;
      }
      photoDialog.setAttribute("open", "open");
      photoDialog.classList.add("is-open-fallback");
      closePhotoBtn?.focus();
    });
  });

  closePhotoBtn?.addEventListener("click", closePhotoDialog);
  previousPhotoBtn?.addEventListener("click", () => updatePhotoDialog(activePhotoIndex - 1));
  nextPhotoBtn?.addEventListener("click", () => updatePhotoDialog(activePhotoIndex + 1));

  if (photoDialog) {
    photoDialog.addEventListener("click", (event) => {
      if (event.target === photoDialog) closePhotoDialog();
    });
    photoDialog.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft" && galleryImages.length > 1) {
        event.preventDefault();
        updatePhotoDialog(activePhotoIndex - 1);
      }
      if (event.key === "ArrowRight" && galleryImages.length > 1) {
        event.preventDefault();
        updatePhotoDialog(activePhotoIndex + 1);
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closePhotoDialog();
      }
    });
    photoDialog.addEventListener("close", () => {
      photoDialogTrigger?.focus();
    });
  }

  if (getPageMode() === "games") bindGbaGameEvents();
  if (getPageMode() === "accessories") bindAccessoryEvents();
}

function render() {
  appState.root.innerHTML = renderPage(appState.item);
  bindDetailEvents();
}

async function init() {
  appState.root = document.getElementById("detailRoot");
  appState.id = new URLSearchParams(window.location.search).get("id");

  if (!appState.id) {
    appState.root.innerHTML = "<p>Falta el id de consola. Volvé al catálogo y elegí una consola.</p>";
    return;
  }

  try {
    const cacheKey = "20260623g";
    const [consolesRes, gamesRes, accessoriesRes] = await Promise.all([
      fetch(`./data/consoles.json?v=${cacheKey}`),
      fetch(`./data/console-games.json?v=${cacheKey}`),
      fetch(`./data/console-accessories.json?v=20260622f`)
    ]);

    if (!consolesRes.ok) throw new Error("No se pudo cargar data/consoles.json");

    const consolesPayload = await consolesRes.json();
    const gamesPayload = gamesRes.ok ? await gamesRes.json() : { byConsole: {} };
    const accessoriesPayload = accessoriesRes.ok ? await accessoriesRes.json() : { byConsole: {} };
    const auctionWatchRefresh = loadAuctionWatchSnapshot().catch((error) => {
      console.info("[AuctionWatch] console refresh unavailable", error);
      return null;
    });
    appState.accessoryCatalogByConsole = accessoriesPayload.byConsole || {};

    const merged = mergeWithAdditions(consolesPayload.consolas || []);
    appState.allStatuses = [...new Set(merged.map((item) => item.estado).filter(Boolean))];

    const found = merged.find((item) => item.id === appState.id);
    if (!found) {
      appState.root.innerHTML = "<p>No encontramos esa consola. Volvé al catálogo.</p>";
      return;
    }

    const consoleGames = gamesPayload.byConsole?.[appState.id]?.juegosCatalogo || [];
    const consoleAccessories = appState.accessoryCatalogByConsole?.[appState.id]?.accessoriesCatalog || [];
    window.CollectionRepository.migrateConsoleEntityState(appState.id, {
      baseGames: consoleGames,
      baseAccessories: consoleAccessories
    });
    appState.baseGames = consoleGames;
    appState.baseAccessories = consoleAccessories;
    const withGames = {
      ...found,
      juegosCatalogo: Array.isArray(found.juegosCatalogo) && found.juegosCatalogo.length
        ? found.juegosCatalogo
        : consoleGames,
      accesoriosItems: Array.isArray(found.accesoriosItems) ? found.accesoriosItems : consoleAccessories
    };

    appState.item = applyDetailEdits(applyItemOverride(withGames));
    const reconciledGames = composeGamesForConsole(consoleGames, appState.item.juegosCatalogo || []);
    const reconciledAccessories = composeAccessoriesForConsole(
      consoleAccessories,
      appState.item.accesoriosItems || [],
      appState.item.accesorios || []
    );
    appState.item = {
      ...appState.item,
      juegosCatalogo: reconciledGames,
      accesoriosItems: reconciledAccessories
    };
    appState.draft = createDraftFromItem(appState.item);
    appState.saveMessage = "Sin cambios pendientes";
    render();
    auctionWatchRefresh.then(() => {
      refreshAutomaticOpportunitiesBlock();
    });
  } catch (error) {
    appState.root.innerHTML = `<p>Error al cargar el detalle: ${error.message}</p>`;
  }
}

window.addEventListener("storage", (event) => {
  if (event.key !== "consolas.appState.v2" || !appState.item) return;
  appState.item = applyDetailEdits(appState.item);
  appState.item = {
    ...appState.item,
    juegosCatalogo: composeGamesForConsole(appState.baseGames || [], appState.item.juegosCatalogo || []),
    accesoriosItems: composeAccessoriesForConsole(
      appState.baseAccessories || [],
      appState.item.accesoriosItems || [],
      appState.item.accesorios || []
    )
  };
  appState.draft = createDraftFromItem(appState.item);
  render();
});

init();
