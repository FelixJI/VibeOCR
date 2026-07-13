import { Bridge } from "./bridge.js";
import { CommandStack } from "./editor/command-stack.js";
import { createAnnotation, createEditorState, drawEditor, rotateEditorState } from "./editor/canvas.js";
import { BBOX_NORM, clampRect, hitTest, rectFromPoints, translateRect } from "./editor/geometry.js";
import { buildResultModel, markdownToModel, renderResult } from "./result/renderer.js";

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
  const canvas = documentRef.getElementById("editor-canvas");
  const undo = documentRef.getElementById("editor-undo");
  const redo = documentRef.getElementById("editor-redo");
  const stack = new CommandStack();
  let editorState = createEditorState();
  let tool = "select";
  let gesture = null;

  const canvasPoint = (event) => {
    const bounds = canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(BBOX_NORM, (event.clientX - bounds.left) * BBOX_NORM / bounds.width)),
      Math.max(0, Math.min(BBOX_NORM, (event.clientY - bounds.top) * BBOX_NORM / bounds.height)),
    ];
  };

  const publishEditor = () => {
    drawEditor(canvas, editorState);
    undo.disabled = !stack.canUndo;
    redo.disabled = !stack.canRedo;
    bridge.emit("editor.changed", { state: editorState, history: stack.serialize() });
  };

  bridge.on("preview.setState", (payload) => {
    applyText(status, payload.label);
    status.dataset.state = typeof payload.state === "string" ? payload.state : "unknown";
    return { accepted: true };
  });
  bridge.on("preview.setResult", (payload) => {
    const model = payload.format === "markdown"
      ? markdownToModel(payload.text)
      : buildResultModel(payload.result ?? { text: payload.text });
    renderResult(result, model, documentRef);
    return { accepted: true };
  });
  bridge.on("editor.apply", (payload) => {
    if (payload.state && typeof payload.state === "object") editorState = structuredClone(payload.state);
    drawEditor(canvas, editorState);
    return { accepted: true };
  });
  bridge.on("preview.setImage", (payload) => {
    if (!isAllowedPreviewUrl(payload.url)) throw new TypeError("Untrusted preview URL");
    image.src = payload.url;
    image.hidden = false;
    empty.hidden = true;
    return { accepted: true };
  });
  documentRef.querySelectorAll("[data-editor-tool]").forEach((button) => button.addEventListener("click", () => {
    tool = button.dataset.editorTool;
    documentRef.querySelectorAll("[data-editor-tool]").forEach((candidate) => candidate.dataset.active = String(candidate === button));
  }));
  documentRef.getElementById("editor-rotate").addEventListener("click", () => {
    const beforeState = editorState;
    const afterState = rotateEditorState(editorState, 90);
    editorState = stack.execute({ kind: "rotate", from: beforeState.image.rotation, to: afterState.image.rotation, beforeState, afterState }, editorState);
    publishEditor();
  });
  undo.addEventListener("click", () => { editorState = stack.undo(editorState); publishEditor(); });
  redo.addEventListener("click", () => { editorState = stack.redo(editorState); publishEditor(); });
  canvas.addEventListener("pointerdown", (event) => {
    const point = canvasPoint(event);
    canvas.setPointerCapture(event.pointerId);
    if (tool === "select") {
      const selected = hitTest(editorState.annotations, point);
      editorState.selection = selected ? [selected.id] : [];
      const resize = selected && Math.abs(point[0] - selected.rect[2]) <= 35 && Math.abs(point[1] - selected.rect[3]) <= 35;
      gesture = selected ? { kind: resize ? "resize" : "move", id: selected.id, start: point, from: [...selected.rect] } : null;
      drawEditor(canvas, editorState);
    } else gesture = { kind: tool, start: point };
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!gesture) return;
    const end = canvasPoint(event);
    if (gesture.kind === "move" || gesture.kind === "resize") {
      const to = gesture.kind === "move"
        ? translateRect(gesture.from, end[0] - gesture.start[0], end[1] - gesture.start[1])
        : clampRect([gesture.from[0], gesture.from[1], end[0], end[1]]);
      editorState = stack.execute({ kind: gesture.kind, id: gesture.id, from: gesture.from, to }, editorState);
    } else {
      const rect = rectFromPoints(gesture.start, end);
      if (rect[2] - rect[0] >= 5 && rect[3] - rect[1] >= 5) {
        if (gesture.kind === "crop") {
          const beforeState = editorState;
          const afterState = (awaitCrop(editorState, rect));
          editorState = stack.execute({ kind: "crop", from: beforeState.image.crop, to: rect, beforeState, afterState }, editorState);
        } else {
          const text = documentRef.getElementById("editor-text-value").value;
          editorState = stack.execute({ kind: "add", annotation: createAnnotation(gesture.kind, rect, { text }) }, editorState);
        }
      }
    }
    gesture = null;
    publishEditor();
  });
  bridge.emit("preview.ready", { capabilities: ["plain", "markdown", "blocks", "image", "annotations", "undo-redo"] });
  return bridge;
}

function awaitCrop(state, rect) {
  const next = structuredClone(state);
  next.image.crop = clampRect(rect);
  next.annotations = next.annotations.map((item) => ({ ...item, rect: [
    (Math.max(item.rect[0], rect[0]) - rect[0]) * BBOX_NORM / Math.max(1, rect[2] - rect[0]),
    (Math.max(item.rect[1], rect[1]) - rect[1]) * BBOX_NORM / Math.max(1, rect[3] - rect[1]),
    (Math.min(item.rect[2], rect[2]) - rect[0]) * BBOX_NORM / Math.max(1, rect[2] - rect[0]),
    (Math.min(item.rect[3], rect[3]) - rect[1]) * BBOX_NORM / Math.max(1, rect[3] - rect[1]),
  ] })).filter((item) => item.rect[2] > item.rect[0] && item.rect[3] > item.rect[1]);
  return next;
}

if (globalThis.document && globalThis.chrome?.webview) {
  bootPreview(globalThis.document, globalThis.chrome.webview);
}
