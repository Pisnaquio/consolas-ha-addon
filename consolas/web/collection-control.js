(() => {
  const fallbackImage = "./assets/photos/console-placeholder.svg";
  const generationRanks = Array.from({ length: 8 }, (_, index) => index + 2);
  const state = { consoles: [], showUnowned: false, selectedDebugConsoleId: "" };

  const escapeHtml = (value = "") => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const formatDateTime = (value) => {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "Sin dato";
    return new Intl.DateTimeFormat("es-AR", {
      dateStyle: "short",
      timeStyle: "short"
    }).format(date);
  };

  function downloadTextFile(filename, text) {
    const blob = new Blob([text], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "readonly");
    input.style.position = "absolute";
    input.style.left = "-9999px";
    document.body.append(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }

  function getDataStoreState() {
    return window.DataStore?.getState?.() || { user: {}, meta: {} };
  }

  function getConsoleDebugPayload(consoleId) {
    const storeState = getDataStoreState();
    const overrides = storeState.user?.overridesById || {};
    const additions = storeState.user?.additionsById || {};
    const edits = storeState.user?.detailEditsById || {};
    const consoleItem = state.consoles.find((item) => item.id === consoleId) || null;
    const detailEdit = edits[consoleId] || {};
    const entityState = window.CollectionRepository.getConsoleEntityState(consoleId);
    return {
      consoleId,
      consoleName: consoleItem?.nombre || consoleId,
      catalogId: consoleItem?.catalogId || null,
      meta: {
        hasOverride: Boolean(overrides[consoleId]),
        hasAddition: Boolean(additions[consoleId]),
        hasDetailEdit: Boolean(edits[consoleId]),
        accessoryEditCount: Object.keys(entityState.accessoryEditsById || {}).length,
        manualAccessoryCount: Object.keys(entityState.manualAccessoriesById || {}).length,
        gameEditCount: Object.keys(entityState.gameEditsById || {}).length,
        manualGameCount: Object.keys(entityState.manualGamesById || {}).length
      },
      override: overrides[consoleId] || null,
      addition: additions[consoleId] || null,
      detailEdit,
      entityState
    };
  }

  function buildExportPayload() {
    const storeState = getDataStoreState();
    return {
      exportedAt: new Date().toISOString(),
      app: "consolas",
      version: storeState.version || 3,
      state: storeState
    };
  }

  function persistenceMetrics() {
    const storeState = getDataStoreState();
    const overrides = storeState.user?.overridesById || {};
    const additions = storeState.user?.additionsById || {};
    const edits = storeState.user?.detailEditsById || {};
    const detailEntries = Object.values(edits || {});
    const consolesWithState = new Set([...Object.keys(overrides), ...Object.keys(additions), ...Object.keys(edits)]);
    const manualEntityCount = detailEntries.reduce((total, item) => {
      const manualAccessories = Object.keys(item?.manualAccessoriesById || {}).length;
      const manualGames = Object.keys(item?.manualGamesById || {}).length;
      return total + manualAccessories + manualGames;
    }, Object.keys(additions).length);
    return {
      backend: storeState.meta?.storageBackend || "Sin dato",
      updatedAt: storeState.meta?.updatedAt || "",
      consoleCount: consolesWithState.size,
      manualEntityCount,
      overrideCount: Object.keys(overrides).length,
      additionCount: Object.keys(additions).length,
      detailEditCount: Object.keys(edits).length
    };
  }

  function renderPersistencePanel() {
    const metrics = persistenceMetrics();
    const exportPayload = buildExportPayload();
    document.getElementById("storageBackendValue").textContent = metrics.backend;
    document.getElementById("storageBackendHint").textContent =
      metrics.backend === "server"
        ? "Servidor compartido activo: el estado vive en HA/SQLite"
        : metrics.backend === "indexeddb"
          ? "Persistencia local en este navegador con shadow local"
          : "Fallback local activo";
    document.getElementById("storageUpdatedAtValue").textContent = formatDateTime(metrics.updatedAt);
    document.getElementById("storageUpdatedAtHint").textContent = metrics.updatedAt || "Sin writes detectados";
    document.getElementById("persistedConsoleCount").textContent = String(metrics.consoleCount);
    document.getElementById("persistedConsoleHint").textContent = `${metrics.overrideCount} overrides · ${metrics.detailEditCount} buckets`;
    document.getElementById("manualEntityCount").textContent = String(metrics.manualEntityCount);
    document.getElementById("manualEntityHint").textContent = `${metrics.additionCount} consolas manuales + juegos/accesorios`;
    document.getElementById("globalDebugStats").innerHTML = [
      `<span class="debug-chip">version ${escapeHtml(String(exportPayload.version || 3))}</span>`,
      `<span class="debug-chip">backend ${escapeHtml(metrics.backend)}</span>`,
      `<span class="debug-chip">${metrics.consoleCount} consolas con estado</span>`,
      `<span class="debug-chip">${metrics.manualEntityCount} manuales</span>`
    ].join("");
    document.getElementById("globalDebugOutput").textContent = JSON.stringify(exportPayload, null, 2);
    renderConsoleInspector();
  }

  function renderConsoleInspector() {
    const select = document.getElementById("consoleDebugSelect");
    const selectedId = state.selectedDebugConsoleId || select.value || state.consoles[0]?.id || "";
    if (!selectedId) {
      document.getElementById("consoleDebugStats").innerHTML = "";
      document.getElementById("consoleDebugOutput").textContent = "Sin consolas disponibles.";
      return;
    }
    state.selectedDebugConsoleId = selectedId;
    select.value = selectedId;
    const payload = getConsoleDebugPayload(selectedId);
    document.getElementById("consoleDebugStats").innerHTML = [
      `<span class="debug-chip">${payload.consoleName}</span>`,
      `<span class="debug-chip">${payload.meta.accessoryEditCount} edits accesorios</span>`,
      `<span class="debug-chip">${payload.meta.manualAccessoryCount} accesorios manuales</span>`,
      `<span class="debug-chip">${payload.meta.gameEditCount} edits juegos</span>`,
      `<span class="debug-chip">${payload.meta.manualGameCount} juegos manuales</span>`
    ].join("");
    document.getElementById("consoleDebugOutput").textContent = JSON.stringify(payload, null, 2);
  }

  function populateConsoleInspectorSelect() {
    const select = document.getElementById("consoleDebugSelect");
    const options = state.consoles
      .slice()
      .sort((a, b) => a.nombre.localeCompare(b.nombre))
      .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.nombre)}${item.catalogId ? ` · ${escapeHtml(item.catalogId)}` : ""}</option>`)
      .join("");
    select.innerHTML = options;
    if (!state.selectedDebugConsoleId && state.consoles[0]?.id) {
      state.selectedDebugConsoleId = state.consoles[0].id;
    }
  }

  function bindPersistenceEvents() {
    document.getElementById("refreshPersistenceBtn").addEventListener("click", () => {
      renderPersistencePanel();
    });

    document.getElementById("consoleDebugSelect").addEventListener("change", (event) => {
      state.selectedDebugConsoleId = event.target.value;
      renderConsoleInspector();
    });

    document.getElementById("downloadBackupBtn").addEventListener("click", async () => {
      const payload = await (window.DataStore?.exportStateBackup?.() || buildExportPayload());
      const source = payload?.source || "local";
      const filename = `consolas-user-backup-${source}-${new Date().toISOString().replaceAll(":", "-")}.json`;
      downloadTextFile(filename, JSON.stringify(payload, null, 2));
    });

    document.getElementById("copyBackupBtn").addEventListener("click", async () => {
      const payload = await (window.DataStore?.exportStateBackup?.() || buildExportPayload());
      await copyText(JSON.stringify(payload, null, 2));
    });

    document.getElementById("copyConsoleDebugBtn").addEventListener("click", async () => {
      await copyText(JSON.stringify(getConsoleDebugPayload(state.selectedDebugConsoleId), null, 2));
    });

    document.getElementById("copyGlobalDebugBtn").addEventListener("click", async () => {
      await copyText(JSON.stringify(buildExportPayload(), null, 2));
    });
  }

  function consoleIdentity(name = "") {
    return window.CollectionRepository.normalizeText(name)
      .toLowerCase()
      .replace(/\b(original|snes|cx[-\s]?2600)\b/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function dedupeConsoles(items) {
    const byIdentity = new Map();
    items.forEach((item) => {
      const key = item.catalogId || item.catalogBaseId || consoleIdentity(item.nombre);
      const existing = byIdentity.get(key);
      if (!existing || (item.hasPersonalRecord === true && existing.hasPersonalRecord !== true)) {
        byIdentity.set(key, item);
      }
    });
    return [...byIdentity.values()];
  }

  function buildControlConsoles(base, personalBase, guides) {
    const additions = window.CollectionRepository.readAdditionsArray();
    const personalById = Object.fromEntries([...personalBase, ...additions].filter((item) => item?.id).map((item) => [item.id, item]));
    const overrides = window.CollectionRepository.readOverrides();
    const edits = window.CollectionRepository.readDetailEdits();
    const consumedPersonalIds = new Set();

    const catalogConsoles = base.map((catalogItem) => {
      const personalId = window.CollectionRepository.getPersonalIdForBase(catalogItem.id);
      const personal = personalById[personalId] || {};
      const edit = edits[personalId] || {};
      consumedPersonalIds.add(personalId);
      const tengo = typeof overrides[personalId]?.tengo === "boolean" ? overrides[personalId].tengo : personal.tengo === true;
      return {
        id: personalId,
        catalogId: catalogItem.id,
        hasPersonalRecord: Boolean(personalById[personalId]),
        nombre: catalogItem.nombre,
        fabricante: catalogItem.marca,
        generacion: catalogItem.generacion,
        anioLanzamiento: catalogItem.anioLanzamiento,
        collectionGuide: guides[catalogItem.id] || null,
        fotos: catalogItem.fotos || [],
        tengo,
        removedFromWishlist: overrides[personalId]?.removedFromWishlist === true,
        ...personal,
        ...edit,
        tengo,
        fotosPropias: Array.isArray(edit.fotosPropias) ? edit.fotosPropias : personal.fotosPropias || []
      };
    });

    const manualOnly = [...personalBase, ...additions]
      .filter(
        (item) =>
          item?.id &&
          !consumedPersonalIds.has(item.id) &&
          !catalogConsoles.some((catalogItem) => consoleIdentity(catalogItem.nombre) === consoleIdentity(item.nombre))
      )
      .map((item) => {
        const edit = edits[item.id] || {};
        return {
          ...item,
          ...edit,
          hasPersonalRecord: true,
          tengo: typeof overrides[item.id]?.tengo === "boolean" ? overrides[item.id].tengo : item.tengo === true,
          removedFromWishlist: overrides[item.id]?.removedFromWishlist === true,
          fotosPropias: Array.isArray(edit.fotosPropias) ? edit.fotosPropias : item.fotosPropias || []
        };
      });

    return dedupeConsoles([...catalogConsoles, ...manualOnly]);
  }

  function generationRank(generation = "") {
    if (generation === "Pre-generacion") return 0;
    if (generation === "Niche") return 11;
    return Number(String(generation).match(/\d+/)?.[0]) || 99;
  }

  function generationLabel(generation = "") {
    if (String(generation) === "0") return "Pre-generación";
    if (String(generation) === "11") return "Niche";
    const number = String(generation).match(/\d+/)?.[0];
    return number ? `Generación ${number}` : generation || "Sin generación";
  }

  function consoleCard(item) {
    const stateLabel = item.controlState === "owned" ? "Tengo" : item.controlState === "wanted" ? "Quiero" : "No tengo";
    const image = item.fotos?.[0] || item.fotosPropias?.[0] || fallbackImage;
    const href = item.hasPersonalRecord ? window.CollectionRepository.getConsoleDetailHref(item.id) : window.CollectionRepository.getDatabaseHref();
    const status = item.funcionando === true ? "Lista" : item.funcionando === false ? "Revisar" : "Pendiente";
    const guide = item.collectionGuide;
    const guideMarkup = guide
      ? `<div class="market-guide" aria-label="Costo ${guide.costScore} de 10, dificultad ${guide.difficultyScore} de 10. Rango orientativo: US$ ${escapeHtml(guide.entryRangeUsd)}." title="${escapeHtml(guide.note)}"><span class="market-cost">Costo <b>${guide.costScore}/10</b></span><span class="market-difficulty">Dificultad <b>${guide.difficultyScore}/10</b></span><span class="market-range">US$ ${escapeHtml(guide.entryRangeUsd)}</span></div>`
      : "";
    return `<article class="console-node is-${item.controlState}">
      <a class="console-image" href="${href}" aria-label="Abrir ${escapeHtml(item.nombre)}"><img src="${escapeHtml(image)}" alt="Imagen de ${escapeHtml(item.nombre)}" loading="lazy" onerror="this.onerror=null;this.src='${fallbackImage}'" /></a>
      <div class="console-copy">
        <div class="console-heading"><h3><a href="${href}">${escapeHtml(item.nombre)}</a></h3><span class="ownership-pill">${stateLabel}</span></div>
        <p>${escapeHtml(item.fabricante || "Fabricante N/D")} · ${escapeHtml(String(item.anioLanzamiento || "Año N/D"))}</p>
        ${guideMarkup}
        <div class="console-meta"><span>${item.controlState === "owned" ? `${escapeHtml(item.ownershipType || "physical")} · ${status}` : item.controlState === "wanted" ? "En seguimiento" : "No registrada"}</span><a href="${href}">${item.hasPersonalRecord ? "Ver ficha" : "Ver base"}</a></div>
      </div>
    </article>`;
  }

  function controlState(item) {
    if (item.tengo === true) return "owned";
    if (item.hasPersonalRecord === true && item.removedFromWishlist !== true) return "wanted";
    return "unowned";
  }

  function renderLane(label, consoles, tone) {
    const content = consoles.length
      ? consoles.map(consoleCard).join("")
      : '<p class="lane-empty">Sin consolas relevadas.</p>';
    return `<section class="ownership-lane ${tone}"><header class="lane-head"><h3>${label}</h3><span>${consoles.length}</span></header><div class="console-rail">${content}</div></section>`;
  }

  function render() {
    const classified = state.consoles.map((item) => ({ ...item, controlState: controlState(item) }));
    const groups = classified.reduce((result, item) => {
      const rank = generationRank(item.generacion);
      (result[rank] ||= []).push(item);
      return result;
    }, {});
    const ownedTotal = classified.filter((item) => item.controlState === "owned").length;
    const wantedTotal = classified.filter((item) => item.controlState === "wanted").length;
    const unownedTotal = classified.filter((item) => item.controlState === "unowned").length;
    document.getElementById("ownedTotal").textContent = ownedTotal;
    document.getElementById("wantedTotal").textContent = wantedTotal;
    document.getElementById("unownedTotal").textContent = unownedTotal;
    const toggle = document.getElementById("toggleUnownedBtn");
    toggle.textContent = state.showUnowned ? "Ocultar no tengo" : `Ver no tengo (${unownedTotal})`;
    toggle.setAttribute("aria-expanded", String(state.showUnowned));
    const knownRanks = [0, ...generationRanks];
    const unknownRanks = Object.keys(groups).map(Number).filter((rank) => !knownRanks.includes(rank)).sort((a, b) => a - b);
    const entries = [...knownRanks, ...unknownRanks];
    document.getElementById("generationLadder").innerHTML = entries.map((rank) => {
      const consoles = groups[rank] || [];
      const owned = consoles.filter((item) => item.controlState === "owned").sort((a, b) => a.nombre.localeCompare(b.nombre));
      const wanted = consoles.filter((item) => item.controlState === "wanted").sort((a, b) => a.nombre.localeCompare(b.nombre));
      const unowned = consoles.filter((item) => item.controlState === "unowned").sort((a, b) => a.nombre.localeCompare(b.nombre));
      const label = generationLabel(String(rank));
      const eyebrow = rank === 0 ? "Antes de las generaciones" : rank === 11 ? "Categoría especial" : `Generación ${String(rank).padStart(2, "0")}`;
      const lanes = `${renderLane("Tengo", owned, "is-owned")}${renderLane("Quiero", wanted, "is-wanted")}${state.showUnowned ? renderLane("No tengo", unowned, "is-unowned") : ""}`;
      return `<section class="generation-step"><header class="generation-head"><div><p>${eyebrow}</p><h2>${escapeHtml(label)}</h2></div><span>${owned.length} tengo · ${wanted.length} quiero${state.showUnowned ? ` · ${unowned.length} no tengo` : ""}</span></header><div class="ownership-lanes ${state.showUnowned ? "has-unowned" : ""}">${lanes}</div></section>`;
    }).join("");
  }

  async function init() {
    try {
      await Promise.resolve(window.DataStore?.ready);
      const [baseResponse, personalResponse, guideResponse] = await Promise.all([
        fetch("./data/consoles-base.json"),
        fetch("./data/consoles.json"),
        fetch("./data/collection-guide.json").catch(() => null)
      ]);
      if (!baseResponse.ok) throw new Error("No se pudo cargar la base general de consolas.");
      const basePayload = await baseResponse.json();
      const personalPayload = personalResponse.ok ? await personalResponse.json() : { consolas: [] };
      const guidePayload = guideResponse?.ok ? await guideResponse.json() : { guides: {} };
      state.consoles = buildControlConsoles(basePayload.consolas || [], personalPayload.consolas || [], guidePayload.guides || {});
      populateConsoleInspectorSelect();
      bindPersistenceEvents();
      document.getElementById("toggleUnownedBtn").addEventListener("click", () => {
        state.showUnowned = !state.showUnowned;
        render();
      });
      render();
      renderPersistencePanel();
    } catch (error) {
      document.getElementById("generationLadder").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
    }
  }

  init();
})();
