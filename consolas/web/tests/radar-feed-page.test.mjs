import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const repositorySource = await readFile(new URL("../radar-repository.js", import.meta.url), "utf8");
const purchaseSource = await readFile(new URL("../radar-purchase.js", import.meta.url), "utf8");
const pageSource = await readFile(new URL("../radar-feed.js", import.meta.url), "utf8");

function item(overrides = {}) {
  return {
    id: "ebay-us-1",
    sourceId: "ebay-us",
    sourceLabel: "eBay USA",
    title: "PlayStation 2 Slim tested with OEM controller",
    listingUrl: "https://www.ebay.com/itm/1",
    listingType: "Compra directa",
    priceLabel: "USD 149.99",
    priceAmount: 149.99,
    priceCurrency: "USD",
    shippingLabel: "Envío USD 12",
    totalAmount: 161.99,
    conditionLabel: "Pre-owned",
    sellerLabel: "99.4% de feedback",
    score: 72,
    band: "buena",
    valuation: { cost: { currency: "USD", importedTotal: 214.5 } },
    priceDrop: null,
    decision: null,
    matches: [
      {
        searchId: "radar-1",
        searchName: "PS2 lista para usar",
        confidence: 0.82,
        reasons: ["Declara estar probada («tested»)", "Dentro del presupuesto: USD 149.99"],
        unverified: ["Región sin declarar"],
      },
    ],
    firstSeenAt: "2026-09-10T10:00:00Z",
    lastSeenAt: "2026-09-11T10:00:00Z",
    ...overrides,
  };
}

function defaultBudget(overrides = {}) {
  return {
    month: "2026-09",
    currency: "USD",
    monthlyBudgetUsd: null,
    configured: false,
    spent: 0,
    spentCount: 0,
    reserved: 0,
    reservedCount: 0,
    available: null,
    ...overrides,
  };
}

async function renderFeed({
  items = [], following = [], counts = {}, environment = "production", fail = false,
  budget = defaultBudget(), radarPurchase = null, openPurchaseFor = "",
} = {}) {
  let html = "";
  const requests = [];
  // Abrir el formulario de compra es un click real sobre `[data-purchase]`:
  // se le devuelve al binding un botón mínimo y se dispara su handler, en vez
  // de manipular el estado interno de la página desde afuera.
  const purchaseClicks = [];
  const root = {
    set innerHTML(value) { html = String(value); },
    get innerHTML() { return html; },
    querySelectorAll(selector) {
      if (!openPurchaseFor || selector !== "[data-purchase]") return [];
      return [{
        dataset: { purchase: openPurchaseFor },
        addEventListener: (_event, handler) => purchaseClicks.push(handler),
      }];
    },
    querySelector() { return null; },
  };
  const feedPayload = {
    version: 1,
    environment,
    generatedAt: "2026-09-11T12:00:00Z",
    counts: { feed: items.length, following: following.length, dismissed: 0, snoozed: 0, ...counts },
    items,
    following,
  };
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url, options });
    if (fail) return { ok: false, status: 503, async json() { return { error: "sin backend" }; } };
    if (String(url).includes("/radar/budget")) {
      return { ok: true, status: 200, async json() { return budget; } };
    }
    return { ok: true, status: 200, async json() { return feedPayload; } };
  };
  // Se carga el módulo real de compra, no un stub: qué entidad se puede
  // escribir es justamente lo que decide si la card ofrece el botón, y un
  // stub con la firma vieja haría pasar el test con la UI rota.
  const windowStub = {};
  const context = vm.createContext({
    window: windowStub,
    document: { getElementById: (id) => (id === "radarFeedRoot" ? root : null) },
    fetch: fetchImpl,
    Intl,
    FormData: class {},
    console: { error() {}, info() {} },
  });
  vm.runInContext(repositorySource, context, { filename: "radar-repository.js" });
  vm.runInContext(purchaseSource, context, { filename: "radar-purchase.js" });
  if (radarPurchase) Object.assign(windowStub.RadarPurchase, radarPurchase);
  vm.runInContext(pageSource, context, { filename: "radar-feed.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  if (openPurchaseFor) purchaseClicks[0]?.();
  return { html, requests, repository: windowStub.RadarRepository };
}

test("an opportunity leads with why it fits and what it really costs", async () => {
  const { html } = await renderFeed({ items: [item()] });

  assert.match(html, /Para mí/);
  assert.match(html, /PlayStation 2 Slim tested/);
  assert.match(html, /Buena compra · 72\/100/);
  assert.match(html, /Coincide con PS2 lista para usar/);
  assert.match(html, /Declara estar probada/);
  assert.match(html, /≈ USD 214,5 puesto acá/);
  assert.match(html, /data-follow="ebay-us-1"/);
  assert.match(html, /data-dismiss="ebay-us-1"/);
});

test("a price drop is what leads the card, marked as the news it is", async () => {
  const { html } = await renderFeed({
    items: [item({ priceDrop: { previous: 200, current: 150, amount: 50, ratio: 0.25, material: true } })],
  });

  assert.match(html, /has-drop/);
  assert.match(html, /↓ 25%/);
  assert.match(html, /baja importante/);
  assert.match(html, /USD 200/);
});

test("a small drop is shown without calling it important", async () => {
  const { html } = await renderFeed({
    items: [item({ priceDrop: { previous: 100, current: 97, amount: 3, ratio: 0.03, material: false } })],
  });

  assert.match(html, /↓ 3%/);
  assert.doesNotMatch(html, /baja importante/);
});

test("a followed listing says so and offers to stop following", async () => {
  const { html } = await renderFeed({
    items: [item({ decision: { decision: "following", reason: "", note: "" } })],
    counts: { following: 1 },
  });

  assert.match(html, /La estás siguiendo/);
  assert.match(html, /Dejar de seguir/);
});

test("dismissing asks for a reason before it happens", async () => {
  const { html } = await renderFeed({ items: [item()] });

  // El formulario aparece recién al tocar Descartar; el botón lo ofrece.
  assert.match(html, /data-dismiss="ebay-us-1">Descartar/);
  assert.doesNotMatch(html, /data-dismiss-form/);
});

test("snooze is offered in plain spans of time", async () => {
  const { html } = await renderFeed({ items: [item()] });

  assert.match(html, /data-snooze="ebay-us-1" data-days="7"/);
  assert.match(html, /una semana/);
  assert.match(html, /tres meses/);
});

test("an empty feed says silence is the point, not a failure", async () => {
  const { html } = await renderFeed({ items: [] });

  assert.match(html, /Nada accionable ahora/);
  assert.match(html, /Sin novedades no hay aviso/);
  assert.doesNotMatch(html, /data-follow=/);
});

test("the following section is separate from today's feed", async () => {
  const { html } = await renderFeed({
    items: [],
    following: [item({ id: "ebay-us-2", decision: { decision: "following" } })],
    counts: { following: 1 },
  });

  assert.match(html, /Siguiendo/);
  assert.match(html, /Te avisamos si alguna baja de precio/);
});

test("external listing text is escaped, never interpreted", async () => {
  const { html } = await renderFeed({
    items: [item({ title: '"><script>alert(1)</script>' })],
  });

  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});

test("sandbox is announced without hiding the feed", async () => {
  const { html } = await renderFeed({ items: [item()], environment: "sandbox" });

  assert.match(html, /Modo Sandbox/);
  assert.match(html, /PlayStation 2 Slim tested/);
});

test("a backend without the feed degrades to an explicit message", async () => {
  const { html } = await renderFeed({ fail: true });

  assert.match(html, /No está disponible en este origen/);
});

test("deciding goes through the radar endpoint with its write header", async () => {
  const { repository, requests } = await renderFeed({ items: [item()] });
  requests.length = 0;

  await repository.decide("ebay-us-1", { decision: "following" });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/decisions$/);
  assert.match(requests[0].options.body, /"decision":"following"/);
});

test("clearing a decision deletes it and never touches collection state", async () => {
  const { repository, requests } = await renderFeed({ items: [item()] });
  requests.length = 0;

  await repository.clearDecision("ebay-us-1");

  assert.equal(requests[0].options.method, "DELETE");
  assert.match(requests[0].url, /\/radar\/decisions\/ebay-us-1$/);
  assert.equal(requests.filter((entry) => entry.url.includes("/state")).length, 0);
});

test("an unconfigured budget offers to set it up instead of showing zeros", async () => {
  const { html } = await renderFeed({ items: [item()], budget: defaultBudget() });

  assert.match(html, /Presupuesto mensual sin configurar/);
  assert.match(html, /data-edit-budget="1">Configurar/);
});

test("a configured budget shows spent, reserved and available", async () => {
  const { html } = await renderFeed({
    items: [item()],
    budget: defaultBudget({ monthlyBudgetUsd: 200, configured: true, spent: 60, spentCount: 1, reserved: 30, reservedCount: 1, available: 110 }),
  });

  assert.match(html, /USD 200/);
  assert.match(html, /USD 60[\s\S]*gastado \(1\)/);
  assert.match(html, /USD 30[\s\S]*reservado \(1\)/);
  assert.match(html, /USD 110[\s\S]*disponible/);
  assert.doesNotMatch(html, /is-over/);
});

test("going over budget is shown, not hidden — the budget is context, not a wall", async () => {
  const { html } = await renderFeed({
    items: [item()],
    budget: defaultBudget({ monthlyBudgetUsd: 50, configured: true, spent: 90, spentCount: 1, available: -40 }),
  });

  assert.match(html, /budget-widget is-over/);
  assert.match(html, /sobre el presupuesto/);
});

test("reserving only makes sense once you are already following", async () => {
  const notFollowing = await renderFeed({ items: [item()] });
  assert.doesNotMatch(notFollowing.html, /data-reserve=/);

  const following = await renderFeed({ items: [item({ decision: { decision: "following", reserved: false } })] });
  assert.match(following.html, /data-reserve="ebay-us-1">/);
  assert.match(following.html, /Reservar en el presupuesto/);
});

test("an already-reserved listing offers to take it back out", async () => {
  const { html } = await renderFeed({
    items: [item({ decision: { decision: "following", reserved: true } })],
  });

  assert.match(html, /Quitar del presupuesto/);
});

test("registrar compra only appears when the match names a console it can write", async () => {
  const withEntity = await renderFeed({
    items: [item({ matches: [{ searchId: "radar-1", searchName: "PS2", confidence: 0.9, reasons: [], unverified: [], entityType: "console", entityId: "ps2" }] })],
  });
  assert.match(withEntity.html, /data-purchase="ebay-us-1">Registrar compra/);

  const withoutEntity = await renderFeed({ items: [item()] }); // el fixture base no trae entityType
  assert.doesNotMatch(withoutEntity.html, /Registrar compra/);
});

test("a game match whose search has no console behind it does not offer registrar compra", async () => {
  const { html } = await renderFeed({
    items: [item({ matches: [{ searchId: "radar-1", searchName: "Aladdin", confidence: 0.9, reasons: [], unverified: [], entityType: "game", entityId: "aladdin" }] })],
  });

  assert.doesNotMatch(html, /Registrar compra/, "sin consola no se sabe a qué biblioteca escribir");
});

test("a game match with its console offers registrar compra and says which library it writes", async () => {
  const { html } = await renderFeed({
    items: [item({ matches: [{ searchId: "radar-1", searchName: "God of War", confidence: 0.9, reasons: [], unverified: [], entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2" }] })],
  });

  assert.match(html, /data-purchase="ebay-us-1">Registrar compra/);
});

test("the purchase form for a game names the console it will write to", async () => {
  const { html } = await renderFeed({
    items: [item({ matches: [{ searchId: "radar-1", searchName: "God of War", confidence: 0.9, reasons: [], unverified: [], entityType: "game", entityId: "god-of-war", entityConsoleId: "ps2" }] })],
    openPurchaseFor: "ebay-us-1",
  });

  assert.match(html, /data-entity-console-id="ps2"/);
  assert.match(html, /god-of-war en la biblioteca de ps2/);
});

test("an accessory match does not offer registrar compra, console or not", async () => {
  const { html } = await renderFeed({
    items: [item({ matches: [{ searchId: "radar-1", searchName: "DualShock", confidence: 0.9, reasons: [], unverified: [], entityType: "accessory", entityId: "dualshock", entityConsoleId: "ps2" }] })],
  });

  assert.doesNotMatch(html, /Registrar compra/);
});

test("an already-purchased listing does not offer to register it again", async () => {
  const { html } = await renderFeed({
    items: [
      item({
        decision: { decision: "purchased" },
        matches: [{ searchId: "radar-1", searchName: "PS2", confidence: 0.9, reasons: [], unverified: [], entityType: "console", entityId: "ps2" }],
      }),
    ],
  });

  assert.doesNotMatch(html, /Registrar compra/);
});

test("recording a purchase goes through the radar endpoint with its write header", async () => {
  const { repository, requests } = await renderFeed({ items: [item()] });
  requests.length = 0;

  await repository.recordPurchase({ listingId: "ebay-us-1", entityType: "console", entityId: "ps2", priceAmount: 55 });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/purchases$/);
  assert.match(requests[0].options.body, /"entityId":"ps2"/);
});

test("updating the budget goes through the radar endpoint with its write header", async () => {
  const { repository, requests } = await renderFeed({ items: [item()] });
  requests.length = 0;

  await repository.updateBudget(300);

  assert.equal(requests[0].options.method, "POST");
  assert.match(requests[0].url, /\/radar\/preferences$/);
  assert.match(requests[0].options.body, /"monthlyBudgetUsd":300/);
});

test("computing a lot valuation goes through the radar endpoint, pieces and all", async () => {
  const { repository, requests } = await renderFeed({ items: [item()] });
  requests.length = 0;

  await repository.computeLotValuation({
    totalCost: 50,
    pieces: [{ name: "PS2 Slim", comparableValue: 60 }],
  });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/lot-valuation$/);
  assert.match(requests[0].options.body, /"comparableValue":60/);
});
