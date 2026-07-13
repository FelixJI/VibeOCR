export const BBOX_NORM = 1000;

const finite = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const rounded = (value) => Math.round(value * 1000) / 1000;

export function normalizeRect(rect) {
  if (!Array.isArray(rect) || rect.length !== 4) throw new TypeError("A four-value rectangle is required");
  const [ax, ay, bx, by] = rect.map(finite);
  return [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)].map(rounded);
}

export function clampRect(rect, bounds = [0, 0, BBOX_NORM, BBOX_NORM]) {
  const [x1, y1, x2, y2] = normalizeRect(rect);
  const [bx1, by1, bx2, by2] = normalizeRect(bounds);
  return [Math.max(bx1, x1), Math.max(by1, y1), Math.min(bx2, x2), Math.min(by2, y2)].map(rounded);
}

export function scaleRect(rect, scaleX, scaleY = scaleX) {
  const [x1, y1, x2, y2] = normalizeRect(rect);
  return [x1 * finite(scaleX), y1 * finite(scaleY), x2 * finite(scaleX), y2 * finite(scaleY)].map(rounded);
}

export function rotateRect(rect, degrees, size = BBOX_NORM) {
  const turn = ((Number(degrees) % 360) + 360) % 360;
  if (![0, 90, 180, 270].includes(turn)) throw new RangeError("Rotation must be a right angle");
  const [x1, y1, x2, y2] = normalizeRect(rect);
  const points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]].map(([x, y]) => {
    if (turn === 90) return [size - y, x];
    if (turn === 180) return [size - x, size - y];
    if (turn === 270) return [y, size - x];
    return [x, y];
  });
  return normalizeRect([
    Math.min(...points.map((point) => point[0])),
    Math.min(...points.map((point) => point[1])),
    Math.max(...points.map((point) => point[0])),
    Math.max(...points.map((point) => point[1])),
  ]);
}

export function cropRect(rect, crop) {
  const [x1, y1, x2, y2] = clampRect(rect, crop);
  const [cx1, cy1, cx2, cy2] = normalizeRect(crop);
  const width = Math.max(1, cx2 - cx1);
  const height = Math.max(1, cy2 - cy1);
  return clampRect([
    (x1 - cx1) * BBOX_NORM / width,
    (y1 - cy1) * BBOX_NORM / height,
    (x2 - cx1) * BBOX_NORM / width,
    (y2 - cy1) * BBOX_NORM / height,
  ]);
}

export function rectFromPoints(start, end) {
  return clampRect([start[0], start[1], end[0], end[1]]);
}

export function translateRect(rect, deltaX, deltaY) {
  const [x1, y1, x2, y2] = normalizeRect(rect);
  const width = x2 - x1;
  const height = y2 - y1;
  const left = Math.min(BBOX_NORM - width, Math.max(0, x1 + finite(deltaX)));
  const top = Math.min(BBOX_NORM - height, Math.max(0, y1 + finite(deltaY)));
  return [left, top, left + width, top + height].map(rounded);
}

export function hitTest(annotations, point) {
  const [x, y] = point.map(finite);
  return [...annotations].reverse().find((item) => {
    const [x1, y1, x2, y2] = normalizeRect(item.rect);
    return x >= x1 && x <= x2 && y >= y1 && y <= y2;
  }) ?? null;
}
