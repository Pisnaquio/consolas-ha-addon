import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../value-ladder.js", import.meta.url), "utf8");
const catalogLadders = JSON.parse(await readFile(new URL("../data/value-ladder.json", import.meta.url), "utf8"));
const catalogConsoles = JSON.parse(await readFile(new URL("../data/consoles.json", import.meta.url), "utf8"));

function load() {
  const context = vm.createContext({ window: {} });
  vm.runInContext(source, context, { filename: "value-ladder.js" });
  return context.window.ValueLadder;
}

const ValueLadder = load();

const sample = {
  priceNote: "El catálogo guarda un precio por consola, no por revisión.",
  ladders: {
    demo: {
      family: "Demo Family",
      verifiedAt: "2026-09-11",
      playPick: "b",
      collectPick: "a",
      sources: [{ label: "Fuente demo", url: "https://example.com/demo" }],
      pending: ["Falta el premium porcentual."],
      variants: [
        { id: "a", name: "Primera", years: "1977", region: "NTSC", identify: "Plástico grueso", technical: "Original", note: "La histórica" },
        { id: "b", name: "Tardía", years: "1985", region: "NTSC", identify: "Más chica", technical: "Fuente integrada", reliability: "La más confiable" },
      ],
    },
  },
};

test("the ladder explains which variant to play and which to collect", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { consoleName: "Demo" });

  assert.match(html, /Guía de modelos y valor/);
  assert.match(html, /Demo Family/);
  assert.match(html, /Primera[\s\S]*Para coleccionar/);
  assert.match(html, /Tardía[\s\S]*Para jugar/);
});

test("every variant says how to tell it apart before any price talk", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), {});

  assert.match(html, /Cómo reconocerla/);
  assert.match(html, /Plástico grueso/);
  assert.match(html, /Más chica/);
});

test("no variant carries a price, and the page says why", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { priceNote: sample.priceNote });

  assert.doesNotMatch(html, /USD|US\$|\$\s?\d/);
  assert.match(html, /precio por consola, no por revisión/);
});

test("what is not verified is declared, not filled in", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), {});

  assert.match(html, /Todavía sin verificar/);
  assert.match(html, /Falta el premium porcentual/);
  assert.match(html, /verificado 2026-09-11/);
  assert.match(html, /https:\/\/example\.com\/demo/);
});

test("a console without a guide says so instead of showing an empty section", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "no-existe"), { consoleName: "Wii" });

  assert.match(html, /Todavía no hay guía de variantes para Wii/);
  assert.doesNotMatch(html, /data-ladder-own/);
});

test("a ladder with no variants counts as no guide at all", () => {
  assert.equal(ValueLadder.findLadder({ ladders: { x: { family: "X", variants: [] } } }, "x"), null);
  assert.equal(ValueLadder.findLadder(null, "x"), null);
});

test("knowing which variant you own turns into a concrete upgrade, or into nothing", () => {
  const upgradeable = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { owned: true, ownedVariantId: "a" });
  assert.match(upgradeable, /Tenés una Primera/);
  assert.match(upgradeable, /para jugar, la recomendada es la Tardía/);
  // La «a» ya es el sweet spot de colección: no hay upgrade que sugerir por ese lado.
  assert.doesNotMatch(upgradeable, /para coleccionar, la buscada/);

  const noUpgrade = ValueLadder.render(
    ValueLadder.findLadder({ ladders: { x: { family: "X", playPick: "1", collectPick: "1", variants: [{ id: "1", name: "Única", identify: "ok" }] } } }, "x"),
    { owned: true, ownedVariantId: "1" },
  );
  assert.match(noUpgrade, /Es la que la guía recomienda/);
});

test("what is pending across the whole guide is stated once, apart from each console's gaps", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), {
    pendingAll: ["Tramos de condición por variante."],
  });

  assert.match(html, /Falta el premium porcentual/);
  assert.match(html, /En toda la guía/);
  assert.match(html, /Tramos de condición por variante/);
});

test("an owned console can record which variant it is, and says so when it has not", () => {
  const missing = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { owned: true });
  assert.match(missing, /No registraste qué variante tenés/);
  assert.match(missing, /data-ladder-own="a"/);
  assert.match(missing, /Esta es la mía/);

  const known = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { owned: true, ownedVariantId: "b" });
  assert.match(known, /Tenés una Tardía/);
  assert.match(known, /No es la mía/);
  assert.match(known, /class="ladder-variant is-mine" data-variant="b"/);
});

test("a console you do not own is read-only", () => {
  const html = ValueLadder.render(ValueLadder.findLadder(sample, "demo"), { owned: false });
  assert.doesNotMatch(html, /data-ladder-own/);
});

test("ladder text is escaped, never interpreted", () => {
  const html = ValueLadder.render(
    ValueLadder.findLadder(
      { ladders: { x: { family: "<b>x</b>", variants: [{ id: "1", name: '"><script>alert(1)</script>', identify: "ok" }] } } },
      "x",
    ),
    {},
  );

  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});

test("the shipped guide only covers consoles that exist in the catalog", () => {
  const ids = new Set((catalogConsoles.consolas || []).map((entry) => entry.id));
  for (const consoleId of Object.keys(catalogLadders.ladders)) {
    assert.ok(ids.has(consoleId), `${consoleId} no está en el catálogo de consolas`);
  }
});

test("the shipped guide never states a price and always cites its source", () => {
  for (const [consoleId, ladder] of Object.entries(catalogLadders.ladders)) {
    assert.ok(ladder.sources?.length, `${consoleId} sin fuentes`);
    assert.match(ladder.verifiedAt || "", /^\d{4}-\d{2}-\d{2}$/, `${consoleId} sin fecha de verificación`);
    assert.ok(ladder.pending?.length, `${consoleId} debería declarar qué falta`);
    assert.ok(catalogLadders.pendingAll?.length, "la guía debe declarar qué falta en todas las consolas");

    const ids = new Set(ladder.variants.map((variant) => variant.id));
    assert.ok(ids.has(ladder.playPick), `${consoleId}: playPick apunta a una variante inexistente`);
    assert.ok(ids.has(ladder.collectPick), `${consoleId}: collectPick apunta a una variante inexistente`);
    assert.equal(ids.size, ladder.variants.length, `${consoleId}: ids de variante duplicados`);

    for (const variant of ladder.variants) {
      assert.ok(variant.identify, `${consoleId}/${variant.id} sin señal visual para identificarla`);
      const text = JSON.stringify(variant);
      assert.doesNotMatch(text, /US\$|USD|\$\s?\d/, `${consoleId}/${variant.id} declara un precio inventado`);
    }
  }
});
