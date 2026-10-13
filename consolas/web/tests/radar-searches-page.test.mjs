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
    // Previsualizar no depende del estado: sólo de que una fuente pueda correr.
    canPreview: sources.some((id) => SOURCES.find((item) => item.id === id)?.executable),
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

async function renderPage({
  items = [], environment = "production", failLoad = false, runs = null, search = "",
  coverage = { consoles: [], ownedWithoutSearch: [], wantedGames: 0, uncoveredGames: 0, explicitWanted: 0, explicitUncovered: 0 },
  expanded = null, resultSortBy = null, coverageOpen = false,
  // Casi todos los tests miran lo que muestra una búsqueda desplegada; los que
  // prueban cómo arranca la página piden `expandAll: false`.
  expandAll = true, viewMode = null, collapsedGroups = null, storedPrefs = undefined,
  // Los objetivos canónicos los manda el servidor; sin ellos (un servidor
  // anterior) la vista por plataforma muestra las búsquedas como antes.
  taxonomy = null,
} = {}) {
  let html = "";
  const requests = [];
  // Un DOM mínimo: lo justo para que la página enganche sus handlers sobre el
  // HTML que acaba de pintar y el test pueda dispararlos como un click.
  let bound = new Map();
  const camel = (name) => name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  const root = {
    set innerHTML(value) {
      html = String(value);
      bound = new Map();
    },
    get innerHTML() {
      return html;
    },
    querySelectorAll(selector) {
      const match = /^\[([a-z-]+)\]$/.exec(selector);
      if (!match) return [];
      const elements = [...html.matchAll(new RegExp(`<[a-z]+\\b[^>]*\\s${match[1]}="[^"]*"[^>]*>`, "g"))].map((tag) => {
        const dataset = {};
        for (const [, name, value] of tag[0].matchAll(/\sdata-([a-z-]+)="([^"]*)"/g)) dataset[camel(name)] = value;
        const handlers = {};
        return { dataset, handlers, addEventListener(type, fn) { (handlers[type] ||= []).push(fn); } };
      });
      bound.set(selector, elements);
      return elements;
    }
  };
  async function fire(selector, predicate = () => true, { type = "click", value } = {}) {
    const element = (bound.get(selector) || []).find((candidate) => predicate(candidate.dataset));
    if (!element) throw new Error(`Nada que disparar en ${selector}`);
    for (const handler of element.handlers[type] || []) {
      await handler({ preventDefault() {}, target: { value }, currentTarget: element });
    }
    await new Promise((resolve) => setImmediate(resolve));
    return html;
  }
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
    ...(taxonomy || {}),
    items
  };
  const fetchImpl = async (url, options = {}) => {
    requests.push({ url, options });
    if (failLoad) return { ok: false, status: 503, async json() { return { error: "sin backend" }; } };
    const body = url.includes("/radar/coverage")
      ? coverage
      : url.includes("/radar/runs")
        ? runs || { slots: [], runs: [] }
        : model;
    return { ok: true, status: 200, async json() { return body; } };
  };
  // Las preferencias de la vista viven en el navegador, así que el harness
  // necesita un localStorage para poder probar plegada y desplegada.
  const abiertas = expanded || (expandAll ? items.map((item) => item.id) : []);
  let guardado =
    storedPrefs !== undefined
      ? storedPrefs
      : JSON.stringify({
          sortBy: "default",
          expanded: abiertas,
          coverageOpen,
          ...(resultSortBy ? { resultSortBy } : {}),
          ...(viewMode ? { viewMode } : {}),
          ...(collapsedGroups ? { collapsedGroups } : {}),
        });
  const windowStub = {
    location: { search },
    localStorage: {
      getItem: (key) => (key === "consolas.radar.searchView" ? guardado : null),
      setItem: (key, value) => {
        if (key === "consolas.radar.searchView") guardado = value;
      },
    },
  };
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
    URLSearchParams,
    console: { error() {}, info() {} }
  });

  vm.runInContext(repositorySource, context, { filename: "radar-repository.js" });
  vm.runInContext(pageSource, context, { filename: "chasing-games.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return {
    get html() {
      return html;
    },
    requests,
    repository: windowStub.RadarRepository,
    fire,
    savedPrefs: () => JSON.parse(guardado || "{}"),
  };
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
  // Plegada —como arranca— igual tiene que decir qué es y dejar activarla:
  // con setenta propuestas, activar una no puede costar dos clicks.
  const { html } = await renderPage({
    items: [search({ status: "draft", origin: "master", lastCheckedAt: null })]
  });

  assert.match(html, /chase-status is-draft">Propuesta/);
  assert.match(html, /data-status="radar-1" data-next="active">Activar/);
  assert.doesNotMatch(html, /data-run=/);
  assert.match(html, /todavía no buscado/);
});

test("once opened, the draft explains why it is not running", async () => {
  const { html } = await renderPage({
    items: [search({ status: "draft", origin: "master", lastCheckedAt: null })],
    expanded: ["radar-1"],
  });

  assert.match(html, /No va a ejecutarse hasta que la actives/);
  assert.match(html, /por el Master de colección/);
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
  assert.match(html, /1 resultado activo/);
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

test("offers to add a ShopGoodwill find by hand, never a scan of its own", async () => {
  const { html } = await renderPage({ items: [search()] });

  assert.match(html, /data-manual-entry="radar-1">Agregar de ShopGoodwill/);
});

test("an archived search does not offer to add a manual find", async () => {
  const { html } = await renderPage({ items: [search({ status: "archived", archivedAt: "2026-09-01T00:00:00Z" })] });

  assert.doesNotMatch(html, /Agregar de ShopGoodwill/);
});

test("adding a ShopGoodwill find goes through its own endpoint, never a crawler", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.createManualListing({
    searchId: "radar-1",
    title: "PS2 Slim tested",
    listingUrl: "https://shopgoodwill.com/item/1",
    priceAmount: 45,
  });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/manual-listings$/);
  assert.match(requests[0].options.body, /"searchId":"radar-1"/);
});

test("a result pending verification says so and offers to confirm it", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "shopgoodwill-1",
            sourceId: "shopgoodwill",
            title: "PS2 Slim from ShopGoodwill",
            priceLabel: "USD 45.00",
            priceAmount: 45,
            priceCurrency: "USD",
            listingUrl: "https://shopgoodwill.com/item/1",
            requiresVerification: true,
            verifiedAt: null,
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z",
          },
        ],
        resultCount: 1,
      }),
    ],
  });

  assert.match(html, /Verificación pendiente/);
  assert.match(html, /data-verify="shopgoodwill-1">Marcar verificada/);
});

test("a verified result does not ask to be verified again", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "shopgoodwill-1",
            sourceId: "shopgoodwill",
            title: "PS2 Slim from ShopGoodwill",
            priceLabel: "USD 45.00",
            priceAmount: 45,
            priceCurrency: "USD",
            listingUrl: "https://shopgoodwill.com/item/1",
            requiresVerification: false,
            verifiedAt: "2026-09-11T10:00:00Z",
            reasons: [],
            unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z",
          },
        ],
        resultCount: 1,
      }),
    ],
  });

  assert.doesNotMatch(html, /Verificación pendiente/);
  assert.doesNotMatch(html, /Marcar verificada/);
});

test("verifying a listing goes through its own endpoint with the write header", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.verifyListing("shopgoodwill-1");

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/listings\/shopgoodwill-1\/verify$/);
});

test("every result offers to value it as a lot, pieces filled in by hand", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-lot-1", sourceId: "ebay-us", title: "PS2 bundle, 8 games",
            priceLabel: "USD 60.00", priceAmount: 60, priceCurrency: "USD", totalAmount: 60,
            listingUrl: "https://www.ebay.com/itm/1", reasons: [], unverified: [],
            lastSeenAt: "2026-09-10T10:00:00Z",
          },
        ],
        resultCount: 1,
      }),
    ],
  });

  assert.match(html, /data-lot-calc="ebay-us-lot-1">Valorar como lote/);
  assert.doesNotMatch(html, /radar-lot-calc/, "el panel no se abre solo");
});

test("the lot calculator computes through its own endpoint, pieces and totalCost, never a title parsed", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.computeLotValuation({
    totalCost: 60,
    pieces: [
      { name: "PS2 Slim", comparableValue: 40, wanted: true, alreadyOwned: false, isDuplicate: false },
      { name: "God of War", comparableValue: 15, wanted: true, alreadyOwned: false, isDuplicate: false },
    ],
  });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/lot-valuation$/);
  assert.match(requests[0].options.body, /"comparableValue":40/);
  assert.match(requests[0].options.body, /"comparableValue":15/);
});

test("arriving from the Franchise Tracker's Buscar con Radar link opens the create form pre-filled", async () => {
  const { html } = await renderPage({
    items: [],
    search: "?open=create&prefillName=God+of+War+III+%28PlayStation+3%29&prefillPlatform=PlayStation+3"
  });

  assert.match(html, /chasing-add is-open/);
  assert.match(html, /value="God of War III \(PlayStation 3\)"/);
  assert.match(html, /value="PlayStation 3"/);
});

test("with no query string at all, the create form stays closed as always", async () => {
  const { html } = await renderPage({ items: [] });
  assert.doesNotMatch(html, /chasing-add is-open/);
});

test("a result can be discarded right where it appears, with a reason", async () => {
  const { html } = await renderPage({
    items: [
      search({
        results: [
          {
            id: "ebay-us-9",
            sourceId: "ebay-us",
            title: "God of War PS2 EMPTY CASE ONLY no disc",
            priceLabel: "USD 9.99",
            conditionLabel: "Pre-owned",
            shippingLabel: "",
            locationLabel: "US",
            listingType: "Compra directa",
            listingUrl: "https://www.ebay.com/itm/9",
            imageUrl: "",
            lastSeenAt: "2026-09-11T10:00:00Z",
          },
        ],
        resultCount: 1,
      }),
    ],
  });

  assert.match(html, /data-dismiss="ebay-us-9">Descartar/);
  assert.doesNotMatch(html, /radar-dismiss-form/, "el motivo se pide recién al tocar Descartar");
});

test("discarding goes through the decisions endpoint, never the collection state", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.decide("ebay-us-9", {
    decision: "dismissed",
    reason: "no-es-lo-que-busco",
    note: "caja suelta",
  });

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/decisions$/);
  assert.match(requests[0].options.body, /"listingId":"ebay-us-9"/);
  assert.match(requests[0].options.body, /"decision":"dismissed"/);
  assert.match(requests[0].options.body, /"reason":"no-es-lo-que-busco"/);
  assert.doesNotMatch(requests[0].url, /\/api\/state/);
});

test("both places that discard offer the same reasons, from one shared list", async () => {
  const { repository } = await renderPage({ items: [search()] });

  const ids = repository.DISMISS_REASONS.map((reason) => reason.id);
  assert.ok(ids.includes("no-es-lo-que-busco"), "el caso de la caja suelta tiene su motivo");
  assert.ok(ids.includes("ya-lo-tengo"));
});

test("the form offers a target price and says it does not filter", async () => {
  const { html } = await renderPage({ items: [], search: "?open=create" });

  assert.match(html, /name="targetItemPrice"/);
  assert.match(html, /No filtra/);
});

test("the lot minimum says what it does and what it cannot do", async () => {
  // El campo prometía un filtro absoluto. Descarta lo que declara menos
  // piezas, pero un título que no las cuenta no se puede descartar sin
  // inventar el dato, y eso tiene que estar dicho donde se carga.
  const { html } = await renderPage({ items: [], search: "?open=create" });

  assert.match(html, /name="minLotSize"/);
  assert.match(html, /Descarta lo que declare menos piezas/);
  assert.match(html, /sin verificar/);
});

test("a lot search shows its piece minimum as a chip", async () => {
  const lot = search();
  lot.searchType = "lot";
  lot.criteria = { ...lot.criteria, minLotSize: 6 };
  const { html } = await renderPage({ items: [lot] });

  assert.match(html, /Lotes desde 6 piezas/);
});

test("an existing target price comes back into the edit form", async () => {
  const withTarget = search();
  withTarget.criteria = { ...withTarget.criteria, targetItemPrice: 80 };
  const { html } = await renderPage({ items: [withTarget], search: "?open=create" });

  // El alta viene vacía; lo que importa es que el campo exista para cargarlo.
  assert.match(html, /name="targetItemPrice"/);
});

test("a search that points at nothing says what it cannot do", async () => {
  const item = search();
  item.entityType = "";
  item.entityId = "";
  const { html } = await renderPage({ items: [item] });

  assert.match(html, /radar-unlinked-note/);
  assert.match(html, /no apunta a nada del catálogo/);
  assert.match(html, /Registrar compra/);
});

test("a game search without its console says exactly that", async () => {
  const item = search();
  item.entityType = "game";
  item.entityId = "aladdin";
  item.entityConsoleId = "";
  const { html } = await renderPage({ items: [item] });

  assert.match(html, /no dice de qué consola es ese juego/);
});

test("a linked search says nothing about linking", async () => {
  const { html } = await renderPage({ items: [search()] });
  assert.doesNotMatch(html, /radar-unlinked-note/);
});

test("a lot search is not nagged: it exists to bring mixed things", async () => {
  const item = search();
  item.searchType = "lot";
  item.entityType = "";
  item.entityId = "";
  const { html } = await renderPage({ items: [item] });

  assert.doesNotMatch(html, /radar-unlinked-note/);
});

test("the form lets you link the entity, so the note is actionable", async () => {
  const { html } = await renderPage({ items: [], search: "?open=create" });

  assert.match(html, /name="entityType"/);
  assert.match(html, /name="entityId"/);
  assert.match(html, /name="entityConsoleId"/);
});

test("what the radar is not watching is spelled out, with the real denominator", async () => {
  const { html } = await renderPage({
    items: [search()],
    coverage: {
      consoles: [
        { id: "snes", name: "Super Nintendo", owned: true, hasSearch: true, wantedGames: 15, uncoveredGames: 14, explicitUncovered: 9, examples: ["Chrono Trigger", "Super Metroid"] },
        { id: "ps1", name: "PlayStation", owned: true, hasSearch: false, wantedGames: 11, uncoveredGames: 11, explicitUncovered: 6, examples: ["Silent Hill"] },
      ],
      ownedWithoutSearch: ["ps1"],
      wantedGames: 26,
      uncoveredGames: 25,
      explicitWanted: 16,
      explicitUncovered: 15,
    },
  });

  // El resumen se lee sin abrir nada: es el número que decide si vale la pena
  // mirar el detalle.
  assert.match(html, /15 de los 16 juegos que marcaste/);
  assert.match(html, /10 recomendaciones conservadas/);
  // El detalle —ocho consolas con ejemplos— empujaba las búsquedas fuera de la
  // primera pantalla, así que arranca plegado.
  assert.doesNotMatch(html, /Consolas tuyas sin ninguna búsqueda/);
  assert.doesNotMatch(html, /Chrono Trigger/);
  assert.match(html, /data-toggle-coverage/);
});

test("the coverage detail is there once you open it", async () => {
  const { html } = await renderPage({
    items: [search()],
    coverageOpen: true,
    coverage: {
      consoles: [
        { id: "snes", name: "Super Nintendo", owned: true, hasSearch: true, wantedGames: 15, uncoveredGames: 14, explicitUncovered: 9, examples: ["Chrono Trigger", "Super Metroid"] },
        { id: "ps1", name: "PlayStation", owned: true, hasSearch: false, wantedGames: 11, uncoveredGames: 11, explicitUncovered: 6, examples: ["Silent Hill"] },
      ],
      ownedWithoutSearch: ["ps1"],
      wantedGames: 26,
      uncoveredGames: 25,
      explicitWanted: 16,
      explicitUncovered: 15,
    },
  });

  assert.match(html, /Consolas tuyas sin ninguna búsqueda: PlayStation/);
  assert.match(html, /Chrono Trigger/);
  assert.match(html, /15 de los 16 juegos que marcaste/);
});

test("full coverage says nothing at all", async () => {
  const { html } = await renderPage({
    items: [search()],
    coverage: { consoles: [{ id: "snes", name: "Super Nintendo", owned: true, hasSearch: true, wantedGames: 2, uncoveredGames: 0, explicitUncovered: 0, examples: [] }], ownedWithoutSearch: [], wantedGames: 2, uncoveredGames: 0, explicitWanted: 2, explicitUncovered: 0 },
  });

  assert.doesNotMatch(html, /radar-coverage/);
});

test("the page still works when coverage cannot be loaded", async () => {
  const { html } = await renderPage({ items: [search()], coverage: null });
  assert.match(html, /Búsquedas del radar/);
  assert.doesNotMatch(html, /radar-coverage/);
});

test("every search starts as a compact row, draft or active", async () => {
  const { html } = await renderPage({
    items: [search({ status: "draft" }), search({ id: "radar-2", name: "PS2 activa" })],
    expandAll: false,
  });

  assert.match(html, /data-toggle-card="radar-1"/);
  assert.match(html, /radar-row is-draft"/);
  assert.match(html, /radar-row is-active"/);
  // Plegada muestra lo justo: tipo, nombre, estado, prioridad y números. Nada de criterios.
  assert.doesNotMatch(html, /radar-chips/);
  assert.doesNotMatch(html, /radar-row-detail/);
  assert.match(html, /aria-expanded="false" aria-label="Desplegar PS2 activa"/);
});

test("once opened, a search shows its criteria, notes and results", async () => {
  const { html } = await renderPage({ items: [search({ notes: "Revisar el lector" })] });

  assert.match(html, /radar-row is-active is-open/);
  assert.match(html, /radar-chips/);
  assert.match(html, /Revisar el lector/);
  assert.match(html, /aria-expanded="true"/);
});

test("the order can be chosen, and the default is the configured priority", async () => {
  const { html } = await renderPage({ items: [search()] });

  assert.match(html, /data-sort="1"/);
  assert.match(html, /<option value="default" selected>Prioridad configurada/);
  assert.match(html, /Buscadas hace más tiempo/);
});

test("a paused search can be previewed without being resumed", async () => {
  // La única forma de ver qué traería una búsqueda suspendida sin activarla.
  const { html } = await renderPage({ items: [search({ status: "paused" })] });

  assert.match(html, /data-preview="radar-1"/);
  assert.doesNotMatch(html, /data-preview="radar-1" disabled/);
  assert.match(html, /data-status="radar-1" data-next="active">Reanudar/);
});

test("previewing is offered for a draft too, and refused when no source can run", async () => {
  const draft = await renderPage({ items: [search({ status: "draft" })] });
  assert.match(draft.html, /data-preview="radar-1"/);

  const blocked = await renderPage({ items: [search({ status: "paused", sources: ["shopgoodwill"] })] });
  assert.match(blocked.html, /data-preview="radar-1" disabled/);
});

test("the card says how many extra queries a search carries", async () => {
  const item = search({
    status: "paused",
    criteria: { ...search().criteria, queries: ["Daxter PSP", "Patapon PSP"] }
  });
  const { html } = await renderPage({ items: [item], expanded: ["radar-1"] });

  assert.match(html, /2 consultas extra/);

  const one = await renderPage({
    items: [search({ criteria: { ...search().criteria, queries: ["Daxter PSP"] } })],
    expanded: ["radar-1"]
  });
  assert.match(one.html, /1 consulta extra/);
});

test("the search form carries a field for the extra queries", async () => {
  const source = await readFile(new URL("../chasing-games.js", import.meta.url), "utf8");

  assert.match(source, /name="queries"/);
  assert.match(source, /Consultas extra/);
  // Una por línea: partir por coma rompería "Patapon 2, the War of the Lions".
  assert.match(source, /data\.get\("queries"\) \|\| ""\)\.split\("\\n"\)/);
});

/**
 * Ordenar las publicaciones dentro de una búsqueda.
 *
 * Los títulos y precios son los de una corrida real de «God of War PSP» del
 * 2026-09-15: once resultados, nueve de Chains of Olympus y uno de Ghost of
 * Sparta, a precios que van de 25 a 99,99. El orden del servidor es por score,
 * que sirve para decidir rápido y no para comparar once copias del mismo juego.
 */
function godOfWarResults() {
  return [
    { id: "r1", title: "God of War Chains of Olympus (Sony PlayStation Portable PSP, 2008)", priceAmount: 30, priceCurrency: "USD" },
    { id: "r2", title: "God of War Chains of Olympus (Sony PSP, 2008)", priceAmount: 25, priceCurrency: "USD" },
    { id: "r3", title: "God of War: Ghost of Sparta (Sony PSP, 2010) - CIB Complete W/ Manual & Insert", priceAmount: 99.99, priceCurrency: "USD" },
    { id: "r4", title: "God of War Chains of Olympus Sony PSP 2008 CIB Complete With Manual", priceAmount: 33.99, priceCurrency: "USD" },
    { id: "r5", title: "Sony God of War: Chains of Olympus PSP 2007 M Action Adventure w/ Manual", priceAmount: 49.99, priceCurrency: "USD" },
  ];
}

function searchWithResults(results) {
  return { ...search({ id: "gow", name: "God of War — PSP", status: "active" }), id: "gow", results };
}

test("results can be ordered by game and price, so copies sit together and cheapest first", async () => {
  const page = await renderPage({
    items: [searchWithResults(godOfWarResults())],
    expanded: ["gow"],
    resultSortBy: "title-price",
  });
  // El orden elegido aplica a la lista completa: plegada muestra las tres mejores.
  const html = await page.fire("[data-show-all]", (data) => data.showAll === "gow");

  const order = [...html.matchAll(/<h3>([^<]*God of War[^<]*)/g)].map((match) => match[1]);
  const chains = order.filter((title) => title.includes("Chains"));
  const sparta = order.findIndex((title) => title.includes("Ghost of Sparta"));

  // Las cuatro de Chains quedan juntas y ordenadas de más barata a más cara.
  assert.equal(chains.length, 4);
  assert.ok(order.indexOf(chains[0]) < sparta, "Chains va antes que Ghost of Sparta");
  assert.match(chains[0], /Sony PSP, 2008/);
});

test("results can be ordered by price alone", async () => {
  const page = await renderPage({
    items: [searchWithResults(godOfWarResults())],
    expanded: ["gow"],
    resultSortBy: "price-asc",
  });
  const html = await page.fire("[data-show-all]", (data) => data.showAll === "gow");

  const first = html.indexOf("God of War Chains of Olympus (Sony PSP, 2008)");
  const last = html.indexOf("Ghost of Sparta");
  assert.ok(first >= 0 && last > first, "la de 25 va antes que la de 99,99");
});

test("a single result gets no sort control: there is nothing to order", async () => {
  const { html } = await renderPage({
    items: [searchWithResults([godOfWarResults()[0]])],
    expanded: ["gow"],
  });

  assert.doesNotMatch(html, /data-result-sort/);
  assert.doesNotMatch(html, /data-show-all/);
});

/* ------------------------------------------------------------------------ */
/* Pantalla agrupada: por plataforma, por objetivo, filas compactas          */
/* ------------------------------------------------------------------------ */
//
// Las búsquedas de acá imitan la forma del portafolio retro —una consola, un
// lote y unos deseados por plataforma— pero son datos inventados: la página no
// sabe qué búsquedas existen, agrupa por lo que cada una declara.

function portfolio() {
  return [
    search({ id: "n64-lot", name: "Lotes de juegos N64", searchType: "lot", priority: "media", platform: "N64",
      platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2, entityType: "", entityId: "" }),
    search({ id: "snes-console", name: "Super Nintendo — consola", searchType: "console", priority: "alta",
      platform: "SNES", platformKey: "snes", platformLabel: "SNES", platformOrder: 0 }),
    search({ id: "n64-wish", name: "Deseados N64", searchType: "chase", priority: "media-alta", platform: "N64",
      platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2, entityType: "game", entityConsoleId: "n64" }),
    search({ id: "n64-console", name: "Nintendo 64 — consola", searchType: "console", priority: "alta",
      platform: "Nintendo 64", platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2,
      lastCheckedAt: "2026-10-12T19:00:00Z" }),
    search({ id: "otra", name: "Algo sin plataforma", searchType: "discovery", priority: "baja", platform: "" }),
  ];
}

function result(id, overrides = {}) {
  return {
    id,
    sourceId: "ebay-us",
    title: `Publicación ${id}`,
    priceLabel: "USD 20",
    priceAmount: 20,
    priceCurrency: "USD",
    listingUrl: `https://www.ebay.com/itm/${id}`,
    reasons: [],
    unverified: [],
    lastSeenAt: "2026-10-12T19:00:00Z",
    ...overrides,
  };
}

const sectionOrder = (html) => [...html.matchAll(/<section class="radar-group[^"]*" data-group="([^"]+)"/g)].map((m) => m[1]);
const rowOrder = (html) => [...html.matchAll(/<article class="radar-row[^"]*" id="search-([^"]+)"/g)].map((m) => m[1]);

test("by default the searches are grouped by platform, one section each, in the platform order", async () => {
  const { html } = await renderPage({ items: portfolio(), expandAll: false });

  assert.deepEqual(sectionOrder(html), ["platform:snes", "platform:n64", "platform:none"]);
  // «N64» y «Nintendo 64» son la misma plataforma: una sola sección.
  assert.equal((html.match(/data-group="platform:n64"/g) || []).length, 1);
  assert.match(html, /radar-group-name">Nintendo 64</);
  assert.match(html, /radar-group-name">Sin plataforma</);
  assert.match(html, /aria-pressed="true">Por plataforma/);
});

test("inside a platform, searches follow the configured priority: console, wishlist, lots", async () => {
  const { html } = await renderPage({ items: portfolio(), expandAll: false });

  assert.deepEqual(rowOrder(html), ["snes-console", "n64-console", "n64-wish", "n64-lot", "otra"]);
});

test("a platform header sums up searches, active results, last check, errors and state", async () => {
  const items = portfolio();
  items[0] = { ...items[0], status: "paused", results: [result("a")], resultCount: 1 };
  items[2] = { ...items[2], results: [result("b"), result("c")], resultCount: 2, lastError: "eBay respondió 500" };
  const { html } = await renderPage({ items, expandAll: false });
  const n64 = html.slice(html.indexOf('data-group="platform:n64"'), html.indexOf('data-group="platform:none"'));

  assert.match(n64, /3 búsquedas/);
  assert.match(n64, /3 resultados activos/);
  assert.match(n64, /revisada 12 oct/);
  assert.match(n64, /radar-group-state is-partial">Parcial/);
  assert.match(n64, /1 con error/);
});

test("the group state reads active, paused or no results", async () => {
  const { repository } = await renderPage({ items: [] });

  assert.equal(repository.summarizeGroup([search({ results: [result("a")], resultCount: 1 })]).status, "active");
  assert.equal(repository.summarizeGroup([search({ resultCount: 0 })]).status, "empty");
  assert.equal(repository.summarizeGroup([search({ status: "paused" })]).statusLabel, "Pausada");
});

test("the objective view groups consoles, wishlists, lots and anything else", async () => {
  const { html } = await renderPage({ items: portfolio(), expandAll: false, viewMode: "objective" });

  assert.deepEqual(sectionOrder(html), [
    "objective:console",
    "objective:wishlist",
    "objective:lot",
    "objective:other",
  ]);
  assert.match(html, /radar-group-name">Consolas completas</);
  assert.match(html, /radar-group-name">Juegos deseados</);
  assert.match(html, /radar-group-name">Lotes de juegos</);
  assert.match(html, /radar-group-name">Otros tipos</);
  // Por objetivo, cada fila dice de qué plataforma es.
  assert.match(html, /Consola completa · Nintendo 64/);
  assert.deepEqual(rowOrder(html).slice(0, 2), ["snes-console", "n64-console"]);
});

test("the chosen view is a browser preference that survives a reload, never collection state", async () => {
  const page = await renderPage({ items: portfolio(), expandAll: false });
  await page.fire("[data-view]", (data) => data.view === "objective");

  assert.equal(page.savedPrefs().viewMode, "objective");
  assert.ok(page.requests.every((request) => !request.url.includes("/api/state")));
  assert.ok(page.requests.every((request) => !request.options.method || request.options.method === "GET"));

  const reloaded = await renderPage({ items: portfolio(), storedPrefs: JSON.stringify(page.savedPrefs()) });
  assert.match(reloaded.html, /aria-pressed="true">Por tipo de búsqueda/);
  assert.match(reloaded.html, /data-group="objective:console"/);
});

test("status filters still apply, and empty groups disappear", async () => {
  const items = portfolio();
  items[0] = { ...items[0], status: "paused" };
  const page = await renderPage({ items, expandAll: false });
  const html = await page.fire("[data-filter]", (data) => data.filter === "paused");

  assert.deepEqual(sectionOrder(html), ["platform:n64"]);
  assert.deepEqual(rowOrder(html), ["n64-lot"]);
});

test("a platform can be folded, and the page remembers it", async () => {
  const page = await renderPage({ items: portfolio(), expandAll: false });
  const html = await page.fire("[data-toggle-group]", (data) => data.toggleGroup === "platform:n64");

  assert.deepEqual(page.savedPrefs().collapsedGroups, ["platform:n64"]);
  assert.match(html, /radar-group is-collapsed" data-group="platform:n64"/);
  assert.doesNotMatch(html, /id="search-n64-console"/);
  // El encabezado plegado sigue diciendo qué hay adentro.
  assert.match(html, /data-group="platform:n64"[\s\S]*?3 búsquedas/);
});

test("a compact row carries type, name, state, priority, last run, count, best result and daily actions", async () => {
  const item = search({
    results: [result("best", { band: "buena", title: "PS2 Slim SCPH-79001 controller cables" })],
    resultCount: 1,
    lastCheckedAt: "2026-10-12T19:00:00Z",
  });
  const { html } = await renderPage({ items: [item], expandAll: false });
  const row = html.slice(html.indexOf('<article class="radar-row'));

  assert.match(row, /Consola completa/);
  assert.match(row, /PlayStation 2 lista para usar/);
  assert.match(row, /chase-status is-active">Activa/);
  assert.match(row, /Prioridad alta/);
  assert.match(row, /12 oct/);
  assert.match(row, /1 resultado</);
  assert.match(row, /radar-row-best[\s\S]*Buena compra[\s\S]*PS2 Slim SCPH-79001 controller cables/);
  assert.match(row, /data-run="radar-1"/);
  assert.match(row, /data-status="radar-1" data-next="paused">Pausar/);
  assert.match(row, /data-edit="radar-1">Editar/);
  // Lo menos frecuente vive en el detalle.
  assert.doesNotMatch(row, /data-delete=/);
  assert.doesNotMatch(row, /<article class="chase-result"/);
});

test("Editar opens the form inside the row, without unfolding anything else", async () => {
  const page = await renderPage({ items: portfolio(), expandAll: false });
  const html = await page.fire("[data-edit]", (data) => data.edit === "n64-wish");

  assert.match(html, /id="radarEdit-n64-wish"/);
  assert.match(html, /radar-row is-active is-open" id="search-n64-wish"/);
  assert.doesNotMatch(html, /radar-row is-active is-open" id="search-n64-console"/);
});

test("only the three best results show until all of them are asked for", async () => {
  const item = search({ results: ["a", "b", "c", "d", "e"].map((id) => result(id)), resultCount: 5 });
  const page = await renderPage({ items: [item] });

  assert.equal((page.html.match(/<article class="chase-result"/g) || []).length, 3);
  assert.match(page.html, /data-show-all="radar-1" aria-expanded="false">Ver todos los 5 resultados/);
  assert.doesNotMatch(page.html, /data-result-sort/);

  const html = await page.fire("[data-show-all]");
  assert.equal((html.match(/<article class="chase-result"/g) || []).length, 5);
  assert.match(html, /Mostrar sólo las 3 mejores/);
  assert.match(html, /data-result-sort/);
  // Ver todo sigue dejando descartar, valorar como lote y abrir la publicación.
  assert.equal((html.match(/data-dismiss="/g) || []).length, 5);
  assert.equal((html.match(/data-lot-calc="/g) || []).length, 5);
});

test("a wishlist shows one listing per title before repeating copies", async () => {
  const target = (name) => ({ query: `${name} n64`, gameId: name.toLowerCase(), name });
  const item = search({
    searchType: "chase",
    results: [
      result("t1", { title: "Tetris copia 1", titleTarget: target("Tetris") }),
      result("t2", { title: "Tetris copia 2", titleTarget: target("Tetris") }),
      result("m1", { title: "Mario copia 1", titleTarget: target("Mario") }),
      result("z1", { title: "Zelda copia 1", titleTarget: target("Zelda") }),
    ],
    resultCount: 4,
  });
  const { html } = await renderPage({ items: [item] });
  const titles = [...html.matchAll(/<h3>([^<]+copia[^<]*)<\/h3>/g)].map((m) => m[1]);

  assert.deepEqual(titles, ["Tetris copia 1", "Mario copia 1", "Zelda copia 1"]);
});

test("a wishlist says how many titles it covers and which it does not", async () => {
  const item = search({
    searchType: "chase",
    targetCoverage: { total: 12, covered: 4, uncovered: ["Paper Mario", "Banjo-Kazooie"] },
    hiddenCopies: 9,
  });
  const { html } = await renderPage({ items: [item] });

  assert.match(html, /4 de 12 títulos con resultado · sin cubrir: Paper Mario, Banjo-Kazooie · 9 copias repetidas no se muestran/);
});

test("lots with an unconfirmed quantity are kept apart from the ones that qualify", async () => {
  const item = search({
    searchType: "lot",
    results: [
      result("ok", { title: "Lot of 20 PS2 Games", band: "buena" }),
      result("dudoso", { title: "PS2 Game Bundle Kingdom Hearts", section: "unconfirmed-quantity", band: "sin-referencia" }),
    ],
    resultCount: 2,
  });
  const page = await renderPage({ items: [item], expandAll: false });

  assert.match(page.html, /radar-row-best[\s\S]*Lot of 20 PS2 Games/);
  let html = await page.fire("[data-toggle-card]");
  assert.match(html, /Cantidad sin confirmar · 1/);
  assert.doesNotMatch(html, /<h3>PS2 Game Bundle Kingdom Hearts/);
  html = await page.fire("[data-toggle-unconfirmed]");
  assert.match(html, /<h3>PS2 Game Bundle Kingdom Hearts/);
});

test("an unknown availability is shown as pending, never as available", async () => {
  const pending = result("p", {
    availability: "unknown",
    verification: { state: "pending", lastSeenAt: new Date(Date.now() - 3 * 3600000).toISOString(), ttlHours: 24 },
    requiresVerification: true,
  });
  const { html } = await renderPage({ items: [search({ results: [pending], resultCount: 1 })] });

  assert.match(html, /radar-verification is-pending">Verificación pendiente: encontrada hace 3 h, sin confirmar que se pueda comprar/);
  assert.match(html, /Buy It Now o Add to Cart/);
  assert.match(html, /data-verify="p">Marcar verificada/);
});

test("a fresh verification says when it expires, and an expired one asks again", async () => {
  const now = Date.now();
  const verified = result("v", {
    verification: {
      state: "verified",
      verifiedAt: new Date(now - 2 * 3600000).toISOString(),
      expiresAt: new Date(now + 22 * 3600000).toISOString(),
      ttlHours: 24,
    },
    requiresVerification: false,
  });
  const expired = result("x", {
    verification: { state: "expired", reason: "price-changed", verifiedAt: new Date(now - 5 * 3600000).toISOString() },
    requiresVerification: true,
  });
  const { html } = await renderPage({ items: [search({ results: [verified, expired], resultCount: 2 })] });

  assert.match(html, /is-verified">Verificada hace 2 h · vence en 22 h\./);
  assert.doesNotMatch(html, /data-verify="v"/);
  assert.match(html, /Verificación vencida \(cambió de precio después\)/);
  assert.match(html, /data-verify="x">Marcar verificada/);
});

test("a result that needs review says so, and says why", async () => {
  const review = result("r", {
    band: "revisar",
    valuation: {
      priceBand: "ganga",
      verdictNotes: ["Cartucho sin originalidad declarada: mirá etiqueta, placa y tornillos en las fotos."],
    },
  });
  const { html } = await renderPage({ items: [search({ results: [review], resultCount: 1 })] });

  assert.match(html, /chase-result-band is-revisar">Requiere revisión/);
  assert.match(html, /radar-verdict-notes"><li>Cartucho sin originalidad declarada/);
});

/* ------------------------------------------------------------------------ */
/* Objetivos canónicos dentro de cada plataforma                             */
/* ------------------------------------------------------------------------ */
//
// La taxonomía la manda el servidor con las búsquedas. Los tests usan la misma
// forma que `server/radar/taxonomy.py` produce, y uno usa otra a propósito para
// probar que la página no conoce ninguna lista de objetivos por su cuenta.

const TAXONOMY = {
  objectives: [
    { id: "game-lot", label: "Lotes de juegos", kind: "lot", completeness: "lot", canonical: true, order: 0 },
    { id: "game-loose", label: "Juegos loose", kind: "game", completeness: "loose", canonical: true, order: 1 },
    { id: "game-cib", label: "Juegos CIB", kind: "game", completeness: "cib", canonical: true, order: 2 },
    { id: "console-loose", label: "Consolas loose", kind: "console", completeness: "loose", canonical: true, order: 3 },
    { id: "console-cib", label: "Consolas CIB", kind: "console", completeness: "cib", canonical: true, order: 4 },
    { id: "other", label: "Otras piezas", kind: "other", completeness: "", canonical: false, order: 5 },
  ],
  sections: {
    "unconfirmed-quantity": { label: "Cantidad sin confirmar", detail: "No dicen cuántos juegos traen." },
    incomplete: { label: "No llega a CIB", detail: "Traen caja o manual, no los dos." },
  },
};

const objectiveResult = (id, objective, overrides = {}) =>
  result(id, { objective: { id: objective, status: overrides.status || "loose" }, score: 50, ...overrides });

function platformWithResults() {
  const console64 = search({
    id: "n64-console", name: "Nintendo 64 — consola", searchType: "console", platform: "N64",
    platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2,
    results: [
      objectiveResult("bundle", "console-loose", { title: "N64 Console Controller Jumper Pak Cords", band: "buena", score: 80, priceLabel: "USD 85" }),
      objectiveResult("alone", "console-loose", { title: "N64 Console Only", band: "razonable", score: 55 }),
      objectiveResult("cib", "console-cib", { title: "N64 Console Complete in Box", band: "revisar", score: 60, status: "declared" }),
    ],
  });
  const wish64 = search({
    id: "n64-wish", name: "Deseados N64", searchType: "chase", priority: "media-alta", platform: "N64",
    platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2,
    results: [
      objectiveResult("mario", "game-loose", { title: "Super Mario 64 Cartridge", band: "revisar", score: 40 }),
      objectiveResult("zelda", "game-loose", { title: "Zelda Ocarina Cartridge", band: "revisar", score: 45 }),
      objectiveResult("sm64-box", "game-cib", { title: "Super Mario 64 with box no manual", section: "incomplete", status: "incomplete", score: 10 }),
      // La misma publicación que encontró la búsqueda de consola: cuenta una vez.
      objectiveResult("bundle", "console-loose", { title: "N64 Console Controller Jumper Pak Cords", band: "buena", score: 70 }),
    ],
  });
  const lots64 = search({
    id: "n64-lot", name: "Lotes de juegos N64", searchType: "lot", priority: "media", platform: "N64",
    platformKey: "n64", platformLabel: "Nintendo 64", platformOrder: 2, results: [],
  });
  const ps2 = search({
    id: "ps2-lot", name: "Lotes de juegos PS2", searchType: "lot", priority: "media", platform: "PS2",
    platformKey: "ps2", platformLabel: "PlayStation 2", platformOrder: 4,
    results: [objectiveResult("lot22", "game-lot", { title: "Lot of 22 PS2 Games", band: "buena", score: 70, status: "lot" })],
  });
  return [console64, wish64, lots64, ps2];
}

const objectiveRows = (html, group) => {
  const start = html.indexOf(`data-group="${group}"`);
  const next = html.indexOf('<section class="radar-group', start + 1);
  const section = html.slice(start, next === -1 ? undefined : next);
  return [...section.matchAll(/data-objective="[^"/]+\/([^"]+)"/g)].map((m) => m[1]);
};

test("with the server's taxonomy, each platform shows its objectives in order, for any platform", async () => {
  const { html } = await renderPage({ items: platformWithResults(), taxonomy: TAXONOMY, expandAll: false });

  assert.deepEqual(objectiveRows(html, "platform:n64"), ["game-loose", "game-cib", "console-loose", "console-cib"]);
  assert.deepEqual(objectiveRows(html, "platform:ps2"), ["game-lot"]);
  // Los vacíos no ocupan una fila cada uno: se nombran juntos, atenuados.
  const n64 = html.slice(html.indexOf('data-group="platform:n64"'), html.indexOf('data-group="platform:ps2"'));
  assert.match(n64, /radar-objectives-empty"><span>Sin resultados<\/span><span class="radar-objective-chip">Lotes de juegos/);
  const ps2 = html.slice(html.indexOf('data-group="platform:ps2"'));
  assert.match(ps2, /Juegos loose<\/span><span class="radar-objective-chip">Juegos CIB/);
  // «Otras piezas» no es canónico: si no trae nada, ni se nombra.
  assert.doesNotMatch(html, /Otras piezas/);
});

test("the page draws whatever objectives the server sends, not a list of its own", async () => {
  const custom = {
    objectives: [
      { id: "console-cib", label: "Consolas en caja", canonical: true, order: 0 },
      { id: "game-loose", label: "Cartuchos sueltos", canonical: true, order: 1 },
    ],
    sections: {},
  };
  const { html } = await renderPage({ items: platformWithResults(), taxonomy: custom, expandAll: false });

  assert.deepEqual(objectiveRows(html, "platform:n64"), ["console-cib", "game-loose"]);
  assert.match(html, /radar-objective-label">Consolas en caja</);
  assert.doesNotMatch(html, /Juegos CIB/);
});

test("an objective row carries its count and its best result; a listing two searches found counts once", async () => {
  const { html } = await renderPage({ items: platformWithResults(), taxonomy: TAXONOMY, expandAll: false });
  const row = (id) => {
    const start = html.indexOf(`data-objective="platform:n64/${id}"`);
    return html.slice(start, html.indexOf("</article>", start));
  };

  assert.match(row("console-loose"), /radar-objective-count">2 resultados</);
  assert.match(row("console-loose"), /Buena compra[\s\S]*USD 85[\s\S]*N64 Console Controller Jumper Pak Cords/);
  assert.match(row("game-loose"), /Zelda Ocarina Cartridge/, "el mejor por score");
  assert.match(row("game-cib"), /0 resultados · 1 no llega a cib/);
  assert.match(row("game-cib"), /Nada cumple todavía/);
  // Encabezado de la plataforma: publicaciones únicas.
  assert.match(html, /data-group="platform:n64"[\s\S]*?6 resultados activos/);
});

test("an objective unfolds to its best three, the rest on demand, and the page remembers it", async () => {
  const items = platformWithResults();
  items[0].results.push(
    objectiveResult("c3", "console-loose", { title: "N64 Console Bundle Three", score: 30 }),
    objectiveResult("c4", "console-loose", { title: "N64 Console Bundle Four", score: 20 })
  );
  const page = await renderPage({ items, taxonomy: TAXONOMY, expandAll: false });
  let html = await page.fire("[data-toggle-objective]", (data) => data.toggleObjective === "platform:n64/console-loose");

  assert.deepEqual(page.savedPrefs().openObjectives, ["platform:n64/console-loose"]);
  const start = html.indexOf('data-objective="platform:n64/console-loose"');
  const body = html.slice(start, html.indexOf('data-objective="platform:n64/console-cib"'));
  assert.equal((body.match(/<article class="chase-result"/g) || []).length, 3);
  assert.match(html, /data-show-all="platform:n64\/console-loose" aria-expanded="false">Ver todos los 4 resultados/);
  // Cada publicación dice qué búsqueda la trajo; la repetida, las dos.
  assert.match(html, /De Nintendo 64 — consola · Deseados N64/);

  html = await page.fire("[data-show-all]", (data) => data.showAll === "platform:n64/console-loose");
  assert.match(html, /N64 Console Bundle Four/);
});

test("what does not meet its objective stays apart inside it", async () => {
  const page = await renderPage({
    items: platformWithResults(),
    taxonomy: TAXONOMY,
    expandAll: false,
    storedPrefs: JSON.stringify({ openObjectives: ["platform:n64/game-cib"] }),
  });

  assert.match(page.html, /No llega a CIB · 1/);
  assert.doesNotMatch(page.html, /<h3>Super Mario 64 with box no manual/);
  const html = await page.fire("[data-toggle-unconfirmed]");
  assert.match(html, /<h3>Super Mario 64 with box no manual/);
});

test("the searches of a platform fold under the objectives, with their actions one click away", async () => {
  const page = await renderPage({ items: platformWithResults(), taxonomy: TAXONOMY, expandAll: false });

  assert.match(page.html, /data-toggle-searches="platform:n64" aria-expanded="false">[\s\S]*?3 búsquedas[\s\S]*?todas activas/);
  assert.doesNotMatch(page.html, /id="search-n64-console"/);

  const html = await page.fire("[data-toggle-searches]", (data) => data.toggleSearches === "platform:n64");
  assert.deepEqual(page.savedPrefs().openSearchLists, ["platform:n64"]);
  assert.match(html, /id="search-n64-console"/);
  assert.match(html, /data-run="n64-console"/);
  assert.match(html, /data-edit="n64-wish">Editar/);
});

test("the search-type view and an older server keep showing searches as rows", async () => {
  const byType = await renderPage({ items: platformWithResults(), taxonomy: TAXONOMY, expandAll: false, viewMode: "objective" });
  assert.match(byType.html, /aria-pressed="true">Por tipo de búsqueda/);
  assert.match(byType.html, /radar-group-name">Consolas completas</);
  assert.match(byType.html, /id="search-n64-console"/);
  assert.doesNotMatch(byType.html, /data-toggle-objective/);

  const older = await renderPage({ items: platformWithResults(), expandAll: false });
  assert.match(older.html, /id="search-n64-console"/);
  assert.doesNotMatch(older.html, /data-toggle-objective/);
});

test("a declared CIB asks to check box and manual; a checked one says so", async () => {
  const declared = objectiveResult("cib", "game-cib", { status: "declared", band: "revisar" });
  const checked = objectiveResult("cib2", "console-cib", {
    status: "declared",
    verification: { state: "pending", contentsVerifiedAt: new Date(Date.now() - 3600000).toISOString() },
  });
  const { html } = await renderPage({ items: [search({ results: [declared, checked] })] });

  assert.match(html, /radar-contents is-declared">CIB declarado por el vendedor, sin comprobar: mirá caja y manual en las fotos/);
  assert.match(html, /data-verify-contents="cib">Comprobé caja y manual/);
  assert.match(html, /radar-contents is-verified">Caja y contenido comprobados hace 1 h/);
  assert.doesNotMatch(html, /data-verify-contents="cib2"/);
});

test("checking box and manual goes through its own radar endpoint, never collection state", async () => {
  const { repository, requests } = await renderPage({ items: [search()] });
  requests.length = 0;

  await repository.verifyListingContents("ebay-us-1");

  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers["X-Consolas-Radar"], "1");
  assert.match(requests[0].url, /\/radar\/listings\/ebay-us-1\/verify-contents$/);
});

test("once box and manual are checked, the card stops asking to check them", async () => {
  const note = "CIB declarado por el vendedor: confirmá caja original y manual en las fotos.";
  const checked = objectiveResult("cib", "game-cib", {
    status: "declared",
    band: "revisar",
    objective: { id: "game-cib", status: "declared", note },
    verification: { state: "pending", contentsVerifiedAt: new Date().toISOString() },
    valuation: { verdictNotes: [note, "Cartucho sin originalidad declarada"] },
  });
  const { html } = await renderPage({ items: [search({ results: [checked] })] });

  assert.match(html, /radar-contents is-verified/);
  assert.doesNotMatch(html, /<li>CIB declarado por el vendedor/);
  assert.match(html, /<li>Cartucho sin originalidad declarada/);
});
