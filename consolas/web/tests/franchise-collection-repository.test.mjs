import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const collectionRepoSource = await readFile(new URL("../collection-repository.js", import.meta.url), "utf8");
const franchiseRepoSource = await readFile(new URL("../franchise-collection-repository.js", import.meta.url), "utf8");

/**
 * Carga las dos capas reales (nunca un stub de CollectionRepository): el
 * Franchise Tracker delega en `normalizeOwnershipType`, `getGamesForConsole`,
 * `persistGamePatch`, etc., y lo que hay que probar es justamente que esa
 * delegación es correcta — un stub la escondería.
 */
function loadRepositories(detailEditsById = {}, overridesById = {}) {
  const state = { user: { detailEditsById, overridesById } };
  const context = vm.createContext({
    window: {
      location: { pathname: "/franchise-collection.html", search: "" },
      DataStore: {
        getDetailEdits: () => state.user.detailEditsById,
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
  vm.runInContext(franchiseRepoSource, context, { filename: "franchise-collection-repository.js" });
  return { repository: context.window.FranchiseCollectionRepository, state };
}

function work(overrides = {}) {
  return {
    id: "work-1",
    title: "Work 1",
    era: "greek",
    year: 2005,
    workStatus: "released",
    originalPlatformId: "ps2",
    ...overrides
  };
}

function release(overrides = {}) {
  return {
    id: "release-1",
    title: "Release 1",
    workIds: ["work-1"],
    platformId: "ps2",
    releaseType: "original",
    isPrimaryPhysicalTarget: true,
    ...overrides
  };
}

test("a release with no catalogRef resolves ownership from a manual entry on its own platform", () => {
  const { repository } = loadRepositories({
    ps2: { manualGamesById: { "release-1": { id: "release-1", ownershipType: "physical" } } }
  });

  const [annotated] = repository.annotateReleases([release()], { ps2: [{ id: "release-1", ownershipType: "physical" }] });
  assert.equal(annotated.ownershipType, "physical");
  assert.equal(annotated.isOwned, true);
  assert.equal(annotated.isOwnedPhysical, true);
});

test("a release with a catalogRef reads the real catalog game, not a copy", () => {
  const { repository } = loadRepositories();
  const gamesByConsole = { ps4: [{ id: "god-of-war", ownershipType: "digital", loQuiero: false }] };
  const r = release({ id: "gow-2018-ps4-na", platformId: "ps4", catalogRef: { consoleId: "ps4", gameId: "god-of-war" } });

  const [annotated] = repository.annotateReleases([r], gamesByConsole);
  assert.equal(annotated.ownershipType, "digital");
  assert.equal(annotated.isOwnedPhysical, false);
});

test("a release nobody ever touched is none, not a crash", () => {
  const { repository } = loadRepositories();
  const [annotated] = repository.annotateReleases([release()], {});
  assert.equal(annotated.ownershipType, "none");
  assert.equal(annotated.isOwned, false);
});

test("a work is covered if ANY of its releases is owned, on any platform", () => {
  const { repository } = loadRepositories();
  const releases = repository.annotateReleases(
    [
      release({ id: "r-ps2", platformId: "ps2" }),
      release({ id: "r-collection", platformId: "ps3", isPrimaryPhysicalTarget: false })
    ],
    { ps3: [{ id: "r-collection", ownershipType: "physical" }] }
  );
  const byWorkId = repository.groupReleasesByWorkId(releases);
  const [annotatedWork] = repository.annotateWorks([work()], byWorkId);

  assert.equal(annotatedWork.isCovered, true, "la compilación en PS3 cubre la obra igual que el original en PS2");
});

test("an announced work with no releases yet is simply not covered, not broken", () => {
  const { repository } = loadRepositories();
  const [annotatedWork] = repository.annotateWorks([work({ id: "future-work", workStatus: "announced" })], {});
  assert.equal(annotatedWork.isCovered, false);
  assert.equal(annotatedWork.isDenominator, false, "un anunciado no cuenta para el denominador todavía");
});

test("a work explicitly excluded from the denominator never counts, released or not", () => {
  const { repository } = loadRepositories();
  const w = work({ id: "laufey", workStatus: "released", excludeFromDenominator: true });
  assert.equal(repository.isDenominatorWork(w), false);
});

test("saga coverage only counts denominator works, and the percentage matches", () => {
  const { repository } = loadRepositories();
  const works = [
    { id: "w1", isDenominator: true, isCovered: true },
    { id: "w2", isDenominator: true, isCovered: false },
    { id: "w3", isDenominator: false, isCovered: false } // anunciado, afuera
  ];
  const coverage = repository.computeSagaCoverage(works);
  assert.equal(coverage.total, 2);
  assert.equal(coverage.covered, 1);
  assert.equal(coverage.percent, 50);
});

test("physical shelf only counts primary releases, and digital-only does not complete it", () => {
  const { repository } = loadRepositories();
  const releases = [
    { id: "r1", isPrimaryPhysicalTarget: true, isOwnedPhysical: true },
    { id: "r2", isPrimaryPhysicalTarget: true, isOwnedPhysical: false },
    { id: "r3", isPrimaryPhysicalTarget: false, isOwnedPhysical: true } // una compilación, no cuenta acá
  ];
  const shelf = repository.computePhysicalShelf(releases);
  assert.equal(shelf.total, 2);
  assert.equal(shelf.owned, 1);
});

test("owning only a digital copy does not complete the physical shelf for that work", () => {
  const { repository } = loadRepositories();
  const releases = repository.annotateReleases([release({ isPrimaryPhysicalTarget: true })], {
    ps2: [{ id: "release-1", ownershipType: "digital" }]
  });
  const shelf = repository.computePhysicalShelf(releases);
  assert.equal(shelf.owned, 0, "digital no es estantería física");
});

test("a collector box without the disc does not count as owning the game physically", () => {
  // Regla del PRD: un collector box sin disco no completa lo físico. Se
  // modela así: la edición se trackea aparte de la propiedad del release —
  // marcarla a mano no toca el release ni cuenta para su estantería.
  const { repository, state } = loadRepositories();
  repository.setTrackedOwnership("god-of-war", "some-collectors-edition", true);
  const releases = repository.annotateReleases([release({ isPrimaryPhysicalTarget: true })], {});
  const shelf = repository.computePhysicalShelf(releases);
  assert.equal(shelf.owned, 0);
  assert.equal(state.user.detailEditsById["franchise-tracking:god-of-war"].trackedItemsById["some-collectors-edition"].owned, true);
});

test("crown jewels count editions, crown releases and hardware together", () => {
  const { repository } = loadRepositories();
  const editions = repository.annotateTrackedList([{ id: "e1" }, { id: "e2" }], { e1: { owned: true } }, "edition");
  const crownReleases = repository.annotateTrackedList([{ id: "r1" }], { r1: { owned: true } }, "release");
  const hardware = repository.annotateTrackedList([{ id: "h1" }], {}, "hardware");

  const jewels = repository.computeCrownJewels(editions, crownReleases, hardware);
  assert.equal(jewels.total, 4);
  assert.equal(jewels.owned, 2);
  assert.equal(jewels.percent, 50);
});

test("the next action recommends the platform that unlocks the most uncovered works", () => {
  const { repository } = loadRepositories();
  const works = [
    { id: "chains", isDenominator: true, isCovered: false },
    { id: "ghost", isDenominator: true, isCovered: false },
    { id: "gow3", isDenominator: true, isCovered: false }
  ];
  const releases = [
    { id: "chains-psp", platformId: "psp", workIds: ["chains"] },
    { id: "ghost-psp", platformId: "psp", workIds: ["ghost"] },
    { id: "gow3-ps3", platformId: "ps3", workIds: ["gow3"] }
  ];

  const action = repository.computeNextAction({ works, releases, ownedPlatformIds: new Set(["ps4"]) });
  assert.equal(action.type, "platform");
  assert.equal(action.platformId, "psp", "PSP habilita 2 obras nuevas contra 1 de PS3");
  assert.equal(JSON.stringify(action.newWorkIds.slice().sort()), JSON.stringify(["chains", "ghost"]));
});

test("the next action ignores a platform you already own", () => {
  const { repository } = loadRepositories();
  const works = [{ id: "chains", isDenominator: true, isCovered: false }];
  const releases = [{ id: "chains-psp", platformId: "psp", workIds: ["chains"] }];

  const action = repository.computeNextAction({ works, releases, ownedPlatformIds: new Set(["psp"]) });
  assert.equal(action, null, "ya tenés la única plataforma que hacía falta, y no queda nada más que recomendar");
});

test("the next action never recommends a platform that would not cover anything new", () => {
  const { repository } = loadRepositories();
  const works = [{ id: "chains", isDenominator: true, isCovered: true }]; // ya cubierta por otra plataforma
  const releases = [{ id: "chains-psp", platformId: "psp", workIds: ["chains"] }];

  const action = repository.computeNextAction({ works, releases, ownedPlatformIds: new Set([]) });
  assert.equal(action, null, "conseguir PSP no agregaría nada: la obra ya está cubierta");
});

test("once every relevant platform is owned, the next action falls back to a missing primary release", () => {
  const { repository } = loadRepositories();
  const works = [{ id: "w1", isDenominator: true, isCovered: true }];
  const releases = [
    { id: "r1", platformId: "ps2", workIds: ["w1"], isPrimaryPhysicalTarget: true, isOwnedPhysical: false, year: 2005 }
  ];

  const action = repository.computeNextAction({ works, releases, ownedPlatformIds: new Set(["ps2"]) });
  assert.equal(action.type, "release");
  assert.equal(action.releaseId, "r1");
});

test("a release with no year yet never jumps ahead of a real, dated release in the fallback recommendation", () => {
  const { repository } = loadRepositories();
  const works = [{ id: "w1", isDenominator: true, isCovered: true }];
  const releases = [
    // Sin año (por ejemplo un anunciado sin fecha) — no puede "ganar" por
    // ordenar null como si fuera año 0.
    { id: "undated", platformId: "ps5", workIds: ["w1"], isPrimaryPhysicalTarget: true, isOwnedPhysical: false, year: null },
    { id: "dated-2005", platformId: "ps2", workIds: ["w1"], isPrimaryPhysicalTarget: true, isOwnedPhysical: false, year: 2005 }
  ];

  const action = repository.computeNextAction({ works, releases, ownedPlatformIds: new Set(["ps2", "ps5"]) });
  assert.equal(action.releaseId, "dated-2005");
});

test("setReleaseOwnership on a catalogRef release writes the real catalog game via persistGamePatch", () => {
  const { repository, state } = loadRepositories();
  const r = release({ id: "gow-2018-ps4-na", platformId: "ps4", catalogRef: { consoleId: "ps4", gameId: "god-of-war" } });
  const baseGamesByConsole = { ps4: [{ id: "god-of-war", nombre: "God of War", sourceType: "catalog" }] };

  repository.setReleaseOwnership(r, "physical", baseGamesByConsole);

  assert.equal(state.user.detailEditsById.ps4.gameEditsById["god-of-war"].ownershipType, "physical");
});

test("setReleaseOwnership on a release with no catalogRef creates a manual entry on its own platform", () => {
  const { repository, state } = loadRepositories();
  const r = release({ id: "gow-2005-ps2-na", platformId: "ps2" });

  repository.setReleaseOwnership(r, "physical", {});

  assert.equal(state.user.detailEditsById.ps2.manualGamesById["gow-2005-ps2-na"].ownershipType, "physical");
});

test("a catalogRef release borrows the cover and price range of the real catalog game", () => {
  const { repository } = loadRepositories();
  const gamesByConsole = {
    ps4: [{ id: "god-of-war", ownershipType: "digital", coverUrl: "./assets/game-covers/ps4/god-of-war.jpg", priceRange: { low: 10, mid: 18, high: 25 } }]
  };
  const r = release({ id: "gow-2018-ps4-na", platformId: "ps4", catalogRef: { consoleId: "ps4", gameId: "god-of-war" } });

  const [annotated] = repository.annotateReleases([r], gamesByConsole);
  assert.equal(annotated.coverUrl, "./assets/game-covers/ps4/god-of-war.jpg");
  assert.equal(annotated.priceRange.mid, 18);
});

test("a release with no catalogRef never gets a cover or a price, even once it has a manual entry", () => {
  const { repository } = loadRepositories();
  const gamesByConsole = { ps2: [{ id: "release-1", ownershipType: "physical", sourceType: "manual" }] };
  const [annotated] = repository.annotateReleases([release()], gamesByConsole);
  assert.equal(annotated.coverUrl, null, "una entrada manual nunca trae portada inventada");
  assert.equal(annotated.priceRange, null);
});

test("annotateTrackedList exposes each item's own checklist progress, isolated from the others", () => {
  const { repository } = loadRepositories();
  const items = repository.annotateTrackedList(
    [{ id: "e1" }, { id: "e2" }],
    { e1: { owned: true, checklist: { artbook: true, disc: false } } },
    "edition"
  );
  assert.equal(items[0].checklist.artbook, true);
  assert.equal(items[1].checklist.artbook, undefined, "e2 nunca tuvo checklist: no hereda el de e1");
});

test("setTrackedChecklistItem merges one key into the existing checklist without dropping the others", () => {
  const { repository, state } = loadRepositories();
  repository.setTrackedChecklistItem("god-of-war", "gow-ultimate-trilogy-edition", { artbook: true }, "postcards", true);

  const stored = state.user.detailEditsById["franchise-tracking:god-of-war"].trackedItemsById["gow-ultimate-trilogy-edition"];
  assert.equal(stored.checklist.artbook, true, "el chequeo previo no se pierde");
  assert.equal(stored.checklist.postcards, true);
});

test("compose model end to end: a compilation covers two works without duplicating the physical shelf", () => {
  const { repository } = loadRepositories(
    {},
    {}
  );
  const franchise = {
    works: [work({ id: "w1" }), work({ id: "w2", title: "Work 2" })],
    releases: [
      release({ id: "r1-original", workIds: ["w1"], platformId: "ps2", isPrimaryPhysicalTarget: true }),
      release({ id: "r2-original", workIds: ["w2"], platformId: "ps2", isPrimaryPhysicalTarget: true }),
      release({
        id: "collection",
        workIds: ["w1", "w2"],
        platformId: "ps3",
        releaseType: "collection",
        isPrimaryPhysicalTarget: false
      })
    ],
    editions: [],
    hardware: [],
    historical: []
  };
  const gamesByConsole = { ps3: [{ id: "collection", ownershipType: "physical" }] };

  const model = repository.composeModel({ franchise, gamesByConsole, ownedPlatformIds: new Set(["ps3"]), trackedItems: {} });

  assert.equal(model.progress.sagaCoverage.covered, 2, "la compilación cubre las dos obras");
  assert.equal(model.progress.physicalShelf.owned, 0, "pero ninguna estantería física original se completó con la compilación");
});
