import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const collectionRepoSource = await readFile(new URL("../collection-repository.js", import.meta.url), "utf8");
const wishlistRepoSource = await readFile(new URL("../personal-wishlist-repository.js", import.meta.url), "utf8");

/** Los objetos del contexto vm son de otro realm: se comparan por valor. */
const deepEqual = (actual, expected, message) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, message);

/** Carga las capas reales, igual que los tests del tracker de franquicias. */
function loadRepositories(detailEditsById = {}) {
  const state = { user: { detailEditsById, overridesById: {} } };
  const context = vm.createContext({
    window: {
      location: { pathname: "/nachos-wishlist.html", search: "" },
      DataStore: {
        getDetailEdits: () => ({ ...state.user.detailEditsById }),
        replaceDetailEdit: (key, bucket) => {
          state.user.detailEditsById[key] = { ...bucket };
        },
        getOverrides: () => state.user.overridesById,
        getAdditionsMap: () => ({})
      }
    },
    URLSearchParams,
    fetch: async () => {
      throw new Error("fetch no debería llamarse en estos tests: son puros");
    },
    console
  });
  vm.runInContext(collectionRepoSource, context, { filename: "collection-repository.js" });
  vm.runInContext(wishlistRepoSource, context, { filename: "personal-wishlist-repository.js" });
  return { wishlist: context.window.PersonalWishlistRepository, collection: context.window.CollectionRepository, state };
}

const consolesById = { snes: { id: "snes", nombre: "Super Nintendo" }, ps3: { id: "ps3", nombre: "PlayStation 3" } };

function snesGames(overrides = {}) {
  return [
    { id: "donkey-kong-country", nombre: "Donkey Kong Country", priceRange: { low: 20, mid: 30, high: 45 }, coverUrl: "./dkc.png", ...overrides.dkc1 },
    { id: "donkey-kong-country-2-diddy-s-kong-quest", nombre: "Donkey Kong Country 2: Diddy's Kong Quest", ...overrides.dkc2 },
    { id: "international-superstar-soccer-deluxe", nombre: "International Superstar Soccer Deluxe", ...overrides.issd }
  ];
}

test("a wish is persisted in its own auxiliary bucket and never touches loQuiero", () => {
  const { wishlist, state } = loadRepositories({ snes: { gameEditsById: { "donkey-kong-country": { loQuiero: true } } } });

  const wish = wishlist.saveWish(
    { title: "Todos los Donkey Kong de Super Nintendo", items: [{ consoleId: "snes", gameId: "donkey-kong-country" }] },
    { wishesById: {} }
  );

  assert.equal(wish.id, "todos-los-donkey-kong-de-super-nintendo");
  deepEqual(Object.keys(state.user.detailEditsById).sort(), ["snes", "wishlist:personal"]);
  assert.equal(state.user.detailEditsById["wishlist:personal"].wishesById[wish.id].items[0].gameId, "donkey-kong-country");
  deepEqual(state.user.detailEditsById.snes, { gameEditsById: { "donkey-kong-country": { loQuiero: true } } });
});

test("loQuiero alone never makes something a personal wish", () => {
  const { wishlist } = loadRepositories();
  const model = wishlist.composeModel({
    wishesById: {},
    gamesByConsole: { snes: snesGames({ dkc1: { loQuiero: true }, issd: { keepInWishlist: true } }) },
    consolesById
  });
  assert.equal(model.wishes.length, 0);
  assert.equal(model.totals.missingPieces, 0);
});

test("progress is read from the real game: owned pieces count, missing ones are listed", () => {
  const { wishlist } = loadRepositories();
  const model = wishlist.composeModel({
    wishesById: {
      dk: {
        title: "Todos los Donkey Kong de Super Nintendo",
        kind: "games",
        createdAt: "2026-10-16T00:00:00.000Z",
        items: [
          { consoleId: "snes", gameId: "donkey-kong-country" },
          { consoleId: "snes", gameId: "donkey-kong-country-2-diddy-s-kong-quest" }
        ]
      }
    },
    gamesByConsole: { snes: snesGames({ dkc2: { ownershipType: "physical" } }) },
    consolesById
  });

  const [wish] = model.wishes;
  assert.equal(wish.owned, 1);
  assert.equal(wish.total, 2);
  assert.equal(wish.fulfilled, false);
  deepEqual(wish.missing.map((entry) => entry.gameId), ["donkey-kong-country"]);
  assert.equal(wish.missing[0].platform, "Super Nintendo");
  assert.equal(wish.missing[0].priceRange.mid, 30);
  assert.equal(model.totals.missingPieces, 1);
});

test("fulfilled wishes sort after pending ones", () => {
  const { wishlist } = loadRepositories();
  const model = wishlist.composeModel({
    wishesById: {
      a: { title: "ISS Deluxe", createdAt: "2026-01-01", items: [{ consoleId: "snes", gameId: "international-superstar-soccer-deluxe" }] },
      b: { title: "DKC", createdAt: "2026-02-01", items: [{ consoleId: "snes", gameId: "donkey-kong-country" }] }
    },
    gamesByConsole: { snes: snesGames({ issd: { ownershipType: "both" } }) },
    consolesById
  });
  deepEqual(model.wishes.map((wish) => [wish.id, wish.fulfilled]), [["b", false], ["a", true]]);
  assert.equal(model.totals.fulfilled, 1);
});

test("a game that disappeared from the library stays visible as missing instead of vanishing", () => {
  const { wishlist } = loadRepositories();
  const model = wishlist.composeModel({
    wishesById: { x: { title: "Algo", items: [{ consoleId: "snes", gameId: "borrado" }] } },
    gamesByConsole: { snes: snesGames() },
    consolesById
  });
  assert.equal(model.wishes[0].missing[0].exists, false);
  assert.equal(model.wishes[0].missing[0].coverUrl, "./assets/photos/game-placeholder.svg");
});

test("a franchise wish is the physical shelf of the tracker: digital copies still count as missing", () => {
  const { wishlist } = loadRepositories();
  const franchiseModels = {
    "god-of-war": {
      franchise: { id: "god-of-war", name: "God of War" },
      releases: [
        { id: "gow3", title: "God of War III", platformId: "ps3", isPrimaryPhysicalTarget: true, isOwnedPhysical: true, ownershipType: "physical" },
        { id: "gow2018", title: "God of War", platformId: "ps4", isPrimaryPhysicalTarget: true, isOwnedPhysical: false, ownershipType: "digital" },
        { id: "saga", title: "Saga", platformId: "ps3", isPrimaryPhysicalTarget: false, isOwnedPhysical: false }
      ]
    }
  };
  const model = wishlist.composeModel({
    wishesById: { gow: { kind: "franchise", franchiseId: "god-of-war" } },
    gamesByConsole: {},
    consolesById,
    franchiseModels
  });
  const [wish] = model.wishes;
  assert.equal(wish.title, "God of War");
  assert.equal(wish.total, 2);
  assert.equal(wish.owned, 1);
  assert.equal(wish.missing[0].releaseId, "gow2018");
  assert.equal(wish.missing[0].ownershipType, "digital");
  assert.equal(wish.missing[0].source, "franchise");
});

test("search matches every word against the game and its console", () => {
  const { wishlist } = loadRepositories();
  const context = {
    gamesByConsole: { snes: snesGames(), ps3: [{ id: "dk-ps3", nombre: "Donkey Kong Fake" }] },
    consolesById
  };
  deepEqual(
    wishlist.searchGames("donkey super nintendo", context).map((result) => result.gameId),
    ["donkey-kong-country", "donkey-kong-country-2-diddy-s-kong-quest"]
  );
  deepEqual(wishlist.searchGames("   ", context), []);
});

test("buildWish refuses empty wishes and keeps createdAt when editing", () => {
  const { wishlist } = loadRepositories();
  assert.throws(() => wishlist.buildWish({ title: "Nada", items: [] }), /al menos un juego/);
  assert.throws(() => wishlist.buildWish({ kind: "franchise", title: "GoW" }), /colección temática/);
  assert.throws(() => wishlist.buildWish({ items: [{ consoleId: "snes", gameId: "x" }] }), /nombre/);

  const existing = { id: "dk", createdAt: "2026-01-01T00:00:00.000Z" };
  const edited = wishlist.buildWish(
    { id: "dk", title: "DK", items: [{ consoleId: "snes", gameId: "a" }, { consoleId: "snes", gameId: "a" }] },
    { existing, now: new Date("2026-10-16T12:00:00.000Z") }
  );
  assert.equal(edited.id, "dk");
  assert.equal(edited.createdAt, "2026-01-01T00:00:00.000Z");
  assert.equal(edited.updatedAt, "2026-10-16T12:00:00.000Z");
  assert.equal(edited.items.length, 1, "un mismo juego no se repite dentro del deseo");
});

test("two wishes with the same name get different ids", () => {
  const { wishlist } = loadRepositories();
  const first = wishlist.buildWish({ title: "Pokémon", items: [{ consoleId: "snes", gameId: "a" }] });
  const second = wishlist.buildWish(
    { title: "Pokémon", items: [{ consoleId: "snes", gameId: "b" }] },
    { existingIds: [first.id] }
  );
  assert.equal(first.id, "pokemon");
  assert.notEqual(second.id, first.id);
});

test("deleting a wish removes only that wish", () => {
  const { wishlist, collection, state } = loadRepositories();
  collection.persistPersonalWish("a", { title: "A", items: [{ consoleId: "snes", gameId: "x" }] });
  collection.persistPersonalWish("b", { title: "B", items: [{ consoleId: "snes", gameId: "y" }] });
  wishlist.deleteWish("a");
  deepEqual(Object.keys(state.user.detailEditsById["wishlist:personal"].wishesById), ["b"]);
});

test("marking a piece as owned writes the real game, not the wish", () => {
  const { wishlist, state } = loadRepositories();
  const entry = { source: "game", consoleId: "snes", gameId: "donkey-kong-country" };
  wishlist.markGameOwned(entry, { snes: snesGames() });
  assert.equal(state.user.detailEditsById.snes.gameEditsById["donkey-kong-country"].ownershipType, "physical");
  assert.equal(state.user.detailEditsById["wishlist:personal"], undefined);
});

test("migrating every console skips the wishlist bucket", () => {
  const { collection, state } = loadRepositories({
    "wishlist:personal": { wishesById: { a: { title: "A", items: [] } } }
  });
  assert.equal(collection.migrateAllEntityState({}), 0);
  deepEqual(Object.keys(state.user.detailEditsById["wishlist:personal"]), ["wishesById"]);
});

test("pickSurprise only offers missing pieces of pending wishes", () => {
  const { wishlist } = loadRepositories();
  const model = {
    wishes: [
      { id: "done", title: "Done", fulfilled: true, missing: [] },
      { id: "dk", title: "DK", fulfilled: false, missing: [{ key: "snes/a" }, { key: "snes/b" }] }
    ]
  };
  assert.equal(wishlist.pickSurprise(model, () => 0.99).entry.key, "snes/b");
  assert.equal(wishlist.pickSurprise({ wishes: [] }), null);
});
