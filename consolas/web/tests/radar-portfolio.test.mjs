import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_QUERIES_PER_SEARCH,
  expandPlan,
  expandWishlist,
  queryText,
  titleCeiling,
  wishedGames,
} from "../../scripts/lib/radar-portfolio.mjs";

// Las búsquedas de deseados se arman con el estado real al aplicar el plan, no
// con una lista escrita a mano. Estos tests fijan las reglas que deciden qué
// se persigue y hasta cuánto: pertenencia igual que la capa central, techo por
// título que nunca pasa el general, y nada inventado cuando falta el precio.

const catalog = [
  { id: "a", nombre: "Juego A", priceRange: { low: 15, mid: 22, high: 30 } },
  { id: "b", nombre: "Juego B", priceRange: { low: 40, mid: 60, high: 80 } },
  { id: "c", nombre: "Juego C: Edición", priceRange: { low: 150, mid: 260, high: 400 } },
  { id: "d", nombre: "Juego D", priceRange: { low: 10, mid: 14, high: 20 } },
  { id: "e", nombre: "Juego E", priceRange: { low: 10, mid: 14, high: 20 } },
];

const state = {
  user: {
    detailEditsById: {
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

test("Deseados es loQuiero o keepInWishlist, y nunca lo que ya se tiene", () => {
  const games = wishedGames(state, catalog, "zeta");
  assert.deepEqual(
    games.map((game) => [game.id, game.explicit]),
    [["a", true], ["b", false], ["c", false], ["manual-1", true]]
  );
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
  // Explícitos primero (el más barato adelante), después las recomendaciones.
  assert.deepEqual(
    search.criteria.titleTargets.map((target) => [target.gameId, target.maxItemPrice]),
    [["a", 30], ["manual-1", 100], ["b", 60]]
  );
  assert.equal(search.searchQuery, "Juego A zeta");
  assert.deepEqual(search.criteria.queries, ["Juego Manual zeta", "Juego B zeta"]);
  assert.deepEqual(search.criteria.anyTerms, ["zeta"]);
  assert.deepEqual(skipped.map((item) => item.name), ["Juego C: Edición"]);
});

test("con más títulos que consultas posibles, quedan afuera los más caros y se informa", () => {
  const many = Array.from({ length: MAX_QUERIES_PER_SEARCH + 2 }, (_, index) => ({
    id: `g${index}`,
    nombre: `Juego ${index}`,
    priceRange: { low: 5, mid: 10 + index, high: 20 + index },
  }));
  const edits = Object.fromEntries(many.map((game) => [game.id, { keepInWishlist: true }]));
  const { search, dropped } = expandWishlist(
    { name: "Muchos", consoleId: "zeta", querySuffix: "zeta", criteria: { maxItemPrice: 100 } },
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
