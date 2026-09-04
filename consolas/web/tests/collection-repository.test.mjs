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
