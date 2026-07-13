import { sanitizeTableHtml } from "./sanitizer.js";

const DISCARDED = new Set(["header", "footer", "page_number", "page_footnote", "aside_text"]);

export function buildResultModel(result) {
  if (typeof result === "string") return [{ kind: "text", text: result }];
  const blocks = Array.isArray(result?.content_list) ? result.content_list : Array.isArray(result?.blocks) ? result.blocks : [];
  if (!blocks.length && typeof result?.text === "string") return [{ kind: "text", text: result.text }];
  return blocks.filter((block) => !DISCARDED.has(String(block.type ?? block.label ?? "text").toLowerCase())).map((block) => {
    const type = String(block.type ?? block.label ?? "text").toLowerCase();
    const text = String(block.text ?? block.content ?? "");
    if (type === "title") return { kind: "heading", level: Math.min(6, Math.max(1, Number(block.level) || 2)), text };
    if (type === "table") return { kind: "table", html: sanitizeTableHtml(block.table_body ?? block.html ?? text) };
    if (["formula", "equation"].includes(type)) return { kind: "formula", text };
    if (type === "code") return { kind: "code", text };
    if (type === "list") return { kind: "list", items: Array.isArray(block.items) ? block.items.map(String) : text.split(/\r?\n/).filter(Boolean) };
    if (["image", "figure", "chart"].includes(type)) return { kind: "placeholder", text: block.alt ?? "图像区域" };
    return { kind: "text", text };
  });
}

export function markdownToModel(markdown) {
  const lines = String(markdown ?? "").split(/\r?\n/);
  const model = [];
  let code = null;
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (code === null) code = [];
      else { model.push({ kind: "code", text: code.join("\n") }); code = null; }
    } else if (code) code.push(line);
    else if (/^#{1,6}\s/.test(line)) {
      const marker = line.match(/^#+/)[0];
      model.push({ kind: "heading", level: marker.length, text: line.slice(marker.length).trim() });
    } else if (/^\s*\|.*\|\s*$/.test(line)) model.push({ kind: "text", text: line });
    else if (/^\$\$.*\$\$$/.test(line)) model.push({ kind: "formula", text: line.slice(2, -2).trim() });
    else if (line.trim()) model.push({ kind: "text", text: line });
  }
  if (code !== null) model.push({ kind: "code", text: code.join("\n") });
  return model;
}

export function renderResult(container, model, documentRef = container.ownerDocument) {
  container.replaceChildren();
  for (const block of model) {
    let node;
    if (block.kind === "heading") node = documentRef.createElement(`h${block.level}`);
    else if (block.kind === "code") { node = documentRef.createElement("pre"); node.className = "result-code"; }
    else if (block.kind === "formula") { node = documentRef.createElement("div"); node.className = "result-formula"; }
    else if (block.kind === "table") {
      node = documentRef.createElement("div");
      node.className = "result-table";
      const template = documentRef.createElement("template");
      template.innerHTML = block.html;
      node.append(template.content.cloneNode(true));
    } else if (block.kind === "list") {
      node = documentRef.createElement("ul");
      block.items.forEach((item) => { const li = documentRef.createElement("li"); li.textContent = item; node.append(li); });
    } else { node = documentRef.createElement("p"); node.className = block.kind === "placeholder" ? "result-placeholder" : ""; }
    if (block.kind !== "table" && block.kind !== "list") node.textContent = block.text;
    node.dataset.kind = block.kind;
    container.append(node);
  }
}
