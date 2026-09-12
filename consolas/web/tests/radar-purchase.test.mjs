import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../radar-purchase.js", import.meta.url), "utf8");

function load({ recordPurchaseResult = { ok: true, purchase: {}, decision: {} }, recordPurchaseImpl = null } = {}) {
  const overrideCalls = [];
  const detailEditCalls = [];
  const recordPurchaseCalls = [];
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
  const windowStub = { DataStore: dataStore, RadarRepository: repository };
  const context = vm.createContext({ window: windowStub, console: { error() {} } });
  vm.runInContext(source, context, { filename: "radar-purchase.js" });
  return { RadarPurchase: windowStub.RadarPurchase, overrideCalls, detailEditCalls, recordPurchaseCalls };
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

test("a game or accessory purchase records evidence but does not touch the collection yet", async () => {
  const { RadarPurchase, overrideCalls, detailEditCalls } = load();

  const result = await RadarPurchase.registerPurchase({
    listingId: "ebay-us-1", entityType: "game", entityId: "aladdin", priceAmount: 15,
  });

  assert.equal(result.collectionWritten, false);
  assert.equal(overrideCalls.length, 0);
  assert.equal(detailEditCalls.length, 0);
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

  assert.equal(RadarPurchase.canWriteCollection("console"), true);
  assert.equal(RadarPurchase.canWriteCollection("game"), false);
  assert.equal(RadarPurchase.canWriteCollection("accessory"), false);
  assert.equal(RadarPurchase.canWriteCollection(""), false);
});
