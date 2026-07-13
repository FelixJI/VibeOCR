import { BBOX_NORM, clampRect, cropRect, rotateRect } from "./geometry.js";

export const ANNOTATION_TYPES = Object.freeze(["rect", "ellipse", "arrow", "text", "mosaic", "blur"]);

export function createEditorState(image = {}) {
  return {
    version: 1,
    image: { width: Number(image.width) || 0, height: Number(image.height) || 0, rotation: 0, crop: [0, 0, BBOX_NORM, BBOX_NORM] },
    annotations: [],
    selection: [],
  };
}

export function createAnnotation(type, rect, options = {}) {
  if (!ANNOTATION_TYPES.includes(type)) throw new TypeError("Unsupported annotation type");
  return {
    id: options.id ?? crypto.randomUUID(),
    type,
    rect: clampRect(rect),
    text: typeof options.text === "string" ? options.text : "",
    color: typeof options.color === "string" ? options.color : "#ef4444",
    width: Math.max(1, Number(options.width) || 2),
  };
}

export function rotateEditorState(state, degrees) {
  const next = structuredClone(state);
  next.image.rotation = ((next.image.rotation + degrees) % 360 + 360) % 360;
  next.annotations.forEach((item) => { item.rect = rotateRect(item.rect, degrees); });
  return next;
}

export function cropEditorState(state, crop) {
  const next = structuredClone(state);
  next.image.crop = clampRect(crop);
  next.annotations = next.annotations
    .map((item) => ({ ...item, rect: cropRect(item.rect, next.image.crop) }))
    .filter((item) => item.rect[2] > item.rect[0] && item.rect[3] > item.rect[1]);
  return next;
}

export function drawEditor(canvas, state) {
  const context = canvas?.getContext?.("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  for (const item of state.annotations) {
    const [x1, y1, x2, y2] = item.rect.map((value, index) => value * (index % 2 ? canvas.height : canvas.width) / BBOX_NORM);
    context.strokeStyle = item.color;
    context.lineWidth = item.width;
    if (item.type === "ellipse") {
      context.beginPath();
      context.ellipse((x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / 2, (y2 - y1) / 2, 0, 0, Math.PI * 2);
      context.stroke();
    } else if (item.type === "arrow") {
      context.beginPath(); context.moveTo(x1, y1); context.lineTo(x2, y2); context.stroke();
      const angle = Math.atan2(y2 - y1, x2 - x1);
      context.beginPath(); context.moveTo(x2, y2); context.lineTo(x2 - 18 * Math.cos(angle - 0.5), y2 - 18 * Math.sin(angle - 0.5)); context.moveTo(x2, y2); context.lineTo(x2 - 18 * Math.cos(angle + 0.5), y2 - 18 * Math.sin(angle + 0.5)); context.stroke();
    } else if (item.type === "text") {
      context.font = "24px sans-serif"; context.strokeText(item.text || "文本", x1, y2);
    } else if (["mosaic", "blur"].includes(item.type)) {
      context.save(); context.globalAlpha = item.type === "mosaic" ? 0.72 : 0.38; context.fillStyle = item.color; context.fillRect(x1, y1, x2 - x1, y2 - y1); context.restore();
    } else context.strokeRect(x1, y1, x2 - x1, y2 - y1);
    if (state.selection.includes(item.id)) {
      context.save(); context.setLineDash([8, 6]); context.strokeStyle = "#ff9d2e"; context.lineWidth = 2; context.strokeRect(x1 - 3, y1 - 3, x2 - x1 + 6, y2 - y1 + 6); context.restore();
    }
  }
}
