import { Bridge } from "./bridge.js";

export function applyText(node, value) {
  node.textContent = typeof value === "string" ? value : "";
}

export function isAllowedPreviewUrl(value) {
  if (typeof value !== "string") return false;
  if (value.startsWith("blob:")) return true;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "app.vibeocr" && url.port === "";
  } catch {
    return false;
  }
}

export function bootPreview(documentRef, transport) {
  const bridge = new Bridge(transport);
  const status = documentRef.getElementById("bridge-status");
  const result = documentRef.getElementById("result-text");
  const image = documentRef.getElementById("preview-image");
  const empty = documentRef.getElementById("image-empty");

  bridge.on("preview.setState", (payload) => {
    applyText(status, payload.label);
    status.dataset.state = typeof payload.state === "string" ? payload.state : "unknown";
    return { accepted: true };
  });
  bridge.on("preview.setResult", (payload) => {
    applyText(result, payload.text);
    return { accepted: true };
  });
  bridge.on("editor.apply", (payload) => {
    applyText(result, payload.text);
    return { accepted: true };
  });
  bridge.on("preview.setImage", (payload) => {
    if (!isAllowedPreviewUrl(payload.url)) throw new TypeError("Untrusted preview URL");
    image.src = payload.url;
    image.hidden = false;
    empty.hidden = true;
    return { accepted: true };
  });
  bridge.emit("preview.ready", { capabilities: ["text", "image", "editor"] });
  return bridge;
}

if (globalThis.document && globalThis.chrome?.webview) {
  bootPreview(globalThis.document, globalThis.chrome.webview);
}
