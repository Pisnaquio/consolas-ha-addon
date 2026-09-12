import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../collection-repository.js", import.meta.url), "utf8");

function loadRepository(detailEditsById = {}) {
  const state = { user: { detailEditsById } };
  const context = {
    URLSearchParams,
    window: {
      location: { pathname: "/console.html", search: "" },
      DataStore: {
        getDetailEdits: () => state.user.detailEditsById,
        replaceDetailEdit: (consoleId, bucket) => {
          state.user.detailEditsById[consoleId] = { ...bucket };
        }
      }
    }
  };
  vm.runInNewContext(source, context);
  return { repository: context.window.CollectionRepository, state };
}

test("migrates a legacy game snapshot into patches without overwriting persisted edits", () => {
  const { repository, state } = loadRepository({
    ps4: {
      juegosCatalogo: [
        { id: "catalog-game", ownershipType: "physical", loQuiero: false },
        { id: "manual-game", sourceType: "manual", nombre: "Manual", ownershipType: "physical" }
      ],
      gameEditsById: { "catalog-game": { ownershipType: "digital", prioridad: "alta" } }
    }
  });
  const baseGames = [{ id: "catalog-game", nombre: "Catalog", sourceType: "catalog" }];

  assert.equal(repository.migrateConsoleEntityState("ps4", { baseGames }), true);
  const migrated = state.user.detailEditsById.ps4;
  assert.equal("juegosCatalogo" in migrated, false);
  assert.deepEqual(migrated.gameEditsById["catalog-game"], { ownershipType: "digital", prioridad: "alta" });
  assert.equal(migrated.manualGamesById["manual-game"].nombre, "Manual");
  const composed = repository.getGamesForConsole({ ps4: baseGames }, "ps4");
  assert.equal(composed.length, 2);
  assert.equal(composed[0].ownershipType, "digital");
  assert.equal(composed[0].prioridad, "alta");
  assert.equal(composed[1].id, "manual-game");
});

test("never reads a legacy snapshot as the live catalog", () => {
  const { repository } = loadRepository({
    ps4: { juegosCatalogo: [{ id: "stale-game", nombre: "Stale" }] }
  });
  const baseGames = [{ id: "current-game", nombre: "Current" }];
  assert.deepEqual(repository.getGamesForConsole({ ps4: baseGames }, "ps4"), baseGames);
});

// persistGamePatch existe para la escritura cross-console (Franchise Collection
// Tracker, otras pantallas futuras que tocan un juego sin cargar la consola
// entera). El riesgo que estos tests cierran: escribir un solo juego sin
// destruir los demás patches/manuales de esa misma consola.

test("persistGamePatch changes one catalog game and leaves every other patch untouched", () => {
  const { repository, state } = loadRepository({
    ps4: {
      gameEditsById: {
        "other-game": { ownershipType: "physical", prioridad: "alta" }
      }
    }
  });
  const baseGames = [
    { id: "god-of-war-2018", nombre: "God of War", sourceType: "catalog" },
    { id: "other-game", nombre: "Other", sourceType: "catalog" }
  ];

  repository.persistGamePatch("ps4", "god-of-war-2018", { ownershipType: "physical" }, baseGames);

  const bucket = state.user.detailEditsById.ps4;
  assert.equal(bucket.gameEditsById["god-of-war-2018"].ownershipType, "physical");
  // El juego que no se tocó sigue exactamente como estaba.
  assert.equal(bucket.gameEditsById["other-game"].ownershipType, "physical");
  assert.equal(bucket.gameEditsById["other-game"].prioridad, "alta");
});

test("persistGamePatch does not wipe an existing manual game on the same console", () => {
  const { repository, state } = loadRepository({
    ps4: {
      manualGamesById: {
        "manual-game": { id: "manual-game", nombre: "Manual", sourceType: "manual", ownershipType: "physical" }
      }
    }
  });
  const baseGames = [{ id: "god-of-war-2018", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch("ps4", "god-of-war-2018", { ownershipType: "digital" }, baseGames);

  assert.equal(state.user.detailEditsById.ps4.manualGamesById["manual-game"].nombre, "Manual");
  assert.equal(state.user.detailEditsById.ps4.gameEditsById["god-of-war-2018"].ownershipType, "digital");
});

test("persistGamePatch on an id that exists nowhere yet adds it as a new manual entry", () => {
  const { repository, state } = loadRepository({ ps4: {} });

  repository.persistGamePatch("ps4", "sons-of-sparta-2026", { nombre: "Sons of Sparta", ownershipType: "digital" }, []);

  const manual = state.user.detailEditsById.ps4.manualGamesById["sons-of-sparta-2026"];
  assert.equal(manual.nombre, "Sons of Sparta");
  assert.equal(manual.ownershipType, "digital");
  assert.equal(manual.sourceType, "manual");
});

test("persistGamePatch reads through the composed list, so it merges into an already-patched game correctly", () => {
  const { repository, state } = loadRepository({
    ps4: { gameEditsById: { "god-of-war-2018": { prioridad: "alta", notas: "ya anotado" } } }
  });
  const baseGames = [{ id: "god-of-war-2018", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch("ps4", "god-of-war-2018", { ownershipType: "physical" }, baseGames);

  const patch = state.user.detailEditsById.ps4.gameEditsById["god-of-war-2018"];
  assert.equal(patch.ownershipType, "physical");
  assert.equal(patch.prioridad, "alta", "un patch nuevo no puede pisar un campo que no vino a cambiar");
  assert.equal(patch.notas, "ya anotado");
});

// El Franchise Collection Tracker necesita marcar ediciones especiales y
// hardware como propios sin que aparezcan como "juegos" en la biblioteca de
// ninguna consola real. Estos tests cierran ese riesgo.

test("persistFranchiseTrackedItem stores state without touching any console's game library", () => {
  const { repository, state } = loadRepository({});

  repository.persistFranchiseTrackedItem("god-of-war", "gow-2018-collectors-edition", { owned: true });

  assert.equal(
    JSON.stringify(repository.getFranchiseTrackedItems("god-of-war")),
    JSON.stringify({ "gow-2018-collectors-edition": { owned: true } })
  );
  // No se creó ni tocó ningún bucket de consola real.
  assert.equal("ps4" in state.user.detailEditsById, false);
});

test("persistFranchiseTrackedItem for one franchise never leaks into another franchise's bucket", () => {
  const { repository } = loadRepository({});

  repository.persistFranchiseTrackedItem("god-of-war", "gow-2018-collectors-edition", { owned: true });
  repository.persistFranchiseTrackedItem("other-franchise", "some-item", { owned: true });

  assert.equal(
    JSON.stringify(repository.getFranchiseTrackedItems("god-of-war")),
    JSON.stringify({ "gow-2018-collectors-edition": { owned: true } })
  );
  assert.equal(
    JSON.stringify(repository.getFranchiseTrackedItems("other-franchise")),
    JSON.stringify({ "some-item": { owned: true } })
  );
});

test("persistFranchiseTrackedItem merges into an existing item instead of replacing it", () => {
  const { repository } = loadRepository({});

  repository.persistFranchiseTrackedItem("god-of-war", "omega-collection", { owned: true, note: "regalo" });
  repository.persistFranchiseTrackedItem("god-of-war", "omega-collection", { owned: false });

  assert.equal(
    JSON.stringify(repository.getFranchiseTrackedItems("god-of-war")),
    JSON.stringify({ "omega-collection": { owned: false, note: "regalo" } })
  );
});

test("persistFranchiseTrackedItem does not disturb other tracked items in the same franchise", () => {
  const { repository } = loadRepository({});

  repository.persistFranchiseTrackedItem("god-of-war", "item-a", { owned: true });
  repository.persistFranchiseTrackedItem("god-of-war", "item-b", { owned: true });

  const tracked = repository.getFranchiseTrackedItems("god-of-war");
  assert.equal(tracked["item-a"].owned, true);
  assert.equal(tracked["item-b"].owned, true);
});

test("persistGamePatch never touches accessory edits or other detail-edit fields on the console", () => {
  const { repository, state } = loadRepository({
    ps4: {
      precioPagado: 450,
      accessoryEditsById: { controller: { tengo: true, cantidad: 2 } }
    }
  });
  const baseGames = [{ id: "god-of-war-2018", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch("ps4", "god-of-war-2018", { ownershipType: "physical" }, baseGames);

  const bucket = state.user.detailEditsById.ps4;
  assert.equal(bucket.precioPagado, 450);
  assert.deepEqual(bucket.accessoryEditsById.controller, { tengo: true, cantidad: 2 });
});

test("what you paid for a game survives being persisted, price currency and all", () => {
  // Se perdía en silencio: el normalizador de juegos tiene lista blanca de
  // campos, así que un campo nuevo se descartaba al guardar aunque la UI ya
  // lo mostrara.
  const { repository, state } = loadRepository();
  const baseGames = [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch(
    "ps2",
    "god-of-war",
    { ownershipType: "physical", precioPagado: 34.99, monedaPago: "USD", formaObtencion: "Collection Radar" },
    baseGames,
  );

  const stored = state.user.detailEditsById.ps2.gameEditsById["god-of-war"];
  assert.equal(stored.precioPagado, 34.99);
  assert.equal(stored.monedaPago, "USD");
  assert.equal(stored.formaObtencion, "Collection Radar");
});

test("clearing the paid price stores nothing, never a zero that reads as free", () => {
  const { repository, state } = loadRepository();
  const baseGames = [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch("ps2", "god-of-war", { ownershipType: "physical", precioPagado: 20 }, baseGames);
  repository.persistGamePatch("ps2", "god-of-war", { precioPagado: null }, baseGames);

  const stored = state.user.detailEditsById.ps2.gameEditsById["god-of-war"];
  assert.equal(stored.precioPagado ?? null, null, "sin dato es null, no 0");
});

test("a junk paid price is not persisted as a number", () => {
  const { repository, state } = loadRepository();
  const baseGames = [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }];

  repository.persistGamePatch("ps2", "god-of-war", { ownershipType: "physical", precioPagado: "ni idea" }, baseGames);

  const stored = state.user.detailEditsById.ps2.gameEditsById["god-of-war"];
  assert.equal(stored.precioPagado ?? null, null);
});
