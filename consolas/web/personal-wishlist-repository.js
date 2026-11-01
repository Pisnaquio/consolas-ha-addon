(() => {
  /**
   * Nacho's Wishlist — capa central de datos.
   *
   * Un deseo es una decisión del dueño, no una recomendación: «International
   * Superstar Soccer Deluxe», «todos los Donkey Kong de Super Nintendo», «la
   * colección de God of War». Por eso no se infiere de `loQuiero` (que la
   * calibración del radar usó para marcar el top de cada consola) ni de
   * ningún ranking: existe sólo si el dueño lo agregó.
   *
   * Dos formas de deseo:
   * - `games`: uno o más juegos con nombre propio. Cada juego es una
   *   referencia `{ consoleId, gameId }` al juego real (de catálogo o manual):
   *   si ya lo tenés se lee del estado real, nunca de una copia.
   * - `franchise`: una colección temática entera. Las piezas son los
   *   lanzamientos físicos principales del tracker de franquicias, y el
   *   progreso es su «estantería física».
   *
   * Lo que se persiste es sólo la lista de deseos, por
   * `CollectionRepository.persistPersonalWish`. Nada de esta capa escribe
   * `loQuiero` ni `keepInWishlist`.
   */

  const CONSOLES_URL = "./data/consoles.json";
  const GAMES_URL = "./data/console-games.json";
  const FRANCHISES_URL = "./data/franchise-collections.json";
  const FALLBACK_COVER = "./assets/photos/game-placeholder.svg";
  const WISH_KINDS = ["games", "franchise"];

  function repo() {
    const value = window.CollectionRepository;
    if (!value) throw new Error("CollectionRepository no está disponible.");
    return value;
  }

  // --- Lectura pura (sin fetch, testeable con fixtures) ----------------------

  function itemKey(item) {
    return `${item?.consoleId || ""}/${item?.gameId || ""}`;
  }

  /** Normaliza un deseo leído del estado: nunca confía en la forma persistida. */
  function normalizeWish(raw = {}, id = raw?.id) {
    const kind = WISH_KINDS.includes(raw?.kind) ? raw.kind : "games";
    const seen = new Set();
    const items = (Array.isArray(raw?.items) ? raw.items : [])
      .filter((item) => item && item.consoleId && item.gameId)
      .map((item) => ({ consoleId: String(item.consoleId), gameId: String(item.gameId) }))
      .filter((item) => {
        const key = itemKey(item);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    return {
      id: String(id || ""),
      title: String(raw?.title || "").trim(),
      kind,
      items: kind === "games" ? items : [],
      franchiseId: kind === "franchise" ? String(raw?.franchiseId || "") : "",
      note: String(raw?.note || "").trim(),
      createdAt: raw?.createdAt || "",
      updatedAt: raw?.updatedAt || ""
    };
  }

  function platformName(consolesById, consoleId) {
    return consolesById?.[consoleId]?.nombre || consoleId || "Sin consola";
  }

  function findGame(gamesByConsole, consoleId, gameId) {
    return (gamesByConsole?.[consoleId] || []).find((game) => String(game?.id) === String(gameId)) || null;
  }

  function gameCover(game) {
    return game?.coverImage || game?.coverUrl || FALLBACK_COVER;
  }

  function annotateGameItem(item, { gamesByConsole, consolesById }) {
    const game = findGame(gamesByConsole, item.consoleId, item.gameId);
    const ownershipType = game ? repo().normalizeOwnershipType(game.ownershipType, game.loTengo) : "none";
    return {
      key: itemKey(item),
      source: "game",
      consoleId: item.consoleId,
      gameId: item.gameId,
      exists: Boolean(game),
      title: game?.nombre || item.gameId,
      platform: platformName(consolesById, item.consoleId),
      coverUrl: gameCover(game),
      priceRange: game?.priceRange || null,
      ownershipType,
      owned: ownershipType !== "none"
    };
  }

  /**
   * Una colección temática se desea como estantería: las piezas son los
   * lanzamientos físicos principales, y una ya tenida en digital todavía
   * falta. Es el mismo número que «En la estantería» del tracker.
   */
  function annotateFranchiseItems(franchiseModel, { consolesById }) {
    return (franchiseModel?.releases || [])
      .filter((release) => release.isPrimaryPhysicalTarget)
      .map((release) => ({
        key: `franchise/${release.id}`,
        source: "franchise",
        consoleId: release.platformId,
        // El juego de catálogo detrás del lanzamiento: es lo que el radar
        // reconoce como entidad, igual que en un deseo de juegos.
        gameId: release.catalogRef?.gameId || release.id,
        releaseId: release.id,
        exists: true,
        title: release.title,
        platform: platformName(consolesById, release.platformId),
        coverUrl: release.coverUrl || FALLBACK_COVER,
        priceRange: release.priceRange || null,
        ownershipType: release.ownershipType || "none",
        owned: release.isOwnedPhysical === true
      }));
  }

  function annotateWish(rawWish, context) {
    const wish = normalizeWish(rawWish);
    const franchiseModel = wish.kind === "franchise" ? context.franchiseModels?.[wish.franchiseId] || null : null;
    const entries =
      wish.kind === "franchise"
        ? annotateFranchiseItems(franchiseModel, context)
        : wish.items.map((item) => annotateGameItem(item, context));
    const owned = entries.filter((entry) => entry.owned).length;
    return {
      ...wish,
      title: wish.title || franchiseModel?.franchise?.name || entries[0]?.title || "Deseo sin nombre",
      franchiseName: franchiseModel?.franchise?.name || "",
      entries,
      missing: entries.filter((entry) => !entry.owned),
      owned,
      total: entries.length,
      fulfilled: entries.length > 0 && owned === entries.length
    };
  }

  /** Pendientes primero; dentro de cada grupo, el deseo más viejo arriba. */
  function compareWishes(a, b) {
    if (a.fulfilled !== b.fulfilled) return a.fulfilled ? 1 : -1;
    return String(a.createdAt).localeCompare(String(b.createdAt)) || a.title.localeCompare(b.title);
  }

  function composeModel({ wishesById = {}, gamesByConsole = {}, consolesById = {}, franchiseModels = {} }) {
    const context = { gamesByConsole, consolesById, franchiseModels };
    const wishes = Object.entries(wishesById || {})
      .map(([id, wish]) => annotateWish({ ...wish, id }, context))
      .sort(compareWishes);
    const pending = wishes.filter((wish) => !wish.fulfilled);
    return {
      wishes,
      totals: {
        wishes: wishes.length,
        pending: pending.length,
        fulfilled: wishes.length - pending.length,
        missingPieces: pending.reduce((total, wish) => total + wish.missing.length, 0)
      }
    };
  }

  /**
   * Busca juegos reales para agregar a un deseo. Cada palabra tiene que
   * aparecer en el nombre del juego o de su consola, así «donkey snes» o
   * «pokemon game boy color» encuentran lo que se espera.
   */
  function searchGames(query, { gamesByConsole = {}, consolesById = {} } = {}, { limit = 40 } = {}) {
    const tokens = repo().normalizeSlug(query).split(" ").filter(Boolean);
    if (!tokens.length) return [];
    const results = [];
    Object.entries(gamesByConsole).forEach(([consoleId, games]) => {
      const platform = platformName(consolesById, consoleId);
      (games || []).forEach((game) => {
        if (!game?.id || repo().isNonGameEntry(game)) return;
        const haystack = repo().normalizeSlug(`${game.nombre || game.id} ${platform} ${consoleId}`);
        if (!tokens.every((token) => haystack.includes(token))) return;
        results.push(annotateGameItem({ consoleId, gameId: String(game.id) }, { gamesByConsole, consolesById }));
      });
    });
    return results
      .sort((a, b) => a.platform.localeCompare(b.platform) || a.title.localeCompare(b.title))
      .slice(0, limit);
  }

  /** Para el día que no sabés qué regalarte: una pieza que falta, al azar. */
  function pickSurprise(model, random = Math.random) {
    const candidates = (model?.wishes || []).flatMap((wish) =>
      wish.fulfilled ? [] : wish.missing.map((entry) => ({ wishId: wish.id, wishTitle: wish.title, entry }))
    );
    if (!candidates.length) return null;
    return candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))];
  }

  // --- Radar: el deseo es lo que se busca -----------------------------------
  //
  // La búsqueda no se copia dentro del deseo ni el deseo dentro de la
  // búsqueda: el radar ya guarda a qué juego apunta cada búsqueda
  // (`entityType: "game"`, `entityId`, `entityConsoleId`), así que el vínculo
  // se lee de ahí. Borrar el deseo no toca la búsqueda, y viceversa.

  const RADAR_STATUS_RANK = { active: 0, paused: 1, draft: 2 };

  /** El nombre con el que «Buscar con Radar» creaba búsquedas antes de este vínculo. */
  function radarSearchName(entry) {
    return `${entry.title} (${entry.platform})`;
  }

  /**
   * La búsqueda del radar que persigue esta pieza, o null.
   *
   * Primero por entidad; si no hay, por el nombre que ponía el botón viejo,
   * para que una búsqueda creada antes siga apareciendo en su deseo en vez de
   * duplicarse. Las archivadas no cuentan: el dueño ya las retiró.
   */
  function findRadarSearch(entry, searches = []) {
    if (!entry?.gameId) return null;
    const live = (searches || []).filter((search) => search && search.status in RADAR_STATUS_RANK);
    const byEntity = live.filter(
      (search) =>
        search.entityType === "game" &&
        String(search.entityId) === String(entry.gameId) &&
        (!search.entityConsoleId || String(search.entityConsoleId) === String(entry.consoleId))
    );
    const name = radarSearchName(entry).toLowerCase();
    const candidates = byEntity.length
      ? byEntity
      : live.filter((search) => !search.entityId && String(search.name || "").trim().toLowerCase() === name);
    return candidates.sort((a, b) => RADAR_STATUS_RANK[a.status] - RADAR_STATUS_RANK[b.status])[0] || null;
  }

  /** Lo que se le pide al radar para que salga a buscar esta pieza. */
  function radarSearchDraft(entry) {
    return {
      name: radarSearchName(entry),
      platform: entry.platform,
      searchType: "chase",
      origin: "user",
      status: "active",
      entityType: "game",
      entityId: entry.gameId,
      entityConsoleId: entry.consoleId,
      notes: "Creada desde Nacho's Wishlist."
    };
  }

  function createWishId(title, existingIds = [], now = Date.now()) {
    const slug = repo().normalizeSlug(title).replace(/\s+/g, "-").slice(0, 48) || "deseo";
    const taken = new Set(existingIds);
    let id = slug;
    if (taken.has(id)) id = `${slug}-${now.toString(36)}`;
    return id;
  }

  /**
   * Valida y arma el deseo a persistir. Un deseo sin juegos (o sin
   * colección) no se guarda: sería un texto suelto que nada puede cumplir.
   */
  function buildWish(input = {}, { existing = null, existingIds = [], now = new Date() } = {}) {
    const draft = normalizeWish({ ...input, createdAt: existing?.createdAt });
    if (draft.kind === "games" && !draft.items.length) throw new Error("Elegí al menos un juego para el deseo.");
    if (draft.kind === "franchise" && !draft.franchiseId) throw new Error("Elegí la colección temática.");
    if (!draft.title) throw new Error("Ponele un nombre al deseo.");
    const id = existing?.id || createWishId(draft.title, existingIds, now.getTime());
    const timestamp = now.toISOString();
    return { ...draft, id, createdAt: existing?.createdAt || timestamp, updatedAt: timestamp };
  }

  // --- Fetch + estado real del usuario ---------------------------------------

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`No se pudo cargar ${url}`);
    return response.json();
  }

  function composeFranchiseModels(franchises, gamesByConsole, consoles) {
    const franchiseRepo = window.FranchiseCollectionRepository;
    if (!franchiseRepo) return {};
    const overridden = repo().applyOverrides(consoles, { hideRemovedWishlistBase: false });
    const ownedPlatformIds = new Set(overridden.filter((item) => item.tengo === true).map((item) => item.id));
    return Object.fromEntries(
      Object.entries(franchises || {}).map(([franchiseId, franchise]) => [
        franchiseId,
        franchiseRepo.composeModel({
          franchise,
          gamesByConsole,
          ownedPlatformIds,
          trackedItems: repo().getFranchiseTrackedItems(franchiseId)
        })
      ])
    );
  }

  async function load() {
    // Igual que el tracker: sin esperar el estado del servidor, la página
    // diría que no tenés nada, y eso parece un dato.
    await Promise.resolve(window.DataStore?.ready);

    const [consolesPayload, gamesPayload, franchisesPayload] = await Promise.all([
      fetchJson(CONSOLES_URL),
      fetchJson(GAMES_URL),
      fetchJson(FRANCHISES_URL).catch(() => ({ franchises: {} }))
    ]);

    const consoles = repo().mergeWithAdditions(consolesPayload.consolas || []);
    const consolesById = Object.fromEntries(consoles.map((item) => [item.id, item]));
    const consoleIds = new Set([
      ...Object.keys(gamesPayload.byConsole || {}),
      ...Object.keys(repo().readDetailEdits()).filter((key) => !repo().isAuxiliaryBucketKey(key))
    ]);

    const baseGamesByConsole = {};
    const gamesByConsole = {};
    consoleIds.forEach((consoleId) => {
      const base = gamesPayload.byConsole?.[consoleId]?.juegosCatalogo || [];
      baseGamesByConsole[consoleId] = base;
      gamesByConsole[consoleId] = repo().getGamesForConsole({ [consoleId]: base }, consoleId);
    });

    const franchises = franchisesPayload.franchises || {};
    const franchiseModels = composeFranchiseModels(franchises, gamesByConsole, consoles);
    const wishesById = repo().getPersonalWishes();

    return {
      ...composeModel({ wishesById, gamesByConsole, consolesById, franchiseModels }),
      wishesById,
      gamesByConsole,
      baseGamesByConsole,
      consolesById,
      franchiseOptions: Object.values(franchises).map((franchise) => ({ id: franchise.id, name: franchise.name }))
    };
  }

  // --- Escritura: siempre por la capa central --------------------------------

  function saveWish(input, { wishesById = {} } = {}) {
    const existing = input?.id ? wishesById[input.id] || null : null;
    const wish = buildWish(input, { existing, existingIds: Object.keys(wishesById) });
    repo().persistPersonalWish(wish.id, wish);
    return wish;
  }

  function deleteWish(wishId) {
    return repo().removePersonalWish(wishId);
  }

  /** «Ya lo tengo» marca el juego real, el mismo que ves en la consola. */
  function markGameOwned(entry, baseGamesByConsole) {
    if (entry?.source !== "game") throw new Error("Esta pieza se marca desde su colección temática.");
    const baseGames = baseGamesByConsole?.[entry.consoleId] || [];
    return repo().persistGamePatch(entry.consoleId, entry.gameId, { ownershipType: "physical" }, baseGames);
  }

  window.PersonalWishlistRepository = {
    // puras
    itemKey,
    normalizeWish,
    annotateWish,
    composeModel,
    searchGames,
    pickSurprise,
    radarSearchName,
    findRadarSearch,
    radarSearchDraft,
    createWishId,
    buildWish,
    // con efectos
    load,
    saveWish,
    deleteWish,
    markGameOwned
  };
})();
