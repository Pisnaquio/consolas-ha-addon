import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../radar-purchase.js", import.meta.url), "utf8");

function load({
  recordPurchaseResult = { ok: true, purchase: {}, decision: {} },
  recordPurchaseImpl = null,
  baseGamesByConsole = { ps2: [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }] },
} = {}) {
  const overrideCalls = [];
  const detailEditCalls = [];
  const recordPurchaseCalls = [];
  const gamePatchCalls = [];
  const dataStore = {
    updateOverride(id, patch) { overrideCalls.push({ id, patch }); },
    updateDetailEdit(id, patch) { detailEditCalls.push({ id, patch }); },
  };
  const repository = {
    async recordPurchase(payload) {
      recordPurchaseCalls.push(payload);
      if (recordPurchaseImpl) return recordPurchaseImpl(payload);
      return recordPurchaseResult;
    },
  };
  // La capa central real se prueba aparte (collection-repository.test.mjs);
  // acá importa que el radar la llame con el par completo y las bases reales.
  const collectionRepository = {
    persistGamePatch(consoleId, gameId, patch, baseGames) {
      gamePatchCalls.push({ consoleId, gameId, patch, baseGames });
    },
    getGamesForConsole(byConsole, consoleId) {
      return byConsole[consoleId] || [];
    },
  };
  const windowStub = {
    DataStore: dataStore,
    RadarRepository: repository,
    CollectionRepository: collectionRepository,
  };
  const context = vm.createContext({
    window: windowStub,
    console: { error() {} },
    fetch: async () => ({ ok: true, async json() { return { byConsole: Object.fromEntries(
      Object.entries(baseGamesByConsole).map(([id, juegosCatalogo]) => [id, { juegosCatalogo }])
    ) }; } }),
  });
  vm.runInContext(source, context, { filename: "radar-purchase.js" });
  return {
    RadarPurchase: windowStub.RadarPurchase,
    overrideCalls,
    detailEditCalls,
    recordPurchaseCalls,
    gamePatchCalls,
  };
}

test("a console purchase marks it owned with the price paid, through DataStore", async () => {
  const { RadarPurchase, overrideCalls, detailEditCalls } = load();

  const result = await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "console", entityId: "ps2", priceAmount: 55, currency: "USD",
  });

  assert.equal(result.collectionWritten, true);
  // Objetos cruzan el límite del vm context: se comparan serializados, no por
  // referencia de prototipo (deepEqual falla ahí aunque el contenido coincida).
  assert.equal(JSON.stringify(overrideCalls), JSON.stringify([{ id: "ps2", patch: { tengo: true, categoria: "coleccion" } }]));
  assert.equal(
    JSON.stringify(detailEditCalls),
    JSON.stringify([{ id: "ps2", patch: { precioPagado: 55, monedaPago: "USD", formaObtencion: "Collection Radar" } }]),
  );
});

test("the server always hears about the purchase first, evidence before collection", async () => {
  const { RadarPurchase, recordPurchaseCalls } = load();

  await RadarPurchase.registerPurchase({ listingId: "ebay-us-1", entityType: "console", entityId: "ps2", priceAmount: 55 });

  assert.equal(recordPurchaseCalls.length, 1);
  assert.equal(recordPurchaseCalls[0].listingId, "ebay-us-1");
  assert.equal(recordPurchaseCalls[0].entityId, "ps2");
});

test("a game purchase writes the real game in its own console's library, by composite id", async () => {
  const { RadarPurchase, gamePatchCalls, overrideCalls } = load();

  const result = await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2",
    priceAmount: 15,
  });

  assert.equal(result.collectionWritten, true);
  assert.equal(gamePatchCalls.length, 1);
  assert.equal(gamePatchCalls[0].consoleId, "ps2");
  assert.equal(gamePatchCalls[0].gameId, "god-of-war");
  assert.equal(
    JSON.stringify(gamePatchCalls[0].patch),
    JSON.stringify({ ownershipType: "physical", loTengo: true, keepInWishlist: false }),
  );
  assert.equal(overrideCalls.length, 0, "comprar un juego no marca la consola como tuya");
});

test("the game write carries the console's real catalog as base, never an empty list", async () => {
  const { RadarPurchase, gamePatchCalls } = load();

  await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2",
    priceAmount: 15,
  });

  // Sin las bases reales, persistGamePatch trataría un juego de catálogo como
  // inexistente y lo agregaría como manual duplicado.
  assert.equal(gamePatchCalls[0].baseGames.length, 1);
  assert.equal(gamePatchCalls[0].baseGames[0].id, "god-of-war");
});

test("a game with no console behind it records evidence but never guesses a platform", async () => {
  const { RadarPurchase, gamePatchCalls, overrideCalls, recordPurchaseCalls } = load();

  const result = await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "aladdin", priceAmount: 15,
  });

  assert.equal(result.collectionWritten, false);
  assert.equal(gamePatchCalls.length, 0);
  assert.equal(overrideCalls.length, 0);
  assert.equal(recordPurchaseCalls.length, 1, "la evidencia igual queda para el presupuesto");
});

test("an accessory still records evidence only: it has no catalog id to marry", async () => {
  const { RadarPurchase, gamePatchCalls, overrideCalls } = load();

  const result = await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "accessory", entityId: "dualshock", entityConsoleId: "ps2",
    priceAmount: 15,
  });

  assert.equal(result.collectionWritten, false);
  assert.equal(gamePatchCalls.length, 0);
  assert.equal(overrideCalls.length, 0);
});

test("if the server call fails, nothing gets written to the collection", async () => {
  const { RadarPurchase, overrideCalls, detailEditCalls } = load({
    recordPurchaseImpl: async () => { throw new Error("listing not found"); },
  });

  await assert.rejects(
    RadarPurchase.registerPurchase({ listingId: "ebay-us-1", entityType: "console", entityId: "ps2", priceAmount: 55 }),
    /listing not found/,
  );
  assert.equal(overrideCalls.length, 0, "sin evidencia registrada, no se toca la colección");
  assert.equal(detailEditCalls.length, 0);
});

test("canWriteCollection is the single source of truth for what this version supports", () => {
  const { RadarPurchase } = load();

  assert.equal(RadarPurchase.canWriteCollection({ entityType: "console" }), true);
  assert.equal(
    RadarPurchase.canWriteCollection({ entityType: "game", entityConsoleId: "ps2" }),
    true,
  );
  assert.equal(
    RadarPurchase.canWriteCollection({ entityType: "game" }),
    false,
    "un juego sin consola no se puede escribir: el mismo id existe en varias plataformas",
  );
  assert.equal(RadarPurchase.canWriteCollection({ entityType: "accessory", entityConsoleId: "ps2" }), false);
  assert.equal(RadarPurchase.canWriteCollection({ entityType: "" }), false);
  assert.equal(RadarPurchase.canWriteCollection(), false);
});

test("the server hears the console id too, so the history can say which platform", async () => {
  const { RadarPurchase, recordPurchaseCalls } = load();

  await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2",
    priceAmount: 15,
  });

  assert.equal(recordPurchaseCalls[0].entityConsoleId, "ps2");
});

test("buying a game the library does not have yet names it, so it is never anonymous", async () => {
  const { RadarPurchase, gamePatchCalls } = load({ baseGamesByConsole: { ps2: [] } });

  await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2",
    entityName: "God of War", priceAmount: 34.99,
  });

  assert.equal(gamePatchCalls[0].patch.nombre, "God of War");
});

test("buying a game that already exists never overwrites its catalog name", async () => {
  const { RadarPurchase, gamePatchCalls } = load({
    baseGamesByConsole: { ps2: [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }] },
  });

  await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2",
    entityName: "God of War PS2 lo que sea que escribí en la búsqueda", priceAmount: 34.99,
  });

  assert.equal("nombre" in gamePatchCalls[0].patch, false, "el nombre real del catálogo manda");
  assert.equal(gamePatchCalls[0].patch.ownershipType, "physical");
});
