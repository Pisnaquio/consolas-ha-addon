(() => {
  const REMOTE_API_BASE = window.CONSOLAS_API_BASE || "./api";
  const STATIC_SNAPSHOT_CACHE_KEY = "20260827a";
  const REQUEST_TIMEOUT_MS = Math.max(250, Number(window.CONSOLAS_AUCTION_WATCH_TIMEOUT_MS) || 8000);
  const LEGACY_STALE_AFTER_SECONDS = Math.max(
    60,
    Number(window.CONSOLAS_AUCTION_WATCH_STALE_AFTER_SECONDS) || 36 * 60 * 60
  );
  const CANONICAL_SCAN_STATUSES = new Set(["success", "partial", "failed"]);
  const LEGACY_SCAN_STATUS_ALIASES = {
    partial_failure: "partial",
    failure: "failed"
  };
  const DISMISSALS_STORAGE_KEY = "consolas.auctionWatchDismissals.v1";
  const FOLLOWING_STORAGE_KEY = "consolas.auctionWatchFollowing.v1";

  const SOURCE_LABELS = {
    castells: "Castells",
    bavastro: "Bavastro",
    remotes: "Remotes",
    todoremates: "TodoRemates",
    prado: "Prado Subastas"
  };

  const WATCHLIST_CONSOLE_OVERRIDES = {
    "castells-atari-cx2600-559500": ["atari"]
  };

  const CONSOLE_PATTERNS = {
    atari: [/\batari\b/, /\batari 2600\b/, /\bcx 2600\b/, /\bcx-2600\b/],
    ps1: [/\bplaystation 1\b/, /\bps1\b/, /\bps one\b/, /\bsony playstation\b/],
    ps4: [/\bplaystation 4\b/, /\bps4\b/, /\bdualshock 4\b/, /\bdual shock 4\b/, /\bps vr\b/, /\bplaystation vr\b/],
    ps5: [/\bplaystation 5\b/, /\bps5\b/, /\bdualsense\b/, /\bps vr2\b/],
    wii: [/\bnintendo wii\b/, /\bwii sports\b/, /\bwii remote\b/, /\bnunchuk\b/, /\bsensor bar\b/, /\bwii\b/],
    gamecube: [/\bgamecube\b/, /\bgame cube\b/, /\bgameboy player\b/],
    switch2: [/\bnintendo switch 2\b/, /\bswitch 2\b/],
    switch: [/\bnintendo switch\b(?!\s*2\b)/, /\bswitch oled\b/, /\bswitch lite\b/],
    "gba-sp": [/\bgame boy advance sp\b/, /\bgba sp\b/],
    "nes-clonica": [/\bnes clon/i, /\bfamily game\b/, /\bpolystation\b/],
    genesis: [/\bsega genesis\b/, /\bmega drive\b/, /\bgenesis\b/],
    dreamcast: [/\bdreamcast\b/],
    "ds-lite": [/\bnintendo ds\b/, /\bds lite\b/, /\bnds\b/],
    "gb-color": [/\bgame boy color\b/, /\bgb color\b/],
    "gb-original": [/\bgame boy original\b/, /\bgame boy\b(?!\s*(color|advance|sp)\b)/, /\bgameboy\b(?!\s*(color|advance)\b)/],
    snes: [/\bsuper nintendo\b/, /\bsnes\b/],
    "xbox-360-e": [/\bxbox 360 e\b/, /\bxbox 360\b/],
    n64: [/\bnintendo 64\b/, /\bn64\b/]
  };

  function normalizeText(text = "") {
    return String(text || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function stableArray(values = []) {
    return [...new Set((values || []).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }

  function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function getUrgencyRank(label = "") {
    const normalized = normalizeText(label);
    if (normalized.includes("inminente")) return 0;
    if (normalized.includes("hoy")) return 1;
    if (normalized.includes("pronto")) return 2;
    return 3;
  }

  function getSourceLabel(source = "") {
    const sourceId = String(source || "").trim().toLowerCase();
    if (SOURCE_LABELS[sourceId]) return SOURCE_LABELS[sourceId];
    if (!sourceId) return "Auction Watch";
    return sourceId
      .replace(/[-_]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function getOpportunityKey(item = {}) {
    const candidate = item && typeof item === "object" ? item : {};
    const sourceId = String(candidate.source || candidate.sourceId || "").trim().toLowerCase();
    const lotId = String(candidate.lotId || "").trim();
    return sourceId && lotId ? `${sourceId}:${lotId}` : "";
  }

  function safePublicUrl(value = "") {
    try {
      const parsed = new URL(String(value || "").trim());
      return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
    } catch (_error) {
      return "";
    }
  }

  function parseTimestamp(value = "") {
    const parsed = Date.parse(String(value || ""));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function snapshotAgeSeconds(generatedAt = "") {
    const timestamp = parseTimestamp(generatedAt);
    if (timestamp === null) return null;
    return Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  }

  function normalizeScanStatus(value = "") {
    const status = String(value || "").trim().toLowerCase();
    return LEGACY_SCAN_STATUS_ALIASES[status] || status;
  }

  function getSnapshotScanStatus(raw = {}) {
    return normalizeScanStatus(raw.scanStatus || raw.status);
  }

  function createSyncState(overrides = {}) {
    return {
      status: "unavailable",
      source: "none",
      origin: "none",
      generatedAt: "",
      acceptedAt: "",
      runId: "",
      snapshotHash: "",
      scanStatus: "",
      ageSeconds: null,
      stale: false,
      degraded: false,
      divergence: false,
      divergenceReason: "",
      attempts: [],
      ...overrides
    };
  }

  function getTimeoutFunctions() {
    const schedule = typeof window.setTimeout === "function"
      ? window.setTimeout.bind(window)
      : typeof setTimeout === "function"
        ? setTimeout
        : null;
    const cancel = typeof window.clearTimeout === "function"
      ? window.clearTimeout.bind(window)
      : typeof clearTimeout === "function"
        ? clearTimeout
        : null;
    return { schedule, cancel };
  }

  async function fetchWithTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
    const AbortControllerClass = window.AbortController || (
      typeof AbortController === "function" ? AbortController : null
    );
    const controller = AbortControllerClass ? new AbortControllerClass() : null;
    const { schedule, cancel } = getTimeoutFunctions();
    let timer = null;

    const request = fetch(url, {
      ...options,
      ...(controller ? { signal: controller.signal } : {})
    });

    if (!schedule) return await request;

    const timeout = new Promise((_, reject) => {
      timer = schedule(() => {
        controller?.abort();
        reject(new Error(`auction-watch timeout after ${timeoutMs}ms`));
      }, timeoutMs);
    });

    try {
      return await Promise.race([request, timeout]);
    } finally {
      if (timer !== null && cancel) cancel(timer);
    }
  }

  function validateSnapshotPayload(raw, kind = "server") {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return { ok: false, kind, reason: "invalid_shape", detail: "La respuesta no es un objeto." };
    }
    if (!Array.isArray(raw.matches)) {
      return { ok: false, kind, reason: "invalid_matches", detail: "Falta la lista de oportunidades." };
    }
    if (raw.matches.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
      return { ok: false, kind, reason: "invalid_match_item", detail: "Una oportunidad tiene un formato inválido." };
    }
    if (raw.featured !== undefined && raw.featured !== null && (
      typeof raw.featured !== "object" || Array.isArray(raw.featured)
    )) {
      return { ok: false, kind, reason: "invalid_featured", detail: "El destacado tiene un formato inválido." };
    }

    const scanStatus = getSnapshotScanStatus(raw);
    if (!CANONICAL_SCAN_STATUSES.has(scanStatus)) {
      const reportedStatus = String(raw.scanStatus || raw.status || "").trim().toLowerCase();
      return {
        ok: false,
        kind,
        reason: reportedStatus === "unavailable" ? "unavailable" : "invalid_scan_status",
        detail: reportedStatus
          ? `Estado de corrida no utilizable: ${reportedStatus}.`
          : "Falta el estado de la corrida."
      };
    }

    const generatedAt = String(raw.generatedAt || "").trim();
    const generatedAtMs = parseTimestamp(generatedAt);
    if (generatedAtMs === null) {
      return { ok: false, kind, reason: "invalid_generated_at", detail: "Falta una fecha de generación válida." };
    }

    const runId = String(raw.runId || raw.sync?.runId || "").trim();
    if (!runId) {
      return { ok: false, kind, reason: "invalid_run_id", detail: "Falta el identificador de corrida." };
    }

    const sync = raw.sync && typeof raw.sync === "object" ? raw.sync : {};
    const syncStatus = String(sync.status || "").trim().toLowerCase();
    if (syncStatus === "unavailable") {
      return { ok: false, kind, reason: "unavailable", detail: "El servidor todavía no tiene un snapshot publicado." };
    }
    if (syncStatus && !["current", "stale"].includes(syncStatus)) {
      return { ok: false, kind, reason: "invalid_sync_status", detail: `Estado de sincronización inválido: ${syncStatus}.` };
    }

    const syncRunId = String(sync.runId || "").trim();
    if (syncRunId && syncRunId !== runId) {
      return { ok: false, kind, reason: "run_id_mismatch", detail: "El snapshot y su recibo no pertenecen a la misma corrida." };
    }

    const syncGeneratedAt = String(sync.generatedAt || "").trim();
    if (syncGeneratedAt && parseTimestamp(syncGeneratedAt) !== generatedAtMs) {
      return { ok: false, kind, reason: "generated_at_mismatch", detail: "La fecha del snapshot no coincide con su recibo." };
    }

    const computedAgeSeconds = snapshotAgeSeconds(generatedAt);
    const hasReportedAge = sync.ageSeconds !== undefined && sync.ageSeconds !== null && sync.ageSeconds !== "";
    const reportedAgeSeconds = Number(sync.ageSeconds);
    const ageSeconds = hasReportedAge && Number.isFinite(reportedAgeSeconds) && reportedAgeSeconds >= 0
      ? reportedAgeSeconds
      : computedAgeSeconds;
    const stale = syncStatus === "stale" || (!syncStatus && Number(ageSeconds) > LEGACY_STALE_AFTER_SECONDS);

    return {
      ok: true,
      kind,
      payload: raw,
      status: scanStatus,
      scanStatus,
      generatedAt,
      generatedAtMs,
      runId,
      snapshotHash: String(sync.snapshotHash || raw.snapshotHash || "").trim(),
      acceptedAt: String(sync.acceptedAt || "").trim(),
      source: String(sync.source || (kind === "server" ? "server" : kind)).trim().toLowerCase(),
      ageSeconds,
      stale,
      partial: scanStatus === "partial" || (Array.isArray(raw.issues) && raw.issues.length > 0),
      failed: scanStatus === "failed"
    };
  }

  function candidateAttempt(candidate = {}) {
    return {
      source: candidate.kind || "unknown",
      ok: candidate.ok === true,
      reason: candidate.reason || "",
      detail: candidate.detail || "",
      generatedAt: candidate.generatedAt || "",
      runId: candidate.runId || "",
      snapshotHash: candidate.snapshotHash || "",
      scanStatus: candidate.scanStatus || ""
    };
  }

  function candidatesDiverge(serverCandidate, fallbackCandidate) {
    if (!serverCandidate?.ok || !fallbackCandidate?.ok) return { divergent: false, reason: "" };
    if (serverCandidate.snapshotHash && fallbackCandidate.snapshotHash) {
      return serverCandidate.snapshotHash === fallbackCandidate.snapshotHash
        ? { divergent: false, reason: "" }
        : { divergent: true, reason: "snapshot_hash" };
    }
    if (serverCandidate.runId !== fallbackCandidate.runId) {
      return { divergent: true, reason: "run_id" };
    }
    if (serverCandidate.generatedAtMs !== fallbackCandidate.generatedAtMs) {
      return { divergent: true, reason: "generated_at" };
    }
    return { divergent: false, reason: "" };
  }

  function chooseSnapshotCandidate(serverCandidate, runtimeCandidate, staticCandidate) {
    const valid = [serverCandidate, runtimeCandidate, staticCandidate].filter((candidate) => candidate?.ok);
    const attempts = [
      candidateAttempt(serverCandidate),
      candidateAttempt(runtimeCandidate),
      candidateAttempt(staticCandidate)
    ];
    if (!valid.length) {
      return {
        candidate: null,
        sync: createSyncState({ status: "unavailable", attempts })
      };
    }

    const fallbackCandidate = runtimeCandidate?.ok ? runtimeCandidate : staticCandidate;
    const candidate = serverCandidate?.ok ? serverCandidate : fallbackCandidate;

    const { divergent, reason } = candidatesDiverge(serverCandidate, fallbackCandidate);
    const selectedReadOnlyFallback = ["runtime", "static"].includes(candidate.kind);
    const fallbackIsNewer = Boolean(
      serverCandidate?.ok &&
      fallbackCandidate?.ok &&
      fallbackCandidate.generatedAtMs > serverCandidate.generatedAtMs
    );
    const hasItems = Boolean(candidate.payload.featured || candidate.payload.matches.length);
    const degraded = candidate.partial || candidate.failed || selectedReadOnlyFallback || (divergent && fallbackIsNewer);
    const status = candidate.stale
      ? "stale"
      : degraded
        ? "degraded"
        : hasItems
          ? "ready"
          : "empty";

    return {
      candidate,
      sync: createSyncState({
        status,
        source: candidate.source,
        origin: candidate.kind,
        generatedAt: candidate.generatedAt,
        acceptedAt: candidate.acceptedAt,
        runId: candidate.runId,
        snapshotHash: candidate.snapshotHash,
        scanStatus: candidate.scanStatus,
        ageSeconds: candidate.ageSeconds,
        stale: candidate.stale,
        degraded,
        divergence: divergent,
        divergenceReason: reason,
        attempts
      })
    };
  }

  async function auctionWatchRequestError(response, fallback) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = String(payload?.error || payload?.message || "").trim();
    } catch (_error) {
      // The server normally returns JSON. Keep a useful fallback when an ingress proxy does not.
    }
    return new Error(detail || `${fallback} (${response.status})`);
  }

  function normalizeDismissal(raw = {}) {
    const sourceId = String(raw.sourceId || raw.source || "").trim().toLowerCase();
    const lotId = String(raw.lotId || "").trim();
    if (!sourceId || !lotId) return null;
    return {
      sourceId,
      lotId,
      groupId: String(raw.groupId || ""),
      title: String(raw.title || "Oportunidad descartada"),
      lotUrl: safePublicUrl(raw.lotUrl),
      imageUrl: safePublicUrl(raw.imageUrl || raw.image_url),
      dismissedAt: String(raw.dismissedAt || new Date().toISOString())
    };
  }

  function normalizeFollowing(raw = {}) {
    const sourceId = String(raw.sourceId || raw.source || "").trim().toLowerCase();
    const lotId = String(raw.lotId || "").trim();
    if (!sourceId || !lotId) return null;
    return {
      sourceId,
      lotId,
      groupId: String(raw.groupId || ""),
      title: String(raw.title || "Oportunidad seguida"),
      lotUrl: safePublicUrl(raw.lotUrl),
      followedAt: String(raw.followedAt || new Date().toISOString())
    };
  }

  function createEmptySnapshot() {
    return {
      generatedAt: "",
      generatedAtLabel: "",
      runId: "",
      status: "idle",
      scanStatus: "idle",
      sync: {},
      coverage: {},
      issues: [],
      counts: {},
      dismissalsApplied: 0,
      featured: null,
      matches: [],
      matchesById: {},
      byConsoleId: {}
    };
  }

  function formatGeneratedAt(raw = "") {
    if (!raw) return "";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return "";
    return new Intl.DateTimeFormat("es-UY", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit"
    }).format(parsed);
  }

  function inferConsoleIds(item = {}) {
    const ids = new Set([...(item.consoleIds || []), ...((WATCHLIST_CONSOLE_OVERRIDES[item.watchId] || []))]);
    const haystack = normalizeText([item.watchId, item.title, item.description, (item.matchedKeywords || []).join(" ")].join(" "));

    Object.entries(CONSOLE_PATTERNS).forEach(([consoleId, patterns]) => {
      if ((patterns || []).some((pattern) => pattern.test(haystack))) {
        ids.add(consoleId);
      }
    });

    return stableArray([...ids]);
  }

  function normalizeOpportunity(raw = {}, index = 0) {
    const item = {
      id: raw.id || raw.watchId || `auction-watch-${index + 1}`,
      watchId: raw.watchId || "",
      consoleIds: stableArray(raw.consoleIds || []),
      source: String(raw.source || "").toLowerCase() || "castells",
      groupId: raw.groupId || "",
      groupLabel: raw.groupLabel || "",
      lotId: raw.lotId || "",
      lotNumber: raw.lotNumber || "",
      title: raw.title || "Oportunidad activa",
      description: raw.description || "",
      score: Number(raw.score) || 0,
      matchedKeywords: stableArray(raw.matchedKeywords || []),
      positiveFlags: stableArray(raw.positiveFlags || []),
      riskFlags: stableArray(raw.riskFlags || []),
      closingAt: raw.closingAt || "",
      remainingText: raw.remainingText || "-",
      urgencyLabel: raw.urgencyLabel || "seguimiento",
      priceValue: toNumber(raw.priceValue),
      priceCurrency: raw.priceCurrency || "",
      priceLabel: raw.priceLabel || "",
      lotUrl: raw.lotUrl || "",
      groupUrl: raw.groupUrl || "",
      imageUrl: raw.imageUrl || raw.image_url || "",
      watchlist: raw.watchlist === true,
      notes: raw.notes || "",
      firstSeenAt: raw.firstSeenAt || "",
      lastSeenAt: raw.lastSeenAt || "",
      firstSeenRunId: raw.firstSeenRunId || "",
      lastSeenRunId: raw.lastSeenRunId || "",
      seenCount: Number(raw.seenCount) || 0,
      active: raw.active !== false,
      firstSeenInRun: raw.firstSeenInRun === true,
      wasActive: raw.wasActive === true,
      disappearedAfterAuthoritativeRefresh: raw.disappearedAfterAuthoritativeRefresh === true,
      featured: raw.featured === true
    };

    return {
      ...item,
      following: isOpportunityFollowed(item),
      consoleIds: inferConsoleIds(item)
    };
  }

  function sortMatches(items = []) {
    return [...(items || [])].sort((a, b) => {
      const followingGap = Number(b.following === true) - Number(a.following === true);
      if (followingGap !== 0) return followingGap;

      const watchGap = Number(b.watchlist === true) - Number(a.watchlist === true);
      if (watchGap !== 0) return watchGap;

      const urgencyGap = getUrgencyRank(a.urgencyLabel) - getUrgencyRank(b.urgencyLabel);
      if (urgencyGap !== 0) return urgencyGap;

      const dateA = a.closingAt ? Date.parse(a.closingAt) : Number.POSITIVE_INFINITY;
      const dateB = b.closingAt ? Date.parse(b.closingAt) : Number.POSITIVE_INFINITY;
      if (dateA !== dateB) return dateA - dateB;

      const scoreGap = (b.score || 0) - (a.score || 0);
      if (scoreGap !== 0) return scoreGap;

      return String(a.title || "").localeCompare(String(b.title || ""), "es");
    });
  }

  function buildIndex(matches = [], featured = null) {
    const matchesById = {};
    const byConsoleId = {};
    const all = featured ? [featured, ...matches] : [...matches];

    all.forEach((item) => {
      matchesById[item.id] = item;
      (item.consoleIds || []).forEach((consoleId) => {
        if (!byConsoleId[consoleId]) byConsoleId[consoleId] = [];
        if (!byConsoleId[consoleId].includes(item.id)) byConsoleId[consoleId].push(item.id);
      });
    });

    return { matchesById, byConsoleId };
  }

  function normalizeSnapshot(raw = {}) {
    const scanStatus = getSnapshotScanStatus(raw) || "unknown";
    const matches = sortMatches((raw.matches || []).map((item, index) => normalizeOpportunity(item, index)));
    const featuredInput = raw.featured ? { ...raw.featured, featured: true } : null;
    let featured = featuredInput ? normalizeOpportunity(featuredInput, -1) : null;

    if (featured) {
      const featuredMatchIndex = matches.findIndex(
        (item) =>
          (featured.watchId && item.watchId && item.watchId === featured.watchId) ||
          (featured.lotUrl && item.lotUrl && item.lotUrl === featured.lotUrl)
      );

      if (featuredMatchIndex >= 0) {
        const baseMatch = matches[featuredMatchIndex];
        featured = {
          ...baseMatch,
          ...featured,
          id: baseMatch.id,
          groupId: baseMatch.groupId,
          groupLabel: baseMatch.groupLabel,
          lotId: baseMatch.lotId,
          lotNumber: baseMatch.lotNumber,
          score: baseMatch.score || featured.score,
          priceValue: baseMatch.priceValue ?? featured.priceValue,
          priceCurrency: baseMatch.priceCurrency || featured.priceCurrency,
          matchedKeywords: stableArray([...(baseMatch.matchedKeywords || []), ...(featured.matchedKeywords || [])]),
          consoleIds: stableArray([...(baseMatch.consoleIds || []), ...(featured.consoleIds || [])]),
          watchlist: true,
          featured: true
        };
        matches[featuredMatchIndex] = {
          ...featured,
          featured: false
        };
      }
    }

    const visibleMatches = matches.filter((item) => !isOpportunityDismissed(item));
    if (featured && isOpportunityDismissed(featured)) featured = null;
    const { matchesById, byConsoleId } = buildIndex(visibleMatches, featured);

    return {
      generatedAt: raw.generatedAt || "",
      generatedAtLabel: formatGeneratedAt(raw.generatedAt || ""),
      runId: raw.runId || raw.sync?.runId || "",
      status: scanStatus,
      scanStatus,
      sync: raw.sync && typeof raw.sync === "object" ? { ...raw.sync } : {},
      coverage: raw.coverage && typeof raw.coverage === "object" ? { ...raw.coverage } : {},
      issues: Array.isArray(raw.issues)
        ? raw.issues
            .filter((issue) => issue && typeof issue === "object")
            .map((issue) => ({
              sourceId: String(issue.sourceId || issue.source_id || "").trim().toLowerCase(),
              sourceLabel: String(issue.sourceLabel || issue.source_label || "Fuente externa"),
              status: String(issue.status || "failed"),
              summary: String(issue.summary || "La fuente no pudo completar la consulta.")
            }))
        : [],
      counts: raw.counts || {},
      dismissalsApplied: Number(raw.dismissalsApplied) || 0,
      featured,
      matches: visibleMatches,
      matchesById,
      byConsoleId
    };
  }

  let snapshot = createEmptySnapshot();
  let rawSnapshot = {};
  let snapshotSource = "none";
  let syncState = createSyncState();
  let dismissals = [];
  let dismissalKeys = new Set();
  let dismissalSource = "none";
  let following = [];
  let followingKeys = new Set();
  let followingSource = "none";

  function setSnapshot(raw, options = {}) {
    rawSnapshot = raw || {};
    snapshot = normalizeSnapshot(rawSnapshot);
    if (options.source) snapshotSource = options.source;
    if (options.sync) syncState = createSyncState(options.sync);
    return snapshot;
  }

  function getSnapshot() {
    return snapshot;
  }

  function getSnapshotSource() {
    return snapshotSource;
  }

  function getSyncState() {
    return {
      ...syncState,
      attempts: (syncState.attempts || []).map((attempt) => ({ ...attempt }))
    };
  }

  function hasData() {
    return Boolean(snapshot.featured || snapshot.matches.length);
  }

  function rebuildDismissalIndex(items = []) {
    dismissals = (items || []).map((item) => normalizeDismissal(item)).filter(Boolean);
    dismissalKeys = new Set(dismissals.map((item) => getOpportunityKey(item)).filter(Boolean));
  }

  function readLocalDismissals() {
    try {
      const payload = JSON.parse(window.localStorage?.getItem(DISMISSALS_STORAGE_KEY) || "{}");
      return Array.isArray(payload.items) ? payload.items : [];
    } catch (_error) {
      return [];
    }
  }

  function writeLocalDismissals(items = dismissals) {
    try {
      window.localStorage?.setItem(
        DISMISSALS_STORAGE_KEY,
        JSON.stringify({ version: 1, updatedAt: new Date().toISOString(), items })
      );
    } catch (_error) {
      // Local fallback is best effort; the server remains authoritative when available.
    }
  }

  async function loadDismissals() {
    try {
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/dismissals`, {
        cache: "no-store",
        headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error(`dismissals unavailable (${response.status})`);
      const payload = await response.json();
      rebuildDismissalIndex(Array.isArray(payload.items) ? payload.items : []);
      dismissalSource = "server";
      writeLocalDismissals();
    } catch (error) {
      rebuildDismissalIndex(readLocalDismissals());
      dismissalSource = dismissals.length ? "local" : "none";
      console.info("[AuctionWatch] dismissals server unavailable", error);
    }
    if (Object.keys(rawSnapshot || {}).length) setSnapshot(rawSnapshot);
    return dismissals;
  }

  function isOpportunityDismissed(item = {}) {
    const key = getOpportunityKey(item);
    return Boolean(key && dismissalKeys.has(key));
  }

  function rebuildFollowingIndex(items = []) {
    following = (items || []).map((item) => normalizeFollowing(item)).filter(Boolean);
    followingKeys = new Set(following.map((item) => getOpportunityKey(item)).filter(Boolean));
  }

  function readLocalFollowing() {
    try {
      const payload = JSON.parse(window.localStorage?.getItem(FOLLOWING_STORAGE_KEY) || "{}");
      return Array.isArray(payload.items) ? payload.items : [];
    } catch (_error) {
      return [];
    }
  }

  function writeLocalFollowing(items = following) {
    try {
      window.localStorage?.setItem(
        FOLLOWING_STORAGE_KEY,
        JSON.stringify({ version: 1, updatedAt: new Date().toISOString(), items })
      );
    } catch (_error) {
      // Local fallback is best effort; the server remains authoritative when available.
    }
  }

  async function loadFollowing() {
    try {
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/following`, {
        cache: "no-store",
        headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error(`following unavailable (${response.status})`);
      const payload = await response.json();
      rebuildFollowingIndex(Array.isArray(payload.items) ? payload.items : []);
      followingSource = "server";
      writeLocalFollowing();
    } catch (error) {
      rebuildFollowingIndex(readLocalFollowing());
      followingSource = following.length ? "local" : "none";
      console.info("[AuctionWatch] following server unavailable", error);
    }
    if (Object.keys(rawSnapshot || {}).length) setSnapshot(rawSnapshot);
    return following;
  }

  function isOpportunityFollowed(item = {}) {
    const key = getOpportunityKey(item);
    return Boolean(key && followingKeys.has(key));
  }

  function upsertFollowing(raw = {}) {
    const item = normalizeFollowing(raw);
    if (!item) return null;
    const key = getOpportunityKey(item);
    rebuildFollowingIndex([item, ...following.filter((entry) => getOpportunityKey(entry) !== key)]);
    writeLocalFollowing();
    setSnapshot(rawSnapshot);
    return item;
  }

  async function followOpportunity(item = {}, { requireServer = false } = {}) {
    const record = normalizeFollowing({
      sourceId: item.source || item.sourceId,
      lotId: item.lotId,
      groupId: item.groupId,
      title: item.title,
      lotUrl: item.lotUrl,
      imageUrl: item.imageUrl,
      followedAt: new Date().toISOString()
    });
    if (!record) throw new Error("La oportunidad no tiene una identidad estable.");

    try {
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/following`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Consolas-Auction-Watch": "1"
        },
        body: JSON.stringify(record)
      });
      if (!response.ok) throw new Error(`follow failed (${response.status})`);
      const payload = await response.json();
      followingSource = "server";
      return upsertFollowing(payload.item || record);
    } catch (error) {
      if (requireServer) throw error;
      followingSource = "local";
      console.info("[AuctionWatch] using local following fallback", error);
      return upsertFollowing(record);
    }
  }

  async function unfollowOpportunity(item = {}, { requireServer = false } = {}) {
    const record = normalizeFollowing(item);
    if (!record) throw new Error("La oportunidad no tiene una identidad estable.");
    const key = getOpportunityKey(record);
    let removedOnServer = false;

    try {
      const query = new URLSearchParams({ sourceId: record.sourceId, lotId: record.lotId });
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/following?${query}`, {
        method: "DELETE",
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-Consolas-Auction-Watch": "1" }
      });
      if (!response.ok) throw new Error(`unfollow failed (${response.status})`);
      removedOnServer = true;
      followingSource = "server";
    } catch (error) {
      if (requireServer) throw error;
      followingSource = "local";
      console.info("[AuctionWatch] using local following fallback", error);
    }

    rebuildFollowingIndex(following.filter((entry) => getOpportunityKey(entry) !== key));
    writeLocalFollowing();
    if (removedOnServer) return await loadSnapshot();
    return setSnapshot(rawSnapshot);
  }

  function upsertDismissal(raw = {}) {
    const item = normalizeDismissal(raw);
    if (!item) return null;
    const key = getOpportunityKey(item);
    rebuildDismissalIndex([item, ...dismissals.filter((entry) => getOpportunityKey(entry) !== key)]);
    writeLocalDismissals();
    setSnapshot(rawSnapshot);
    return item;
  }

  async function dismissOpportunity(item = {}, { requireServer = false } = {}) {
    const record = normalizeDismissal({
      sourceId: item.source || item.sourceId,
      lotId: item.lotId,
      groupId: item.groupId,
      title: item.title,
      lotUrl: item.lotUrl,
      dismissedAt: new Date().toISOString()
    });
    if (!record) throw new Error("La oportunidad no tiene una identidad estable.");

    try {
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/dismissals`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Consolas-Auction-Watch": "1"
        },
        body: JSON.stringify(record)
      });
      if (!response.ok) throw await auctionWatchRequestError(response, "No se pudo descartar");
      const payload = await response.json();
      dismissalSource = "server";
      return upsertDismissal(payload.item || record);
    } catch (error) {
      if (requireServer) throw error;
      dismissalSource = "local";
      console.info("[AuctionWatch] using local dismissal fallback", error);
      return upsertDismissal(record);
    }
  }

  async function restoreOpportunity(item = {}, { requireServer = false } = {}) {
    const record = normalizeDismissal(item);
    if (!record) throw new Error("La oportunidad no tiene una identidad estable.");
    const key = getOpportunityKey(record);
    let restoredOnServer = false;

    try {
      const query = new URLSearchParams({ sourceId: record.sourceId, lotId: record.lotId });
      const response = await fetchWithTimeout(`${REMOTE_API_BASE}/auction-watch/dismissals?${query}`, {
        method: "DELETE",
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-Consolas-Auction-Watch": "1" }
      });
      if (!response.ok) throw await auctionWatchRequestError(response, "No se pudo restaurar");
      restoredOnServer = true;
      dismissalSource = "server";
    } catch (error) {
      if (requireServer) throw error;
      dismissalSource = "local";
      console.info("[AuctionWatch] using local restore fallback", error);
    }

    rebuildDismissalIndex(dismissals.filter((entry) => getOpportunityKey(entry) !== key));
    writeLocalDismissals();
    if (restoredOnServer) return await loadSnapshot();
    return setSnapshot(rawSnapshot);
  }

  function getDismissedOpportunities() {
    const rawItems = [rawSnapshot.featured, ...(rawSnapshot.matches || [])]
      .filter(Boolean)
      .map((item, index) => normalizeOpportunity(item, index));
    const rawByKey = new Map(rawItems.map((item) => [getOpportunityKey(item), item]));
    return dismissals.map((entry, index) => {
      const base = rawByKey.get(getOpportunityKey(entry));
      return {
        ...(base || normalizeOpportunity({
          id: `dismissed-${entry.sourceId}-${entry.lotId}`,
          source: entry.sourceId,
          lotId: entry.lotId,
          groupId: entry.groupId,
          title: entry.title,
          lotUrl: entry.lotUrl,
          imageUrl: entry.imageUrl
        }, index)),
        dismissed: true,
        dismissedAt: entry.dismissedAt,
        sourceId: entry.sourceId
      };
    });
  }

  function getFollowingOpportunities() {
    const rawItems = [rawSnapshot.featured, ...(rawSnapshot.matches || [])]
      .filter(Boolean)
      .map((item, index) => normalizeOpportunity(item, index));
    const rawByKey = new Map(rawItems.map((item) => [getOpportunityKey(item), item]));
    return following
      .filter((entry) => !dismissalKeys.has(getOpportunityKey(entry)))
      .map((entry, index) => {
        const base = rawByKey.get(getOpportunityKey(entry));
        return {
          ...(base || normalizeOpportunity({
            id: `following-${entry.sourceId}-${entry.lotId}`,
            source: entry.sourceId,
            lotId: entry.lotId,
            groupId: entry.groupId,
            title: entry.title,
            lotUrl: entry.lotUrl
          }, index)),
          following: true,
          followedAt: entry.followedAt,
          sourceId: entry.sourceId
        };
      });
  }

  function getDismissalSource() {
    return dismissalSource;
  }

  function getFollowingSource() {
    return followingSource;
  }

  async function fetchSnapshotCandidate(kind, url) {
    try {
      const response = await fetchWithTimeout(url, {
        cache: "no-store",
        headers: { Accept: "application/json" }
      });
      if (!response.ok) {
        return {
          ok: false,
          kind,
          reason: "http_error",
          detail: `Auction Watch respondió ${response.status}.`
        };
      }
      const payload = await response.json();
      return validateSnapshotPayload(payload, kind);
    } catch (error) {
      console.info(`[AuctionWatch] snapshot ${kind} unavailable`, error);
      return {
        ok: false,
        kind,
        reason: String(error?.message || "").includes("timeout") ? "timeout" : "request_failed",
        detail: String(error?.message || "No se pudo cargar el snapshot.")
      };
    }
  }

  async function loadSnapshot() {
    const metadataPromise = Promise.allSettled([loadDismissals(), loadFollowing()]);
    const cacheNonce = Date.now();
    const [serverCandidate, runtimeCandidate, staticCandidate] = await Promise.all([
      fetchSnapshotCandidate("server", `${REMOTE_API_BASE}/auction-watch`),
      fetchSnapshotCandidate("runtime", `./runtime/auction-watch.json?ts=${cacheNonce}`),
      fetchSnapshotCandidate(
        "static",
        `./data/auction-watch.json?v=${STATIC_SNAPSHOT_CACHE_KEY}&ts=${cacheNonce}`
      )
    ]);
    await metadataPromise;

    const selection = chooseSnapshotCandidate(serverCandidate, runtimeCandidate, staticCandidate);
    if (!selection.candidate) {
      return setSnapshot(createEmptySnapshot(), {
        source: "none",
        sync: selection.sync
      });
    }

    return setSnapshot(selection.candidate.payload, {
      source: selection.candidate.kind,
      sync: selection.sync
    });
  }

  function getFeaturedOpportunity() {
    return snapshot.featured;
  }

  function getHomeOpportunities(limit = 6) {
    const featuredId = snapshot.featured?.id;
    return snapshot.matches.filter((item) => item.id !== featuredId).slice(0, Math.max(0, Number(limit) || 0));
  }

  function getConsoleOpportunities(consoleId = "") {
    const ids = snapshot.byConsoleId[consoleId] || [];
    return ids
      .map((id) => {
        if (snapshot.featured?.id === id) return snapshot.featured;
        return snapshot.matchesById[id];
      })
      .filter(Boolean);
  }

  function getPrimaryUrl(item = {}) {
    return safePublicUrl(item.lotUrl) || safePublicUrl(item.groupUrl);
  }

  function getSecondaryUrl(item = {}) {
    const lotUrl = safePublicUrl(item.lotUrl);
    const groupUrl = safePublicUrl(item.groupUrl);
    if (lotUrl && groupUrl && groupUrl !== lotUrl) return groupUrl;
    return "";
  }

  function getPrimaryCtaLabel(item = {}) {
    if (item.lotUrl) return "Ver publicación";
    if (item.groupUrl) return item.source === "bavastro" ? "Ver subasta" : "Ver remate";
    return "Ver detalle";
  }

  function getSecondaryCtaLabel(item = {}) {
    if (item.lotUrl && item.groupUrl && item.groupUrl !== item.lotUrl) {
      return item.source === "bavastro" ? "Ver subasta" : "Ver remate";
    }
    return "";
  }

  window.AuctionWatchRepository = {
    normalizeText,
    normalizeScanStatus,
    safePublicUrl,
    loadSnapshot,
    setSnapshot,
    getSnapshot,
    getSnapshotSource,
    getSyncState,
    loadDismissals,
    loadFollowing,
    getDismissalSource,
    getFollowingSource,
    getOpportunityKey,
    isOpportunityDismissed,
    isOpportunityFollowed,
    dismissOpportunity,
    restoreOpportunity,
    followOpportunity,
    unfollowOpportunity,
    getDismissedOpportunities,
    getFollowingOpportunities,
    hasData,
    getFeaturedOpportunity,
    getHomeOpportunities,
    getConsoleOpportunities,
    getSourceLabel,
    getPrimaryUrl,
    getSecondaryUrl,
    getPrimaryCtaLabel,
    getSecondaryCtaLabel
  };
})();
