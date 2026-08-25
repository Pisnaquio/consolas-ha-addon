#!/usr/bin/env node
import fs from "fs";
import path from "path";
import crypto from "crypto";
import { fileURLToPath } from "url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_PATH = path.join(ROOT, "data/console-games.json");
const FALLBACK = "./assets/photos/game-placeholder.svg";

const args = process.argv.slice(2);
const consoleOnly = args.includes("--console") ? args[args.indexOf("--console") + 1] : null;
const force = args.includes("--force");

const data = JSON.parse(fs.readFileSync(DATA_PATH, "utf8"));
if (!data.byConsole) data.byConsole = {};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const sanitizeTitle = (name = "") =>
  name
    .replace(/\([^)]*\b(remake|remaster|definitive|edition|enhanced|version|202[0-9]|201[0-9])[^)]*\)/gi, "")
    .replace(/\b(definitive edition|enhanced|remake|remaster|season update)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();

const isPlaceholder = (value = "") => String(value).includes("game-placeholder.svg");

const norm = (s = "") =>
  s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

const extFromUrl = (url = "") => {
  const m = url.match(/\.(png|jpg|jpeg|webp)(?:\?|$)/i);
  return m ? `.${m[1].toLowerCase()}` : ".jpg";
};

const VERSION_RULES = [
  { match: /pokemon sword/i, include: ["pokemon", "sword"], exclude: ["shield"] },
  { match: /pokemon shield/i, include: ["pokemon", "shield"], exclude: ["sword"] },
  { match: /pokemon brilliant diamond/i, include: ["pokemon", "brilliant", "diamond"], exclude: ["shining", "pearl"] },
  { match: /pokemon shining pearl/i, include: ["pokemon", "shining", "pearl"], exclude: ["brilliant", "diamond"] },
  { match: /pokemon scarlet/i, include: ["pokemon", "scarlet"], exclude: ["violet"] },
  { match: /pokemon violet/i, include: ["pokemon", "violet"], exclude: ["scarlet"] },
  { match: /pokemon firered/i, include: ["pokemon", "firered"], exclude: ["leafgreen"] },
  { match: /pokemon leafgreen/i, include: ["pokemon", "leafgreen"], exclude: ["firered"] },
  { match: /pokemon ruby/i, include: ["pokemon", "ruby"], exclude: ["sapphire"] },
  { match: /pokemon sapphire/i, include: ["pokemon", "sapphire"], exclude: ["ruby"] }
];

const SIBLING_MAP = new Map([
  ["pokemon sword", "pokemon shield"],
  ["pokemon shield", "pokemon sword"],
  ["pokemon brilliant diamond", "pokemon shining pearl"],
  ["pokemon shining pearl", "pokemon brilliant diamond"],
  ["pokemon scarlet", "pokemon violet"],
  ["pokemon violet", "pokemon scarlet"],
  ["pokemon firered", "pokemon leafgreen"],
  ["pokemon leafgreen", "pokemon firered"],
  ["pokemon ruby", "pokemon sapphire"],
  ["pokemon sapphire", "pokemon ruby"]
]);

const PROMO_HINTS = ["double-pack", "double pack", "bundle", "promo", "promotional", "banner", "hero", "wallpaper"];

function findVersionRule(gameName = "") {
  return VERSION_RULES.find((rule) => rule.match.test(gameName));
}

function isLikelySharedPromo(text = "") {
  const value = norm(text);
  return PROMO_HINTS.some((hint) => value.includes(norm(hint)));
}

function isStrictCoverMatch(gameName = "", candidate = {}) {
  const packed = [candidate.url || "", candidate.label || "", candidate.title || "", candidate.hostPage || ""].join(" ");
  const candidateText = norm(packed);
  if (!candidateText) return true;
  if (isLikelySharedPromo(candidateText)) return false;

  const rule = findVersionRule(gameName);
  if (!rule) return true;
  const hasAllIncludes = rule.include.every((token) => candidateText.includes(norm(token)));
  const hasExcluded = rule.exclude.some((token) => candidateText.includes(norm(token)));
  return hasAllIncludes && !hasExcluded;
}

function hashFile(absPath) {
  const buf = fs.readFileSync(absPath);
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function relToAbs(relPath = "") {
  if (!relPath || !relPath.startsWith("./")) return null;
  return path.join(ROOT, relPath.replace("./", ""));
}

function hasTwinDuplicateCover(game, games, currentCoverRel, hashCache) {
  const gameKey = norm(game?.nombre || "");
  const siblingKey = SIBLING_MAP.get(gameKey);
  if (!siblingKey || !currentCoverRel) return false;

  const sibling = games.find((g) => norm(g.nombre || "") === siblingKey);
  if (!sibling) return false;

  const siblingCover = sibling.coverImage || sibling.coverUrl || "";
  if (!siblingCover || isPlaceholder(siblingCover)) return false;

  const aAbs = relToAbs(currentCoverRel);
  const bAbs = relToAbs(siblingCover);
  if (!aAbs || !bAbs || !fs.existsSync(aAbs) || !fs.existsSync(bAbs)) return false;

  if (!hashCache.has(aAbs)) hashCache.set(aAbs, hashFile(aAbs));
  if (!hashCache.has(bAbs)) hashCache.set(bAbs, hashFile(bAbs));
  return hashCache.get(aAbs) === hashCache.get(bAbs);
}

function findLocalRasterCover(consoleId, gameId) {
  const baseDir = path.join(ROOT, "assets", "game-covers", consoleId);
  const candidates = [".png", ".jpg", ".jpeg", ".webp"];
  for (const ext of candidates) {
    const abs = path.join(baseDir, `${gameId}${ext}`);
    if (fs.existsSync(abs)) return toRel(abs);
  }
  return null;
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return await res.json();
}

async function downloadBinary(url, targetAbs, headers = {}) {
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.mkdirSync(path.dirname(targetAbs), { recursive: true });
  fs.writeFileSync(targetAbs, buf);
}

async function steamGridDbCover(gameName) {
  const key = process.env.SGDB_API_KEY;
  if (!key) return null;
  const headers = { Authorization: `Bearer ${key}` };
  const q = encodeURIComponent(sanitizeTitle(gameName));

  const search = await fetchJson(`https://www.steamgriddb.com/api/v2/search/autocomplete/${q}`, { headers });
  const hit = (search.data || [])[0];
  if (!hit?.id) return null;

  const grids = await fetchJson(
    `https://www.steamgriddb.com/api/v2/grids/game/${hit.id}?types=static&dimensions=600x900,660x930`,
    { headers }
  );
  const best = (grids?.data || []).find((entry) =>
    isStrictCoverMatch(gameName, { url: entry?.url || "", label: hit?.name || "", title: entry?.style || "" })
  );
  const cover = best?.url;
  return cover ? { url: cover, imageSource: "steamgriddb", label: hit?.name || "" } : null;
}

let cachedIgdbToken = null;
let cachedIgdbTokenExp = 0;
async function getIgdbToken() {
  const now = Date.now();
  if (cachedIgdbToken && now < cachedIgdbTokenExp) return cachedIgdbToken;

  const clientId = process.env.IGDB_CLIENT_ID;
  const clientSecret = process.env.IGDB_CLIENT_SECRET;
  if (!clientId || !clientSecret) return null;

  const tokenRes = await fetch(
    `https://id.twitch.tv/oauth2/token?client_id=${encodeURIComponent(clientId)}&client_secret=${encodeURIComponent(
      clientSecret
    )}&grant_type=client_credentials`,
    { method: "POST" }
  );
  if (!tokenRes.ok) return null;
  const tokenJson = await tokenRes.json();
  cachedIgdbToken = tokenJson.access_token;
  cachedIgdbTokenExp = Date.now() + Math.max(60, (tokenJson.expires_in || 3600) - 60) * 1000;
  return cachedIgdbToken;
}

async function igdbCover(gameName) {
  const token = await getIgdbToken();
  const clientId = process.env.IGDB_CLIENT_ID;
  if (!token || !clientId) return null;

  const query = `search "${sanitizeTitle(gameName).replace(/"/g, '\\"')}"; fields name,cover.image_id; limit 10;`;
  const res = await fetch("https://api.igdb.com/v4/games", {
    method: "POST",
    headers: {
      "Client-ID": clientId,
      Authorization: `Bearer ${token}`,
      "Content-Type": "text/plain"
    },
    body: query
  });
  if (!res.ok) return null;
  const list = await res.json();
  if (!Array.isArray(list) || !list.length) return null;

  const target = norm(gameName);
  const picked =
    list.find((g) => norm(g.name) === target && g.cover?.image_id) ||
    list.find((g) => norm(g.name).includes(target) && g.cover?.image_id) ||
    list.find((g) => g.cover?.image_id);
  if (!picked?.cover?.image_id) return null;

  const candidate = {
    url: `https://images.igdb.com/igdb/image/upload/t_cover_big/${picked.cover.image_id}.jpg`,
    imageSource: "igdb",
    label: picked.name || ""
  };
  return isStrictCoverMatch(gameName, candidate) ? candidate : null;
}

async function theGamesDbCover(gameName) {
  const key = process.env.TGDB_API_KEY || process.env.THEGAMESDB_API_KEY;
  if (!key) return null;

  const url = `https://api.thegamesdb.net/v1.1/Games/ByGameName?apikey=${encodeURIComponent(
    key
  )}&name=${encodeURIComponent(sanitizeTitle(gameName))}&include=boxart`;
  const json = await fetchJson(url);
  const game = json?.data?.games?.[0];
  if (!game) return null;

  const base = json?.include?.boxart?.base_url?.medium || json?.include?.boxart?.base_url?.original || "";
  const box = json?.include?.boxart?.data?.[game.id]?.find((b) => b.side === "front");
  if (!base || !box?.filename) return null;
  const candidate = { url: `${base}${box.filename}`, imageSource: "thegamesdb", label: game?.game_title || game?.name || "" };
  return isStrictCoverMatch(gameName, candidate) ? candidate : null;
}

async function bingImageCover(gameName) {
  const key = process.env.BING_IMAGE_API_KEY;
  if (!key) return null;

  const endpoint = process.env.BING_IMAGE_ENDPOINT || "https://api.bing.microsoft.com/v7.0/images/search";
  const url = `${endpoint}?q=${encodeURIComponent(`${sanitizeTitle(gameName)} video game cover`)}&count=10&safeSearch=Moderate`;
  const json = await fetchJson(url, {
    headers: { "Ocp-Apim-Subscription-Key": key }
  });

  const picked = (json?.value || []).find((entry) =>
    isStrictCoverMatch(gameName, {
      url: entry?.contentUrl || "",
      label: entry?.name || "",
      hostPage: entry?.hostPageUrl || ""
    })
  );
  const cover = picked?.contentUrl;
  return cover ? { url: cover, imageSource: "bing-image-search", label: picked?.name || "" } : null;
}

async function wikipediaCover(gameName) {
  const base = sanitizeTitle(gameName);
  if (!base) return null;

  const searchUrl = `https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(
    `${base} video game`
  )}&utf8=&format=json`;
  const searchRes = await fetch(searchUrl, { headers: { "User-Agent": "console-catalog/1.0" } });
  if (!searchRes.ok) return null;
  const searchJson = await searchRes.json();
  const results = searchJson?.query?.search || [];

  for (const hit of results.slice(0, 6)) {
    const title = hit?.title || "";
    if (!isStrictCoverMatch(gameName, { label: title })) continue;

    const summaryUrl = `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`;
    const summaryRes = await fetch(summaryUrl, { headers: { "User-Agent": "console-catalog/1.0" } });
    if (!summaryRes.ok) continue;
    const summaryJson = await summaryRes.json();
    const thumb = summaryJson?.thumbnail?.source;
    if (!thumb) continue;

    const candidate = { url: thumb, imageSource: "wikipedia", label: title };
    if (isStrictCoverMatch(gameName, candidate)) return candidate;
  }

  return null;
}

async function resolveCoverUrl(gameName) {
  const providers = [steamGridDbCover, igdbCover, theGamesDbCover, bingImageCover, wikipediaCover];
  for (const p of providers) {
    try {
      const result = await p(gameName);
      if (result?.url) return result;
    } catch {
      // skip provider failure
    }
    await sleep(120);
  }
  return null;
}

function toRel(abs) {
  return `./${path.relative(ROOT, abs).replaceAll("\\", "/")}`;
}

async function run() {
  let total = 0;
  let fetched = 0;
  let fallback = 0;
  let kept = 0;
  const hashCache = new Map();

  for (const [consoleId, payload] of Object.entries(data.byConsole)) {
    if (consoleOnly && consoleOnly !== consoleId) continue;
    const games = payload?.juegosCatalogo;
    if (!Array.isArray(games)) continue;

    for (const game of games) {
      total += 1;
      const searchName = sanitizeTitle(game.imageSearchName || game.nombre || "");
      game.imageSearchName = searchName;
      const existing = game.coverImage || game.coverUrl || "";
      const isManual = game.imageStatus === "manual";
      const hasRealExisting = existing && !isPlaceholder(existing);
      if (isManual && existing) {
        game.coverImage = existing;
        game.coverUrl = game.coverUrl || existing;
        game.imageSource = game.imageSource || "manual";
        game.imageStatus = "manual";
        kept += 1;
        continue;
      }

      if (hasRealExisting && !force && !hasTwinDuplicateCover(game, games, existing, hashCache)) {
        game.coverImage = existing;
        game.coverUrl = game.coverUrl || existing;
        game.imageSource = game.imageSource || "existing";
        game.imageStatus = game.imageStatus || "found";
        kept += 1;
        continue;
      }

      const localRaster = findLocalRasterCover(consoleId, game.id);
      if (localRaster) {
        game.coverImage = localRaster;
        game.coverUrl = localRaster;
        game.imageSource = "local-cache";
        game.imageStatus = "found";
        kept += 1;
        continue;
      }

      let resolved = null;
      try {
        resolved = await resolveCoverUrl(searchName || game.nombre || "");
      } catch {
        resolved = null;
      }

      if (resolved?.url) {
        const ext = extFromUrl(resolved.url);
        const abs = path.join(ROOT, "assets", "game-covers", consoleId, `${game.id}${ext}`);
        try {
          await downloadBinary(resolved.url, abs);
          const rel = toRel(abs);
          game.coverImage = rel;
          game.coverUrl = rel;
          game.imageSource = resolved.imageSource || "resolver";
          game.imageStatus = "found";
          fetched += 1;
          continue;
        } catch {
          // fall through
        }
      }

      game.coverImage = FALLBACK;
      game.coverUrl = FALLBACK;
      game.imageSource = "placeholder";
      game.imageStatus = "placeholder";
      fallback += 1;
    }
  }

  fs.writeFileSync(DATA_PATH, JSON.stringify(data, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ total, fetched, fallback, kept, consoleOnly: consoleOnly || "all" }, null, 2));
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
