const VERSION = 1;
const MAX_BYTES = 64 * 1024;
const FIELDS = new Set(["version", "kind", "id", "type", "payload"]);
const HOST_TYPES = new Set([
  "preview.setState",
  "preview.setImage",
  "preview.setResult",
  "editor.apply",
]);
const WEB_TYPES = new Set([
  "preview.ready",
  "editor.changed",
  "selection.changed",
  "action.copy",
]);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export class ProtocolError extends Error {}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateEnvelope(value, direction = "host", maxBytes = MAX_BYTES) {
  if (!isPlainObject(value)) throw new ProtocolError("Message must be an object");
  const keys = Object.keys(value);
  if (keys.length !== FIELDS.size || keys.some((key) => !FIELDS.has(key))) {
    throw new ProtocolError("Message fields are invalid");
  }
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > maxBytes) {
    throw new ProtocolError("Message exceeds size limit");
  }
  if (value.version !== VERSION || !["request", "response", "event"].includes(value.kind)) {
    throw new ProtocolError("Message version or kind is invalid");
  }
  if (typeof value.id !== "string" || !UUID_V4.test(value.id) || !isPlainObject(value.payload)) {
    throw new ProtocolError("Message id or payload is invalid");
  }
  const allowed = direction === "host" ? HOST_TYPES : WEB_TYPES;
  if (value.kind !== "response" && !allowed.has(value.type)) {
    throw new ProtocolError("Unknown message type");
  }
  return Object.freeze({ ...value, payload: Object.freeze({ ...value.payload }) });
}

export class Bridge {
  #transport;
  #pending = new Map();
  #handlers = new Map();
  #maxBytes;

  constructor(transport, { maxBytes = MAX_BYTES } = {}) {
    if (!transport || typeof transport.postMessage !== "function" ||
        typeof transport.addEventListener !== "function") {
      throw new TypeError("A WebView transport is required");
    }
    this.#transport = transport;
    this.#maxBytes = maxBytes;
    transport.addEventListener("message", (event) => this.#receive(event.data));
  }

  on(type, handler) {
    if (!HOST_TYPES.has(type) || typeof handler !== "function") {
      throw new ProtocolError("Unknown host request handler");
    }
    this.#handlers.set(type, handler);
  }

  emit(type, payload = {}) {
    if (!WEB_TYPES.has(type) || type === "action.copy") {
      throw new ProtocolError("Unknown Web event type");
    }
    this.#post({ version: VERSION, kind: "event", id: crypto.randomUUID(), type, payload });
  }

  request(type, payload = {}, timeoutMs = 5000) {
    if (type !== "action.copy") throw new ProtocolError("Unknown Web request type");
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new ProtocolError("Bridge request timed out"));
      }, timeoutMs);
      this.#pending.set(id, { type, resolve, reject, timer });
      this.#post({ version: VERSION, kind: "request", id, type, payload });
    });
  }

  async #receive(raw) {
    const message = validateEnvelope(raw, "host", this.#maxBytes);
    if (message.kind === "response") {
      const pending = this.#pending.get(message.id);
      if (!pending || pending.type !== message.type) throw new ProtocolError("Unsolicited response");
      clearTimeout(pending.timer);
      this.#pending.delete(message.id);
      pending.resolve(message.payload);
      return;
    }
    const handler = this.#handlers.get(message.type);
    if (!handler) throw new ProtocolError("Unhandled host message");
    const payload = await handler(message.payload);
    if (message.kind === "request") {
      this.#post({
        version: VERSION,
        kind: "response",
        id: message.id,
        type: message.type,
        payload: payload ?? {},
      });
    }
  }

  #post(message) {
    validateEnvelope(message, "web", this.#maxBytes);
    this.#transport.postMessage(message);
  }
}
