import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const repositorySource = await readFile(new URL("../radar-repository.js", import.meta.url), "utf8");
const pageSource = await readFile(new URL("../chasing-games.js", import.meta.url), "utf8");

const SOURCES = [
  { id: "ebay-us", label: "eBay USA", executable: true, unavailableReason: "", capabilities: { officialApi: true } },
  {
    id: "shopgoodwill",
    label: "ShopGoodwill",
    executable: false,
    unavailableReason: "Sólo por Personal Shopper y alertas oficiales.",
    capabilities: { savedSearchAlerts: true }
  }
];

function search(overrides = {}) {
  const status = overrides.status || "active";
  const sources = overrides.sources || ["ebay-us"];
  return {
    id: "radar-1",
    name: "PlayStation 2 lista para usar",
    title: "PlayStation 2 lista para usar",
    searchType: "console",
    status,
    origin: "user",
    priority: "alta",
    platform: "PS2",
    entityType: "console",
    entityId: "ps2",
    searchQuery: "PlayStation 2 lista para usar PS2 tested",
    criteria: {
      includeTerms: ["tested"],
      anyTerms: [],
      excludeTerms: ["parts", "as-is"],
      region: "NTSC-U/C",
      condition: "used",
      completeness: "any",
      tested: "required",
      originalParts: "any",
      returnsRequired: false,
      freeShippingOnly: false,
      currency: "USD",
      maxItemPrice: 300,
      maxTotalUsa: null,
      minLotSize: null,
      resultLimit: 12
    },
    sources,
    executableSources: sources.filter((id) => SOURCES.find((item) => item.id === id)?.executable),
    notes: "",
    enabled: status === "active",
    canRun: status === "active" && sources.some((id) => SOURCES.find((item) => item.id === id)?.executable),
    createdAt: "2026-09-01T10:00:00Z",
    updatedAt: "2026-09-10T10:00:00Z",
    lastCheckedAt: "2026-09-10T10:00:00Z",
    lastError: "",
    archivedAt: null,
    results: [],
    resultCount: 0,
    ...overrides
  };
}

async function renderPage({ items = [], environment = "production", failLoad = false, runs = null } = {}) {
  let html = "";
  const requests = [];
  const root = {
    set innerHTML(value) {
      html = String(value);
    },
    get innerHTML() {
      return html;
    },
    querySelectorAll() {
      return [];
    }
  };
  const counts = ["active", "draft", "paused", "archived"].reduce(
    (acc, status) => ({ ...acc, [status]: items.filter((item) => item.status === status).length }),
    {}
  );
  const model = {
    version: 1,
    source: environment === "sandbox" ? "eBay Sandbox · datos de prueba" : "eBay USA",
    environment,
    sources: SOURCES,
    counts,
    items
  };
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url, options });
    if (failLoad) return { ok: false, status: 503, async json() { return { error: "sin backend" }; } };
    const body = url.includes("/radar/runs") ? runs || { slots: [], runs: [] } : model;
    return { ok: true, status: 200, async json() { return body; } };
  };
  const windowStub = {};
  const document = {
    getElementById(id) {
      return id === "chasingGamesRoot" ? root : null;
    }
  };
  const context = vm.createContext({
    window: windowStub,
    document,
    fetch: fetchImpl,
    confirm: () => true,
    Intl,
    console: { error() {}, info() {} }
  });

  vm.runInContext(repositorySource, context, { filename: "radar-repository.js" });
  vm.runInContext(pageSource, context, { filename: "chasing-games.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return { html, requests, repository: windowStub.RadarRepository };
}

test("renders an active search with its status, criteria and run action", async () => {
  const { html } = await renderPage({ items: [search()] });

  assert.match(html, /Búsquedas del radar/);
  assert.match(html, /PlayStation 2 lista para usar/);
  assert.match(html, /chase-status is-active">Activa/);
  assert.match(html, /Prioridad alta/);
  assert.match(html, /Consola completa/);
  assert.match(html, /Región NTSC-U\/C/);
  assert.match(html, /Hasta USD 300/);
  assert.match(html, /Excluye: parts, as-is/);
  assert.match(html, /data-run="radar-1"/);
  assert.match(html, /data-status="radar-1" data-next="paused"/);
});

test("a draft is shown as a proposal, offers Activar and never offers Buscar ahora", async () => {
  const { html } = await renderPage({
    items: [search({ status: "draft", origin: "master", lastCheckedAt: null })]
  });

  assert.match(html, /chase-status is-draft">Propuesta/);
  assert.match(html, /No va a ejecutarse hasta que la actives/);
  assert.match(html, /por el Master de colección/);
  assert.match(html, /data-status="radar-1" data-next="active">Activar/);
  assert.doesNotMatch(html, /data-run=/);
  assert.match(html, /todavía no buscado/);
});

test("a paused search offers Reanudar and keeps its stored results visible", async () => {
  const { html } = await renderPage({
    items: [
      search({
        status: "paused",
        results: [
          {
            id: "ebay-1",
            sourceId: "ebay-us",
            title: "PS2 Slim SCPH-79001 tested",
            priceLabel: "USD 149.99",
            priceAmount: 149.99,
            priceCurrency: "USD",
            conditionLabel: "Pre-owned",
            shippingLabel: "",
            locationLabel: "US",
            listingType: "Compra directa",
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ]
  });

  assert.match(html, /data-status="radar-1" data-next="active">Reanudar/);
  assert.doesNotMatch(html, /Buscar ahora/);
  assert.match(html, /PS2 Slim SCPH-79001 tested/);
  assert.match(html, /1 resultados activos/);
});

test("an archived search can be reactivated and is not offered Archivar again", async () => {
  const { html } = await renderPage({ items: [search({ status: "archived" })] });

  assert.match(html, /chase-status is-archived">Archivada/);
  assert.match(html, /data-next="active">Reactivar/);
  assert.doesNotMatch(html, /data-next="archived"/);
});

test("a search whose sources cannot execute says so and disables the run button", async () => {
  const { html } = await renderPage({ items: [search({ sources: ["shopgoodwill"] })] });

  assert.match(html, /Sin ejecutar: ShopGoodwill/);
  assert.match(html, /data-run="radar-1" disabled/);
});

test("external listing text is escaped, never interpreted", async () => {
  const { html } = await renderPage({
    items: [
      search({
        name: "<img src=x onerror=alert(1)>",
        results: [
          {
            id: "ebay-2",
            sourceId: "ebay-us",
            title: '"><script>alert(1)</script>',
            priceLabel: "USD 10",
            conditionLabel: "",
            shippingLabel: "",
            locationLabel: "",
            listingType: "",
            listingUrl: "https://www.ebay.com/itm/2",
            imageUrl: "",
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ]
  });

  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("sandbox is announced as a warning without hiding the searches", async () => {
  const { html } = await renderPage({ items: [search()], environment: "sandbox" });

  assert.match(html, /Modo Sandbox/);
  assert.match(html, /Las búsquedas y sus criterios se guardan igual/);
  assert.match(html, /PlayStation 2 lista para usar/);
});

test("the filter tabs count every lifecycle state", async () => {
  const { html } = await renderPage({
    items: [
      search({ id: "radar-1", name: "Activa", status: "active" }),
      search({ id: "radar-2", name: "Propuesta", status: "draft" }),
      search({ id: "radar-3", name: "Pausada", status: "paused" })
    ]
  });

  assert.match(html, /data-filter="all"[\s\S]{0,120}Todas<span>3<\/span>/);
  assert.match(html, /data-filter="active"[\s\S]{0,120}Activas<span>1<\/span>/);
  assert.match(html, /data-filter="draft"[\s\S]{0,120}Propuestas<span>1<\/span>/);
  assert.match(html, /data-filter="archived"[\s\S]{0,120}Archivadas<span>0<\/span>/);
});

test("the empty state invites creating a search instead of inventing one", async () => {
  const { html } = await renderPage({ items: [] });

  assert.match(html, /Sin búsquedas/);
  assert.match(html, /Agregá una búsqueda para que el radar empiece a trabajar/);
  assert.match(html, /data-open-create="1"/);
});

test("a backend without the radar endpoint degrades to an explicit message", async () => {
  const { html } = await renderPage({ failLoad: true });

  assert.match(html, /No está disponible en este origen/);
  assert.doesNotMatch(html, /Buscar ahora/);
});

test("the repository sends the radar write header only on writes", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.setStatus("radar-1", "paused");
  await repository.load();

  const write = requests[0];
  assert.equal(write.options.method, "POST");
  assert.equal(write.options.headers["X-Consolas-Radar"], "1");
  assert.match(write.url, /\/radar\/searches\/radar-1\/status$/);
  assert.equal(requests[1].options.headers["X-Consolas-Radar"], undefined);
});

test("deleting a search goes through the radar endpoint, never the collection state", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.deleteSearch("radar-1");

  assert.equal(requests[0].options.method, "DELETE");
  assert.match(requests[0].url, /\/radar\/searches\/radar-1$/);
  assert.equal(requests.filter((item) => item.url.includes("/state")).length, 0);
});

test("a result explains why it fits and what is still unverified", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-abc",
            listingId: "ebay-us-abc",
            sourceId: "ebay-us",
            sourceLabel: "eBay USA",
            externalId: "v1|123|0",
            title: "PS2 Slim SCPH-79001 tested",
            priceLabel: "USD 149.99",
            priceAmount: 149.99,
            priceCurrency: "USD",
            shippingAmount: 12,
            totalAmount: 161.99,
            conditionLabel: "Pre-owned",
            shippingLabel: "USD 12",
            locationLabel: "US",
            sellerLabel: "retrogames · 99.4%",
            listingKind: "fixed_price",
            listingType: "Compra directa",
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            confidence: 0.82,
            reasons: ["Declara estar probada («tested»)", "Dentro del presupuesto: USD 149.99"],
            unverified: ["Región sin declarar; la búsqueda pide NTSC-U/C"],
            matchedTerms: ["tested"],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ]
  });

  assert.match(html, /Declara estar probada/);
  assert.match(html, /Dentro del presupuesto: USD 149\.99/);
  assert.match(html, /is-unverified">Región sin declarar/);
  assert.match(html, /82% de confianza/);
  assert.match(html, /USD 161,99 recibido/);
  assert.match(html, /retrogames/);
});

test("a total equal to the item price is not shown as a separate received total", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-abc",
            sourceId: "ebay-us",
            title: "PS2 Slim",
            priceLabel: "USD 149.99",
            priceAmount: 149.99,
            priceCurrency: "USD",
            totalAmount: 149.99,
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ]
  });

  assert.doesNotMatch(html, /recibido/);
});

test("the repository reads the deduplicated listing feed", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.loadListings(25);

  assert.match(requests[0].url, /\/radar\/listings\?limit=25$/);
  assert.equal(requests[0].options.method, undefined);
});

const SCHEDULE = {
  version: 1,
  timezone: "America/Montevideo",
  now: "2026-09-11T10:00:00-03:00",
  nextSlot: { slotKey: "afternoon", label: "16:00", at: "2026-09-11T16:00:00-03:00" },
  slots: [
    { slotKey: "morning", label: "09:00", state: "fulfilled", runId: "radar-run-1", detail: "" },
    { slotKey: "afternoon", label: "16:00", state: "pending", runId: "", detail: "" },
    { slotKey: "night", label: "22:30", state: "pending", runId: "", detail: "" }
  ],
  current: null,
  runs: [
    {
      id: "radar-run-1",
      kind: "scheduled",
      slotKey: "morning",
      slotLabel: "09:00",
      status: "completed",
      searchesTotal: 3,
      searchesOk: 3,
      searchesFailed: 0,
      listingsMatched: 4,
      listingsRejected: 9,
      receipts: []
    }
  ]
};

test("the schedule strip shows today's slots, the next run and the last result", async () => {
  const { html } = await renderPage({ items: [search()], runs: SCHEDULE });

  assert.match(html, /Tres revisiones por día/);
  assert.match(html, /radar-slot is-fulfilled[\s\S]{0,80}09:00 <em>corrida<\/em>/);
  assert.match(html, /radar-slot is-pending[\s\S]{0,80}16:00 <em>pendiente<\/em>/);
  assert.match(html, /Próxima:/);
  assert.match(html, /Última corrida completa: 3\/3 búsquedas · 4 encajan · 9 descartadas/);
  assert.match(html, /data-run-all="1"/);
});

test("a skipped slot is labelled as such instead of looking like a run", async () => {
  const { html } = await renderPage({
    items: [search()],
    runs: {
      ...SCHEDULE,
      slots: [
        { slotKey: "morning", label: "09:00", state: "skipped", runId: "", detail: "Slot vencido mientras el add-on no corría" },
        { slotKey: "afternoon", label: "16:00", state: "fulfilled", runId: "radar-run-2", detail: "" },
        { slotKey: "night", label: "22:30", state: "pending", runId: "", detail: "" }
      ]
    }
  });

  assert.match(html, /radar-slot is-skipped[\s\S]{0,80}09:00 <em>salteada<\/em>/);
  assert.match(html, /title="Slot vencido mientras el add-on no corría"/);
});

test("a run in flight disables the global button and says it is working", async () => {
  const { html } = await renderPage({
    items: [search()],
    runs: { ...SCHEDULE, current: { id: "radar-run-9", status: "running", kind: "manual" } }
  });

  assert.match(html, /data-run-all="1" disabled/);
  assert.match(html, /Buscando…/);
});

test("the page still renders when the scheduler endpoint is unavailable", async () => {
  const { html } = await renderPage({ items: [search()], runs: { slots: [], runs: [] } });

  assert.doesNotMatch(html, /Tres revisiones por día/);
  assert.match(html, /PlayStation 2 lista para usar/);
});

test("a search reports which slots it runs in", async () => {
  const { html } = await renderPage({
    items: [search({ slots: ["morning"], slotLabels: ["09:00"] })],
    runs: SCHEDULE
  });

  assert.match(html, /Corre 09:00/);
});

test("the repository posts the global run with the radar write header", async () => {
  const { repository, requests } = await renderPage({ items: [search()], runs: SCHEDULE });
  requests.length = 0;

  await repository.startRun();

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/run-now$/);
});

test("the Master action is offered and never promises to activate anything", async () => {
  const { html } = await renderPage({ items: [search()], runs: SCHEDULE });

  assert.match(html, /data-master="1"/);
  assert.match(html, /Que el Master proponga/);
});

test("the repository asks the Master through its own endpoint", async () => {
  const { repository, requests } = await renderPage({ items: [search()], runs: SCHEDULE });
  requests.length = 0;

  await repository.regenerateMaster();

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/master\/regenerate$/);
});

test("a result leads with its decision band and names the reference behind it", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-abc",
            sourceId: "ebay-us",
            sourceLabel: "eBay USA",
            title: "PS1 console tested",
            priceLabel: "USD 35.00",
            priceAmount: 35,
            priceCurrency: "USD",
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            score: 78,
            band: "ganga",
            valuation: {
              benchmark: { value: 55, currency: "USD", sourceLabel: "PriceCharting", stale: false, independent: true }
            },
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ],
    runs: SCHEDULE
  });

  assert.match(html, /chase-result-band is-ganga">Ganga real · 78\/100/);
  assert.match(html, /vs USD 55 \(PriceCharting\)/);
});

test("a reference that is stale or not independent says so on the card", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-abc",
            sourceId: "ebay-us",
            title: "PS1 console",
            priceLabel: "USD 90.00",
            priceAmount: 90,
            priceCurrency: "USD",
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            score: 31,
            band: "caro",
            valuation: {
              benchmark: { value: 55, currency: "USD", sourceLabel: "PriceCharting", stale: true, independent: false }
            },
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ],
    runs: SCHEDULE
  });

  assert.match(html, /is-caro">Caro/);
  assert.match(html, /referencia vieja/);
  assert.match(html, /no independiente/);
});

test("the imported cost is shown as an estimate, never as a firm total", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-abc",
            sourceId: "ebay-us",
            title: "PS2 Slim tested",
            priceLabel: "USD 150.00",
            priceAmount: 150,
            priceCurrency: "USD",
            totalAmount: 162,
            listingUrl: "https://www.ebay.com/itm/1",
            imageUrl: "",
            score: 62,
            band: "razonable",
            valuation: {
              cost: { currency: "USD", subtotalUsa: 162, importedTotal: 214.5, importedEstimated: true }
            },
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ],
    runs: SCHEDULE
  });

  assert.match(html, /≈ USD 214,5 puesto acá/);
  assert.match(html, /USD 162 recibido/);
});

test("a listing with no imported estimate does not fake one", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-lot",
            sourceId: "ebay-us",
            title: "PS2 game lot",
            priceLabel: "USD 189.00",
            priceAmount: 189,
            priceCurrency: "USD",
            listingUrl: "https://www.ebay.com/itm/2",
            imageUrl: "",
            valuation: { cost: { currency: "USD", subtotalUsa: 189, importedTotal: null } },
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z"
          }
        ],
        resultCount: 1
      })
    ],
    runs: SCHEDULE
  });

  assert.doesNotMatch(html, /puesto acá/);
});
