const state = {
  allConsoles: [],
  gamesByConsole: {},
  filtered: [],
  filters: {
    search: "",
    brand: "",
    generation: "",
    status: "",
    quick: "all",
    noHaveSub: "",
    sort: "year-asc",
    groupBy: "none"
  }
};
const fallbackImage = "./assets/photos/console-placeholder.svg";

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

const normalize = (text = "") => window.CollectionRepository.normalizeText(text);
const normalizeOwnershipType = (raw = "", loTengo = false) => window.CollectionRepository.normalizeOwnershipType(raw, loTengo);

function readOverrides() {
  return window.CollectionRepository.readOverrides();
}

function writeOverrides(overrides) {
  window.DataStore?.setOverrides?.(overrides || {});
}

function readAdditions() {
  return window.CollectionRepository.readAdditionsArray();
}

function mergeWithAdditions(items) {
  return window.CollectionRepository.mergeWithAdditions(items);
}

function applyDetailEdits(items) {
  return window.CollectionRepository.applyDetailEdits(items);
}

function applyOverrides(items) {
  return window.CollectionRepository.applyOverrides(items);
}

function removeConsoleFromWishlist(consoleId) {
  const overrides = readOverrides();
  overrides[consoleId] = {
    ...(overrides[consoleId] || {}),
    removedFromWishlist: true,
    updatedAt: new Date().toISOString()
  };
  writeOverrides(overrides);
  state.allConsoles = applyDetailEdits(applyOverrides(mergeWithAdditions(state.allConsoles)));
  renderSummary(state.allConsoles);
  renderGlobalDashboard(state.allConsoles);
  updateSelectOptions(state.allConsoles);
  applyFilters();
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

function renderSummary(items) {
  const summary = document.getElementById("summary");
  const collection = items.filter((i) => i.categoria === "coleccion");
  const wishlist = items.filter((i) => i.categoria === "wishlist");
  const funcionando = collection.filter((i) => i.funcionando === true).length;

  summary.innerHTML = `
    <article class="stat"><small>Total</small><strong>${items.length}</strong></article>
    <article class="stat"><small>En colección</small><strong>${collection.length}</strong></article>
    <article class="stat"><small>En wishlist</small><strong>${wishlist.length}</strong></article>
    <article class="stat"><small>Funcionando</small><strong>${funcionando}</strong></article>
  `;
}

function getGamesForConsole(consoleId) {
  return window.CollectionRepository.getGamesForConsole(state.gamesByConsole, consoleId);
}

function isNonGameEntry(game = {}) {
  return window.CollectionRepository.isNonGameEntry(game);
}

async function loadAuctionWatchSnapshot() {
  return window.AuctionWatchRepository?.loadSnapshot?.() || null;
}

function renderOpportunityBadges(item = {}) {
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

function renderOpportunityCard(item = {}, { featured = false } = {}) {
  const repo = window.AuctionWatchRepository;
  const primaryUrl = repo?.getPrimaryUrl?.(item) || "";
  const secondaryUrl = repo?.getSecondaryUrl?.(item) || "";
  const primaryLabel = repo?.getPrimaryCtaLabel?.(item) || "Ver detalle";
  const secondaryLabel = repo?.getSecondaryCtaLabel?.(item) || "";
  const consoleId = item.consoleIds?.[0] || "";
  const consoleUrl = consoleId ? window.CollectionRepository?.getConsoleDetailHref?.(consoleId) || "" : "";
  const description = item.description || item.notes || "Oportunidad detectada por el monitoreo activo.";

  return `
    <article class="${featured ? "opportunity-card opportunity-card--featured" : "opportunity-card"}">
      <div class="opportunity-card-head">
        <div>
          <p class="opportunity-kicker">${escapeHtml(repo?.getSourceLabel?.(item.source) || "Auction Watch")}</p>
          <h4>${escapeHtml(item.title || "Oportunidad activa")}</h4>
        </div>
        <span class="opportunity-timer">${escapeHtml(item.remainingText || "-")}</span>
      </div>
      <div class="opportunity-badges">
        ${renderOpportunityBadges(item)}
      </div>
      <p class="opportunity-copy">${escapeHtml(description)}</p>
      <div class="opportunity-meta">
        ${item.priceLabel ? `<span>${escapeHtml(item.priceLabel)}</span>` : ""}
        ${item.groupLabel ? `<span>${escapeHtml(item.groupLabel)}</span>` : ""}
      </div>
      <div class="card-actions">
        ${consoleUrl ? `<a class="btn-link" href="${escapeHtml(consoleUrl)}">Ver consola</a>` : ""}
        ${primaryUrl ? `<a class="btn-link" href="${escapeHtml(primaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(primaryLabel)}</a>` : ""}
        ${secondaryUrl ? `<a class="btn-link" href="${escapeHtml(secondaryUrl)}" target="_blank" rel="noreferrer noopener">${escapeHtml(secondaryLabel)}</a>` : ""}
      </div>
    </article>
  `;
}

function renderHomeOpportunitiesBlock() {
  const repo = window.AuctionWatchRepository;
  const snapshot = repo?.getSnapshot?.();
  const sync = repo?.getSyncState?.() || {
    status: repo?.getSnapshotSource?.() === "server" ? "ready" : "unavailable",
    source: repo?.getSnapshotSource?.() || "none"
  };
  const featured = repo?.getFeaturedOpportunity?.();
  const items = repo?.getHomeOpportunities?.(4) || [];
  const activeCount = (snapshot?.matches?.length || 0) + (featured && !snapshot?.matches?.some((item) => item.id === featured.id) ? 1 : 0);

  if (!repo?.hasData?.()) {
    const verifiedEmpty = sync.status === "empty";
    return `
      <section class="action-queue-group">
        <div class="action-queue-head">
          <strong>Oportunidades activas</strong>
          <span>${verifiedEmpty ? "0 abiertas" : "sin confirmar"}</span>
        </div>
        <p class="muted">${
          verifiedEmpty
            ? "La última corrida confirmada no dejó matches activos."
            : "Auction Watch todavía no confirmó un snapshot vigente. Revisá Oportunidades para ver el estado de sincronización."
        }</p>
      </section>
    `;
  }

  return `
    <section class="action-queue-group">
      <div class="action-queue-head">
        <strong>Oportunidades activas</strong>
        <span>${activeCount} ${sync.status === "stale" ? "en snapshot" : "abiertas"}${snapshot?.generatedAtLabel ? ` • act. ${escapeHtml(snapshot.generatedAtLabel)}` : ""}</span>
      </div>
      ${["stale", "degraded", "unavailable"].includes(sync.status) ? `<p class="muted">Snapshot sin vigencia completa. Revisá Oportunidades antes de decidir.</p>` : ""}
      <div class="opportunity-stack">
        ${featured ? renderOpportunityCard(featured, { featured: true }) : ""}
        <div class="opportunity-list">
          ${items.map((item) => renderOpportunityCard(item)).join("")}
        </div>
      </div>
    </section>
  `;
}

function renderOpportunitiesEntry() {
  const repo = window.AuctionWatchRepository;
  const snapshot = repo?.getSnapshot?.();
  const sync = repo?.getSyncState?.() || { status: "unavailable" };
  const href = window.CollectionRepository?.getOpportunitiesHref?.() || "./opportunities.html";
  const featured = repo?.getFeaturedOpportunity?.();
  const snapshotMatches = snapshot?.matches || [];
  const matches = featured && !snapshotMatches.some((item) => item.id === featured.id) ? [featured, ...snapshotMatches] : snapshotMatches;
  const activeCount = matches.length;
  const watchlistCount = matches.filter((item) => item.watchlist === true).length;
  const featuredLabel = featured?.title || "No hay lote destacado ahora mismo.";
  const updatedLabel = snapshot?.generatedAtLabel
    ? `${sync.status === "stale" ? "Snapshot vencido" : "Actualizado"} ${snapshot.generatedAtLabel}`
    : "Esperando snapshot confirmado del agente";

  return `
    <div class="dashboard-progress-wrap">
      <div class="dashboard-head">
        <h3>Oportunidades</h3>
        <small>${activeCount} activa(s)</small>
      </div>
      <p class="muted">Seguimiento de publicaciones activas de Babastro y Castells, con prioridad para los lotes que más te interesan.</p>
      <div class="mini-grid">
        <article class="mini-card">
          <h4>${escapeHtml(featured ? "Lote destacado" : "Monitoreo activo")}</h4>
          <p class="muted">${escapeHtml(featuredLabel)}</p>
        </article>
        <article class="mini-card">
          <h4>${watchlistCount} seguimiento(s) prioritario(s)</h4>
          <p class="muted">${escapeHtml(updatedLabel)}</p>
        </article>
      </div>
      <div class="card-actions">
        <a class="btn-link btn-primary" href="${escapeHtml(href)}">Oportunidades</a>
      </div>
    </div>
  `;
}

function renderGlobalDashboard(items) {
  const node = document.getElementById("globalDashboard");
  if (!node) return;

  const perConsole = items.map((consoleItem) => {
    const allEntries = getGamesForConsole(consoleItem.id);
    const games = allEntries.filter((g) => !isNonGameEntry(g));
    const extras = allEntries.filter((g) => isNonGameEntry(g));
    const total = games.length;
    const owned = games.filter((g) => normalizeOwnershipType(g.ownershipType, g.loTengo) !== "none").length;
    const available = games.length;
    const standby = games.filter((g) => g.standby === true).length;
    const physical = games.filter((g) => normalizeOwnershipType(g.ownershipType, g.loTengo) === "physical").length;
    const digital = games.filter((g) => normalizeOwnershipType(g.ownershipType, g.loTengo) === "digital").length;
    const both = games.filter((g) => normalizeOwnershipType(g.ownershipType, g.loTengo) === "both").length;
    const progress = available > 0 ? Math.round((owned / available) * 100) : 0;
    return {
      id: consoleItem.id,
      nombre: consoleItem.nombre,
      tengo: consoleItem.tengo === true,
      funcionando: consoleItem.funcionando === true,
      funcionandoRaw: consoleItem.funcionando,
      total,
      owned,
      available,
      standby,
      physical,
      digital,
      both,
      extras: extras.length,
      progress
    };
  });

  const totals = perConsole.reduce(
    (acc, item) => {
      acc.total += item.total;
      acc.owned += item.owned;
      acc.standby += item.standby;
      acc.physical += item.physical;
      acc.digital += item.digital;
      acc.both += item.both;
      acc.extras += item.extras;
      return acc;
    },
    { total: 0, owned: 0, standby: 0, physical: 0, digital: 0, both: 0, extras: 0 }
  );

  const ownedConsoles = items.filter((c) => c.tengo === true).length;
  const operationalConsoles = items.filter((c) => c.tengo === true && c.funcionando === true).length;
  const ownedNotRunning = items.filter((c) => c.tengo === true && c.funcionando === false).length;
  const unknownRunning = items.filter((c) => c.tengo === true && c.funcionando == null).length;
  const alertCount = ownedNotRunning + unknownRunning;

  const ranked = [...perConsole]
    .filter((item) => item.available > 0)
    .sort((a, b) => b.owned - a.owned || b.progress - a.progress || a.nombre.localeCompare(b.nombre));

  const topOwned = ranked
    .filter((item) => item.owned > 0)
    .slice(0, 8)
    .map(
      (item) => `
        <article class="dashboard-progress-item">
          <div class="dashboard-progress-item-header">
            <strong>${item.nombre}</strong>
            <span>${item.owned} registrados</span>
          </div>
          <div class="dashboard-progress-bar"><span style="width:${item.progress}%"></span></div>
        </article>
      `
    )
    .join("");

  const byConsoleInventory = ranked
    .slice(0, 8)
    .map(
      (item) => `
        <article class="dashboard-console-row">
          <div class="dashboard-console-row-head">
            <a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">${item.nombre}</a>
            <span>${item.owned}/${item.total} juegos</span>
          </div>
          <p>${item.physical} físico • ${item.digital} digital • ${item.both} ambos${item.extras ? ` • ${item.extras} extras` : ""}</p>
        </article>
      `
    )
    .join("");

  const operationalAlerts = perConsole
    .filter((item) => item.tengo && item.funcionandoRaw === false)
    .map(
      (item) => `
        <article class="dashboard-alert-row">
          <strong>${item.nombre}</strong>
          <span>Marcada como no funcionando</span>
          <a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">Revisar</a>
        </article>
      `
    )
    .join("");

  const unknownAlerts = perConsole
    .filter((item) => item.tengo && item.funcionandoRaw == null)
    .map(
      (item) => `
        <article class="dashboard-alert-row">
          <strong>${item.nombre}</strong>
          <span>Sin estado de funcionamiento</span>
          <a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">Completar</a>
        </article>
      `
    )
    .join("");

  node.innerHTML = `
    <div class="dashboard-head">
      <h2>Panel operativo de colección</h2>
      <small>estado compacto</small>
    </div>
    <div class="dashboard-kpis">
      <article class="dashboard-kpi"><small>Consolas que tengo</small><strong>${ownedConsoles}</strong><span>inventario actual</span></article>
      <article class="dashboard-kpi"><small>Listas para jugar</small><strong>${operationalConsoles}</strong><span>tengo + funcionando</span></article>
      <article class="dashboard-kpi"><small>Alertas</small><strong>${alertCount}</strong><span>requieren revisión</span></article>
      <article class="dashboard-kpi"><small>Juegos registrados</small><strong>${totals.owned}</strong><span>solo juegos reales</span></article>
    </div>
  `;

  const insightsNode = document.getElementById("collectionInsights");
  if (!insightsNode) return;

  insightsNode.innerHTML = `
    <div class="dashboard-head">
      <h2>Biblioteca y seguimiento</h2>
      <small>después de la colección activa</small>
    </div>
    <div class="dashboard-progress-wrap">
      <div class="dashboard-head">
        <h3>Alertas operativas</h3>
        <small>${alertCount} alerta(s) operativa(s)</small>
      </div>
      <div class="dashboard-alerts">
        ${operationalAlerts || ""}
        ${unknownAlerts || ""}
        ${!operationalAlerts && !unknownAlerts ? '<p class="muted">No hay alertas operativas por ahora.</p>' : ""}
      </div>
    </div>
    ${renderOpportunitiesEntry()}
    <div class="dashboard-progress-wrap">
      <div class="dashboard-head">
        <h3>Biblioteca por consola</h3>
        <small>top 8</small>
      </div>
      <div class="dashboard-console-list">
        ${byConsoleInventory || '<p class="muted">Sin consolas con catálogo cargado.</p>'}
      </div>
      <p class="dashboard-footnote">Extras no-juego detectados (demos/apps/soundtracks): ${totals.extras}</p>
    </div>
    <details class="dashboard-progress-wrap">
      <summary>Top consolas por juegos registrados</summary>
      <div class="dashboard-progress-list">
        ${topOwned || '<p class="muted">Todavía no hay juegos registrados en consolas con catálogo.</p>'}
      </div>
    </details>
  `;
}

function updateSelectOptions(items) {
  const brandFilter = document.getElementById("brandFilter");
  const generationFilter = document.getElementById("generationFilter");
  const statusFilter = document.getElementById("statusFilter");

  const unique = (arr) => [...new Set(arr.filter(Boolean))].sort((a, b) => a.localeCompare(b));

  const fillSelect = (select, values) => {
    const first = select.options[0];
    select.innerHTML = "";
    select.appendChild(first);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  };

  fillSelect(brandFilter, unique(items.map((i) => i.fabricante)));
  fillSelect(generationFilter, unique(items.map((i) => i.generacion)));
  fillSelect(statusFilter, unique(items.map((i) => i.estado)));
}

function cardMarkup(item) {
  const template = document.getElementById("consoleCardTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  node.classList.add(item.tengo ? "owned" : "wanted");
  const image = node.querySelector("img");
  const imageSrc = window.CollectionRepository?.getConsoleImage?.(item, fallbackImage) || fallbackImage;

  image.src = imageSrc;
  image.alt = `Imagen representativa de ${item.nombre}`;
  image.onerror = () => {
    image.onerror = null;
    image.src = fallbackImage;
  };

  node.querySelector("h3").textContent = item.nombre;
  node.querySelector(".badge").textContent = item.fabricante;

  const meta = `${item.generacion || "Gen. N/D"} • ${item.anioLanzamiento || "Año N/D"}`;
  node.querySelector(".meta").textContent = meta;

  node.querySelector(".state").textContent = `Estado: ${item.estado || "N/D"} • Funcionando: ${yesNo(item.funcionando)}`;

  const chipsContainer = node.querySelector(".chips");
  const catalogGames = getGamesForConsole(item.id).filter((game) => !isNonGameEntry(game));
  const gameCount = catalogGames.length || item.juegos?.length || 0;
  const chips = [
    item.tengo ? "En colección" : "Wishlist",
    `Tengo: ${yesNo(item.tengo)}`,
    `Accesorios: ${item.accesorios?.length || 0}`,
    `Juegos: ${gameCount}`
  ];
  chips.forEach((text) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = text;
    chipsContainer.appendChild(chip);
  });

  node.querySelector(".notes").textContent = item.notas || "Sin notas";
  node.querySelector(".price-reference").innerHTML = renderPriceReference(getPriceModel(item), item);

  const priceRow = node.querySelector(".price-row");
  priceRow.innerHTML = item.tengo ? `<span>Pagado: ${formatPrice(item.precioPagado, item.monedaPago)}</span>` : "";

  const detailLink = node.querySelector(".detail-link");
  detailLink.href = window.CollectionRepository.getConsoleDetailHref(item.id);

  const actions = node.querySelector(".card-actions");
  if (item.tengo === false || (item.categoria || "").toLowerCase() === "wishlist") {
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn-link js-remove-wishlist-console";
    removeBtn.dataset.id = item.id;
    removeBtn.textContent = "Quitar de wishlist";
    actions.prepend(removeBtn);
  }

  return node;
}

function isRegisteredConsole(item = {}) {
  return item.tengo === true || (item.categoria || "").toLowerCase() === "coleccion";
}

function isWishlistConsole(item = {}) {
  return !isRegisteredConsole(item);
}

function renderSection(list, containerId, countId) {
  const container = document.getElementById(containerId);
  const count = document.getElementById(countId);

  container.innerHTML = "";
  count.textContent = `${list.length} resultado(s)`;

  if (!list.length) {
    container.innerHTML = '<div class="empty">No hay resultados con los filtros actuales.</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  list.forEach((item) => fragment.appendChild(cardMarkup(item)));
  container.appendChild(fragment);
}

function getGroupMeta(item, groupBy) {
  if (groupBy === "brand") {
    return {
      key: item.fabricante || "Sin marca",
      label: item.fabricante || "Sin marca"
    };
  }
  if (groupBy === "ownership") {
    return {
      key: item.tengo === true ? "have" : "want",
      label: item.tengo === true ? "Tengo" : "No tengo"
    };
  }
  return {
    key: "all",
    label: "Todas"
  };
}

function renderGroupedSection(list, containerId, countId, groupBy) {
  const container = document.getElementById(containerId);
  const count = document.getElementById(countId);

  container.innerHTML = "";
  count.textContent = `${list.length} resultado(s)`;

  if (!list.length) {
    container.innerHTML = '<div class="empty">No hay resultados con los filtros actuales.</div>';
    return;
  }

  const groupedMap = new Map();
  list.forEach((item) => {
    const meta = getGroupMeta(item, groupBy);
    if (!groupedMap.has(meta.key)) {
      groupedMap.set(meta.key, { label: meta.label, items: [] });
    }
    groupedMap.get(meta.key).items.push(item);
  });

  let entries = [...groupedMap.entries()];
  if (groupBy === "ownership") {
    const rank = { have: 0, want: 1 };
    entries = entries.sort((a, b) => (rank[a[0]] ?? 99) - (rank[b[0]] ?? 99));
  } else {
    entries = entries.sort((a, b) => a[1].label.localeCompare(b[1].label));
  }

  const fragment = document.createDocumentFragment();
  entries.forEach(([_, group], idx) => {
    const details = document.createElement("details");
    details.className = "generation-group";
    details.open = idx < 2;

    const summary = document.createElement("summary");
    summary.innerHTML = `<strong>${group.label}</strong><span>${group.items.length} consola(s)</span>`;
    details.appendChild(summary);

    const wrap = document.createElement("div");
    wrap.className = "generation-list";
    group.items.forEach((item) => wrap.appendChild(cardMarkup(item)));
    details.appendChild(wrap);

    fragment.appendChild(details);
  });

  container.appendChild(fragment);
}

function bindCardActions() {
  document.querySelectorAll(".js-remove-wishlist-console").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.id;
      if (!id) return;
      removeConsoleFromWishlist(id);
    });
  });
}

function applyFilters() {
  const { search, brand, generation, status, quick, noHaveSub, sort } = state.filters;

  const result = state.allConsoles.filter((item) => {
    const searchableText = normalize(
      [
        item.nombre,
        item.fabricante,
        item.generacion,
        item.estado,
        item.notas,
        (item.accesorios || []).join(" "),
        (item.juegos || []).join(" ")
      ].join(" ")
    );

    const matchesSearch = !search || searchableText.includes(normalize(search));
    const matchesBrand = !brand || item.fabricante === brand;
    const matchesGeneration = !generation || item.generacion === generation;
    const matchesStatus = !status || item.estado === status;
    const matchesQuick =
      quick === "all" || (quick === "have" ? item.tengo === true : item.tengo === false);
    const matchesNoHaveSub =
      quick !== "want" || !noHaveSub || (item.categoria || "").toLowerCase() === noHaveSub;

    return (
      matchesSearch &&
      matchesBrand &&
      matchesGeneration &&
      matchesStatus &&
      matchesQuick &&
      matchesNoHaveSub
    );
  });

  const sorted = [...result].sort((a, b) => {
    if (sort === "name-asc") return a.nombre.localeCompare(b.nombre);

    const yearA = Number(a.anioLanzamiento) || (sort === "year-asc" ? 9999 : -1);
    const yearB = Number(b.anioLanzamiento) || (sort === "year-asc" ? 9999 : -1);
    if (yearA !== yearB) return sort === "year-desc" ? yearB - yearA : yearA - yearB;
    return a.nombre.localeCompare(b.nombre);
  });

  state.filtered = sorted;
  const registered = sorted.filter((item) => isRegisteredConsole(item));
  const wishlist = sorted.filter((item) => isWishlistConsole(item));

  if (state.filters.groupBy === "none") {
    renderSection(registered, "registeredGrid", "registeredCount");
    renderSection(wishlist, "wishlistGrid", "wishlistCount");
  } else {
    renderGroupedSection(registered, "registeredGrid", "registeredCount", state.filters.groupBy);
    renderGroupedSection(wishlist, "wishlistGrid", "wishlistCount", state.filters.groupBy);
  }
  bindCardActions();
}

function updateNoHaveSubFilterVisibility() {
  const wrap = document.getElementById("noHaveSubFilterWrap");
  if (!wrap) return;
  wrap.hidden = state.filters.quick !== "want";
}

function bindControls() {
  document.getElementById("search").addEventListener("input", (event) => {
    state.filters.search = event.target.value;
    applyFilters();
  });

  document.getElementById("brandFilter").addEventListener("change", (event) => {
    state.filters.brand = event.target.value;
    applyFilters();
  });

  document.getElementById("generationFilter").addEventListener("change", (event) => {
    state.filters.generation = event.target.value;
    applyFilters();
  });

  document.getElementById("statusFilter").addEventListener("change", (event) => {
    state.filters.status = event.target.value;
    applyFilters();
  });

  document.getElementById("quickFilter").addEventListener("change", (event) => {
    state.filters.quick = event.target.value;
    if (state.filters.quick !== "want") {
      state.filters.noHaveSub = "";
      document.getElementById("noHaveSubFilter").value = "";
    }
    updateNoHaveSubFilterVisibility();
    applyFilters();
  });

  document.getElementById("noHaveSubFilter").addEventListener("change", (event) => {
    state.filters.noHaveSub = event.target.value;
    applyFilters();
  });

  document.getElementById("sortFilter").addEventListener("change", (event) => {
    state.filters.sort = event.target.value;
    applyFilters();
  });

  document.getElementById("groupByFilter").addEventListener("change", (event) => {
    state.filters.groupBy = event.target.value;
    applyFilters();
  });
}

async function init() {
  try {
    await Promise.resolve(window.DataStore?.ready);
    const [consolesResponse, gamesResponse, accessoriesResponse] = await Promise.all([
      fetch("./data/consoles.json"),
      fetch("./data/console-games.json"),
      fetch("./data/console-accessories.json")
    ]);
    if (!consolesResponse.ok) throw new Error("No se pudo cargar data/consoles.json");
    if (!gamesResponse.ok) throw new Error("No se pudo cargar data/console-games.json");
    if (!accessoriesResponse.ok) throw new Error("No se pudo cargar data/console-accessories.json");

    const payload = await consolesResponse.json();
    const gamesPayload = await gamesResponse.json();
    const accessoriesPayload = await accessoriesResponse.json();
    const auctionWatchRefresh = loadAuctionWatchSnapshot().catch((error) => {
      console.info("[AuctionWatch] home refresh unavailable", error);
      return null;
    });
    state.gamesByConsole = Object.fromEntries(
      Object.entries(gamesPayload.byConsole || {}).map(([consoleId, entry]) => [consoleId, Array.isArray(entry?.juegosCatalogo) ? entry.juegosCatalogo : []])
    );
    const accessoriesByConsole = Object.fromEntries(
      Object.entries(accessoriesPayload.byConsole || {}).map(([consoleId, entry]) => [
        consoleId,
        Array.isArray(entry?.accessoriesCatalog) ? entry.accessoriesCatalog : []
      ])
    );
    window.CollectionRepository.migrateAllEntityState({
      gamesByConsole: state.gamesByConsole,
      accessoriesByConsole
    });
    const merged = mergeWithAdditions(payload.consolas || []);
    state.allConsoles = applyDetailEdits(applyOverrides(merged));

    renderSummary(state.allConsoles);
    renderGlobalDashboard(state.allConsoles);
    updateSelectOptions(state.allConsoles);
    updateNoHaveSubFilterVisibility();
    bindControls();
    applyFilters();
    auctionWatchRefresh.then(() => {
      renderGlobalDashboard(state.allConsoles);
    });
  } catch (error) {
    document.body.innerHTML = `<main class="container"><p>Error al cargar la colección: ${error.message}</p></main>`;
  }
}

init();
