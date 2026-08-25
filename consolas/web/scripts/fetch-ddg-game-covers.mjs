#!/usr/bin/env node
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_PATH = path.join(ROOT, "data/console-games.json");
const TARGETS = new Set(["ps1", "ps4", "switch2", "switch", "gb-color", "gamecube", "xbox-360-e", "atari"]);

const args = process.argv.slice(2);
const onlyConsole = args.includes("--console") ? args[args.indexOf("--console") + 1] : null;
const force = args.includes("--force");

const data = JSON.parse(fs.readFileSync(DATA_PATH, "utf8"));

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

const BAD_HINTS = ["wallpaper", "banner", "hero", "youtube", "steam", "header", "fanart", "double pack", "bundle"];

const norm = (s = "") =>
  s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

const sanitize = (name = "") =>
  name
    .replace(/\([^)]*\)/g, " ")
    .replace(/\b(nuevo|new|eventual|wishlist|rumored|confirmado|confirmed|switch 2 version)\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();

function findVersionRule(gameName = "") {
  return VERSION_RULES.find((rule) => rule.match.test(gameName));
}

function strictMatch(gameName = "", title = "", url = "") {
  const text = norm(`${title} ${url}`);
  if (!text) return false;
  if (BAD_HINTS.some((h) => text.includes(norm(h)))) return false;

  const rule = findVersionRule(gameName);
  if (!rule) return true;
  const hasAllIncludes = rule.include.every((token) => text.includes(norm(token)));
  const hasExcluded = rule.exclude.some((token) => text.includes(norm(token)));
  return hasAllIncludes && !hasExcluded;
}

function shouldReplace(game = {}) {
  if (force) return true;
  const cover = String(game.coverImage || game.coverUrl || "");
  if (!cover) return true;
  if (cover.includes("game-placeholder.svg")) return true;
  return cover.endsWith(".svg");
}

function extFromType(type = "") {
  const v = type.toLowerCase();
  if (v.includes("png")) return ".png";
  if (v.includes("webp")) return ".webp";
  if (v.includes("jpeg") || v.includes("jpg")) return ".jpg";
  return ".jpg";
}

function toRel(abs) {
  return `./${path.relative(ROOT, abs).replaceAll("\\", "/")}`;
}

async function ddgSearch(query) {
  const first = await fetch(`https://duckduckgo.com/?q=${encodeURIComponent(query)}`, {
    headers: { "User-Agent": "Mozilla/5.0" }
  });
  if (!first.ok) return [];
  const html = await first.text();
  const m = html.match(/vqd='([^']+)'/) || html.match(/vqd="([^"]+)"/);
  if (!m?.[1]) return [];

  const apiUrl = `https://duckduckgo.com/i.js?o=json&q=${encodeURIComponent(query)}&vqd=${encodeURIComponent(
    m[1]
  )}&f=,,,&p=1`;
  const apiRes = await fetch(apiUrl, {
    headers: {
      "User-Agent": "Mozilla/5.0",
      Referer: "https://duckduckgo.com/"
    }
  });
  if (!apiRes.ok) return [];
  const json = await apiRes.json();
  return json?.results || [];
}

async function downloadImage(url, absBase) {
  const res = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const type = res.headers.get("content-type") || "";
  if (!type.startsWith("image/")) throw new Error("Not image");
  const ext = extFromType(type);
  const target = absBase.replace(/\.(png|jpg|jpeg|webp)$/i, ext);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, buf);
  return target;
}

async function resolveGameCover(game, consoleId) {
  const base = sanitize(game.imageSearchName || game.nombre || "");
  if (!base) return null;
  const consoleHints = {
    ps1: "PlayStation 1",
    ps4: "PS4",
    switch: "Nintendo Switch",
    switch2: "Nintendo Switch 2",
    "gb-color": "Game Boy Color",
    gamecube: "Nintendo GameCube",
    "xbox-360-e": "Xbox 360",
    atari: "Atari 2600"
  };
  const consoleHint = consoleHints[consoleId] || "video game";
  const query = `${base} ${consoleHint} game box cover art`;
  const results = await ddgSearch(query);
  for (const r of results.slice(0, 18)) {
    const title = r?.title || "";
    const image = r?.image || "";
    if (!image) continue;
    if (!strictMatch(base, title, image)) continue;
    return { image, title };
  }
  return null;
}

async function run() {
  let scanned = 0;
  let updated = 0;
  let skipped = 0;

  for (const [consoleId, payload] of Object.entries(data.byConsole || {})) {
    if (onlyConsole && consoleId !== onlyConsole) continue;
    if (!TARGETS.has(consoleId)) continue;
    const games = payload?.juegosCatalogo || [];

    for (const game of games) {
      scanned += 1;
      if (!shouldReplace(game)) {
        skipped += 1;
        continue;
      }

      try {
        const match = await resolveGameCover(game, consoleId);
        if (!match?.image) {
          skipped += 1;
          continue;
        }
        const baseAbs = path.join(ROOT, "assets", "game-covers", consoleId, `${game.id}.jpg`);
        const saved = await downloadImage(match.image, baseAbs);
        const rel = toRel(saved);
        game.coverImage = rel;
        game.coverUrl = rel;
        game.imageSource = "ddg-image-search";
        game.imageStatus = "found";
        game.imageSearchName = game.imageSearchName || game.nombre || "";
        updated += 1;
      } catch {
        skipped += 1;
      }
    }
  }

  fs.writeFileSync(DATA_PATH, JSON.stringify(data, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ scanned, updated, skipped, console: onlyConsole || "ps4+switch2" }, null, 2));
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
