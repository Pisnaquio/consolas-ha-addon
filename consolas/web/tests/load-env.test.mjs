import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { loadLocalEnv } from "../../scripts/lib/load-env.mjs";

// consolas.env carries the Home Assistant token that lets any agent in this checkout update
// the app. These tests pin the two properties that keep that safe: a variable already in the
// environment is never overridden (so CI secrets win over a stale local file), and the loader
// reports key names only, never values.

async function withEnvFile(contents, run) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "consolas-env-"));
  const file = path.join(dir, "consolas.env");
  await writeFile(file, contents);
  try {
    return await run(file);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("carga pares clave=valor e ignora comentarios y líneas vacías", async () => {
  await withEnvFile("# comentario\n\nCONSOLAS_TEST_A=uno\nexport CONSOLAS_TEST_B=\"dos\"\n", async (file) => {
    delete process.env.CONSOLAS_TEST_A;
    delete process.env.CONSOLAS_TEST_B;
    const result = loadLocalEnv({ file });
    assert.equal(result.loaded, true);
    assert.deepEqual([...result.keys].sort(), ["CONSOLAS_TEST_A", "CONSOLAS_TEST_B"]);
    assert.equal(process.env.CONSOLAS_TEST_A, "uno");
    assert.equal(process.env.CONSOLAS_TEST_B, "dos");
    delete process.env.CONSOLAS_TEST_A;
    delete process.env.CONSOLAS_TEST_B;
  });
});

test("una variable ya presente en el entorno gana sobre el archivo", async () => {
  await withEnvFile("CONSOLAS_TEST_C=del-archivo\n", async (file) => {
    process.env.CONSOLAS_TEST_C = "del-entorno";
    const result = loadLocalEnv({ file });
    assert.equal(process.env.CONSOLAS_TEST_C, "del-entorno");
    assert.equal(result.keys.includes("CONSOLAS_TEST_C"), false);
    delete process.env.CONSOLAS_TEST_C;
  });
});

test("no revienta si el archivo no existe", () => {
  const result = loadLocalEnv({ file: path.join(os.tmpdir(), "no-existe-consolas.env") });
  assert.deepEqual(result, { loaded: false, keys: [] });
});

test("descarta nombres de variable inválidos", async () => {
  await withEnvFile("no-es-valido=x\n123=y\nCONSOLAS_TEST_D=ok\n", async (file) => {
    delete process.env.CONSOLAS_TEST_D;
    const result = loadLocalEnv({ file });
    assert.deepEqual(result.keys, ["CONSOLAS_TEST_D"]);
    delete process.env.CONSOLAS_TEST_D;
  });
});
