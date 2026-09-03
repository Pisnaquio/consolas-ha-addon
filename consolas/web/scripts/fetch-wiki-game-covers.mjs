#!/usr/bin/env node
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_PATH = path.join(ROOT, "data/console-games.json");
const TARGETS = new Set(["ps1", "ps4", "switch2", "gb-color", "gamecube", "xbox-360-e", "atari"]);

const args = process.argv.slice(2);
const onlyConsole = args.includes("--console") ? args[args.indexOf("--console") + 1] : null;
const force = args.includes("--force");

const data = JSON.parse(fs.readFileSync(DATA_PATH, "utf8"));

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

function toRel(abs) {
  return `./${path.relative(ROOT, abs).replaceAll("\\", "/")}`;
}

function shouldReplace(game = {}) {
  if (force) return true;
  const cover = String(game.coverImage || game.coverUrl || "");
  if (!cover) return true;
  if (cover.includes("game-placeholder.svg")) return true;
  if (cover.endsWith(".svg")) return true;
  return false;
}

function extFromContentType(type = "") {
  const v = type.toLowerCase();
  if (v.includes("png")) return ".png";
  if (v.includes("webp")) return ".webp";
  if (v.includes("jpeg") || v.includes("jpg")) return ".jpg";
  return ".jpg";
}

async function searchWikipedia(query) {
  const url = `https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(
    query
  )}&utf8=&format=json`;
  const res = await fetch(url, { headers: { "User-Agent": "console-catalog/1.0" } });
  if (!res.ok) return [];
  const json = await res.json();
  return json?.query?.search || [];
}

async function pageSummary(title) {
  const url = `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`;
  const res = await fetch(url, { headers: { "User-Agent": "console-catalog/1.0" } });
  if (!res.ok) return null;
  const json = await res.json();
  return json;
}

function looksReasonableMatch(gameName, pageTitle) {
  const a = norm(sanitize(gameName));
  const b = norm(pageTitle);
  if (!a || !b) return false;
  const tokens = a.split(" ").filter((t) => t.length > 2).slice(0, 6);
  const hits = tokens.filter((t) => b.includes(t)).length;
  return hits >= Math.max(1, Math.ceil(tokens.length * 0.34));
}

async function resolveWikiCover(game) {
  const base = sanitize(game.imageSearchName || game.nombre || "");
  if (!base) return null;

  const titleCandidates = [
    game.nombre,
    `${game.nombre || ""} (video game)`.trim(),
    base.replace(/\bxbox 360\b/gi, "").trim()
  ].filter(Boolean);
  for (const title of titleCandidates) {
    const summary = await pageSummary(title);
    const thumb = summary?.thumbnail?.source;
    if (thumb && looksReasonableMatch(game.nombre || base, summary?.title || title)) {
      return { title: summary?.title || title, url: thumb };
    }
  }

  const queries = [base, `${base} video game`];
  for (const query of queries) {
    const results = await searchWikipedia(query);
    for (const hit of results.slice(0, 6)) {
      if (!looksReasonableMatch(base, hit.title || "")) continue;
      const summary = await pageSummary(hit.title);
      const thumb = summary?.thumbnail?.source;
      if (thumb) {
        return { title: hit.title, url: thumb };
      }
    }
  }
  return null;
}

async function downloadImage(url, absPath) {
  const res = await fetch(url, { headers: { "User-Agent": "console-catalog/1.0" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const type = res.headers.get("content-type") || "";
  const ext = extFromContentType(type);
  const target = absPath.replace(/\.(png|jpg|jpeg|webp)$/i, ext);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, buf);
  return target;
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
        const found = await resolveWikiCover(game);
        if (!found?.url) {
          skipped += 1;
          continue;
        }
        const baseAbs = path.join(ROOT, "assets", "game-covers", consoleId, `${game.id}.jpg`);
        const finalAbs = await downloadImage(found.url, baseAbs);
        const rel = toRel(finalAbs);
        game.coverImage = rel;
        game.coverUrl = rel;
        game.imageSource = "wikipedia";
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
