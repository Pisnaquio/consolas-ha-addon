(() => {
  const fallbackImage = "./assets/photos/console-placeholder.svg";

  const state = {
    baseConsoles: [],
    consoles: [],
    gamesByConsole: {}
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

  const formatPrice = (value) => {
    if (!value || value <= 0) return "Sin dato";
    return new Intl.NumberFormat("es-UY", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0
    }).format(value);
  };

  const normalizeOwnershipType = (raw = "", loTengo = false) => window.CollectionRepository.normalizeOwnershipType(raw, loTengo);

  function readOverrides() {
    return window.CollectionRepository.readOverrides();
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

  function getGamesForConsole(consoleId) {
    return window.CollectionRepository.getGamesForConsole(state.gamesByConsole, consoleId);
  }

  function refreshFromPersistence() {
    const merged = mergeWithAdditions(state.baseConsoles);
    state.consoles = applyDetailEdits(applyOverrides(merged));
    render(state.consoles);
  }

  function isNonGameEntry(game = {}) {
    return window.CollectionRepository.isNonGameEntry(game);
  }

  function gameIsOwned(game = {}) {
    return window.CollectionRepository.gameIsOwned(game);
  }

  function gameBelongsToWishlist(game = {}) {
    return window.CollectionRepository.gameBelongsToWishlist(game);
  }

  function getConsoleImage(item = {}) {
    return window.CollectionRepository?.getConsoleImage?.(item, fallbackImage) || fallbackImage;
  }

  function statusMeta(value) {
    if (value === true) {
      return { label: "Lista para jugar", tone: "ready", detail: "Funcionando" };
    }
    if (value === false) {
      return { label: "Requiere revisión", tone: "critical", detail: "No funcionando" };
    }
    return { label: "Estado pendiente", tone: "unknown", detail: "Sin dato" };
  }

  function metricCard(label, value, caption, tone = "") {
    return `
      <article class="status-metric ${tone ? `metric-${tone}` : ""}">
        <small>${escapeHtml(label)}</small>
        <strong>${escapeHtml(value)}</strong>
        <span>${escapeHtml(caption)}</span>
      </article>
    `;
  }

  function emptyState(title, body) {
    return `
      <div class="empty-state">
        <span aria-hidden="true"></span>
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(body)}</p>
      </div>
    `;
  }

  function metrics(items) {
    const perConsole = items.map((consoleItem) => {
      const all = getGamesForConsole(consoleItem.id);
      const games = all.filter((game) => !isNonGameEntry(game));
      const extras = all.length - games.length;
      const ownedGames = games.filter((game) => gameIsOwned(game));
      const wishlistGames = games.filter((game) => gameBelongsToWishlist(game));
      const physical = games.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "physical").length;
      const digital = games.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "digital").length;
      const both = games.filter((game) => normalizeOwnershipType(game.ownershipType, game.loTengo) === "both").length;
      const standby = games.filter((game) => game.standby === true).length;
      const progress = games.length ? Math.round((ownedGames.length / games.length) * 100) : 0;
      return {
        ...consoleItem,
        tengo: consoleItem.tengo === true,
        image: getConsoleImage(consoleItem),
        total: games.length,
        owned: ownedGames.length,
        wishlist: wishlistGames.length,
        physical,
        digital,
        both,
        standby,
        extras,
        progress
      };
    });

    const totals = perConsole.reduce(
      (acc, item) => {
        acc.games += item.total;
        acc.owned += item.owned;
        acc.wishlist += item.wishlist;
        acc.physical += item.physical;
        acc.digital += item.digital;
        acc.both += item.both;
        acc.standby += item.standby;
        acc.extras += item.extras;
        acc.opportunities += Array.isArray(item.oportunidades) ? item.oportunidades.length : 0;
        return acc;
      },
      { games: 0, owned: 0, wishlist: 0, physical: 0, digital: 0, both: 0, standby: 0, extras: 0, opportunities: 0 }
    );

    return { perConsole, totals };
  }

  function buildActionQueue(perConsole) {
    const operational = perConsole
      .filter((item) => item.tengo && (item.funcionando === false || item.funcionando == null))
      .map((item) => {
        const meta = statusMeta(item.funcionando);
        return {
          consoleId: item.id,
          consoleName: item.nombre,
          severity: meta.tone,
          label: item.funcionando === false ? "Revisión" : "Completar",
          title: meta.detail,
          reason: item.funcionando === false ? "Marcada como no funcionando." : "Falta confirmar si está lista para jugar.",
          action: item.funcionando === false ? "Revisar consola" : "Completar ficha"
        };
      });

    const opportunities = perConsole.flatMap((item) =>
      (item.oportunidades || []).map((op) => {
        const seen = Number(op.precioVisto) || null;
        const target = Number(op.precioObjetivo) || null;
        const isDeal = seen && target && seen <= target;
        return {
          consoleId: item.id,
          consoleName: item.nombre,
          severity: isDeal ? "deal" : "watch",
          label: isDeal ? "Oportunidad" : "Seguimiento",
          title: op.titulo || "Oportunidad registrada",
          reason: `${op.fuente || "Fuente N/D"}${op.fecha ? ` · ${op.fecha}` : ""} · ${formatPrice(seen)} vs objetivo ${formatPrice(target)}`,
          action: "Ver detalle"
        };
      })
    );

    const severityRank = { critical: 0, unknown: 1, deal: 2, watch: 3 };
    return [...operational, ...opportunities].sort(
      (a, b) => (severityRank[a.severity] ?? 9) - (severityRank[b.severity] ?? 9) || a.consoleName.localeCompare(b.consoleName)
    );
  }

  function renderCollectionCard(item) {
    const meta = statusMeta(item.funcionando);
    return `
      <article class="collection-card ${meta.tone}">
        <a class="collection-image" href="${window.CollectionRepository.getConsoleDetailHref(item.id)}" aria-label="Abrir ${escapeHtml(item.nombre)}">
          <img src="${escapeHtml(item.image)}" alt="Imagen representativa de ${escapeHtml(item.nombre)}" loading="lazy" onerror="this.onerror=null;this.src='${fallbackImage}'" />
        </a>
        <div class="collection-body">
          <div class="collection-title-row">
            <div>
              <h3><a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">${escapeHtml(item.nombre)}</a></h3>
              <p>${escapeHtml(item.fabricante || "Fabricante N/D")} · ${escapeHtml(item.generacion || "Gen. N/D")} · ${escapeHtml(String(item.anioLanzamiento || "Año N/D"))}</p>
            </div>
            <span class="status-pill ${meta.tone}">${escapeHtml(meta.label)}</span>
          </div>
          <div class="collection-stats" aria-label="Resumen de ${escapeHtml(item.nombre)}">
            <span><strong>${item.owned}</strong> registrados</span>
            <span><strong>${item.total}</strong> catálogo</span>
            <span><strong>${item.physical + item.digital + item.both}</strong> disponibles</span>
          </div>
          <div class="collection-meta">
            <span>Estado: ${escapeHtml(item.estado || "N/D")}</span>
            <span>${item.physical} fisico · ${item.digital} digital · ${item.both} ambos</span>
          </div>
          <div class="progress collection-progress" aria-label="${item.progress}% registrado">
            <span style="width:${item.progress}%"></span>
          </div>
          <a class="text-link" href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">Abrir detalle</a>
        </div>
      </article>
    `;
  }

  function renderActionItem(item) {
    return `
      <article class="action-item ${escapeHtml(item.severity)}">
        <div class="action-marker" aria-hidden="true"></div>
        <div>
          <div class="action-topline">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.consoleName)}</strong>
          </div>
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.reason)}</p>
        </div>
        <a class="text-link" href="${window.CollectionRepository.getConsoleDetailHref(item.consoleId)}">${escapeHtml(item.action)}</a>
      </article>
    `;
  }

  function renderLibraryRow(item, index) {
    return `
      <article class="library-row">
        <span class="rank">${String(index + 1).padStart(2, "0")}</span>
        <div class="library-main">
          <div class="library-title">
            <a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">${escapeHtml(item.nombre)}</a>
            <span>${item.owned}/${item.total} juegos</span>
          </div>
          <div class="progress" aria-label="${item.progress}% registrado">
            <span style="width:${item.progress}%"></span>
          </div>
        </div>
        <div class="library-meta">
          <strong>${item.progress}%</strong>
          <span>${item.extras ? `${item.extras} extras` : `${item.wishlist} deseados`}</span>
        </div>
      </article>
    `;
  }

  function renderWantedCard(item) {
    const target = Number(item.precioObjetivoCompra) || null;
    return `
      <article class="wanted-card">
        <a class="wanted-image" href="${window.CollectionRepository.getConsoleDetailHref(item.id)}" aria-label="Abrir ${escapeHtml(item.nombre)}">
          <img src="${escapeHtml(item.image)}" alt="Imagen representativa de ${escapeHtml(item.nombre)}" loading="lazy" onerror="this.onerror=null;this.src='${fallbackImage}'" />
        </a>
        <div>
          <h3><a href="${window.CollectionRepository.getConsoleDetailHref(item.id)}">${escapeHtml(item.nombre)}</a></h3>
          <p>${escapeHtml(item.fabricante || "Fabricante N/D")} · ${escapeHtml(item.generacion || "Gen. N/D")}</p>
          <div class="wanted-meta">
            <span>${item.total} juegos en catálogo</span>
            <span>Objetivo: ${formatPrice(target)}</span>
          </div>
        </div>
      </article>
    `;
  }

  function render(items) {
    const { perConsole, totals } = metrics(items);
    const owned = perConsole.filter((item) => item.tengo);
    const wanted = perConsole.filter((item) => !item.tengo);
    const readyConsoles = owned.filter((item) => item.funcionando === true).length;
    const operationalAlerts = owned.filter((item) => item.funcionando === false || item.funcionando == null).length;
    const actions = buildActionQueue(perConsole);

    document.getElementById("heroStatus").innerHTML = `
      <span>${owned.length} consolas registradas</span>
      <span>${readyConsoles} listas para jugar</span>
      <span>${actions.length} acciones</span>
      <span>${totals.owned} juegos registrados</span>
    `;

    document.getElementById("kpiGrid").innerHTML = [
      metricCard("Consolas que tengo", String(owned.length), "inventario real", "owned"),
      metricCard("Listas para jugar", String(readyConsoles), "tengo + funcionando", "ready"),
      metricCard("Alertas", String(actions.length), `${operationalAlerts} operativas · ${totals.opportunities} oportunidades`, "alerts"),
      metricCard("Juegos registrados", String(totals.owned), `${totals.physical} fisico · ${totals.digital} digital · ${totals.both} ambos`, "games")
    ].join("");

    const ownedSorted = [...owned].sort(
      (a, b) => Number(b.funcionando === true) - Number(a.funcionando === true) || b.owned - a.owned || a.nombre.localeCompare(b.nombre)
    );
    document.getElementById("ownedConsoles").innerHTML =
      ownedSorted.map(renderCollectionCard).join("") ||
      emptyState("Colección sin consolas registradas", "Marcá consolas como propias desde la vista principal para que aparezcan acá.");
    document.getElementById("ownedConsolesCount").textContent = `${owned.length} consola(s)`;

    document.getElementById("actionsList").innerHTML =
      actions.slice(0, 8).map(renderActionItem).join("") ||
      emptyState("Sin acciones urgentes", "No hay alertas operativas ni oportunidades registradas por ahora.");
    document.getElementById("actionsCount").textContent = actions.length ? `${actions.length} item(s)` : "al día";

    const ranked = [...perConsole]
      .filter((item) => item.total > 0)
      .sort((a, b) => b.owned - a.owned || b.progress - a.progress || a.nombre.localeCompare(b.nombre));
    document.getElementById("libraryRows").innerHTML =
      ranked.slice(0, 8).map(renderLibraryRow).join("") ||
      emptyState("Sin bibliotecas cargadas", "Cuando haya catálogos por consola, el avance va a aparecer en este ranking.");
    document.getElementById("libraryCount").textContent = `${Math.min(ranked.length, 8)} de ${ranked.length}`;

    const wantedSorted = [...wanted].sort((a, b) => a.nombre.localeCompare(b.nombre));
    document.getElementById("wantedConsoles").innerHTML =
      wantedSorted.map(renderWantedCard).join("") ||
      emptyState("Sin deseados pendientes", "La wishlist de consolas está limpia con los datos actuales.");
    document.getElementById("wantedCount").textContent = `${wanted.length} consola(s)`;
  }

  async function init() {
    await Promise.resolve(window.DataStore?.ready);
    const [consolesRes, gamesRes, accessoriesRes] = await Promise.all([
      fetch("./data/consoles.json"),
      fetch("./data/console-games.json"),
      fetch("./data/console-accessories.json")
    ]);
    if (!consolesRes.ok || !gamesRes.ok || !accessoriesRes.ok) throw new Error("No se pudo cargar la data.");

    const consolesPayload = await consolesRes.json();
    const gamesPayload = await gamesRes.json();
    const accessoriesPayload = await accessoriesRes.json();
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

    state.baseConsoles = consolesPayload.consolas || [];
    refreshFromPersistence();

    // A change saved from another open view updates the operational queue without a manual reload.
    window.addEventListener("storage", (event) => {
      if (event.key === "consolas.appState.v2") refreshFromPersistence();
    });
  }

  init().catch((error) => {
    document.querySelector(".studio-shell").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
})();
