(() => {
  /**
   * Franchise Collection Tracker — capa central de datos.
   *
   * Igual regla que el resto de la app: el catálogo (`web/data/franchise-
   * collections.json`) es sólo relación editorial — obra, lanzamiento,
   * edición, hardware. Nunca ownership, nunca wishlist. Lo que el usuario
   * tiene se lee siempre del estado persistido real, vía
   * `CollectionRepository`/`DataStore` — nunca se asume.
   *
   * Un lanzamiento vinculado a un juego de catálogo existente (`catalogRef`)
   * lee y escribe ESE juego real, por `persistGamePatch` — es el mismo juego
   * que ves en la ficha de la consola, no una copia. Un lanzamiento sin
   * catalogRef todavía no existe como juego en ningún lado: se crea como
   * entrada manual en la consola correspondiente, la primera vez que se
   * marca, también por `persistGamePatch` — nunca a mano ni por otro camino.
   *
   * Una edición especial o una pieza de hardware no es un juego — marcarla
   * como tal ensuciaría la biblioteca real de la consola. Se trackea aparte,
   * por `persistFranchiseTrackedItem`, que sigue siendo `DataStore` (la
   * misma fuente única), sólo que bajo una clave que nunca se confunde con
   * una consola.
   */

  const DATA_URL = "./data/franchise-collections.json";
  const CONSOLES_URL = "./data/consoles.json";
  const GAMES_URL = "./data/console-games.json";

  function repo() {
    const value = window.CollectionRepository;
    if (!value) throw new Error("CollectionRepository no está disponible.");
    return value;
  }

  // --- Lectura pura de lo que ya existe (sin fetch, testeable con fixtures) --

  function releaseGameRef(release) {
    return {
      consoleId: release.catalogRef?.consoleId || release.platformId,
      gameId: release.catalogRef?.gameId || release.id
    };
  }

  function findGame(gamesByConsole, consoleId, gameId) {
    const games = gamesByConsole?.[consoleId] || [];
    return games.find((game) => String(game?.id) === String(gameId)) || null;
  }

  function resolveReleaseState(release, gamesByConsole) {
    const { consoleId, gameId } = releaseGameRef(release);
    const game = findGame(gamesByConsole, consoleId, gameId);
    if (!game) return { ownershipType: "none", loQuiero: false, exists: false, game: null };
    return {
      ownershipType: repo().normalizeOwnershipType(game.ownershipType, game.loTengo),
      loQuiero: game.loQuiero === true,
      exists: true,
      game
    };
  }

  function isReleaseOwned(state) {
    return state.ownershipType !== "none";
  }

  function isReleaseOwnedPhysical(state) {
    return state.ownershipType === "physical" || state.ownershipType === "both";
  }

  function isDenominatorWork(work) {
    return work.workStatus === "released" && work.excludeFromDenominator !== true;
  }

  function annotateReleases(releases, gamesByConsole) {
    return (releases || []).map((release) => {
      const state = resolveReleaseState(release, gamesByConsole);
      // Cover y precio sólo se toman de un juego de catálogo real (catalogRef):
      // ese dato ya pasó por validate-game-covers.mjs. Un lanzamiento sin
      // catalogRef resuelve a una entrada manual sin esos campos — nunca se
      // inventa una portada ni un precio para él.
      const catalogGame = release.catalogRef ? state.game : null;
      return {
        ...release,
        ownershipType: state.ownershipType,
        loQuiero: state.loQuiero,
        isOwned: isReleaseOwned(state),
        isOwnedPhysical: isReleaseOwnedPhysical(state),
        coverUrl: catalogGame?.coverUrl || null,
        priceRange: catalogGame?.priceRange || null
      };
    });
  }

  function annotateTrackedList(items, trackedItems, kind) {
    return (items || []).map((item) => ({
      ...item,
      kind,
      owned: trackedItems?.[item.id]?.owned === true,
      checklist: trackedItems?.[item.id]?.checklist || {}
    }));
  }

  function groupReleasesByWorkId(releases) {
    const byWorkId = {};
    releases.forEach((release) => {
      (release.workIds || []).forEach((workId) => {
        (byWorkId[workId] ||= []).push(release);
      });
    });
    return byWorkId;
  }

  function annotateWorks(works, releasesByWorkId) {
    return (works || []).map((work) => {
      const releases = releasesByWorkId[work.id] || [];
      const isCovered = releases.some((release) => release.isOwned);
      return { ...work, releases, isCovered, isDenominator: isDenominatorWork(work) };
    });
  }

  // --- Progreso: los tres números del PRD, cada uno con su propio sentido --

  function computeSagaCoverage(works) {
    const denom = works.filter((work) => work.isDenominator);
    const covered = denom.filter((work) => work.isCovered);
    return {
      covered: covered.length,
      total: denom.length,
      percent: denom.length ? Math.round((covered.length / denom.length) * 100) : 0
    };
  }

  function computePhysicalShelf(releases) {
    const primaries = releases.filter((release) => release.isPrimaryPhysicalTarget);
    const owned = primaries.filter((release) => release.isOwnedPhysical);
    return {
      owned: owned.length,
      total: primaries.length,
      percent: primaries.length ? Math.round((owned.length / primaries.length) * 100) : 0
    };
  }

  function computeCrownJewels(editions, crownReleases, hardware) {
    const items = [...editions, ...crownReleases, ...hardware];
    const owned = items.filter((item) => item.owned);
    return {
      owned: owned.length,
      total: items.length,
      percent: items.length ? Math.round((owned.length / items.length) * 100) : 0,
      items
    };
  }

  // --- Próxima compra: derivada del estado real, nunca fija ------------------

  function computeNextAction({ works, releases, ownedPlatformIds }) {
    const coveredWorkIds = new Set(works.filter((work) => work.isCovered).map((work) => work.id));
    const denominatorWorkIds = new Set(works.filter((work) => work.isDenominator).map((work) => work.id));

    const byPlatform = {};
    releases.forEach((release) => {
      (byPlatform[release.platformId] ||= []).push(release);
    });

    const platformCandidates = Object.entries(byPlatform)
      .filter(([platformId]) => !ownedPlatformIds.has(platformId))
      .map(([platformId, platformReleases]) => {
        const newWorkIds = new Set();
        platformReleases.forEach((release) => {
          (release.workIds || []).forEach((workId) => {
            if (denominatorWorkIds.has(workId) && !coveredWorkIds.has(workId)) newWorkIds.add(workId);
          });
        });
        return { type: "platform", platformId, newWorkIds: [...newWorkIds] };
      })
      .filter((candidate) => candidate.newWorkIds.length > 0)
      .sort((a, b) => b.newWorkIds.length - a.newWorkIds.length);

    if (platformCandidates.length) return platformCandidates[0];

    // Ya tenés las plataformas que hacen falta: lo que queda es un lanzamiento
    // físico principal puntual, no una plataforma nueva.
    const missingPrimary = releases
      .filter((release) => release.isPrimaryPhysicalTarget && !release.isOwnedPhysical)
      .sort((a, b) => {
        if (a.year == null && b.year == null) return 0;
        if (a.year == null) return 1; // sin año todavía nunca se recomienda antes que uno real
        if (b.year == null) return -1;
        return a.year - b.year;
      })[0];
    if (missingPrimary) return { type: "release", releaseId: missingPrimary.id };

    return null;
  }

  // --- Composición completa, pura — fácil de testear con fixtures -----------

  function composeModel({ franchise, gamesByConsole, ownedPlatformIds, trackedItems }) {
    const releases = annotateReleases(franchise.releases, gamesByConsole);
    const releasesByWorkId = groupReleasesByWorkId(releases);
    const works = annotateWorks(franchise.works, releasesByWorkId);

    const editions = annotateTrackedList(
      (franchise.editions || []).filter((edition) => edition.isCrownJewel),
      trackedItems,
      "edition"
    );
    const crownReleases = annotateTrackedList(
      releases.filter((release) => release.isCrownJewel),
      trackedItems,
      "release"
    );
    const hardware = annotateTrackedList(franchise.hardware, trackedItems, "hardware");

    return {
      franchise,
      works,
      releases,
      editions: annotateTrackedList(franchise.editions, trackedItems, "edition"),
      hardware,
      historical: franchise.historical || [],
      progress: {
        sagaCoverage: computeSagaCoverage(works),
        physicalShelf: computePhysicalShelf(releases),
        crownJewels: computeCrownJewels(editions, crownReleases, hardware)
      },
      nextAction: computeNextAction({ works, releases, ownedPlatformIds })
    };
  }

  // --- Fetch + estado real del usuario ---------------------------------------

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`No se pudo cargar ${url}`);
    return response.json();
  }

  function computeOwnedPlatformIds(consoles) {
    const overridden = repo().applyOverrides(consoles, { hideRemovedWishlistBase: false });
    return new Set(overridden.filter((item) => item.tengo === true).map((item) => item.id));
  }

  async function load(franchiseId) {
    const [catalog, consolesPayload, gamesPayload] = await Promise.all([
      fetchJson(DATA_URL),
      fetchJson(CONSOLES_URL),
      fetchJson(GAMES_URL)
    ]);

    const franchise = catalog.franchises?.[franchiseId];
    if (!franchise) throw new Error(`Franquicia desconocida: ${franchiseId}`);

    const consoles = repo().mergeWithAdditions(consolesPayload.consolas || []);
    const ownedPlatformIds = computeOwnedPlatformIds(consoles);

    const baseGamesByConsole = {};
    const gamesByConsole = {};
    const platformIds = new Set([
      ...(franchise.releases || []).map((release) => release.platformId),
      ...(franchise.releases || []).map((release) => release.catalogRef?.consoleId).filter(Boolean)
    ]);
    platformIds.forEach((consoleId) => {
      const base = gamesPayload.byConsole?.[consoleId]?.juegosCatalogo || [];
      baseGamesByConsole[consoleId] = base;
      gamesByConsole[consoleId] = repo().getGamesForConsole({ [consoleId]: base }, consoleId);
    });

    const trackedItems = repo().getFranchiseTrackedItems(franchiseId);
    const model = composeModel({ franchise, gamesByConsole, ownedPlatformIds, trackedItems });
    return { ...model, franchiseId, baseGamesByConsole, consolesById: Object.fromEntries(consoles.map((c) => [c.id, c])) };
  }

  // --- Escritura: siempre por la capa central, nunca a mano -------------------

  function setReleaseOwnership(release, ownershipType, baseGamesByConsole) {
    const { consoleId, gameId } = releaseGameRef(release);
    const baseGames = baseGamesByConsole?.[consoleId] || [];
    return repo().persistGamePatch(consoleId, gameId, { ownershipType }, baseGames);
  }

  function setTrackedOwnership(franchiseId, itemId, owned) {
    return repo().persistFranchiseTrackedItem(franchiseId, itemId, { owned });
  }

  function setTrackedChecklistItem(franchiseId, itemId, currentChecklist, key, checked) {
    const nextChecklist = { ...(currentChecklist || {}), [key]: checked };
    return repo().persistFranchiseTrackedItem(franchiseId, itemId, { checklist: nextChecklist });
  }

  window.FranchiseCollectionRepository = {
    // puras, testeables sin fetch
    releaseGameRef,
    resolveReleaseState,
    isDenominatorWork,
    annotateReleases,
    annotateWorks,
    annotateTrackedList,
    groupReleasesByWorkId,
    computeSagaCoverage,
    computePhysicalShelf,
    computeCrownJewels,
    computeNextAction,
    composeModel,
    // con efectos (fetch / DataStore)
    load,
    setReleaseOwnership,
    setTrackedOwnership,
    setTrackedChecklistItem
  };
})();
