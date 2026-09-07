import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// End-to-end proof that scripts/ai-collection-import.mjs persists a manifest exactly like the
// browser's "Carga asistida" tool would: it drives the real CLI as a child process against a
// real server/app.py instance (same GET/PUT /api/state and POST /api/media routes the browser
// calls), then reads /api/state back to confirm games, accessories, and uploaded photo URLs
// landed where web/collection-control.js would have put them. The pure merge/conflict logic
// itself is already covered by web/tests/collection-import.test.mjs; this test covers the wiring
// (HTTP round trip, file upload, dry-run vs --confirm, idempotent re-run) that file cannot.

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const cliPath = path.join(repoRoot, "scripts", "ai-collection-import.mjs");

async function freePort() {
  return new Promise((resolveWithPort, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolveWithPort(port));
    });
  });
}

async function waitForHealth(baseUrl, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 150));
  }
  throw new Error(`Server never became healthy: ${lastError?.message || "timeout"}`);
}

function runCli(args) {
  return new Promise((resolveRun) => {
    const child = spawn(process.execPath, [cliPath, ...args], { cwd: repoRoot });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code) => resolveRun({ code, stdout, stderr }));
  });
}

test("ai-collection-import.mjs: dry-run previews without writing, --confirm persists like the browser tool, re-run is idempotent", async (t) => {
  const workDir = await mkdtemp(path.join(os.tmpdir(), "consolas-ai-import-"));
  const dataDir = path.join(workDir, "data");
  const photosDir = path.join(workDir, "photos");
  const backupDir = path.join(workDir, "backups");
  await mkdir(dataDir, { recursive: true });
  await mkdir(photosDir, { recursive: true });

  await writeFile(path.join(photosDir, "console.jpg"), Buffer.from([0xff, 0xd8, 0xff, 0xdb, 0, 0, 0, 0]));
  await writeFile(path.join(photosDir, "game.jpg"), Buffer.from([0xff, 0xd8, 0xff, 0xdb, 0, 0, 0, 1]));

  const manifest = {
    schemaVersion: 1,
    importId: "cli-e2e-lote-1",
    consoleId: "ps4",
    consolePatch: {},
    games: [
      { clientRef: "g1", title: "CLI Test Game One", ownershipType: "physical" },
      { clientRef: "g2", title: "CLI Test Game Two", ownershipType: "physical" },
    ],
    accessories: [],
    photos: [
      { clientRef: "p1", fileName: "console.jpg", entityType: "console", entityId: "ps4", role: "principal" },
      { clientRef: "p2", fileName: "game.jpg", entityType: "game", entityId: "g1", role: "principal" },
    ],
  };
  const manifestPath = path.join(workDir, "manifest.json");
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2));

  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const server = spawn("python3", ["server/app.py"], {
    cwd: repoRoot,
    env: {
      ...process.env,
      CONSOLAS_HOST: "127.0.0.1",
      CONSOLAS_PORT: String(port),
      CONSOLAS_DATA_DIR: dataDir,
      CONSOLAS_STATIC_DIR: path.join(repoRoot, "web"),
    },
  });
  let serverOutput = "";
  server.stdout.on("data", (chunk) => { serverOutput += chunk; });
  server.stderr.on("data", (chunk) => { serverOutput += chunk; });
  t.after(async () => {
    server.kill("SIGTERM");
    await rm(workDir, { recursive: true, force: true });
  });

  await waitForHealth(baseUrl);

  const commonArgs = [manifestPath, "--photos-dir", photosDir, "--api-base", `${baseUrl}/api`, "--backup-dir", backupDir];

  const dryRun = await runCli(commonArgs);
  assert.equal(dryRun.code, 0, dryRun.stderr || serverOutput);
  assert.match(dryRun.stdout, /Dry-run \(sin --confirm\)/);
  assert.match(dryRun.stdout, /2 por crear/);

  const stateAfterDryRun = await (await fetch(`${baseUrl}/api/state`)).json();
  assert.equal(stateAfterDryRun.user?.detailEditsById?.ps4, undefined, "dry-run must not write state");

  const confirmed = await runCli([...commonArgs, "--confirm"]);
  assert.equal(confirmed.code, 0, confirmed.stderr || serverOutput);
  assert.match(confirmed.stdout, /Importación confirmada/);
  assert.match(confirmed.stdout, /Creadas: 2/);
  assert.match(confirmed.stdout, /Fotos persistidas: 2\/2/);

  const stateAfterConfirm = await (await fetch(`${baseUrl}/api/state`)).json();
  const bucket = stateAfterConfirm.user.detailEditsById.ps4;
  const gameIds = Object.keys(bucket.manualGamesById || {});
  assert.equal(gameIds.length, 2);
  assert.ok(bucket.fotosPropias?.length === 1, "console photo should be attached");
  assert.match(bucket.fotosPropias[0], /^\.\/media\//);
  const g1 = Object.values(bucket.manualGamesById).find((game) => game.nombre === "CLI Test Game One");
  assert.ok(g1.coverImage?.startsWith("./media/"), "game photo should set coverImage");
  assert.equal(g1.imageStatus, "manual");

  const backupFiles = await import("node:fs/promises").then((fs) => fs.readdir(backupDir));
  assert.ok(backupFiles.some((name) => name.includes("cli-e2e-lote-1")), "a pre-write backup should be saved");

  const reconfirmed = await runCli([...commonArgs, "--confirm"]);
  assert.equal(reconfirmed.code, 0, reconfirmed.stderr || serverOutput);
  assert.match(reconfirmed.stdout, /Nada que confirmar/);

  const stateAfterReconfirm = await (await fetch(`${baseUrl}/api/state`)).json();
  const gameIdsAfterReconfirm = Object.keys(stateAfterReconfirm.user.detailEditsById.ps4.manualGamesById || {});
  assert.equal(gameIdsAfterReconfirm.length, 2, "re-running an already-imported manifest must not duplicate entities");

  const mediaFiles = await readFile(path.join(dataDir, "consolas.sqlite")).catch(() => null);
  assert.ok(mediaFiles, "sqlite db should exist after a real write");
});
