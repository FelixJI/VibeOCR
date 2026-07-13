import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Bridge, ProtocolError, validateEnvelope } from "../../src/dotnet/VibeOCR.App/WebAssets/src/bridge.js";
import { applyText, isAllowedPreviewUrl } from "../../src/dotnet/VibeOCR.App/WebAssets/src/preview.js";

class FakeTransport {
  constructor() {
    this.messages = [];
    this.listener = null;
  }
  addEventListener(type, listener) {
    assert.equal(type, "message");
    this.listener = listener;
  }
  postMessage(message) {
    this.messages.push(message);
  }
  receive(message) {
    this.listener({ data: message });
  }
}

test("rejects unknown versions, fields, types, and oversized messages", () => {
  const base = {
    version: 1,
    kind: "event",
    id: crypto.randomUUID(),
    type: "preview.setState",
    payload: {},
  };
  assert.throws(() => validateEnvelope({ ...base, version: 2 }), ProtocolError);
  assert.throws(() => validateEnvelope({ ...base, extra: true }), ProtocolError);
  assert.throws(() => validateEnvelope({ ...base, type: "unknown" }), ProtocolError);
  assert.throws(() => validateEnvelope({ ...base, payload: { value: "x".repeat(512) } }, "host", 128), ProtocolError);
});

test("correlates web requests with host responses", async () => {
  const transport = new FakeTransport();
  const bridge = new Bridge(transport);
  const pending = bridge.request("action.copy", { format: "text" });
  const request = transport.messages[0];
  transport.receive({
    version: 1,
    kind: "response",
    id: request.id,
    type: request.type,
    payload: { copied: true },
  });
  assert.deepEqual(await pending, { copied: true });
});

test("handles typed host requests and returns correlated response", async () => {
  const transport = new FakeTransport();
  const bridge = new Bridge(transport);
  bridge.on("preview.setState", (payload) => ({ state: payload.state }));
  const id = crypto.randomUUID();
  transport.receive({
    version: 1,
    kind: "request",
    id,
    type: "preview.setState",
    payload: { state: "ready" },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(transport.messages[0], {
    version: 1,
    kind: "response",
    id,
    type: "preview.setState",
    payload: { state: "ready" },
  });
});

test("renders hostile text as text and blocks data or remote image URLs", () => {
  const node = { textContent: "", innerHTML: "sentinel" };
  const attack = "<img src=x onerror=alert(1)>";
  applyText(node, attack);
  assert.equal(node.textContent, attack);
  assert.equal(node.innerHTML, "sentinel");
  assert.equal(isAllowedPreviewUrl("data:image/png;base64,AAAA"), false);
  assert.equal(isAllowedPreviewUrl("https://evil.example/a.png"), false);
  assert.equal(isAllowedPreviewUrl("https://app.vibeocr/assets/a.png"), true);
  assert.equal(isAllowedPreviewUrl("blob:https://app.vibeocr/id"), true);
});

test("packaged page has strict CSP and no inline or remote scripts", async () => {
  const html = await readFile(new URL("../../src/dotnet/VibeOCR.App/WebAssets/index.html", import.meta.url), "utf8");
  assert.match(html, /default-src 'none'/);
  assert.match(html, /script-src 'self'/);
  assert.match(html, /connect-src 'none'/);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i);
  assert.doesNotMatch(html, /https:\/\/(?!app\.vibeocr)/i);
});
