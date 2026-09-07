import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = fs.readFileSync(new URL("../collection-import.js", import.meta.url), "utf8");
let currentEntity = {};
const context = { window: { CollectionRepository: { normalizeSlug: (value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(), getConsoleEntityState: () => currentEntity, composeGamesFromEntity: (base, entity) => [...base, ...Object.values(entity.manualGamesById || {})].map((game) => ({ ...game, ...(entity.gameEditsById?.[game.id] || {}) })), composeAccessoriesFromEntity: (base) => base } } };
vm.runInNewContext(source, context);
const importer = context.window.CollectionImport;

test("valida manifiesto v1 y errores por campo", () => {
  const result = importer.validateManifest({ schemaVersion: 2, importId: "", games: [{ title: "" }] });
  assert.equal(result.valid, false);
  assert.ok(result.errors.some((error) => error.path === "schemaVersion"));
  assert.ok(result.errors.some((error) => error.path === "importId"));
  assert.ok(result.errors.some((error) => error.path === "consoleId"));
  assert.ok(result.errors.some((error) => error.path === "games[0].clientRef"));
});

test("prepara manuales, coincidencias y preserva conflictos", () => {
  const result = importer.prepareImport({ schemaVersion: 1, importId: "lote-1", consoleId: "ps4", consolePatch: { notas: "nueva" }, games: [{ clientRef: "one", title: "Existing", ownershipType: "physical" }, { clientRef: "two", title: "Manual", ownershipType: "physical" }], accessories: [], photos: [] }, { baseGames: [{ id: "game-1", nombre: "Existing", ownershipType: "none" }], baseAccessories: [], detailEdits: { ps4: { notas: "persistida" } } });
  assert.equal(result.changes.length, 2);
  assert.equal(result.changes.filter((item) => item.type === "create").length, 1);
  assert.ok(result.conflicts.some((item) => item.path === "consolePatch.notas" && item.action === "preserve"));
  assert.ok(result.manualGamesById["manual-game-lote-1-two"]);
  assert.equal(result.gameEditsById["game-1"].ownershipType, "physical");
});

test("IDs estables permiten reimportar sin duplicar", () => {
  assert.equal(importer.stableId("manual-game", "batch", "ref"), importer.stableId("manual-game", "batch", "ref"));
});

test("attachUploadedPhotos escribe cada foto subida en la entidad correcta (consola, juego, accesorio)", () => {
  const manifest = { schemaVersion: 1, importId: "lote-fotos", consoleId: "ps4", games: [{ clientRef: "g1", title: "Juego Uno", ownershipType: "physical" }], accessories: [{ clientRef: "a1", name: "Control" }], photos: [{ clientRef: "p1", fileName: "console.jpg", entityType: "console", entityId: "ps4" }, { clientRef: "p2", fileName: "game.jpg", entityType: "game", entityId: "g1" }, { clientRef: "p3", fileName: "acc.jpg", entityType: "accessory", entityId: "a1" }] };
  currentEntity = {};
  const prepared = importer.prepareImport(manifest, { baseGames: [], baseAccessories: [], detailEdits: {} });
  let state = { version: 3, user: { overridesById: {}, additionsById: {}, detailEditsById: {} }, meta: {} };
  state = importer.applyPreparedState(prepared, state);
  const uploaded = [
    { ...manifest.photos[0], url: "./media/console.jpg" },
    { ...manifest.photos[1], url: "./media/game.jpg" },
    { ...manifest.photos[2], url: "./media/acc.jpg" },
  ];
  const next = importer.attachUploadedPhotos(state, prepared, uploaded);
  const bucket = next.user.detailEditsById.ps4;
  // fotosPropias is an array built inside the vm context; compare contents rather than using
  // deepEqual against an outer-realm array literal, which Node's assert treats as non-equal
  // across realms even when the values match.
  assert.equal(bucket.fotosPropias.length, 1);
  assert.equal(bucket.fotosPropias[0], "./media/console.jpg");
  const gameId = prepared.changes.find((item) => item.entity === "game").id;
  assert.equal(bucket.manualGamesById[gameId].coverImage, "./media/game.jpg");
  assert.equal(bucket.manualGamesById[gameId].imageStatus, "manual");
  const accessoryId = prepared.changes.find((item) => item.entity === "accessory").id;
  assert.equal(bucket.manualAccessoriesById[accessoryId].image, "./media/acc.jpg");
});

test("attachUploadedPhotos no revienta si falta el bucket de la consola", () => {
  const state = { user: { detailEditsById: {} } };
  const result = importer.attachUploadedPhotos(state, { consoleId: "sin-bucket", changes: [] }, [{ entityType: "console", url: "./media/x.jpg" }]);
  assert.equal(result, state);
});

test("fixture de aceptación: 20 físicos registrados y reimportación idempotente", () => {
  const fixture = JSON.parse(fs.readFileSync(new URL("../../fixtures/collection-import-synthetic.json", import.meta.url), "utf8"));
  let state = { version: 3, user: { overridesById: {}, additionsById: {}, detailEditsById: {} }, meta: {} };
  currentEntity = {};
  const first = importer.prepareImport(fixture, { baseGames: [], baseAccessories: [], detailEdits: state.user.detailEditsById });
  state = importer.applyPreparedState(first, state);
  currentEntity = state.user.detailEditsById.ps4;
  const composed = [...Object.values(currentEntity.manualGamesById || {}), ...Object.values(currentEntity.gameEditsById || {})];
  assert.equal(fixture.games.length, 20);
  assert.equal(composed.length, 20);
  assert.equal(composed.filter((game) => game.ownershipType === "physical").length, 20);
  assert.equal(composed.filter((game) => game.loQuiero === true || game.keepInWishlist === true).length, 0);
  const second = importer.prepareImport(fixture, { baseGames: [], baseAccessories: [], detailEdits: state.user.detailEditsById });
  assert.equal(second.alreadyImported, true);
  assert.equal(second.changes.length, 0);
  assert.equal(Object.keys(state.user.detailEditsById.ps4.manualGamesById).length, 20);
});
