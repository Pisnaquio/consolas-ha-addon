import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const actionSource = await readFile(new URL("../auction-watch-action.js", import.meta.url), "utf8");

async function renderActionPage({ sync, active = [] } = {}) {
  let html = "";
  const listeners = new Map();
  const root = {
    set innerHTML(value) {
      html = String(value);
    },
    get innerHTML() {
      return html;
    }
  };
  const opportunityKey = (item = {}) => {
    const source = String(item.source || item.sourceId || "").trim().toLowerCase();
    const lotId = String(item.lotId || "").trim();
    return source && lotId ? `${source}:${lotId}` : "";
  };
  const repository = {
    async loadSnapshot() {},
    getSyncState() {
      return sync;
    },
    getSnapshotSource() {
      return sync?.origin || "none";
    },
    getSnapshot() {
      return { matches: active };
    },
    getFeaturedOpportunity() {
      return null;
    },
    getDismissedOpportunities() {
      return [];
    },
    getOpportunityKey: opportunityKey,
    safePublicUrl(value) {
      return String(value || "");
    },
    getSourceLabel() {
      return "Remotes";
    }
  };
  const document = {
    title: "Descartar oportunidad",
    getElementById(id) {
      if (id === "auctionWatchActionRoot") return root;
      return {
        addEventListener(type, listener) {
          listeners.set(`${id}:${type}`, listener);
        }
      };
    },
    querySelector() {
      return null;
    }
  };
  const window = {
    location: {
      search: "?source=remotes&lot=7687%3A92&title=Switch%20HDMI"
    },
    history: {
      state: {},
      replaceState(nextState) {
        this.state = nextState;
      }
    },
    AuctionWatchRepository: repository,
    requestAnimationFrame(callback) {
      callback();
    }
  };
  const context = vm.createContext({
    window,
    document,
    console: { error() {} },
    URL,
    URLSearchParams
  });

  vm.runInContext(actionSource, context, { filename: "auction-watch-action.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return html;
}

test("mail action blocks a stale snapshot instead of reporting the lot as gone", async () => {
  const html = await renderActionPage({
    sync: { status: "stale", source: "latest", origin: "server" },
    active: [{ source: "remotes", lotId: "7687:92", title: "Switch HDMI" }]
  });

  assert.match(html, /Snapshot desactualizado/);
  assert.match(html, /No vamos a asumir que este lote terminó/);
  assert.doesNotMatch(html, /Descartar oportunidad/);
  assert.doesNotMatch(html, /ya no está entre las oportunidades/);
});

test("mail action keeps runtime fallback read only", async () => {
  const html = await renderActionPage({
    sync: { status: "degraded", source: "runtime", origin: "runtime" },
    active: [{ source: "remotes", lotId: "7687:92", title: "Switch HDMI" }]
  });

  assert.match(html, /Sincronización pendiente/);
  assert.match(html, /requieren el snapshot confirmado por el servidor/);
  assert.doesNotMatch(html, /Confirmar descarte/);
});

test("mail action allows confirmation for a current server snapshot", async () => {
  const html = await renderActionPage({
    sync: { status: "ready", source: "export", origin: "server" },
    active: [{
      source: "remotes",
      lotId: "7687:92",
      title: "Switch HDMI",
      lotUrl: "https://example.test/lot"
    }]
  });

  assert.match(html, /Descartar oportunidad/);
  assert.match(html, /Confirmar descarte/);
});
