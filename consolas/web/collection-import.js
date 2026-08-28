(() => {
  const PHOTO_ROLES = new Set(["principal", "frontal", "trasera", "etiqueta", "accesorios", "contenido", "estado_cosmetico"]);
  const DIGITAL_ORIGINS = new Set(["purchased", "ps_plus_claimed", "subscription", "free_to_play", "unknown"]);
  const CLASSIFICATION_STATUS = new Set(["pending", "inferred", "verified"]);
  const OWNERSHIP = new Set(["none", "physical", "digital", "both"]);

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const text = (value) => String(value ?? "").trim();
  const key = (value) => window.CollectionRepository?.normalizeSlug?.(text(value)) || text(value).toLowerCase();
  const stableId = (prefix, importId, ref) => `${prefix}-${key(importId).replaceAll(" ", "-")}-${key(ref).replaceAll(" ", "-")}`.slice(0, 160);

  function validateManifest(input) {
    const errors = [];
    const manifest = input && typeof input === "object" ? input : null;
    if (!manifest) return { valid: false, errors: [{ path: "manifest", message: "El manifiesto debe ser un objeto JSON." }] };
    if (manifest.schemaVersion !== 1) errors.push({ path: "schemaVersion", message: "schemaVersion debe ser 1." });
    if (!text(manifest.importId)) errors.push({ path: "importId", message: "El identificador de lote es obligatorio." });
    if (!text(manifest.consoleId)) errors.push({ path: "consoleId", message: "consoleId es obligatorio." });
    for (const collection of ["games", "accessories", "photos"]) {
      if (manifest[collection] !== undefined && !Array.isArray(manifest[collection])) errors.push({ path: collection, message: "Debe ser un array." });
    }
    (manifest.games || []).forEach((game, index) => {
      const path = `games[${index}]`;
      if (!text(game?.clientRef)) errors.push({ path: `${path}.clientRef`, message: "clientRef es obligatorio." });
      if (!text(game?.title)) errors.push({ path: `${path}.title`, message: "title es obligatorio." });
      if (game?.ownershipType && !OWNERSHIP.has(game.ownershipType)) errors.push({ path: `${path}.ownershipType`, message: "ownershipType no es válido." });
      if (game?.digitalOrigin && !DIGITAL_ORIGINS.has(game.digitalOrigin)) errors.push({ path: `${path}.digitalOrigin`, message: "digitalOrigin no es válido." });
      if (game?.classificationStatus && !CLASSIFICATION_STATUS.has(game.classificationStatus)) errors.push({ path: `${path}.classificationStatus`, message: "classificationStatus no es válido." });
      if (game?.entitledPlatforms && (!Array.isArray(game.entitledPlatforms) || game.entitledPlatforms.some((platform) => !["PS4", "PS5"].includes(platform)))) errors.push({ path: `${path}.entitledPlatforms`, message: "entitledPlatforms sólo admite PS4 y PS5." });
    });
    (manifest.accessories || []).forEach((accessory, index) => {
      const path = `accessories[${index}]`;
      if (!text(accessory?.clientRef)) errors.push({ path: `${path}.clientRef`, message: "clientRef es obligatorio." });
      if (!text(accessory?.name)) errors.push({ path: `${path}.name`, message: "name es obligatorio." });
      if (accessory?.completeness && !["complete", "incomplete", "unknown"].includes(accessory.completeness)) errors.push({ path: `${path}.completeness`, message: "completeness no es válido." });
    });
    (manifest.photos || []).forEach((photo, index) => {
      const path = `photos[${index}]`;
      if (!text(photo?.clientRef)) errors.push({ path: `${path}.clientRef`, message: "clientRef es obligatorio." });
      if (!text(photo?.fileName)) errors.push({ path: `${path}.fileName`, message: "fileName es obligatorio." });
      if (!text(photo?.entityType) || !["console", "game", "accessory"].includes(photo.entityType)) errors.push({ path: `${path}.entityType`, message: "entityType debe ser console, game o accessory." });
      if (!text(photo?.entityId)) errors.push({ path: `${path}.entityId`, message: "entityId es obligatorio." });
      if (photo?.role && !PHOTO_ROLES.has(photo.role)) errors.push({ path: `${path}.role`, message: "role no es válido." });
    });
    return { valid: errors.length === 0, errors, manifest };
  }

  function findExisting(list, candidate, nameFields = ["nombre", "title", "name"]) {
    const candidateId = text(candidate.catalogId || candidate.entityId || candidate.id);
    return (list || []).find((item) => candidateId && text(item.id) === candidateId) || (list || []).find((item) => nameFields.some((field) => key(item[field]) && key(item[field]) === key(candidate.title || candidate.name)));
  }

  function mergePreservingExisting(existing, incoming, overwrite, conflicts, path) {
    const result = { ...(existing || {}) };
    Object.entries(incoming || {}).forEach(([field, value]) => {
      if (value === undefined || value === null || value === "") return;
      if (existing && existing[field] !== undefined && JSON.stringify(existing[field]) !== JSON.stringify(value)) {
        conflicts.push({ path: `${path}.${field}`, existing: existing[field], incoming: value, action: overwrite ? "overwrite" : "preserve" });
        if (!overwrite) return;
      }
      result[field] = value;
    });
    return result;
  }

  function buildGranularPatch(incoming, catalog, persisted, overwrite, conflicts, path) {
    const patch = {};
    Object.entries(incoming || {}).forEach(([field, value]) => {
      if (["id", "nombre", "sourceType"].includes(field) || value === undefined || value === null || value === "") return;
      const persistedValue = persisted?.[field];
      if (persisted && persistedValue !== undefined && JSON.stringify(persistedValue) !== JSON.stringify(value)) {
        conflicts.push({ path: `${path}.${field}`, existing: persistedValue, incoming: value, action: overwrite ? "overwrite" : "preserve" });
        if (!overwrite) return;
      }
      if (overwrite || !catalog || JSON.stringify(catalog[field]) !== JSON.stringify(value) || persistedValue !== undefined) patch[field] = value;
    });
    return patch;
  }

  function prepareImport(manifest, context = {}) {
    const validation = validateManifest(manifest);
    if (!validation.valid) return { ...validation, changes: [], warnings: [] };
    const overwrite = context.overwrite === true;
    const consoleId = text(manifest.consoleId);
    const conflicts = [];
    const existingDetail = clone(context.detailEdits?.[consoleId] || {});
    const detail = mergePreservingExisting(existingDetail, manifest.consolePatch || {}, overwrite, conflicts, "consolePatch");
    const alreadyImported = detail.importBatches?.[text(manifest.importId)] !== undefined;
    const baseGames = context.baseGames || [];
    const baseAccessories = context.baseAccessories || [];
    const entity = window.CollectionRepository?.getConsoleEntityState?.(consoleId) || {};
    const currentGames = window.CollectionRepository?.composeGamesFromEntity?.(baseGames, entity) || baseGames;
    const currentAccessories = window.CollectionRepository?.composeAccessoriesFromEntity?.(baseAccessories, entity) || baseAccessories;
    const changes = [];
    const warnings = [];
    const gameEditsById = { ...(entity.gameEditsById || {}) };
    const manualGamesById = { ...(entity.manualGamesById || {}) };
    const accessoryEditsById = { ...(entity.accessoryEditsById || {}) };
    const manualAccessoriesById = { ...(entity.manualAccessoriesById || {}) };

    (manifest.games || []).forEach((game) => {
      const existing = findExisting(currentGames, game);
      const id = existing?.id || (game.catalogId ? text(game.catalogId) : stableId("manual-game", manifest.importId, game.clientRef));
      const incoming = { id, nombre: text(game.title), sourceType: existing ? "catalog" : "manual", ownershipType: game.ownershipType || "none", loQuiero: game.loQuiero === true, keepInWishlist: game.keepInWishlist === true, digitalOrigin: game.digitalOrigin || "unknown", entitledPlatforms: Array.isArray(game.entitledPlatforms) ? game.entitledPlatforms : [], classificationStatus: game.classificationStatus || "pending", plataforma: game.platform || "", region: game.region || "", condicion: game.condition || "", discoCartucho: game.discCartridge || "", caja: game.box || "", manualInsertos: game.manualInserts || "", notas: game.notes || "", ...(game.edition ? { editions: [game.edition] } : {}) };
      const persisted = entity.gameEditsById?.[id] || null;
      const patch = buildGranularPatch(incoming, existing, persisted, overwrite, conflicts, `games.${game.clientRef}`);
      if (existing) gameEditsById[id] = { ...(gameEditsById[id] || {}), ...patch };
      else manualGamesById[id] = { ...(manualGamesById[id] || {}), ...incoming, sourceType: "manual" };
      changes.push({ type: existing ? "update" : "create", entity: "game", id, title: incoming.nombre, clientRef: game.clientRef });
    });
    (manifest.accessories || []).forEach((accessory) => {
      const existing = findExisting(currentAccessories, accessory, ["nombre", "name"]);
      const id = existing?.id || stableId("manual-accessory", manifest.importId, accessory.clientRef);
      const incoming = { id, nombre: text(accessory.name), sourceType: existing ? "catalog" : "manual", tipo: accessory.type || "otro", marcaModelo: accessory.brandModel || "", edicion: accessory.edition || "", cantidad: Number(accessory.quantity) || 1, funcionando: accessory.functioning, estado: accessory.cosmeticState || "", caja: accessory.box || "", dependencias: accessory.dependencies || [], componentesIncluidos: accessory.includedComponents || [], componentesFaltantes: accessory.missingComponents || [], completitud: accessory.completeness || "unknown", completeness: accessory.completeness || "unknown", notas: accessory.notes || "" };
      const persisted = entity.accessoryEditsById?.[id] || null;
      const patch = buildGranularPatch(incoming, existing, persisted, overwrite, conflicts, `accessories.${accessory.clientRef}`);
      if (existing) accessoryEditsById[id] = { ...(accessoryEditsById[id] || {}), ...patch };
      else manualAccessoriesById[id] = { ...(manualAccessoriesById[id] || {}), ...incoming, sourceType: "manual" };
      changes.push({ type: existing ? "update" : "create", entity: "accessory", id, title: incoming.nombre, clientRef: accessory.clientRef });
    });
    (manifest.photos || []).forEach((photo) => {
      if (!context.filesByName?.[photo.fileName]) warnings.push(`Falta el archivo ${photo.fileName}; se conservará placeholder.`);
    });
    return { valid: true, manifest, consoleId, detail, gameEditsById, manualGamesById, accessoryEditsById, manualAccessoriesById, conflicts, changes: alreadyImported ? [] : changes, warnings, media: alreadyImported ? [] : manifest.photos || [], overwrite, alreadyImported };
  }

  function applyPreparedState(prepared, state) {
    const next = clone(state);
    const bucket = next.user.detailEditsById[prepared.consoleId] || {};
    next.user.detailEditsById[prepared.consoleId] = { ...bucket, ...prepared.detail, gameEditsById: prepared.gameEditsById, manualGamesById: prepared.manualGamesById, accessoryEditsById: prepared.accessoryEditsById, manualAccessoriesById: prepared.manualAccessoriesById, importBatches: { ...(bucket.importBatches || {}), [prepared.manifest.importId]: { importedAt: new Date().toISOString(), schemaVersion: prepared.manifest.schemaVersion } } };
    return next;
  }

  window.CollectionImport = { PHOTO_ROLES, DIGITAL_ORIGINS, CLASSIFICATION_STATUS, validateManifest, prepareImport, applyPreparedState, stableId };
})();
