import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_QUERIES_PER_SEARCH,
  expandPlan,
  expandWishlist,
  queryText,
  recommendedGames,
  titleCeiling,
  wishedGames,
} from "../../scripts/lib/radar-portfolio.mjs";

// Las búsquedas de deseados se arman con el estado real al aplicar el plan, no
// con una lista escrita a mano. Estos tests fijan las reglas que deciden qué
// se persigue y hasta cuánto: pertenencia igual que la capa central, techo por
// título que nunca pasa el general, y nada inventado cuando falta el precio.

const catalog = [
  { id: "a", nombre: "Juego A", priceRange: { low: 15, mid: 22, high: 30 }, priceGuide: { cib: 55 } },
  { id: "b", nombre: "Juego B", priceRange: { low: 40, mid: 60, high: 80 } },
  { id: "c", nombre: "Juego C: Edición", priceRange: { low: 150, mid: 260, high: 400 } },
  { id: "d", nombre: "Juego D", priceRange: { low: 10, mid: 14, high: 20 } },
  { id: "e", nombre: "Juego E", priceRange: { low: 10, mid: 14, high: 20 } },
];

const state = {
  user: {
    detailEditsById: {
      // Nacho's Wishlist: lo único que el dueño pidió.
      "wishlist:personal": {
        wishesById: {
          "deseo-1": { id: "deseo-1", kind: "games", items: [{ consoleId: "zeta", gameId: "a" }, { consoleId: "zeta", gameId: "c" }] },
          "deseo-2": { id: "deseo-2", kind: "games", items: [{ consoleId: "zeta", gameId: "manual-1" }, { consoleId: "zeta", gameId: "d" }] },
          "deseo-3": { id: "deseo-3", kind: "games", items: [{ consoleId: "otra", gameId: "x" }] },
          gow: { id: "gow", kind: "franchise", franchiseId: "god-of-war", items: [] },
        },
      },
      zeta: {
        gameEditsById: {
          a: { loQuiero: true },
          b: { keepInWishlist: true },
          c: { keepInWishlist: true },
          d: { loQuiero: true, ownershipType: "physical" },
          e: {},
        },
        manualGamesById: {
          "manual-1": { nombre: "Juego Manual", loQuiero: true },
          "manual-2": { nombre: "Ya lo tengo", loQuiero: true, loTengo: true },
        },
      },
    },
  },
};

test("los deseados salen sólo de Nacho's Wishlist, nunca de loQuiero/keepInWishlist", () => {
  const games = wishedGames(state, catalog, "zeta");
  // b es keepInWishlist y no está en la wishlist: no entra. d ya se tiene.
  assert.deepEqual(games.map((game) => game.id), ["a", "c", "manual-1"]);
  assert.ok(games.every((game) => game.explicit));
});

test("una recomendación vieja no consume cupo de wishlist", () => {
  const onlyOld = { user: { detailEditsById: { zeta: { gameEditsById: { b: { loQuiero: true, keepInWishlist: true } } } } } };
  assert.deepEqual(wishedGames(onlyOld, catalog, "zeta"), []);
  const { search } = expandWishlist({ name: "Deseados Zeta", consoleId: "zeta", querySuffix: "zeta", criteria: {} },
    { state: onlyOld, gamesByConsole: { zeta: catalog } });
  assert.equal(search, null);
});

test("las recomendaciones son loQuiero o keepInWishlist, y nunca lo que ya se tiene", () => {
  const games = recommendedGames(state, catalog, "zeta");
  assert.deepEqual(
    games.map((game) => [game.id, game.explicit]),
    [["a", true], ["b", false], ["c", false], ["manual-1", true]]
  );
});

test("una plantilla de recomendaciones corre a la mañana y lo dice", () => {
  const { search } = expandWishlist(
    { name: "Recomendados Zeta", consoleId: "zeta", querySuffix: "zeta", source: "recommendations", criteria: { maxItemPrice: 100 } },
    { state, gamesByConsole: { zeta: catalog } }
  );
  assert.deepEqual(search.slots, ["morning"]);
  assert.match(search.notes, /nunca alertan/);
  assert.equal(search.criteria.titleTargets.length, 3);
});

test("el explícito llega al precio alto; la recomendación, al típico", () => {
  assert.equal(titleCeiling({ explicit: true, priceRange: { low: 15, mid: 22, high: 30 } }, 100), 30);
  assert.equal(titleCeiling({ explicit: false, priceRange: { low: 40, mid: 60, high: 80 } }, 100), 60);
});

test("ningún título pasa el techo general", () => {
  assert.equal(titleCeiling({ explicit: true, priceRange: { low: 40, mid: 60, high: 80 } }, 50), 50);
});

test("si ni la copia más barata entra en el techo, no se persigue", () => {
  assert.equal(titleCeiling({ explicit: true, priceRange: { low: 150, mid: 260, high: 400 } }, 100), null);
});

test("sin precio de catálogo manda el techo general", () => {
  assert.equal(titleCeiling({ explicit: true, priceRange: null }, 100), 100);
});

test("la consulta es el nombre sin signos más la plataforma", () => {
  assert.equal(queryText("Juego C: Edición!", "snes"), "Juego C Edición snes");
  assert.equal(queryText("Tony Hawk’s Pro Skater 2", "ps1"), "Tony Hawk's Pro Skater 2 ps1");
});

test("la plantilla se vuelve una búsqueda con un objetivo por título", () => {
  const { search, skipped } = expandWishlist(
    {
      name: "Deseados Zeta",
      platform: "Zeta",
      consoleId: "zeta",
      querySuffix: "zeta",
      criteria: { anyTerms: ["zeta"], maxItemPrice: 100 },
    },
    { state, gamesByConsole: { zeta: catalog } }
  );
  assert.equal(search.entityType, "game");
  assert.equal(search.entityConsoleId, "zeta");
  assert.equal(search.consoleId, undefined);
  assert.equal(search.slots, undefined, "un deseo corre en todos los slots");
  // El más barato adelante; el manual sin precio, al techo general.
  assert.deepEqual(
    search.criteria.titleTargets.map((target) => [target.gameId, target.maxItemPrice]),
    [["a", 30], ["manual-1", 100]]
  );
  assert.equal(search.searchQuery, "Juego A zeta");
  // El techo CIB sale del precio CIB del título; sin uno defendible no hay techo.
  assert.equal(search.criteria.titleTargets[0].maxBoxedPrice, 55);
  assert.equal(search.criteria.titleTargets[1].maxBoxedPrice, undefined);
  assert.deepEqual(search.criteria.queries, ["Juego Manual zeta"]);
  assert.deepEqual(search.criteria.anyTerms, ["zeta"]);
  assert.deepEqual(skipped.map((item) => item.name), ["Juego C: Edición"]);
});

test("sin precio CIB del título, la caja no hereda el techo general", () => {
  const { search } = expandWishlist(
    { name: "Recomendados Zeta", consoleId: "zeta", querySuffix: "zeta", source: "recommendations", criteria: { maxItemPrice: 100 } },
    { state, gamesByConsole: { zeta: catalog } }
  );
  const b = search.criteria.titleTargets.find((target) => target.gameId === "b");
  assert.equal(b.maxBoxedPrice, undefined);
});

test("con más títulos que consultas posibles, quedan afuera los más caros y se informa", () => {
  const many = Array.from({ length: MAX_QUERIES_PER_SEARCH + 2 }, (_, index) => ({
    id: `g${index}`,
    nombre: `Juego ${index}`,
    priceRange: { low: 5, mid: 10 + index, high: 20 + index },
  }));
  const edits = Object.fromEntries(many.map((game) => [game.id, { keepInWishlist: true }]));
  const { search, dropped } = expandWishlist(
    { name: "Muchos", consoleId: "zeta", querySuffix: "zeta", source: "recommendations", criteria: { maxItemPrice: 100 } },
    { state: { user: { detailEditsById: { zeta: { gameEditsById: edits } } } }, gamesByConsole: { zeta: many } }
  );
  assert.equal(search.criteria.titleTargets.length, MAX_QUERIES_PER_SEARCH);
  assert.deepEqual(dropped.map((item) => item.name), [`Juego ${MAX_QUERIES_PER_SEARCH}`, `Juego ${MAX_QUERIES_PER_SEARCH + 1}`]);
});

test("una consola sin deseados no genera búsqueda vacía", () => {
  const { searches, report } = expandPlan(
    { searches: [{ name: "Fija" }], wishlists: [{ name: "Vacía", consoleId: "otra", criteria: {} }] },
    { state, gamesByConsole: {} }
  );
  assert.deepEqual(searches.map((search) => search.name), ["Fija"]);
  assert.equal(report[0].titles, 0);
});
