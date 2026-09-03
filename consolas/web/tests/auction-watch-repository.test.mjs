import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const repositorySource = await readFile(
  new URL("../auction-watch-repository.js", import.meta.url),
  "utf8"
);

function response(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() {
      return JSON.parse(JSON.stringify(payload));
    }
  };
}

function createRepository({
  snapshot,
  runtimeSnapshot,
  staticSnapshot,
  initialDismissals = [],
  initialFollowing = [],
  failWrites = false,
  failServerSnapshot = false,
  hangServerSnapshot = false,
  requestTimeoutMs = 8000
} = {}) {
  let dismissalItems = [...initialDismissals];
  let followingItems = [...initialFollowing];
  const stored = new Map();
  const requests = [];
  const localStorage = {
    getItem(key) {
      return stored.get(key) || null;
    },
    setItem(key, value) {
      stored.set(key, String(value));
    }
  };

  const fetch = async (url, options = {}) => {
    const target = String(url);
    const method = String(options.method || "GET").toUpperCase();
    requests.push({ target, method, body: options.body || "", headers: options.headers || {} });

    if (target.includes("/auction-watch/dismissals")) {
      if (method === "POST") {
        if (failWrites) throw new Error("server unavailable");
        const item = JSON.parse(options.body);
        dismissalItems = [
          item,
          ...dismissalItems.filter(
            (entry) => !(entry.sourceId === item.sourceId && entry.lotId === item.lotId)
          )
        ];
        return response({ ok: true, item }, { status: 201 });
      }
      if (method === "DELETE") {
        if (failWrites) throw new Error("server unavailable");
        const query = new URL(target, "http://local.test").searchParams;
        dismissalItems = dismissalItems.filter(
          (entry) => !(entry.sourceId === query.get("sourceId") && entry.lotId === query.get("lotId"))
        );
        return response({ ok: true, removed: true });
      }
      return response({ version: 1, items: dismissalItems });
    }

    if (target.includes("/auction-watch/following")) {
      if (method === "POST") {
        if (failWrites) throw new Error("server unavailable");
        const item = JSON.parse(options.body);
        followingItems = [
          item,
          ...followingItems.filter(
            (entry) => !(entry.sourceId === item.sourceId && entry.lotId === item.lotId)
          )
        ];
        return response({ ok: true, item }, { status: 201 });
      }
      if (method === "DELETE") {
        if (failWrites) throw new Error("server unavailable");
        const query = new URL(target, "http://local.test").searchParams;
        followingItems = followingItems.filter(
          (entry) => !(entry.sourceId === query.get("sourceId") && entry.lotId === query.get("lotId"))
        );
        return response({ ok: true, removed: true });
      }
      return response({ version: 1, items: followingItems });
    }

    if (target.includes("/runtime/auction-watch.json")) {
      return runtimeSnapshot
        ? response(runtimeSnapshot)
        : response({}, { ok: false, status: 404 });
    }
    if (target.includes("/data/auction-watch.json")) {
      return staticSnapshot
        ? response(staticSnapshot)
        : response({}, { ok: false, status: 404 });
    }
    if (target.endsWith("/auction-watch")) {
      if (failServerSnapshot) throw new Error("server snapshot unavailable");
      if (hangServerSnapshot) return await new Promise(() => {});
      return response(snapshot || { generatedAt: null, matches: [], status: "unavailable" });
    }
    return response({}, { ok: false, status: 404 });
  };

  const window = {
    CONSOLAS_API_BASE: "./api",
    CONSOLAS_AUCTION_WATCH_TIMEOUT_MS: requestTimeoutMs,
    localStorage,
    setTimeout,
    clearTimeout,
    AbortController
  };
  const context = vm.createContext({
    window,
    fetch,
    console: { info() {}, error() {} },
    URL,
    URLSearchParams,
    Intl
  });
  vm.runInContext(repositorySource, context, { filename: "auction-watch-repository.js" });
  return { repo: window.AuctionWatchRepository, requests };
}

const activeSnapshot = {
  generatedAt: new Date().toISOString(),
  runId: "run-active",
  status: "success",
  sync: {
    runId: "run-active",
    snapshotHash: "hash-active",
    source: "export",
    status: "current"
  },
  featured: { id: "featured-1", source: "remotes", lotId: "1", title: "Soundic" },
  matches: [
    { id: "match-1", source: "remotes", lotId: "1", title: "Soundic" },
    { id: "match-2", source: "prado", lotId: "2", title: "Radofin" }
  ]
};

test("server dismissals hide matches and featured by source plus lot id", async () => {
  const { repo } = createRepository({
    snapshot: activeSnapshot,
    initialDismissals: [{ sourceId: "remotes", lotId: "1", title: "Soundic" }]
  });

  await repo.loadSnapshot();

  assert.equal(repo.getFeaturedOpportunity(), null);
  assert.equal(repo.getSnapshot().matches.length, 1);
  assert.equal(repo.getSnapshot().matches[0].title, "Radofin");
  assert.equal(repo.getDismissedOpportunities().length, 1);
  assert.equal(repo.getDismissedOpportunities()[0].title, "Soundic");
  assert.equal(repo.getOpportunityKey(null), "");
  assert.equal(repo.safePublicUrl("javascript:alert(1)"), "");
});

test("dismissed fallback keeps the server-stored opportunity image", async () => {
  const { repo } = createRepository({
    snapshot: {
      generatedAt: new Date().toISOString(),
      runId: "run-empty-dismissed",
      status: "success",
      matches: []
    },
    initialDismissals: [{
      sourceId: "remotes",
      lotId: "42",
      title: "Soundic",
      imageUrl: "https://images.example.test/soundic.jpg"
    }]
  });

  await repo.loadSnapshot();
  assert.equal(repo.getDismissedOpportunities()[0].imageUrl, "https://images.example.test/soundic.jpg");
});

test("partial run issues survive snapshot normalization", async () => {
  const { repo } = createRepository({
    snapshot: {
      generatedAt: new Date().toISOString(),
      runId: "run-partial",
      status: "partial_failure",
      issues: [
        {
          sourceId: "prado",
          sourceLabel: "Prado Subastas",
          status: "failed",
          summary: "El servidor cortó la conexión mientras se consultaban los lotes."
        }
      ],
      matches: []
    }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().issues.length, 1);
  assert.equal(repo.getSnapshot().scanStatus, "partial");
  assert.equal(repo.getSnapshot().status, "partial");
  assert.equal(repo.getSnapshot().issues[0].sourceId, "prado");
  assert.match(repo.getSnapshot().issues[0].summary, /cortó la conexión/);
  assert.equal(repo.getSyncState().status, "degraded");
  assert.equal(repo.getSyncState().scanStatus, "partial");
});

test("accepts canonical scanStatus without requiring the legacy status field", async () => {
  const { repo } = createRepository({
    snapshot: {
      generatedAt: new Date().toISOString(),
      runId: "run-canonical-partial",
      scanStatus: "partial",
      matches: []
    }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().scanStatus, "partial");
  assert.equal(repo.getSnapshot().status, "partial");
  assert.equal(repo.getSyncState().scanStatus, "partial");
  assert.equal(repo.getSyncState().status, "degraded");
});

test("normalizes the legacy failure status to canonical failed", async () => {
  const { repo } = createRepository({
    snapshot: {
      generatedAt: new Date().toISOString(),
      runId: "run-legacy-failed",
      status: "failure",
      matches: []
    }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().scanStatus, "failed");
  assert.equal(repo.getSnapshot().status, "failed");
  assert.equal(repo.getSyncState().scanStatus, "failed");
  assert.equal(repo.getSyncState().status, "degraded");
});

test("dismiss and restore update the server-backed visible snapshot", async () => {
  const { repo, requests } = createRepository({ snapshot: activeSnapshot });
  await repo.loadSnapshot();
  const item = repo.getSnapshot().matches.find((entry) => entry.title === "Radofin");

  await repo.dismissOpportunity(item, { requireServer: true });
  assert.equal(repo.isOpportunityDismissed(item), true);
  assert.equal(repo.getSnapshot().matches.some((entry) => entry.title === "Radofin"), false);
  assert.equal(repo.getDismissalSource(), "server");

  await repo.restoreOpportunity(item, { requireServer: true });
  assert.equal(repo.isOpportunityDismissed(item), false);
  assert.equal(repo.getSnapshot().matches.some((entry) => entry.title === "Radofin"), true);
  assert.equal(requests.some((entry) => entry.method === "POST"), true);
  assert.equal(requests.some((entry) => entry.method === "DELETE"), true);
  assert.equal(
    requests.find((entry) => entry.method === "POST").headers["X-Consolas-Auction-Watch"],
    "1"
  );
});

test("strict mail action does not fall back to browser-only state", async () => {
  const { repo } = createRepository({ snapshot: activeSnapshot, failWrites: true });
  repo.setSnapshot(activeSnapshot);
  const item = repo.getSnapshot().matches.find((entry) => entry.title === "Radofin");

  await assert.rejects(
    repo.dismissOpportunity(item, { requireServer: true }),
    /server unavailable/
  );

  assert.equal(repo.isOpportunityDismissed(item), false);
  assert.equal(repo.getSnapshot().matches.some((entry) => entry.title === "Radofin"), true);
});


test("follow and unfollow persist independently from dismissed opportunities", async () => {
  const { repo } = createRepository({ snapshot: activeSnapshot });
  await repo.loadSnapshot();
  const item = repo.getSnapshot().matches.find((entry) => entry.title === "Radofin");

  await repo.followOpportunity(item, { requireServer: true });
  assert.equal(repo.isOpportunityFollowed(item), true);
  assert.equal(repo.getFollowingOpportunities().length, 1);
  assert.equal(repo.getSnapshot().matches[0].title, "Radofin");

  await repo.unfollowOpportunity(item, { requireServer: true });
  assert.equal(repo.isOpportunityFollowed(item), false);
  assert.equal(repo.getFollowingOpportunities().length, 0);
});

test("loads server and static candidates and keeps a current newer server snapshot", async () => {
  const staticSnapshot = {
    generatedAt: new Date(Date.now() - 60_000).toISOString(),
    runId: "run-static-old",
    status: "success",
    matches: [{ source: "prado", lotId: "old", title: "Respaldo anterior" }]
  };
  const { repo, requests } = createRepository({ snapshot: activeSnapshot, staticSnapshot });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().runId, "run-active");
  assert.equal(repo.getSnapshotSource(), "server");
  assert.equal(repo.getSyncState().status, "ready");
  assert.equal(repo.getSyncState().source, "export");
  assert.equal(repo.getSyncState().divergence, true);
  assert.equal(requests.some((entry) => entry.target.includes("/data/auction-watch.json")), true);
});

test("keeps a valid server snapshot authoritative when a fresher static snapshot diverges", async () => {
  const serverSnapshot = {
    generatedAt: new Date(Date.now() - 120_000).toISOString(),
    runId: "run-server-old",
    status: "success",
    sync: { status: "current", source: "export", runId: "run-server-old" },
    matches: []
  };
  const staticSnapshot = {
    generatedAt: new Date().toISOString(),
    runId: "run-static-new",
    status: "success",
    matches: [{ source: "remotes", lotId: "new", title: "Snapshot local nuevo" }]
  };
  const { repo } = createRepository({ snapshot: serverSnapshot, staticSnapshot });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().runId, "run-server-old");
  assert.equal(repo.getSnapshotSource(), "server");
  assert.equal(repo.getSyncState().status, "degraded");
  assert.equal(repo.getSyncState().source, "export");
  assert.equal(repo.getSyncState().divergence, true);
});

test("never presents a 2xx unavailable server response as a healthy empty snapshot", async () => {
  const { repo } = createRepository({
    snapshot: { generatedAt: null, runId: "", status: "unavailable", matches: [] }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSyncState().status, "unavailable");
  assert.equal(repo.getSnapshotSource(), "none");
  assert.equal(repo.getSnapshot().matches.length, 0);
  assert.equal(repo.getSyncState().attempts[0].reason, "unavailable");
});

test("maps a current successful zero-match server snapshot to a verified empty state", async () => {
  const generatedAt = new Date().toISOString();
  const { repo } = createRepository({
    snapshot: {
      generatedAt,
      runId: "run-empty",
      status: "success",
      sync: {
        generatedAt,
        runId: "run-empty",
        snapshotHash: "hash-empty",
        source: "export",
        status: "current"
      },
      matches: []
    }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSyncState().status, "empty");
  assert.equal(repo.getSnapshotSource(), "server");
});

test("maps an explicitly stale backend snapshot to stale even when it has matches", async () => {
  const generatedAt = new Date(Date.now() - 60_000).toISOString();
  const { repo } = createRepository({
    snapshot: {
      generatedAt,
      runId: "run-stale",
      status: "success",
      sync: {
        generatedAt,
        runId: "run-stale",
        snapshotHash: "hash-stale",
        status: "stale",
        source: "latest"
      },
      matches: [{ source: "remotes", lotId: "stale", title: "Dato anterior" }]
    }
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().matches.length, 1);
  assert.equal(repo.getSyncState().status, "stale");
  assert.equal(repo.getSyncState().stale, true);
  assert.equal(repo.getSyncState().source, "latest");
});

test("rejects a server receipt whose run id does not match the snapshot", async () => {
  const generatedAt = new Date().toISOString();
  const runtimeSnapshot = {
    generatedAt,
    runId: "run-runtime-valid",
    status: "success",
    matches: []
  };
  const { repo } = createRepository({
    snapshot: {
      generatedAt,
      runId: "run-payload",
      status: "success",
      sync: { generatedAt, runId: "run-receipt", status: "current", source: "export" },
      matches: []
    },
    runtimeSnapshot
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().runId, "run-runtime-valid");
  assert.equal(repo.getSyncState().status, "degraded");
  assert.equal(repo.getSyncState().attempts[0].reason, "run_id_mismatch");
});

test("falls back to static after a server request error and records the failed attempt", async () => {
  const staticSnapshot = {
    generatedAt: new Date().toISOString(),
    runId: "run-static-only",
    status: "success",
    matches: []
  };
  const { repo } = createRepository({ staticSnapshot, failServerSnapshot: true });

  await repo.loadSnapshot();

  assert.equal(repo.getSyncState().status, "degraded");
  assert.equal(repo.getSnapshotSource(), "static");
  assert.equal(repo.getSyncState().attempts[0].ok, false);
  assert.equal(repo.getSyncState().attempts[1].ok, false);
  assert.equal(repo.getSyncState().attempts[2].ok, true);
});

test("prefers the local runtime snapshot over tracked static when the server is unavailable", async () => {
  const runtimeSnapshot = {
    generatedAt: new Date(Date.now() - 60_000).toISOString(),
    runId: "run-runtime",
    status: "success",
    matches: [{ source: "remotes", lotId: "runtime", title: "Runtime local" }]
  };
  const staticSnapshot = {
    generatedAt: new Date().toISOString(),
    runId: "run-static",
    status: "success",
    matches: [{ source: "prado", lotId: "static", title: "Archivo trackeado" }]
  };
  const { repo } = createRepository({
    snapshot: { generatedAt: null, runId: "", status: "unavailable", matches: [] },
    runtimeSnapshot,
    staticSnapshot
  });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().runId, "run-runtime");
  assert.equal(repo.getSnapshotSource(), "runtime");
  assert.equal(repo.getSyncState().status, "degraded");
  assert.equal(repo.getSyncState().source, "runtime");
});

test("times out a hanging server candidate instead of blocking the static fallback", async () => {
  const staticSnapshot = {
    generatedAt: new Date().toISOString(),
    runId: "run-after-timeout",
    status: "success",
    matches: []
  };
  const { repo } = createRepository({ staticSnapshot, hangServerSnapshot: true, requestTimeoutMs: 250 });

  await repo.loadSnapshot();

  assert.equal(repo.getSnapshot().runId, "run-after-timeout");
  assert.equal(repo.getSyncState().attempts[0].reason, "timeout");
  assert.equal(repo.getSyncState().status, "degraded");
});
