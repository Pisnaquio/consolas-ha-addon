import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const webDir = new URL("../", import.meta.url);
const source = await readFile(new URL("site-nav.js", webDir), "utf8");

function loadNav() {
  const module = { exports: {} };
  vm.runInNewContext(source, { module, window: { location: { pathname: "/" } } });
  return module.exports;
}

test("every section points at pages that exist", async () => {
  const { SECTIONS } = loadNav();
  const files = new Set(await readdir(webDir));
  assert.equal(SECTIONS.length, 5);
  for (const section of SECTIONS) {
    for (const page of section.pages) assert.ok(files.has(page), `${section.id}: falta ${page}`);
  }
});

test("a page belongs to at most one section", () => {
  const { SECTIONS } = loadNav();
  const pages = SECTIONS.flatMap((section) => section.pages);
  assert.equal(new Set(pages).size, pages.length);
});

test("the current page is resolved behind the Ingress prefix", () => {
  const { currentPage, findSection } = loadNav();
  assert.equal(currentPage("/api/hassio_ingress/token/"), "index.html");
  assert.equal(currentPage("/api/hassio_ingress/token/opportunities.html"), "opportunities.html");
  assert.equal(findSection("console.html").id, "collection");
  assert.equal(findSection("chasing-games.html").id, "radar");
  assert.equal(findSection("unknown.html"), null);
});

test("pages that load the bar also load its styles", async () => {
  const files = (await readdir(webDir)).filter((name) => name.endsWith(".html"));
  let withNav = 0;
  for (const name of files) {
    const html = await readFile(new URL(name, webDir), "utf8");
    if (!html.includes("site-nav.js")) continue;
    withNav += 1;
    assert.ok(html.includes("estante-shell.css"), `${name} carga la barra sin sus estilos`);
    assert.ok(html.includes('class="studio-variant"'), `${name} no usa el tema Estante`);
  }
  assert.ok(withNav >= 5);
});
