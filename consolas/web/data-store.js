(() => {
  const ROOT_KEY = "consolas.appState.v2";
  const LEGACY_OVERRIDES_KEY = "consolas.personalOverrides.v1";
  const LEGACY_ADDITIONS_KEY = "consolas.personalAdditions.v1";
  const LEGACY_DETAIL_EDITS_KEY = "consolas.consoleDetailEdits.v1";
  const DB_NAME = "consolas-app-db";
  const DB_VERSION = 1;
  const STORE_NAME = "app_state";
  const STATE_RECORD_KEY = "root";
  const REMOTE_API_BASE = window.CONSOLAS_API_BASE || "./api";
  const BASE_TO_PERSONAL_ID = Object.freeze({
    "sony-playstation": "ps1",
    "sony-playstation-4": "ps4",
    "sony-playstation-5": "ps5",
    "sega-dreamcast": "dreamcast",
    "sega-genesis-mega-drive": "genesis",
    "nintendo-gamecube": "gamecube",
    "nintendo-wii": "wii",
    "nintendo-switch": "switch",
    "nintendo-switch-2": "switch2",
    "game-boy-advance": "gba-sp",
    "game-boy-color": "gb-color",
    "game-boy": "gb-original",
    "nintendo-ds": "ds-lite",
    "nintendo-64": "n64",
    "super-nintendo-snes": "snes",
    "nintendo-nes-famicom": "nes-clonica",
    "atari-2600": "atari",
    "microsoft-xbox-360": "xbox-360-e"
  });

  function parseJson(raw, fallback) {
    try {
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch {
      return fallback;
    }
  }

  function createDefaultState() {
    return {
      version: 3,
      user: {
        overridesById: {},
        additionsById: {},
        detailEditsById: {}
      },
      meta: {
        migratedLegacy: false,
        storageBackend: "local-bootstrap",
        updatedAt: new Date().toISOString()
      }
    };
  }

  function normalizeState(rawState) {
    const base = createDefaultState();
    const state = rawState && typeof rawState === "object" ? rawState : {};
    return {
      version: 3,
      user: {
        overridesById:
          state.user && typeof state.user.overridesById === "object" ? state.user.overridesById : base.user.overridesById,
        additionsById:
          state.user && typeof state.user.additionsById === "object" ? state.user.additionsById : base.user.additionsById,
        detailEditsById:
          state.user && typeof state.user.detailEditsById === "object"
            ? state.user.detailEditsById
            : base.user.detailEditsById
      },
      meta: {
        ...(base.meta || {}),
        ...(state.meta || {})
      }
    };
  }

  function readLegacyState() {
    return {
      overridesById: parseJson(localStorage.getItem(LEGACY_OVERRIDES_KEY), {}),
      additionsById: parseJson(localStorage.getItem(LEGACY_ADDITIONS_KEY), {}),
      detailEditsById: parseJson(localStorage.getItem(LEGACY_DETAIL_EDITS_KEY), {})
    };
  }

  function mergeLegacyIntoState(rawState) {
    const state = normalizeState(rawState);
    if (state.meta.migratedLegacy === true) return state;
    const legacy = readLegacyState();
    state.user.overridesById = {
      ...legacy.overridesById,
      ...state.user.overridesById
    };
    state.user.additionsById = {
      ...legacy.additionsById,
      ...state.user.additionsById
    };
    state.user.detailEditsById = {
      ...legacy.detailEditsById,
      ...state.user.detailEditsById
    };
    state.meta.migratedLegacy = true;
    return state;
  }

  function readLocalShadowState() {
    return mergeLegacyIntoState(parseJson(localStorage.getItem(ROOT_KEY), null));
  }

  function updatedAtMs(state) {
    const value = Date.parse(state?.meta?.updatedAt || "");
    return Number.isFinite(value) ? value : 0;
  }

  function hasUserData(state) {
    const user = state?.user || {};
    return (
      Object.keys(user.overridesById || {}).length > 0 ||
      Object.keys(user.additionsById || {}).length > 0 ||
      Object.keys(user.detailEditsById || {}).length > 0
    );
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  let memoryState = readLocalShadowState();
  let remoteEnabled = false;

  function writeLocalShadow(state) {
    localStorage.setItem(ROOT_KEY, JSON.stringify(state));
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      if (!("indexedDB" in window)) {
        reject(new Error("IndexedDB no disponible"));
        return;
      }
      const request = window.indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("No se pudo abrir IndexedDB"));
    });
  }

  async function readIndexedDbState(db) {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const request = store.get(STATE_RECORD_KEY);
      request.onsuccess = () => resolve(request.result ? normalizeState(request.result) : null);
      request.onerror = () => reject(request.error || new Error("No se pudo leer IndexedDB"));
    });
  }

  async function writeIndexedDbState(db, state) {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      const request = store.put(clone(state), STATE_RECORD_KEY);
      request.onsuccess = () => resolve(state);
      request.onerror = () => reject(request.error || new Error("No se pudo escribir IndexedDB"));
    });
  }

  let dbRef = null;
  let lastPersistPromise = Promise.resolve();

  async function loadRuntimeBootstrap() {
    try {
      const response = await fetch("./runtime/user-bootstrap.json", { cache: "no-store" });
      if (!response.ok) return null;
      const payload = await response.json();
      return normalizeState(payload);
    } catch {
      return null;
    }
  }

  async function readRemoteState() {
    const response = await fetch(`${REMOTE_API_BASE}/state`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      throw new Error(`Backend state unavailable (${response.status})`);
    }
    return normalizeState(await response.json());
  }

  async function writeRemoteState(state) {
    const response = await fetch(`${REMOTE_API_BASE}/state`, {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(normalizeState(state))
    });
    if (!response.ok) {
      throw new Error(`Backend write failed (${response.status})`);
    }
    return normalizeState(await response.json());
  }

  async function exportRemoteState() {
    const response = await fetch(`${REMOTE_API_BASE}/state/export`, {
      cache: "no-store",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) {
      throw new Error(`Backend export failed (${response.status})`);
    }
    return await response.json();
  }

  function scheduleIndexedDbWrite(state) {
    if (!dbRef) return Promise.resolve(state);
    lastPersistPromise = lastPersistPromise
      .catch(() => undefined)
      .then(() => writeIndexedDbState(dbRef, state))
      .catch((error) => {
        console.warn("[DataStore] Falló persistencia en IndexedDB, se mantiene shadow local.", error);
        return state;
      });
    return lastPersistPromise;
  }

  let lastRemotePersistPromise = Promise.resolve();

  function scheduleRemoteWrite(state) {
    if (!remoteEnabled) return Promise.resolve(state);
    const snapshot = clone(state);
    lastRemotePersistPromise = lastRemotePersistPromise
      .catch(() => undefined)
      .then(() => writeRemoteState(snapshot))
      .catch((error) => {
        console.warn("[DataStore] Falló persistencia en backend, se mantiene cache local.", error);
        return snapshot;
      });
    return lastRemotePersistPromise;
  }

  async function initializeLocalState() {
    return openDb()
      .then(async (db) => {
        dbRef = db;
        const indexedState = await readIndexedDbState(db);
        if (indexedState && updatedAtMs(indexedState) > updatedAtMs(memoryState)) {
          memoryState = normalizeState(indexedState);
          memoryState.meta.storageBackend = "indexeddb";
          writeLocalShadow(memoryState);
        } else {
          memoryState.meta.storageBackend = "indexeddb";
          writeLocalShadow(memoryState);
          await writeIndexedDbState(db, memoryState);
        }

        if (!hasUserData(memoryState) && !memoryState.meta.bootstrapImportedAt) {
          const bootstrapState = await loadRuntimeBootstrap();
          if (bootstrapState && hasUserData(bootstrapState)) {
            memoryState = normalizeState(bootstrapState);
            memoryState.meta.storageBackend = "indexeddb";
            memoryState.meta.bootstrapImportedAt = new Date().toISOString();
            writeLocalShadow(memoryState);
            await writeIndexedDbState(db, memoryState);
          }
        }
        return memoryState;
      })
      .catch((error) => {
        console.warn("[DataStore] IndexedDB no disponible, se usa localStorage shadow.", error);
        memoryState.meta.storageBackend = "local-fallback";
        if (!hasUserData(memoryState) && !memoryState.meta.bootstrapImportedAt) {
          return loadRuntimeBootstrap().then((bootstrapState) => {
            if (bootstrapState && hasUserData(bootstrapState)) {
              memoryState = normalizeState(bootstrapState);
              memoryState.meta.storageBackend = "local-fallback";
              memoryState.meta.bootstrapImportedAt = new Date().toISOString();
            }
            writeLocalShadow(memoryState);
            return memoryState;
          });
        }
        writeLocalShadow(memoryState);
        return memoryState;
      });
  }

  async function initializeState() {
    const localState = await initializeLocalState();

    try {
      const remoteState = await readRemoteState();
      remoteEnabled = true;

      if (!hasUserData(remoteState) && hasUserData(localState)) {
        const seedState = normalizeState(localState);
        seedState.meta.storageBackend = "server";
        seedState.meta.serverSeededAt = new Date().toISOString();
        memoryState = await writeRemoteState(seedState);
      } else {
        memoryState = normalizeState(remoteState);
        memoryState.meta.storageBackend = "server";
      }

      writeLocalShadow(memoryState);
      if (dbRef) {
        await writeIndexedDbState(dbRef, memoryState);
      }
      return memoryState;
    } catch (error) {
      remoteEnabled = false;
      console.info("[DataStore] Backend no disponible, se usa persistencia local.", error);
      return localState;
    }
  }

  const ready = initializeState();

  function persist(nextState) {
    const normalized = normalizeState(nextState);
    normalized.meta.updatedAt = new Date().toISOString();
    normalized.meta.storageBackend = remoteEnabled ? "server" : dbRef ? "indexeddb" : normalized.meta.storageBackend || "local-fallback";
    memoryState = normalized;
    writeLocalShadow(memoryState);
    scheduleIndexedDbWrite(memoryState);
    scheduleRemoteWrite(memoryState);
    return memoryState;
  }

  async function persistAndWait(nextState) {
    const normalized = normalizeState(nextState);
    normalized.meta.updatedAt = new Date().toISOString();
    normalized.meta.storageBackend = remoteEnabled ? "server" : dbRef ? "indexeddb" : normalized.meta.storageBackend || "local-fallback";
    memoryState = normalized;
    writeLocalShadow(memoryState);
    await scheduleIndexedDbWrite(memoryState);
    if (remoteEnabled) {
      memoryState = await writeRemoteState(memoryState);
      memoryState.meta.storageBackend = "server";
      writeLocalShadow(memoryState);
      if (dbRef) await writeIndexedDbState(dbRef, memoryState);
    }
    return memoryState;
  }

  function readState() {
    return memoryState;
  }

  function transaction(mutator) {
    const current = readState();
    const cloned = clone(current);
    const result = mutator(cloned);
    return persist(result || cloned);
  }

  const DataStore = {
    ready,
    getState() {
      return readState();
    },
    setState(nextState) {
      return persist(nextState);
    },
    persistAndWait,
    async exportStateBackup() {
      if (remoteEnabled) {
        try {
          return await exportRemoteState();
        } catch (error) {
          console.warn("[DataStore] Falló export server-side, se usa export local.", error);
        }
      }
      return {
        exportedAt: new Date().toISOString(),
        app: "consolas",
        version: readState().version || 3,
        source: remoteEnabled ? "server-fallback-local" : "local",
        state: readState()
      };
    },
    transaction,

    getOverrides() {
      return { ...(readState().user.overridesById || {}) };
    },
    setOverrides(nextOverrides) {
      return transaction((state) => {
        state.user.overridesById = nextOverrides && typeof nextOverrides === "object" ? nextOverrides : {};
      });
    },
    updateOverride(id, patch) {
      return transaction((state) => {
        if (!id) return;
        state.user.overridesById[id] = {
          ...(state.user.overridesById[id] || {}),
          ...(patch || {})
        };
      });
    },
    removeOverride(id) {
      return transaction((state) => {
        if (!id) return;
        delete state.user.overridesById[id];
      });
    },

    getAdditionsMap() {
      return { ...(readState().user.additionsById || {}) };
    },
    setAdditionsMap(nextAdditionsMap) {
      return transaction((state) => {
        state.user.additionsById = nextAdditionsMap && typeof nextAdditionsMap === "object" ? nextAdditionsMap : {};
      });
    },

    getDetailEdits() {
      return { ...(readState().user.detailEditsById || {}) };
    },
    setDetailEdits(nextEdits) {
      return transaction((state) => {
        state.user.detailEditsById = nextEdits && typeof nextEdits === "object" ? nextEdits : {};
      });
    },
    updateDetailEdit(consoleId, patch) {
      return transaction((state) => {
        if (!consoleId) return;
        state.user.detailEditsById[consoleId] = {
          ...(state.user.detailEditsById[consoleId] || {}),
          ...(patch || {})
        };
      });
    },
    replaceDetailEdit(consoleId, nextValue) {
      return transaction((state) => {
        if (!consoleId) return;
        if (!nextValue || typeof nextValue !== "object") {
          delete state.user.detailEditsById[consoleId];
          return;
        }
        state.user.detailEditsById[consoleId] = { ...nextValue };
      });
    },

    getPersonalIdForBase(baseId) {
      return BASE_TO_PERSONAL_ID[baseId] || `base-${baseId}`;
    },

    getStorageBackend() {
      return readState().meta.storageBackend || (remoteEnabled ? "server" : "local");
    },

    isRemoteEnabled() {
      return remoteEnabled === true;
    }
  };

  window.DataStore = DataStore;
})();
