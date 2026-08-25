import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const pageSource = await readFile(new URL("../opportunities.js", import.meta.url), "utf8");

async function renderOpportunitiesPage({
  active = [],
  following = [],
  dismissed = [],
  runRequests = [],
  syncStates = [],
  scanStatus = "success",
  issues = []
} = {}) {
  let html = "";
  let nextTimerId = 1;
  let runRequestIndex = 0;
  let snapshotLoadIndex = 0;
  const scheduledTimers = [];
  const root = {
    set innerHTML(value) {
      html = String(value);
    },
    get innerHTML() {
      return html;
    },
    addEventListener() {},
    querySelector() {
      return null;
    }
  };
  const snapshot = {
    generatedAt: new Date().toISOString(),
    generatedAtLabel: "22 ago, 21:10",
    runId: "run-current",
    status: scanStatus,
    scanStatus,
    issues,
    featured: null,
    matches: active
  };
  let currentSync = syncStates[0] || {
    status: active.length ? "ready" : "empty",
    source: "export",
    origin: "server",
    generatedAt: snapshot.generatedAt,
    runId: snapshot.runId,
    snapshotHash: "hash-current",
    divergence: false
  };
  const opportunityKey = (item = {}) => {
    const candidate = item || {};
    const source = String(candidate.source || candidate.sourceId || "").toLowerCase();
    return source && candidate.lotId ? `${source}:${candidate.lotId}` : "";
  };
  const repository = {
    async loadSnapshot() {
      currentSync = syncStates[Math.min(snapshotLoadIndex, syncStates.length - 1)] || currentSync;
      snapshotLoadIndex += 1;
      return snapshot;
    },
    getSnapshot() {
      return snapshot;
    },
    getFeaturedOpportunity() {
      return null;
    },
    getDismissedOpportunities() {
      return dismissed;
    },
    getFollowingOpportunities() {
      return following;
    },
    getSnapshotSource() {
      return currentSync.origin || "none";
    },
    getSyncState() {
      return currentSync;
    },
    getOpportunityKey: opportunityKey,
    isOpportunityFollowed(item) {
      return following.some((entry) => opportunityKey(entry) === opportunityKey(item));
    },
    getSourceLabel() {
      return "Remotes";
    },
    getPrimaryUrl(item) {
      return item.lotUrl || "";
    },
    getSecondaryUrl() {
      return "";
    },
    getPrimaryCtaLabel() {
      return "Ver publicación";
    },
    getSecondaryCtaLabel() {
      return "";
    },
    normalizeText(value) {
      return String(value || "").toLowerCase();
    },
    normalizeScanStatus(value) {
      const status = String(value || "").toLowerCase();
      return { partial_failure: "partial", failure: "failed" }[status] || status;
    }
  };
  const window = {
    AuctionWatchRepository: repository,
    CollectionRepository: {
      getHomeHref() {
        return "./index.html";
      }
    },
    requestAnimationFrame(callback) {
      callback();
    },
    setTimeout(callback) {
      const id = nextTimerId;
      nextTimerId += 1;
      scheduledTimers.push({ id, callback });
      return id;
    },
    clearTimeout(id) {
      const index = scheduledTimers.findIndex((timer) => timer.id === id);
      if (index >= 0) scheduledTimers.splice(index, 1);
    }
  };
  const document = {
    getElementById(id) {
      return id === "opportunitiesRoot" ? root : null;
    }
  };
  const fetch = async () => {
    const configured = runRequests.length
      ? runRequests[Math.min(runRequestIndex, runRequests.length - 1)]
      : null;
    runRequestIndex += 1;
    if (configured instanceof Error) throw configured;
    if (configured?.httpStatus) {
      return {
        ok: false,
        status: configured.httpStatus,
        async json() {
          return {};
        }
      };
    }
    const request = configured?.request || configured;
    return {
      ok: true,
      status: 200,
      async json() {
        return { ok: true, request };
      }
    };
  };
  const context = vm.createContext({
    window,
    document,
    fetch,
    console: { error() {}, info() {} },
    setTimeout,
    clearTimeout
  });

  vm.runInContext(pageSource, context, { filename: "opportunities.js" });
  const flush = async () => {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
  };
  await flush();
  return {
    get html() {
      return html;
    },
    async runNextPoll() {
      const timer = scheduledTimers.shift();
      if (!timer) return false;
      timer.callback();
      await flush();
      return true;
    }
  };
}

test("opens following automatically when there are no active opportunities", async () => {
  const page = await renderOpportunitiesPage({
    following: [
      {
        id: "following-remotes-1",
        source: "remotes",
        sourceId: "remotes",
        lotId: "7691:12",
        title: "Super Nintendo Mini",
        lotUrl: "https://www.remotes.com.uy/participar/remate/7691?lote=12",
        following: true
      }
    ]
  });

  assert.match(page.html, /Publicaciones que seguís/);
  assert.match(page.html, /Ver publicación/);
  assert.match(page.html, /Dejar de seguir/);
  assert.match(page.html, /Descartar/);
  assert.match(page.html, /data-opportunity-view="following" aria-pressed="true"/);
  assert.doesNotMatch(page.html, /Sin oportunidades activas/);
});

test("keeps active as the initial view when a new opportunity exists", async () => {
  const page = await renderOpportunitiesPage({
    active: [
      {
        id: "active-remotes-1",
        source: "remotes",
        lotId: "7653:50",
        title: "Consola portátil",
        lotUrl: "https://www.remotes.com.uy/participar/remate/7653?lote=50"
      }
    ],
    following: [
      {
        id: "following-remotes-1",
        source: "remotes",
        lotId: "7691:12",
        title: "Super Nintendo Mini",
        lotUrl: "https://www.remotes.com.uy/participar/remate/7691?lote=12",
        following: true
      }
    ]
  });

  assert.match(page.html, /Publicaciones activas/);
  assert.match(page.html, /Consola portátil/);
  assert.match(page.html, /data-opportunity-view="active" aria-pressed="true"/);
});

test("unlocks a reloaded page when a pending request expires", async () => {
  const page = await renderOpportunitiesPage({
    runRequests: [
      { id: "run_stale", status: "pending", detail: "" },
      {
        id: "run_stale",
        status: "failed",
        detail: "La solicitud venció porque el buscador no estaba disponible."
      }
    ]
  });

  assert.match(page.html, /Solicitud en cola…/);
  assert.match(page.html, /data-auction-run-now disabled/);
  assert.equal(await page.runNextPoll(), true);
  assert.match(page.html, /Reintentar búsqueda/);
  assert.match(page.html, /buscador no estaba disponible/);
  assert.doesNotMatch(page.html, /data-auction-run-now disabled/);
});

test("shows unavailable as a synchronization error instead of a healthy empty state", async () => {
  const page = await renderOpportunitiesPage({
    syncStates: [{
      status: "unavailable",
      source: "none",
      origin: "none",
      runId: "",
      snapshotHash: ""
    }]
  });

  assert.match(page.html, /Sin conexión de datos/);
  assert.match(page.html, /No pudimos cargar las oportunidades/);
  assert.match(page.html, /No asumimos que la lista esté vacía/);
  assert.doesNotMatch(page.html, /no dejó publicaciones abiertas/);
});

test("keeps stale cards visible as reference but disables persistent actions", async () => {
  const page = await renderOpportunitiesPage({
    active: [{
      id: "stale-remotes-1",
      source: "remotes",
      lotId: "stale:1",
      title: "Consola en snapshot anterior",
      lotUrl: "https://example.test/stale"
    }],
    syncStates: [{
      status: "stale",
      source: "latest",
      origin: "server",
      runId: "run-stale",
      snapshotHash: "hash-stale",
      stale: true
    }]
  });

  assert.match(page.html, /Desactualizado/);
  assert.match(page.html, /Consola en snapshot anterior/);
  assert.match(page.html, /La app necesita un snapshot vigente/);
  assert.doesNotMatch(page.html, /data-opportunity-action="dismiss"/);
});

test("marks a completed manual run as pending while snapshot identity does not match", async () => {
  const staleSync = {
    status: "ready",
    source: "export",
    origin: "server",
    generatedAt: new Date().toISOString(),
    runId: "run-old",
    snapshotHash: "hash-old"
  };
  const page = await renderOpportunitiesPage({
    syncStates: [staleSync, staleSync],
    runRequests: [{
      id: "request-new",
      status: "completed",
      finishedAt: new Date().toISOString(),
      runId: "run-new",
      snapshotHash: "hash-new",
      snapshotStatus: "published",
      emailStatus: "sent"
    }]
  });

  assert.match(page.html, /página todavía no confirmó el snapshot nuevo/);
  assert.match(page.html, /Sincronizando página…/);
  assert.match(page.html, /data-auction-run-now disabled/);
});

test("reports a terminal snapshot publish failure without claiming the page updated", async () => {
  const page = await renderOpportunitiesPage({
    runRequests: [{
      id: "request-failed-publish",
      status: "completed",
      finishedAt: new Date().toISOString(),
      runId: "run-not-published",
      snapshotStatus: "not_configured",
      emailStatus: "sent"
    }]
  });

  assert.match(page.html, /no pudo publicar el snapshot/);
  assert.match(page.html, /La página conserva la versión anterior/);
  assert.match(page.html, /Reintentar búsqueda/);
  assert.doesNotMatch(page.html, /data-auction-run-now disabled/);
  assert.doesNotMatch(page.html, /página confirmada/);
});

test("refreshes a published snapshot even when the manual scan finished failed", async () => {
  const page = await renderOpportunitiesPage({
    syncStates: [
      {
        status: "ready",
        source: "export",
        origin: "server",
        generatedAt: new Date().toISOString(),
        runId: "run-old",
        snapshotHash: "hash-old"
      },
      {
        status: "ready",
        source: "export",
        origin: "server",
        generatedAt: new Date().toISOString(),
        runId: "run-current",
        snapshotHash: "hash-current"
      }
    ],
    runRequests: [{
      id: "request-failed-but-published",
      status: "failed",
      finishedAt: new Date().toISOString(),
      runId: "run-current",
      snapshotHash: "hash-current",
      snapshotStatus: "published",
      emailStatus: "sent",
      overallStatus: "failed"
    }]
  });

  assert.match(page.html, /La corrida no terminó correctamente/);
  assert.doesNotMatch(page.html, /Sincronizando página/);
  assert.equal(await page.runNextPoll(), false);
});

test("keeps polling after a transient HTTP error and later unlocks", async () => {
  const page = await renderOpportunitiesPage({
    runRequests: [
      { id: "run-retry", status: "pending", detail: "" },
      { httpStatus: 503 },
      { id: "run-retry", status: "failed", detail: "El proceso remoto falló." }
    ]
  });

  assert.match(page.html, /Solicitud en cola…/);
  assert.equal(await page.runNextPoll(), true);
  assert.match(page.html, /vamos a reintentar/);
  assert.equal(await page.runNextPoll(), true);
  assert.match(page.html, /Reintentar búsqueda/);
  assert.match(page.html, /proceso remoto falló/);
  assert.doesNotMatch(page.html, /data-auction-run-now disabled/);
});

test("keeps a delivery-pending result occupied until the same request completes", async () => {
  const finishedAt = new Date().toISOString();
  const page = await renderOpportunitiesPage({
    runRequests: [
      {
        id: "request-delivery",
        status: "failed",
        finishedAt,
        runId: "run-current",
        snapshotHash: "hash-current",
        scanStatus: "success",
        snapshotStatus: "published",
        emailStatus: "failed",
        overallStatus: "delivery_pending"
      },
      {
        id: "request-delivery",
        status: "completed",
        finishedAt,
        runId: "run-current",
        snapshotHash: "hash-current",
        scanStatus: "success",
        snapshotStatus: "published",
        emailStatus: "sent",
        overallStatus: "completed"
      }
    ]
  });

  assert.match(page.html, /Finalizando entrega…/);
  assert.match(page.html, /sin repetir la búsqueda/);
  assert.match(page.html, /data-auction-run-now disabled/);
  assert.doesNotMatch(page.html, /Reintentar búsqueda/);

  assert.equal(await page.runNextPoll(), true);
  assert.match(page.html, /Corrida terminada y página confirmada\. Mail enviado\./);
  assert.match(page.html, /Buscar otra vez/);
  assert.doesNotMatch(page.html, /data-auction-run-now disabled/);
});

test("keeps polling when delivery_pending is the backend lifecycle status", async () => {
  const page = await renderOpportunitiesPage({
    runRequests: [{
      id: "request-delivery-lifecycle",
      status: "delivery_pending",
      runId: "run-current",
      snapshotHash: "hash-current",
      scanStatus: "success",
      snapshotStatus: "published",
      emailStatus: "failed"
    }]
  });

  assert.match(page.html, /Finalizando entrega…/);
  assert.match(page.html, /data-auction-run-now disabled/);
  assert.doesNotMatch(page.html, /Reintentar búsqueda/);
  assert.equal(await page.runNextPoll(), true);
  assert.match(page.html, /data-auction-run-now disabled/);
});

test("shows an uncertain email as terminal ambiguity without retrying automatically", async () => {
  const page = await renderOpportunitiesPage({
    runRequests: [{
      id: "request-email-uncertain",
      status: "failed",
      finishedAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      runId: "run-current",
      snapshotHash: "hash-current",
      scanStatus: "success",
      snapshotStatus: "published",
      emailStatus: "uncertain",
      overallStatus: "failed"
    }]
  });

  assert.match(page.html, /proveedor no confirmó si el mail salió/);
  assert.match(page.html, /No lo reenviamos automáticamente para evitar duplicados/);
  assert.match(page.html, /Buscar otra vez/);
  assert.doesNotMatch(page.html, /Mail enviado/);
  assert.doesNotMatch(page.html, /Reintentar búsqueda/);
  assert.equal(await page.runNextPoll(), false);
});

test("renders the canonical partial scan status without depending on legacy values", async () => {
  const page = await renderOpportunitiesPage({
    scanStatus: "partial",
    issues: []
  });

  assert.match(page.html, /La última corrida quedó parcial/);
  assert.match(page.html, /No todas las fuentes pudieron completar la consulta/);
});

test("keeps a failed canonical scan visibly degraded instead of calling it current", async () => {
  const page = await renderOpportunitiesPage({
    scanStatus: "failed",
    syncStates: [{
      status: "degraded",
      source: "export",
      origin: "server",
      scanStatus: "failed",
      runId: "run-current",
      snapshotHash: "hash-current"
    }]
  });

  assert.match(page.html, /La última corrida falló/);
  assert.match(page.html, /sin presentarlo como una actualización completa/);
  assert.match(page.html, /En snapshot/);
  assert.match(page.html, /La corrida fallida no confirmó oportunidades/);
  assert.doesNotMatch(page.html, /Activas ahora/);
});
