const ALLOWED = new Set(["table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption"]);
const VOID = new Set([]);

export function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

export function sanitizeTableHtml(value) {
  const source = String(value ?? "").replace(/<(script|style)[^>]*>[\s\S]*?<\/\1\s*>/gi, "");
  const tokens = source.match(/<[^>]*>|[^<]+/g) ?? [];
  return tokens.map((token) => {
    if (!token.startsWith("<")) return escapeHtml(token);
    const closing = /^<\s*\/\s*([a-z0-9]+)/i.exec(token);
    if (closing) return ALLOWED.has(closing[1].toLowerCase()) ? `</${closing[1].toLowerCase()}>` : "";
    const opening = /^<\s*([a-z0-9]+)/i.exec(token);
    if (!opening || !ALLOWED.has(opening[1].toLowerCase())) return "";
    const tag = opening[1].toLowerCase();
    const attrs = [];
    for (const name of ["rowspan", "colspan"]) {
      const match = new RegExp(`${name}\\s*=\\s*["']?(\\d{1,3})`, "i").exec(token);
      if (match && Number(match[1]) >= 1) attrs.push(`${name}="${Number(match[1])}"`);
    }
    return `<${tag}${attrs.length ? ` ${attrs.join(" ")}` : ""}${VOID.has(tag) ? "/" : ""}>`;
  }).join("");
}
