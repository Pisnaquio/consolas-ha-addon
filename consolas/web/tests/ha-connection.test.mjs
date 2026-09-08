import assert from "node:assert/strict";
import test from "node:test";
import { supervisorRequest, ingressFetch, websocketUrl, DEFAULT_ADDON_SLUG, unwrap } from "../../scripts/lib/ha-connection.mjs";

// Mocks the Supervisor WebSocket handshake (auth_required -> auth -> auth_ok -> supervisor/api
// request -> id:1 response) so this file can run without a real Home Assistant instance or
// token, while still verifying scripts/ha-consolas.mjs and scripts/ai-collection-import.mjs get
// the exact protocol behavior they depend on from this shared module: the timeout:null
// passthrough, Supervisor error detail, auth_invalid handling, and the Ingress session chain.
class FakeSocket {
  constructor(url, script) {
    this.url = url;
    this.script = script;
    this.listeners = {};
    queueMicrotask(() => this.emit("message", { data: JSON.stringify({ type: "auth_required" }) }));
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  emit(type, event) { (this.listeners[type] || []).forEach((handler) => handler(event)); }
  close() { this.emit("close", { code: 1000, reason: "closed by test" }); }
  send(raw) {
    const message = JSON.parse(raw);
    if (message.type === "auth") {
      queueMicrotask(() => this.emit("message", { data: JSON.stringify(this.script.auth ?? { type: "auth_ok" }) }));
    } else if (message.type === "supervisor/api") {
      queueMicrotask(() => this.emit("message", { data: JSON.stringify(this.script.reply(message)) }));
    }
  }
}

function withFakeSocket(script, run) {
  const original = globalThis.WebSocket;
  globalThis.WebSocket = class extends FakeSocket { constructor(url) { super(url, script); } };
  return Promise.resolve(run()).finally(() => { globalThis.WebSocket = original; });
}

test("resolves the Supervisor result and forwards timeout:null", async () => {
  await withFakeSocket({
    reply: (message) => {
      assert.equal(message.method, "get");
      assert.equal(message.endpoint, "/addons/x/info");
      assert.equal(message.timeout, null);
      return { id: 1, success: true, result: { data: { slug: "x", state: "started" } } };
    },
  }, async () => {
    const result = await supervisorRequest({ token: "t", wsUrl: "wss://x", method: "get", endpoint: "/addons/x/info", timeout: null });
    assert.deepEqual(result, { data: { slug: "x", state: "started" } });
  });
});

test("a Supervisor error surfaces its code and message", async () => {
  await withFakeSocket({
    reply: () => ({ id: 1, success: false, error: { code: "not_found", message: "nope" } }),
  }, async () => {
    await assert.rejects(
      supervisorRequest({ token: "t", wsUrl: "wss://x", method: "post", endpoint: "/y" }),
      /Supervisor rejected POST \/y \(not_found: nope\)/,
    );
  });
});

test("auth_invalid is reported as a token problem, not a generic socket close", async () => {
  await withFakeSocket({
    auth: { type: "auth_invalid", message: "bad token" },
    reply: () => { throw new Error("should not reach a request"); },
  }, async () => {
    await assert.rejects(
      supervisorRequest({ token: "bad", wsUrl: "wss://x", method: "get", endpoint: "/z" }),
      /rejected the token \(bad token\); check HOMEASSISTANT_TOKEN/,
    );
  });
});

test("ingressFetch chains addon info -> session -> same-origin fetch with the session cookie", async () => {
  await withFakeSocket({
    reply: (message) => {
      if (message.endpoint.endsWith("/info")) return { id: 1, success: true, result: { data: { ingress_url: "/api/hassio_ingress/abc/" } } };
      if (message.endpoint === "/ingress/session") return { id: 1, success: true, result: { data: { session: "sess-123" } } };
      throw new Error(`unexpected endpoint ${message.endpoint}`);
    },
  }, async () => {
    let capturedRequest;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => { capturedRequest = { url, options }; return { ok: true, status: 200 }; };
    try {
      const { endpoint, response } = await ingressFetch(
        { token: "t", wsUrl: websocketUrl("https://ha.example"), haUrl: "https://ha.example", addonSlug: DEFAULT_ADDON_SLUG },
        "api/health",
      );
      assert.equal(endpoint, "https://ha.example/api/hassio_ingress/abc/api/health");
      assert.equal(capturedRequest.url, endpoint);
      assert.equal(capturedRequest.options.headers.get("Cookie"), "ingress_session=sess-123");
      assert.equal(response.ok, true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

test("unwrap acepta tanto {data:...} como la respuesta pelada del Supervisor", () => {
  assert.deepEqual(unwrap({ data: { slug: "x" } }), { slug: "x" });
  assert.deepEqual(unwrap({ slug: "y" }), { slug: "y" });
  assert.deepEqual(unwrap(null), {});
});
