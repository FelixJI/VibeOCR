import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildResultModel, markdownToModel } from "../../src/dotnet/VibeOCR.App/WebAssets/src/result/renderer.js";
import { escapeHtml, sanitizeTableHtml } from "../../src/dotnet/VibeOCR.App/WebAssets/src/result/sanitizer.js";

const fixture = JSON.parse(await readFile(new URL("../fixtures/parity/result-cases.json", import.meta.url), "utf8"));

test("matches PySide block semantics and preserves Unicode", () => {
  const model = buildResultModel({ content_list: fixture.blocks });
  assert.deepEqual(model.map((block) => block.kind), ["heading", "text", "table", "formula", "code"]);
  assert.equal(model[0].text, "章节：Unicode ✓");
  assert.equal(model.some((block) => block.text === "应丢弃"), false);
});

test("allows only trusted table structure and numeric spans", () => {
  const clean = sanitizeTableHtml(fixture.blocks[2].table_body);
  assert.equal(clean, '<table><tr><th>列</th><td rowspan="2">值</td></tr></table>');
  assert.doesNotMatch(clean, /script|onclick/i);
  assert.equal(escapeHtml("<img onerror='x'>&"), "&lt;img onerror=&#39;x&#39;&gt;&amp;");
});

test("parses Markdown headings, fenced code, formulas and plain text", () => {
  const model = markdownToModel("# 标题\n正文 ✓\n$$E=mc^2$$\n```py\nprint('<x>')\n```");
  assert.deepEqual(model.map((block) => block.kind), ["heading", "text", "formula", "code"]);
  assert.equal(model[3].text, "print('<x>')");
});

test("hostile plain text remains data rather than markup", () => {
  const attack = "<img src=x onerror=alert(1)>";
  assert.deepEqual(buildResultModel({ text: attack }), [{ kind: "text", text: attack }]);
});
