const GENERATION_ORDER = [
  "Pre-generacion",
  "Generacion 2",
  "Generacion 3",
  "Generacion 4",
  "Generacion 5",
  "Generacion 6",
  "Generacion 7",
  "Generacion 8",
  "Generacion 9",
  "Niche"
];

const fallbackImage = "./assets/photos/console-placeholder.svg";

const state = {
  all: [],
  filtered: [],
  personalById: {},
  personalAdditions: {},
  overrides: {},
  filters: {
    search: "",
    brand: "",
    generation: "",
    type: "",
    viewMode: "chronological",
    mine: ""
  }
};

const normalize = (text = "") => window.CollectionRepository.normalizeText(text);

function sortChronological(items) {
  return [...items].sort((a, b) => {
    const yearA = Number(a.anioLanzamiento) || 9999;
    const yearB = Number(b.anioLanzamiento) || 9999;
    if (yearA !== yearB) return yearA - yearB;
    return a.nombre.localeCompare(b.nombre);
  });
}

function generationRank(generation) {
  const index = GENERATION_ORDER.indexOf(generation);
  return index === -1 ? 999 : index;
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function readOverrides() {
  return window.CollectionRepository.readOverrides();
}

function writeOverrides() {
  window.DataStore?.setOverrides?.(state.overrides || {});
}

function readAdditions() {
  return window.CollectionRepository.readAdditionsMap();
}

function writeAdditions() {
  window.DataStore?.setAdditionsMap?.(state.personalAdditions || {});
}

function mergePersonalById(basePersonal, additions) {
  return {
    ...basePersonal,
    ...additions
  };
}

function resolvePersonalId(item) {
  return window.CollectionRepository.getPersonalIdForBase(item.id);
}

function createPersonalFromBase(item, personalId, tengo) {
  return {
    id: personalId,
    nombre: item.nombre,
    fabricante: item.marca,
    generacion: item.generacion,
    anioLanzamiento: item.anioLanzamiento || null,
    tengo,
    estado: tengo ? "Pendiente de carga" : "Buscando",
    funcionando: null,
    accesorios: [],
    juegos: [],
    notas: "Alta rápida desde base general. Completar detalle.",
    precioPriceChart: null,
    precioGameStop: null,
    precioEbaySold: null,
    precioCIB: null,
    precioObjetivoCompra: null,
    precioPagado: null,
    fotos: item.fotos?.length ? [item.fotos[0]] : [],
    fotosPropias: [],
    oportunidades: [],
    priceChartingUrl: null,
    categoria: tengo ? "coleccion" : "wishlist"
  };
}

function statusForBase(item) {
  const personalId = resolvePersonalId(item);

  const personal = state.personalById[personalId] || null;
  const override = state.overrides[personalId] || null;

  if (override?.removedFromWishlist === true) {
    return { key: "unknown", label: "No en catálogo", personalId, personal: null };
  }

  const tengo = override?.tengo ?? personal?.tengo;
  if (tengo === true) {
    return { key: "owned", label: "En colección", personalId, personal };
  }
  if (tengo === false) {
    return { key: "wanted", label: "En wishlist", personalId, personal };
  }

  return { key: "unknown", label: personal ? "No registrada" : "No en catálogo", personalId, personal };
}

function imageForBase(item, statusInfo) {
  const personal = statusInfo.personal;
  return personal?.fotos?.[0] || item.fotos?.[0] || personal?.fotosPropias?.[0] || fallbackImage;
}

function setPersonalOverride(personalId, tengo) {
  state.overrides[personalId] = {
    tengo,
    removedFromWishlist: false,
    categoria: tengo ? "coleccion" : "wishlist",
    updatedAt: new Date().toISOString()
  };
  writeOverrides();
  applyFilters();
}

function clearPersonalOverride(personalId) {
  delete state.overrides[personalId];
  writeOverrides();
  applyFilters();
}

function ensurePersonalEntry(item, personalId, tengo) {
  if (state.personalById[personalId]) return;
  state.personalAdditions[personalId] = createPersonalFromBase(item, personalId, tengo);
  writeAdditions();
  state.personalById = mergePersonalById(state.personalById, state.personalAdditions);
}

function removeWantedFromBase(item, personalId) {
  if (!personalId) return;

  if (state.personalAdditions[personalId]) {
    delete state.personalAdditions[personalId];
    writeAdditions();
  }

  state.overrides[personalId] = {
    removedFromWishlist: true,
    updatedAt: new Date().toISOString()
  };
  writeOverrides();
  state.personalById = mergePersonalById(state.personalById, state.personalAdditions);
  applyFilters();
}

function renderSummary(items) {
  const el = document.getElementById("dbSummary");
  const hogares = items.filter((c) => c.tipo === "hogar").length;
  const portatiles = items.filter((c) => c.tipo === "portatil").length;
  const marcas = new Set(items.map((c) => c.marca)).size;

  el.innerHTML = `
    <article class="stat"><small>Total consolas</small><strong>${items.length}</strong></article>
    <article class="stat"><small>Hogar</small><strong>${hogares}</strong></article>
    <article class="stat"><small>Portátiles</small><strong>${portatiles}</strong></article>
    <article class="stat"><small>Marcas</small><strong>${marcas}</strong></article>
  `;
}

function fillSelect(selectId, values, firstLabel) {
  const select = document.getElementById(selectId);
  select.innerHTML = `<option value="">${firstLabel}</option>`;
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
}

function renderBrandNav(brands) {
  const nav = document.getElementById("brandNav");
  nav.innerHTML = "";

  const allButton = document.createElement("button");
  allButton.type = "button";
  allButton.className = `brand-pill ${state.filters.brand === "" ? "active" : ""}`;
  allButton.textContent = "Todas";
  allButton.addEventListener("click", () => {
    state.filters.brand = "";
    document.getElementById("dbBrandFilter").value = "";
    applyFilters();
  });
  nav.appendChild(allButton);

  brands.forEach((brand) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `brand-pill ${state.filters.brand === brand ? "active" : ""}`;
    button.textContent = brand;
    button.addEventListener("click", () => {
      state.filters.brand = brand;
      document.getElementById("dbBrandFilter").value = brand;
      applyFilters();
    });
    nav.appendChild(button);
  });
}

function makeCard(item) {
  const template = document.getElementById("dbCardTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  const statusInfo = statusForBase(item);

  node.classList.add(`db-${statusInfo.key}`);

  node.querySelector("h3").textContent = item.nombre;
  node.querySelector(".meta").textContent = `${item.marca} • ${item.generacion}`;
  node.querySelector(".notes").textContent = item.notas || "Sin notas.";
  node.querySelector(".year").textContent = item.anioLanzamiento || "N/D";
  node.querySelector(".type").textContent = item.tipo === "portatil" ? "Portátil" : "Hogar";
  node.querySelector(".generation").textContent = item.generacion;
  node.querySelector(".owner-status").textContent = statusInfo.label;

  const image = node.querySelector("img");
  image.src = imageForBase(item, statusInfo);
  image.alt = `Imagen de ${item.nombre}`;
  image.onerror = () => {
    image.onerror = null;
    image.src = fallbackImage;
  };

  const rev = item.revisiones && item.revisiones.length
    ? `Revisiones: ${item.revisiones.join(" • ")}`
    : "Revisiones: no registradas";
  node.querySelector(".revisions").textContent = rev;

  const btnOwned = node.querySelector(".js-mark-owned");
  const btnWanted = node.querySelector(".js-mark-wanted");
  const openPersonal = node.querySelector(".js-open-personal");
  const hasPersonalRecord = Boolean(state.personalById[statusInfo.personalId]);

  if (statusInfo.personalId) {
    btnOwned.addEventListener("click", () => {
      ensurePersonalEntry(item, statusInfo.personalId, true);
      setPersonalOverride(statusInfo.personalId, true);
    });
    btnWanted.addEventListener("click", () => {
      if (statusInfo.key === "wanted") {
        removeWantedFromBase(item, statusInfo.personalId);
        return;
      }
      ensurePersonalEntry(item, statusInfo.personalId, false);
      setPersonalOverride(statusInfo.personalId, false);
    });
    openPersonal.href = window.CollectionRepository.getConsoleDetailHref(statusInfo.personalId);
    openPersonal.style.display = hasPersonalRecord ? "inline-flex" : "none";
    btnOwned.textContent = "Marcar tengo";
    btnWanted.textContent = statusInfo.key === "wanted" ? "Quitar de wishlist" : "Marcar quiero";
  }

  return node;
}

function renderChronological(items) {
  const container = document.getElementById("dbResults");
  container.innerHTML = "";

  if (!items.length) {
    container.innerHTML = '<div class="empty">No hay resultados para esos filtros.</div>';
    return;
  }

  const frag = document.createDocumentFragment();
  items.forEach((item) => frag.appendChild(makeCard(item)));
  container.appendChild(frag);
}

function renderGrouped(items) {
  const container = document.getElementById("dbResults");
  container.innerHTML = "";

  if (!items.length) {
    container.innerHTML = '<div class="empty">No hay resultados para esos filtros.</div>';
    return;
  }

  const grouped = items.reduce((acc, item) => {
    if (!acc[item.generacion]) acc[item.generacion] = [];
    acc[item.generacion].push(item);
    return acc;
  }, {});

  const generations = Object.keys(grouped).sort((a, b) => generationRank(a) - generationRank(b));

  const frag = document.createDocumentFragment();
  generations.forEach((generation, idx) => {
    const group = document.createElement("details");
    group.className = "generation-group";
    group.open = idx < 2;

    const summary = document.createElement("summary");
    summary.innerHTML = `<strong>${generation}</strong><span>${grouped[generation].length} consola(s)</span>`;
    group.appendChild(summary);

    const list = document.createElement("div");
    list.className = "generation-list";
    sortChronological(grouped[generation]).forEach((item) => list.appendChild(makeCard(item)));

    group.appendChild(list);
    frag.appendChild(group);
  });

  container.appendChild(frag);
}

function applyFilters() {
  const { search, brand, generation, type, viewMode, mine } = state.filters;

  const result = state.all.filter((item) => {
    const searchable = normalize([
      item.nombre,
      item.marca,
      item.generacion,
      item.tipo,
      item.notas,
      (item.revisiones || []).join(" ")
    ].join(" "));

    const status = statusForBase(item).key;

    const matchSearch = !search || searchable.includes(normalize(search));
    const matchBrand = !brand || item.marca === brand;
    const matchGeneration = !generation || item.generacion === generation;
    const matchType = !type || item.tipo === type;
    const matchMine = !mine || status === mine;

    return matchSearch && matchBrand && matchGeneration && matchType && matchMine;
  });

  const sorted = sortChronological(result);
  state.filtered = sorted;

  document.getElementById("dbCount").textContent = `${sorted.length} resultado(s)`;
  document.getElementById("groupActions").hidden = viewMode !== "grouped";

  if (viewMode === "grouped") {
    renderGrouped(sorted);
  } else {
    renderChronological(sorted);
  }

  renderBrandNav(uniqueSorted(state.all.map((c) => c.marca)));
}

function bindControls() {
  document.getElementById("dbSearch").addEventListener("input", (event) => {
    state.filters.search = event.target.value;
    applyFilters();
  });

  document.getElementById("dbBrandFilter").addEventListener("change", (event) => {
    state.filters.brand = event.target.value;
    applyFilters();
  });

  document.getElementById("dbGenerationFilter").addEventListener("change", (event) => {
    state.filters.generation = event.target.value;
    applyFilters();
  });

  document.getElementById("dbTypeFilter").addEventListener("change", (event) => {
    state.filters.type = event.target.value;
    applyFilters();
  });

  document.getElementById("dbViewMode").addEventListener("change", (event) => {
    state.filters.viewMode = event.target.value;
    applyFilters();
  });

  document.getElementById("dbMineFilter").addEventListener("change", (event) => {
    state.filters.mine = event.target.value;
    applyFilters();
  });

  document.getElementById("expandAllBtn").addEventListener("click", () => {
    document.querySelectorAll(".generation-group").forEach((group) => {
      group.open = true;
    });
  });

  document.getElementById("collapseAllBtn").addEventListener("click", () => {
    document.querySelectorAll(".generation-group").forEach((group) => {
      group.open = false;
    });
  });
}

async function init() {
  try {
    await Promise.resolve(window.DataStore?.ready);
    const [baseRes, personalRes] = await Promise.all([
      fetch("./data/consoles-base.json"),
      fetch("./data/consoles.json")
    ]);

    if (!baseRes.ok) throw new Error("No se pudo cargar la base general de consolas.");

    const basePayload = await baseRes.json();
    const personalPayload = personalRes.ok ? await personalRes.json() : { consolas: [] };

    state.all = basePayload.consolas || [];
    const basePersonal = Object.fromEntries((personalPayload.consolas || []).map((c) => [c.id, c]));
    state.personalAdditions = readAdditions();
    state.personalById = mergePersonalById(basePersonal, state.personalAdditions);
    state.overrides = readOverrides();

    fillSelect("dbBrandFilter", uniqueSorted(state.all.map((c) => c.marca)), "Todas");
    fillSelect(
      "dbGenerationFilter",
      uniqueSorted(state.all.map((c) => c.generacion)).sort((a, b) => generationRank(a) - generationRank(b)),
      "Todas"
    );

    renderSummary(state.all);
    bindControls();
    applyFilters();
  } catch (error) {
    document.getElementById("dbResults").innerHTML = `<div class="empty">Error: ${error.message}</div>`;
  }
}

init();
