import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const repositorySource = await readFile(new URL("../radar-repository.js", import.meta.url), "utf8");
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

async function renderFeed({ items = [], following = [], counts = {}, environment = "production", fail = false } = {}) {
  let html = "";
  const requests = [];
  const root = {
    set innerHTML(value) { html = String(value); },
    get innerHTML() { return html; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  const payload = {
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
    return { ok: true, status: 200, async json() { return payload; } };
  };
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
  vm.runInContext(pageSource, context, { filename: "radar-feed.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
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
