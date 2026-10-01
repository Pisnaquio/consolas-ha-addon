import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SCRIPT = path.join(ROOT_DIR, "scripts", "audit-user-state.mjs");

// RELEASE.md manda auditar el archivo que devuelve `ha-consolas.mjs export`, que envuelve
// el estado en `state`. Estos tests fijan las dos propiedades que hacen que la auditoría
// sirva: que lea ese envoltorio, y que nunca informe ceros tranquilizadores sobre algo que
// no pudo leer.

const STATE = {
  version: 3,
  user: {
    overridesById: { ps1: { tengo: true } },
    additionsById: {},
    detailEditsById: {
      ps1: {
        fotosPropias: ["./media/media_abc.jpg", "./assets/photos/no-existe.png"],
        gameEditsById: { "crash-bandicoot": { ownershipType: "physical" } },
        manualGamesById: {},
        manualAccessoriesById: {
          "mando-1": { image: "./media/media_def.jpeg" }
        }
      }
    }
  },
  meta: { storageBackend: "server" }
};

async function withFiles(run) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "consolas-audit-"));
  const write = async (name, value) => {
    const file = path.join(dir, name);
    await writeFile(file, JSON.stringify(value, null, 2));
    return file;
  };
  try {
    return await run(write);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

function runAudit(file) {
  return execFileAsync(process.execPath, [SCRIPT, file]);
}

test("un export envuelto se audita igual que el estado pelado", async () => {
  await withFiles(async (write) => {
    const plain = await write("plain.json", STATE);
    const wrapped = await write("export.json", {
      exportedAt: "2026-09-18T00:00:00.000Z",
      app: "consolas",
      version: 3,
      source: "server",
      state: STATE
    });

    const { stdout: plainOut } = await runAudit(plain);
    const { stdout: wrappedOut } = await runAudit(wrapped);

    const resumen = (output) => output.slice(output.indexOf("Resumen"), output.indexOf("Consolas marcadas"));
    assert.equal(resumen(wrappedOut), resumen(plainOut));
    assert.match(wrappedOut, /- overrides: 1/);
    assert.match(wrappedOut, /- gameEdits: 1/);
    assert.match(wrappedOut, /Export: consolas, source=server/);
    assert.doesNotMatch(plainOut, /^Export:/m);
  });
});

test("un archivo que no es estado falla en vez de informar ceros", async () => {
  await withFiles(async (write) => {
    const junk = await write("junk.json", { hola: "mundo" });
    await assert.rejects(
      runAudit(junk),
      (error) => {
        assert.equal(error.code, 1);
        assert.match(error.stderr, /No parece un estado de usuario ni un export/);
        assert.doesNotMatch(String(error.stdout), /overrides: 0/);
        return true;
      }
    );
  });
});

test("las referencias a /data/media se declaran aparte y no cuentan como faltantes", async () => {
  await withFiles(async (write) => {
    const file = await write("state.json", STATE);
    const { stdout } = await runAudit(file);

    // Dos `./media/...` (una foto y una imagen de accesorio) viven en el add-on, no acá.
    assert.match(stdout, /referencias server-side \(\/data\/media del add-on\), no verificables desde este checkout: 2/);
    assert.match(stdout, /2 referencias apuntan a \/data\/media/);

    // La ruta local inventada sí es un faltante real: la clasificación no tapa rutas rotas.
    assert.match(stdout, /- console photos faltantes: 1/);
    assert.match(stdout, /- manual accessory images faltantes: 0/);
  });
});
