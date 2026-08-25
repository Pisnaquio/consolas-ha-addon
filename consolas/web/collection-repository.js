(() => {
  function normalizeText(text = "") {
    return String(text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function normalizeSlug(text = "") {
    return normalizeText(text)
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function currentShell() {
    const pathname = window.location.pathname || "";
    const shellParam = new URLSearchParams(window.location.search).get("shell");
    if (shellParam === "studio") return "studio";
    if (pathname.includes("-studio.html")) return "studio";
    if (pathname.endsWith("/collection-control.html") || pathname.endsWith("collection-control.html")) return "studio";
    return "default";
  }

  function appendShell(url) {
    if (currentShell() !== "studio") return url;
    return `${url}${url.includes("?") ? "&" : "?"}shell=studio`;
  }

  function getConsoleDetailHref(consoleId) {
    return appendShell(`./console.html?id=${encodeURIComponent(consoleId)}`);
  }

  function getConsoleGamesHref(consoleId) {
    return appendShell(`./console-games.html?id=${encodeURIComponent(consoleId)}`);
  }

  function getConsoleAccessoriesHref(consoleId) {
    return appendShell(`./console-accessories.html?id=${encodeURIComponent(consoleId)}`);
  }

  function isRenderableConsoleImage(value = "") {
    const src = String(value || "").trim();
    if (!src || src.includes("/runtime/media/")) return false;
    return /^(https?:|data:|blob:|\/?\.?\/?(assets|media)\/)/i.test(src);
  }

  function getConsoleImage(item = {}, fallback = "") {
    const candidates = [
      ...(Array.isArray(item.fotos) ? item.fotos : []),
      ...(Array.isArray(item.fotosPropias) ? item.fotosPropias : [])
    ];
    return candidates.find(isRenderableConsoleImage) || fallback;
  }

  function getHomeHref() {
    return currentShell() === "studio" ? "./index-studio.html" : "./index.html";
  }

  function getDatabaseHref() {
    return currentShell() === "studio" ? "./database-studio.html" : "./database.html";
  }

  function getOpportunitiesHref() {
    return appendShell("./opportunities.html");
  }

  function fallbackPersonalIdForBase(baseId) {
    return `base-${baseId}`;
  }

  function getPersonalIdForBase(baseId) {
    return window.DataStore?.getPersonalIdForBase?.(baseId) || fallbackPersonalIdForBase(baseId);
  }

  function readOverrides() {
    return window.DataStore?.getOverrides?.() || {};
  }

  function readAdditionsMap() {
    return window.DataStore?.getAdditionsMap?.() || {};
  }

  function readAdditionsArray() {
    return Object.values(readAdditionsMap() || {});
  }

  function readDetailEdits() {
    return window.DataStore?.getDetailEdits?.() || {};
  }

  function getConsoleEditBucket(consoleId) {
    const edits = readDetailEdits();
    const raw = edits?.[consoleId];
    return raw && typeof raw === "object" ? raw : {};
  }

  function normalizeRecordMap(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? { ...value } : {};
  }

  function getConsoleEntityState(consoleId) {
    const bucket = getConsoleEditBucket(consoleId);
    return {
      accessoryEditsById: normalizeRecordMap(bucket.accessoryEditsById),
      manualAccessoriesById: normalizeRecordMap(bucket.manualAccessoriesById),
      gameEditsById: normalizeRecordMap(bucket.gameEditsById),
      manualGamesById: normalizeRecordMap(bucket.manualGamesById)
    };
  }

  function hasAccessoryEntityState(entityStateOrConsoleId) {
    const entityState =
      typeof entityStateOrConsoleId === "string" ? getConsoleEntityState(entityStateOrConsoleId) : entityStateOrConsoleId || {};
    return (
      Object.keys(entityState.accessoryEditsById || {}).length > 0 || Object.keys(entityState.manualAccessoriesById || {}).length > 0
    );
  }

  function hasGameEntityState(entityStateOrConsoleId) {
    const entityState =
      typeof entityStateOrConsoleId === "string" ? getConsoleEntityState(entityStateOrConsoleId) : entityStateOrConsoleId || {};
    return Object.keys(entityState.gameEditsById || {}).length > 0 || Object.keys(entityState.manualGamesById || {}).length > 0;
  }

  function cleanUndefinedFields(record = {}) {
    return Object.fromEntries(Object.entries(record).filter(([, value]) => value !== undefined));
  }

  function stableStringify(value) {
    return JSON.stringify(value ?? null);
  }

  function normalizeAccessoryPatchForCompare(item = {}) {
    return {
      tengo: item.tengo === true,
      cantidad: Number.isFinite(Number(item.cantidad)) ? Math.max(0, Math.round(Number(item.cantidad))) : 0,
      funcionando: item.funcionando === true ? true : item.funcionando === false ? false : null,
      original:
        item.original === "original" || item.original === "third-party" || item.original === "mixto" ? item.original : "",
      estado: item.estado || "",
      notas: item.notas || ""
    };
  }

  function normalizeManualAccessoryRecord(item = {}) {
    return cleanUndefinedFields({
      id: item.id,
      nombre: item.nombre || "",
      tipo: item.tipo || "otro",
      image: item.image || "",
      tengo: item.tengo === true || Number(item.cantidad) > 0,
      cantidad: Number.isFinite(Number(item.cantidad)) ? Math.max(0, Math.round(Number(item.cantidad))) : 0,
      funcionando: item.funcionando === true ? true : item.funcionando === false ? false : null,
      original:
        item.original === "original" || item.original === "third-party" || item.original === "mixto" ? item.original : "",
      estado: item.estado || "",
      notas: item.notas || "",
      orden: Number(item.orden) || 0,
      sourceType: "manual"
    });
  }

  function normalizeGamePatchForCompare(item = {}) {
    return cleanUndefinedFields({
      ownershipType: item.ownershipType || "none",
      loQuiero: item.loQuiero === true,
      keepInWishlist: item.keepInWishlist === true,
      standby: item.standby === true,
      prioridad: item.prioridad || "media",
      status: item.status || "",
      condicion: item.condicion || "",
      region: item.region || "",
      notas: item.notas || "",
      coverImage: item.coverImage || "",
      coverUrl: item.coverUrl || "",
      imageSource: item.imageSource || "",
      imageStatus: item.imageStatus || "",
      imageSearchName: item.imageSearchName || "",
      variants: Array.isArray(item.variants) ? item.variants : [],
      editions: Array.isArray(item.editions) ? item.editions : [],
      priceGuide: item.priceGuide || {},
      priceRange: item.priceRange || {}
    });
  }

  function normalizeManualGameRecord(item = {}) {
    return cleanUndefinedFields({
      id: item.id,
      nombre: item.nombre || "",
      franquicia: item.franquicia || "",
      genero: item.genero || "",
      ownershipType: item.ownershipType || "none",
      loQuiero: item.loQuiero === true,
      keepInWishlist: item.keepInWishlist === true,
      standby: item.standby === true,
      prioridad: item.prioridad || "media",
      status: item.status || "",
      condicion: item.condicion || "",
      region: item.region || "",
      notas: item.notas || "",
      coverImage: item.coverImage || "",
      coverUrl: item.coverUrl || "",
      imageSource: item.imageSource || "",
      imageStatus: item.imageStatus || "",
      imageSearchName: item.imageSearchName || "",
      variants: Array.isArray(item.variants) ? item.variants : [],
      editions: Array.isArray(item.editions) ? item.editions : [],
      priceGuide: item.priceGuide || {},
      priceRange: item.priceRange || {},
      orden: Number(item.orden) || 0,
      sourceType: "manual"
    });
  }

  function buildAccessoryEntityState(nextAccessories = [], baseAccessories = []) {
    const baseMap = new Map((baseAccessories || []).map((item) => [String(item.id), item]));
    const accessoryEditsById = {};
    const manualAccessoriesById = {};

    (nextAccessories || []).forEach((item) => {
      const id = String(item?.id || "");
      if (!id) return;
      const base = baseMap.get(id);
      if (!base || (item.sourceType || "catalog") === "manual") {
        manualAccessoriesById[id] = normalizeManualAccessoryRecord(item);
        return;
      }

      const baseComparable = normalizeAccessoryPatchForCompare(base);
      const nextComparable = normalizeAccessoryPatchForCompare(item);
      if (stableStringify(baseComparable) !== stableStringify(nextComparable)) {
        accessoryEditsById[id] = nextComparable;
      }
    });

    return { accessoryEditsById, manualAccessoriesById };
  }

  function buildGameEntityState(nextGames = [], baseGames = []) {
    const baseMap = new Map((baseGames || []).map((item) => [String(item.id), item]));
    const gameEditsById = {};
    const manualGamesById = {};

    (nextGames || []).forEach((item) => {
      const id = String(item?.id || "");
      if (!id) return;
      const base = baseMap.get(id);
      if (!base || (item.sourceType || "catalog") === "manual") {
        manualGamesById[id] = normalizeManualGameRecord(item);
        return;
      }

      const baseComparable = normalizeGamePatchForCompare(base);
      const nextComparable = normalizeGamePatchForCompare(item);
      if (stableStringify(baseComparable) !== stableStringify(nextComparable)) {
        gameEditsById[id] = nextComparable;
      }
    });

    return { gameEditsById, manualGamesById };
  }

  function replaceConsoleEntitySlice(consoleId, nextSlice = {}) {
    const bucket = getConsoleEditBucket(consoleId);
    const nextBucket = cleanUndefinedFields({
      ...bucket,
      ...nextSlice
    });
    delete nextBucket.accesoriosItems;
    delete nextBucket.juegosCatalogo;
    if (window.DataStore?.replaceDetailEdit) {
      window.DataStore.replaceDetailEdit(consoleId, nextBucket);
      return;
    }
    const all = readDetailEdits();
    all[consoleId] = nextBucket;
    window.DataStore?.setDetailEdits?.(all);
  }

  function persistAccessoryEntityState(consoleId, nextAccessories = [], baseAccessories = []) {
    const nextState = buildAccessoryEntityState(nextAccessories, baseAccessories);
    replaceConsoleEntitySlice(consoleId, nextState);
    return nextState;
  }

  function persistGameEntityState(consoleId, nextGames = [], baseGames = []) {
    const nextState = buildGameEntityState(nextGames, baseGames);
    replaceConsoleEntitySlice(consoleId, nextState);
    return nextState;
  }

  function composeAccessoriesFromEntity(baseAccessories = [], entityState = {}) {
    const patches = entityState.accessoryEditsById || {};
    const manuals = entityState.manualAccessoriesById || {};
    return [
      ...(baseAccessories || []).map((item) => ({ ...item, ...(patches[item.id] || {}) })),
      ...Object.values(manuals || {})
    ];
  }

  function composeGamesFromEntity(baseGames = [], entityState = {}) {
    const patches = entityState.gameEditsById || {};
    const manuals = entityState.manualGamesById || {};
    return [...(baseGames || []).map((item) => ({ ...item, ...(patches[item.id] || {}) })), ...Object.values(manuals || {})];
  }

  function migrateConsoleEntityState(consoleId, options = {}) {
    if (!consoleId) return false;
    const bucket = getConsoleEditBucket(consoleId);
    const baseGames = Array.isArray(options.baseGames) ? options.baseGames : [];
    const baseAccessories = Array.isArray(options.baseAccessories) ? options.baseAccessories : [];
    const legacyGames = Array.isArray(options.legacyGames)
      ? options.legacyGames
      : Array.isArray(bucket.juegosCatalogo)
        ? bucket.juegosCatalogo
        : [];
    const legacyAccessories = Array.isArray(options.legacyAccessories)
      ? options.legacyAccessories
      : Array.isArray(bucket.accesoriosItems)
        ? bucket.accesoriosItems
        : [];
    const legacyAccessoryText = Array.isArray(options.legacyAccessoryText)
      ? options.legacyAccessoryText
      : Array.isArray(bucket.accesorios)
        ? bucket.accesorios
        : [];

    const existingEntityState = getConsoleEntityState(consoleId);
    const nextSlice = {};
    let changed = false;

    if (!hasAccessoryEntityState(existingEntityState) && (legacyAccessories.length > 0 || legacyAccessoryText.length > 0)) {
      const accessoryState = buildAccessoryEntityState(legacyAccessories, baseAccessories);
      nextSlice.accessoryEditsById = accessoryState.accessoryEditsById;
      nextSlice.manualAccessoriesById = accessoryState.manualAccessoriesById;
      changed = true;
    }

    if (!hasGameEntityState(existingEntityState) && legacyGames.length > 0) {
      const gameState = buildGameEntityState(legacyGames, baseGames);
      nextSlice.gameEditsById = gameState.gameEditsById;
      nextSlice.manualGamesById = gameState.manualGamesById;
      changed = true;
    }

    if (!changed) return false;
    replaceConsoleEntitySlice(consoleId, nextSlice);
    return true;
  }

  function migrateAllEntityState(options = {}) {
    const gamesByConsole = options.gamesByConsole && typeof options.gamesByConsole === "object" ? options.gamesByConsole : {};
    const accessoriesByConsole =
      options.accessoriesByConsole && typeof options.accessoriesByConsole === "object" ? options.accessoriesByConsole : {};
    const edits = readDetailEdits();
    let migratedCount = 0;
    Object.keys(edits || {}).forEach((consoleId) => {
      const changed = migrateConsoleEntityState(consoleId, {
        baseGames: gamesByConsole[consoleId] || [],
        baseAccessories: accessoriesByConsole[consoleId] || []
      });
      if (changed) migratedCount += 1;
    });
    return migratedCount;
  }

  function mergeWithAdditions(items = []) {
    const additions = readAdditionsArray();
    const ids = new Set((items || []).map((item) => item.id));
    const extra = additions.filter((item) => item && item.id && !ids.has(item.id));
    return [...items, ...extra];
  }

  function applyOverrides(items = [], options = {}) {
    const { hideRemovedWishlistBase = true } = options;
    const overrides = readOverrides();
    return (items || []).reduce((acc, item) => {
      const override = overrides[item.id];
      if (!override) {
        acc.push(item);
        return acc;
      }

      const isBaseWanted = item.tengo !== true;
      if (hideRemovedWishlistBase && override.removedFromWishlist === true && isBaseWanted) {
        return acc;
      }

      const tengo = typeof override.tengo === "boolean" ? override.tengo : item.tengo;
      acc.push({
        ...item,
        tengo,
        categoria: tengo ? "coleccion" : "wishlist"
      });
      return acc;
    }, []);
  }

  function applyDetailEdits(items = []) {
    const edits = readDetailEdits();
    return (items || []).map((item) => applyItemDetailEdits(item, edits[item.id]));
  }

  function applyItemDetailEdits(item = {}, edit) {
    if (!edit) return item;
    const hasAccessoryEntities =
      Object.keys(normalizeRecordMap(edit.accessoryEditsById)).length > 0 ||
      Object.keys(normalizeRecordMap(edit.manualAccessoriesById)).length > 0;
    const hasGameEntities =
      Object.keys(normalizeRecordMap(edit.gameEditsById)).length > 0 || Object.keys(normalizeRecordMap(edit.manualGamesById)).length > 0;
    return {
      ...item,
      ...edit,
      accesorios: Array.isArray(edit.accesorios) ? edit.accesorios : item.accesorios,
      accesoriosItems: hasAccessoryEntities ? item.accesoriosItems : Array.isArray(edit.accesoriosItems) ? edit.accesoriosItems : item.accesoriosItems,
      juegos: Array.isArray(edit.juegos) ? edit.juegos : item.juegos,
      oportunidades: Array.isArray(edit.oportunidades) ? edit.oportunidades : item.oportunidades,
      fotosPropias: Array.isArray(edit.fotosPropias) ? edit.fotosPropias : item.fotosPropias,
      juegosCatalogo: hasGameEntities ? item.juegosCatalogo : Array.isArray(edit.juegosCatalogo) ? edit.juegosCatalogo : item.juegosCatalogo
    };
  }

  function normalizeOwnershipType(raw = "", loTengo = false) {
    const value = String(raw || "").toLowerCase();
    if (value === "physical" || value === "digital" || value === "both" || value === "none") return value;
    return loTengo ? "physical" : "none";
  }

  function getGamesForConsole(gamesByConsole = {}, consoleId) {
    const baseGames = gamesByConsole?.[consoleId] || [];
    const entityState = getConsoleEntityState(consoleId);
    if (hasGameEntityState(entityState)) return composeGamesFromEntity(baseGames, entityState);
    const edits = readDetailEdits();
    const editedGames = edits?.[consoleId]?.juegosCatalogo;
    return Array.isArray(editedGames) ? editedGames : baseGames;
  }

  function isNonGameEntry(game = {}) {
    const text = normalizeText([game?.nombre, game?.franquicia, game?.genero, game?.notas].filter(Boolean).join(" "));
    return /(^|[\s-])(demo|soundtrack|artbook|comic|preview disc|vr experience|family fun pack|bundle|season pass|vrv|illusionist|avatar|theme|app)([\s-]|$)/.test(text);
  }

  function gameIsOwned(game = {}) {
    return normalizeOwnershipType(game.ownershipType, game.loTengo) !== "none";
  }

  function gameBelongsToWishlist(game = {}) {
    const owned = gameIsOwned(game);
    const loQuiero = game.loQuiero === true;
    const keepInWishlist = game.keepInWishlist === true;
    const sourceType = (game.sourceType || "catalog").toLowerCase();
    const isCatalogGame = sourceType !== "manual";

    if (owned && !loQuiero && !keepInWishlist) return false;
    if (owned) return loQuiero || keepInWishlist;
    return loQuiero || keepInWishlist || isCatalogGame;
  }

  window.CollectionRepository = {
    normalizeText,
    normalizeSlug,
    currentShell,
    appendShell,
    getConsoleDetailHref,
    getConsoleGamesHref,
    getConsoleAccessoriesHref,
    isRenderableConsoleImage,
    getConsoleImage,
    getHomeHref,
    getDatabaseHref,
    getOpportunitiesHref,
    fallbackPersonalIdForBase,
    getPersonalIdForBase,
    readOverrides,
    readAdditionsMap,
    readAdditionsArray,
    readDetailEdits,
    getConsoleEntityState,
    hasAccessoryEntityState,
    hasGameEntityState,
    persistAccessoryEntityState,
    persistGameEntityState,
    composeAccessoriesFromEntity,
    composeGamesFromEntity,
    migrateConsoleEntityState,
    migrateAllEntityState,
    mergeWithAdditions,
    applyOverrides,
    applyDetailEdits,
    applyItemDetailEdits,
    normalizeOwnershipType,
    getGamesForConsole,
    isNonGameEntry,
    gameIsOwned,
    gameBelongsToWishlist
  };
})();
