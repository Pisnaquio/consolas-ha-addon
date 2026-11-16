import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// La cabecera de «Gestión de juegos» vive dentro de console.js como template string;
// estas pruebas leen el fuente para fijar la estructura compacta sin montar toda la página.
const source = await readFile(new URL("../console.js", import.meta.url), "utf8");
const gamesPage = await readFile(new URL("../console-games.html", import.meta.url), "utf8");
const headerCss = await readFile(new URL("../games-header.css", import.meta.url), "utf8");

const start = source.indexOf('<div class="games-filterbar games-filterbar--primary">');
const moreStart = source.indexOf('<div class="games-filterbar games-filterbar--more"');
const summaryStart = source.indexOf('<div class="games-summary">');
const primary = source.slice(start, moreStart);
const more = source.slice(moreStart, summaryStart);

test("search and frequent filters share the first row; the rest live in «Más filtros»", () => {
  assert.ok(start > 0 && moreStart > start && summaryStart > moreStart);
  for (const id of ["gameSearchInput", "gameListFilter", "gameStatusFilter", "gameOwnershipFilter"]) {
    assert.match(primary, new RegExp(`id="${id}"`), `${id} debe estar en la primera fila`);
  }
  for (const id of ["gamePriorityFilter", "gameFranchiseFilter", "gameGenreFilter", "gameSortFilter"]) {
    assert.match(more, new RegExp(`id="${id}"`), `${id} debe estar en «Más filtros»`);
  }
  assert.match(primary, /<details id="gameMoreFilters"/);
});

test("every filter id keeps exactly one control so existing handlers still bind", () => {
  const ids = ["gameSearchInput", "gameListFilter", "gameStatusFilter", "gameOwnershipFilter", "gamePriorityFilter", "gameFranchiseFilter", "gameGenreFilter", "gameSortFilter", "openManualGameModalBtn"];
  for (const id of ids) {
    assert.equal(source.split(`id="${id}"`).length - 1, 1, `${id} debe aparecer una sola vez`);
    assert.match(source, new RegExp(`getElementById\\("${id}"\\)`), `${id} debe seguir con su listener`);
  }
});

test("«Más filtros» starts closed and its panel stays hidden until opened", () => {
  assert.match(source, /gameMoreFiltersOpen: false/);
  assert.match(more, /appState\.gameMoreFiltersOpen \? "" : " hidden"/);
  assert.match(headerCss, /\.games-filterbar--more\[hidden\]\s*\{\s*display:\s*none;/);
});

test("the six metric cards became one summary line and the manual add moved to the header", () => {
  assert.doesNotMatch(source, /games-stats-strip/);
  // El lanzador aislado desaparece de juegos (accesorios conserva el suyo).
  assert.doesNotMatch(source, /Agregar juego manual \(no listado\)/);
  const head = source.slice(source.indexOf('<div class="section-head games-head">'), start);
  assert.match(head, /id="openManualGameModalBtn"/);
  assert.match(source, /class="games-summary-chip"/);
});

test("Registrados / ya tengo is still rendered before Recomendados", () => {
  const block = source.slice(summaryStart);
  assert.ok(block.indexOf("Registrados / ya tengo") < block.indexOf(">Recomendados<"));
});

test("visible labels carry their accents", () => {
  for (const label of ["Gestión de juegos", "Posesión", ">Físico<", "Género", "Low más barato", "Gestión de consola"]) {
    assert.ok(source.includes(label), `falta «${label}»`);
  }
  for (const wrong of ["Gestion de juegos", "Posesion\n", ">Fisico<", "Genero\n", "Gestion de consola"]) {
    assert.ok(!source.includes(wrong), `sigue «${wrong.trim()}»`);
  }
});

test("the badge counts only the filters hidden inside «Más filtros»", () => {
  const snippet = source.slice(source.indexOf("const MORE_GAME_FILTER_DEFAULTS"), source.indexOf("function renderGbaGamesSection"));
  const context = vm.createContext({ appState: { gameFilters: {} } });
  vm.runInContext(`${snippet}; this.count = countActiveMoreGameFilters;`, context);
  const base = { q: "zelda", status: "have", ownership: "physical", list: "registered", priority: "all", franchise: "all", genre: "all", sort: "default" };
  assert.equal(context.count(base), 0);
  assert.equal(context.count({ ...base, priority: "alta", sort: "name-asc" }), 2);
});

test("the games page loads the compact header stylesheet after the theme", () => {
  const theme = gamesPage.indexOf("studio-theme.css");
  const header = gamesPage.indexOf("games-header.css");
  assert.ok(theme > 0 && header > theme);
});
