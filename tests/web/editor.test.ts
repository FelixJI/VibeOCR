import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { CommandStack } from "../../src/dotnet/VibeOCR.App/WebAssets/src/editor/command-stack.js";
import { createAnnotation, createEditorState, cropEditorState, rotateEditorState } from "../../src/dotnet/VibeOCR.App/WebAssets/src/editor/canvas.js";
import { cropRect, hitTest, rotateRect, scaleRect, translateRect } from "../../src/dotnet/VibeOCR.App/WebAssets/src/editor/geometry.js";

const fixture = JSON.parse(await readFile(new URL("../fixtures/parity/editor-cases.json", import.meta.url), "utf8"));

test("matches scale, rotate and crop geometry fixtures", () => {
  assert.deepEqual(scaleRect(fixture.geometry.rect, 0.5), fixture.geometry.scaled);
  assert.deepEqual(rotateRect(fixture.geometry.rect, 90), fixture.geometry.rotated90);
  assert.deepEqual(cropRect(fixture.geometry.rect, [100, 200, 900, 1000]), fixture.geometry.cropped);
});

test("supports serializable add, move, text, undo and redo commands", () => {
  const stack = new CommandStack();
  let state = createEditorState({ width: 1000, height: 1000 });
  for (const command of fixture.commands) state = stack.execute(command, state);
  assert.equal(state.annotations[0].text, "Unicode：识别 ✓");
  state = stack.undo(state);
  assert.equal(state.annotations[0].text, "");
  const restored = CommandStack.hydrate(stack.serialize());
  state = restored.redo(state);
  assert.equal(state.annotations[0].text, "Unicode：识别 ✓");
});

test("caps history at PySide's 50 command limit and truncates redo", () => {
  const stack = new CommandStack();
  let state = createEditorState();
  for (let index = 0; index < 55; index += 1) {
    state = stack.execute({ kind: "add", annotation: createAnnotation("rect", [index, index, index + 1, index + 1], { id: `a${index}` }) }, state);
  }
  for (let index = 0; index < 50; index += 1) state = stack.undo(state);
  assert.equal(stack.canUndo, false);
  state = stack.execute({ kind: "add", annotation: createAnnotation("text", [0, 0, 10, 10], { id: "new", text: "新" }) }, state);
  assert.equal(stack.canRedo, false);
});

test("transforms annotation models for canvas rotate and crop", () => {
  const state = createEditorState({ width: 1200, height: 800 });
  state.annotations.push(createAnnotation("ellipse", [100, 200, 500, 600], { id: "e1" }));
  const rotated = rotateEditorState(state, 90);
  assert.deepEqual(rotated.annotations[0].rect, [400, 100, 800, 500]);
  const cropped = cropEditorState(state, [100, 200, 900, 1000]);
  assert.deepEqual(cropped.annotations[0].rect, [0, 0, 500, 500]);
});

test("hit testing uses topmost annotation and movement stays normalized", () => {
  const lower = createAnnotation("rect", [100, 100, 400, 400], { id: "lower" });
  const upper = createAnnotation("text", [200, 200, 500, 500], { id: "upper" });
  assert.equal(hitTest([lower, upper], [250, 250]).id, "upper");
  assert.deepEqual(translateRect([800, 800, 1000, 1000], 500, 500), [800, 800, 1000, 1000]);
});
