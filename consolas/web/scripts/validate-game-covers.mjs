#!/usr/bin/env node
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_PATH = path.join(ROOT, "data/console-games.json");
const FALLBACK = "./assets/photos/game-placeholder.svg";

const data = JSON.parse(fs.readFileSync(DATA_PATH, "utf8"));
const issues = [];
let total = 0;

for (const [consoleId, payload] of Object.entries(data.byConsole || {})) {
  const games = payload?.juegosCatalogo || [];
  for (const game of games) {
    total += 1;
    const ref = `${consoleId}:${game.id}`;
    const cover = game.coverImage || "";
    const imageSource = game.imageSource || "";
    const imageStatus = game.imageStatus || "";
    const imageSearchName = game.imageSearchName || "";

    if (!cover || !String(cover).trim()) issues.push(`${ref} -> coverImage vacío`);
    if (!imageSource || !String(imageSource).trim()) issues.push(`${ref} -> imageSource vacío`);
    if (!imageStatus || !String(imageStatus).trim()) issues.push(`${ref} -> imageStatus vacío`);
    if (!imageSearchName || !String(imageSearchName).trim()) issues.push(`${ref} -> imageSearchName vacío`);

    if (imageStatus === "placeholder" && cover !== FALLBACK) {
      issues.push(`${ref} -> imageStatus=placeholder pero coverImage no es fallback`);
    }

    if (cover && cover.startsWith("./")) {
      const abs = path.join(ROOT, cover.replace("./", ""));
      if (!fs.existsSync(abs)) issues.push(`${ref} -> archivo cover no existe: ${cover}`);
    }
  }
}

if (issues.length) {
  console.error(`Validation FAILED. ${issues.length} issue(s) en ${total} juegos.`);
  issues.slice(0, 200).forEach((issue) => console.error(`- ${issue}`));
  process.exit(1);
}

console.log(`Validation OK. ${total} juegos con imagen/fallback y metadatos completos.`);
