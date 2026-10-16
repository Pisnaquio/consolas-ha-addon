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
  const collection = items.filter((i) => isRegisteredConsole(i));
  const wishlist = items.filter((i) => isWishlistConsole(i));
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
      updateSelectOptions(state.allConsoles);
    updateNoHaveSubFilterVisibility();
    bindControls();
    applyFilters();
  } catch (error) {
    document.body.innerHTML = `<main class="container"><p>Error al cargar la colección: ${error.message}</p></main>`;
  }
}

init();
