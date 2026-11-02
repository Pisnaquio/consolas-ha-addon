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

// --- Radar: el vínculo se lee de la búsqueda, no se guarda en el deseo --------

const dkcEntry = { key: "snes/donkey-kong-country", source: "game", consoleId: "snes", gameId: "donkey-kong-country", title: "Donkey Kong Country", platform: "Super Nintendo" };

test("a piece finds the radar search that targets its game, preferring an active one", () => {
  const { wishlist } = loadRepositories();
  const searches = [
    { id: "paused", status: "paused", entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "snes" },
    { id: "other-console", status: "active", entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "gba" },
    { id: "active", status: "active", entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "snes" },
    { id: "archived", status: "archived", entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "snes" }
  ];

  assert.equal(wishlist.findRadarSearch(dkcEntry, searches).id, "active");
  assert.equal(wishlist.findRadarSearch(dkcEntry, searches.filter((item) => item.id !== "active")).id, "paused");
  assert.equal(wishlist.findRadarSearch(dkcEntry, [searches[3]]), null, "una búsqueda archivada ya fue retirada");
});

test("a search created by the old «Buscar con Radar» button is still found by its name", () => {
  const { wishlist } = loadRepositories();
  const legacy = { id: "legacy", status: "active", entityType: "", entityId: "", name: "donkey kong country (super nintendo)" };
  const unrelated = { id: "lot", status: "active", entityType: "", entityId: "", name: "Lote SNES" };

  assert.equal(wishlist.findRadarSearch(dkcEntry, [unrelated, legacy]).id, "legacy");
  assert.equal(wishlist.findRadarSearch(dkcEntry, [unrelated]), null);
});

test("the radar draft points at the real game so the link survives renames", () => {
  const { wishlist } = loadRepositories();
  const draft = wishlist.radarSearchDraft({ ...dkcEntry, priceRange: { low: 15, mid: 22, high: 30 } });
  deepEqual(
    { ...draft, criteria: undefined },
    {
      name: "Donkey Kong Country (Super Nintendo)",
      platform: "Super Nintendo",
      searchType: "chase",
      origin: "user",
      status: "active",
      entityType: "game",
      entityId: "donkey-kong-country",
      entityConsoleId: "snes",
      notes: "Creada desde Nacho's Wishlist. Techo de costo a Miami: USD 30 (precio alto del catálogo)."
    }
  );
});

test("a retro cartridge search is born original-only, region-safe and with a cost ceiling", () => {
  const { wishlist } = loadRepositories();
  const { criteria } = wishlist.radarSearchDraft({ ...dkcEntry, priceRange: { low: 15, mid: 22, high: 30 } });
  assert.equal(criteria.originalParts, "required");
  deepEqual(criteria.excludeRegions, ["ntsc-j", "pal"]);
  assert.equal(criteria.maxTotalUsa, 30);
  assert.ok(criteria.excludeTerms.includes("repro"));
  assert.equal(criteria.completeness, undefined, "loose es aceptable en un cartucho");
});

test("a disc search prefers CIB and leaves loose discs out", () => {
  const { wishlist } = loadRepositories();
  const entry = { consoleId: "psp", gameId: "chains-of-olympus", title: "God of War: Chains of Olympus", platform: "PSP", priceRange: { high: 25 } };
  const { criteria } = wishlist.radarSearchDraft(entry);
  assert.ok(criteria.preferTerms.includes("cib"));
  assert.ok(criteria.excludeTerms.includes("disc only"));
  assert.equal(criteria.originalParts, undefined);
});

test("without a catalog price the search stays in draft until a ceiling is set", () => {
  const { wishlist } = loadRepositories();
  const draft = wishlist.radarSearchDraft(dkcEntry);
  assert.equal(draft.status, "draft");
  assert.equal(draft.criteria.maxTotalUsa, undefined);
  assert.match(draft.notes, /borrador/);
});

test("radar capacity says when the active ceiling is reached", () => {
  const { wishlist } = loadRepositories();
  const searches = [{ status: "active" }, { status: "active" }, { status: "paused" }];
  deepEqual(wishlist.radarCapacity(searches, 2), { active: 2, cap: 2, full: true });
  deepEqual(wishlist.radarCapacity(searches, 3), { active: 2, cap: 3, full: false });
  assert.equal(wishlist.radarCapacity(searches, undefined).full, false, "sin techo conocido no se bloquea");
});

test("retirement candidates are the active searches, oldest first, none preselected", () => {
  const { wishlist } = loadRepositories();
  const searches = [
    { id: "new", status: "active", createdAt: "2026-10-24T10:00:00Z", name: "PS Vita" },
    { id: "paused", status: "paused", createdAt: "2026-10-01T10:00:00Z", name: "Pausada" },
    { id: "old", status: "active", createdAt: "2026-10-12T10:00:00Z", name: "Deseados SNES" }
  ];
  deepEqual(wishlist.radarRetirementCandidates(searches).map((search) => search.id), ["old", "new"]);
});

test("Para mí groups feed items under the wish whose game they found, in feed order", () => {
  const { wishlist } = loadRepositories();
  const index = wishlist.wishIndexByGame(
    {
      dk: { id: "dk", kind: "games", title: "Todos los Donkey Kong", items: [{ consoleId: "snes", gameId: "donkey-kong-country" }] },
      gow: { id: "gow", kind: "franchise", title: "Colección God of War", franchiseId: "god-of-war" }
    },
    { "god-of-war": { name: "God of War", releases: [{ id: "gow-ps2", platformId: "ps2", catalogRef: { consoleId: "ps2", gameId: "god-of-war" } }] } }
  );
  const feed = [
    { id: "lot", matches: [{ entityType: "", entityId: "" }] },
    { id: "gow-cib", matches: [{ entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2" }] },
    { id: "dkc", matches: [{ entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "snes" }] },
    { id: "dkc-gba", matches: [{ entityType: "game", entityId: "donkey-kong-country", entityConsoleId: "gba" }] }
  ];

  const { groups, rest } = wishlist.groupFeedByWish(feed, index);
  deepEqual(
    groups.map((group) => [group.title, group.items.map((item) => item.id)]),
    [["Colección God of War", ["gow-cib"]], ["Todos los Donkey Kong", ["dkc"]]]
  );
  deepEqual(rest.map((item) => item.id), ["lot", "dkc-gba"], "otra consola no es el mismo deseo");
});
