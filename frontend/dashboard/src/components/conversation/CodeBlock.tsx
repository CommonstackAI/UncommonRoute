import Prism from "prismjs";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-jsx";
import "prismjs/components/prism-tsx";
import "prismjs/components/prism-python";
import "prismjs/components/prism-json";
import "prismjs/components/prism-css";
import "prismjs/components/prism-markup";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-yaml";
import "prismjs/components/prism-toml";
import "prismjs/components/prism-go";
import "prismjs/components/prism-rust";
import "prismjs/components/prism-java";
import "prismjs/components/prism-markdown";
import "prismjs/components/prism-sql";

const EXT_TO_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  json: "json",
  css: "css",
  scss: "css",
  html: "markup",
  htm: "markup",
  xml: "markup",
  svg: "markup",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  go: "go",
  rs: "rust",
  java: "java",
  md: "markdown",
  markdown: "markdown",
  sql: "sql",
};

export function languageFromPath(path?: string): string {
  if (!path) return "";
  const m = path.match(/\.([a-zA-Z0-9]+)$/);
  if (!m) return "";
  return EXT_TO_LANG[m[1].toLowerCase()] || "";
}

interface NumberedLine {
  num: number | null;
  content: string;
}

const CAT_N_LINE = /^\s*(\d+)\t(.*)$/;

function parseLineNumbered(text: string): { numbered: boolean; lines: NumberedLine[] } {
  const raw = text.split("\n");
  // Trim trailing blank line that often exists after cat -n output
  while (raw.length > 0 && raw[raw.length - 1] === "") raw.pop();
  if (raw.length === 0) return { numbered: false, lines: [] };
  const parsed: NumberedLine[] = [];
  let matched = 0;
  for (const ln of raw) {
    const m = ln.match(CAT_N_LINE);
    if (m) {
      matched += 1;
      parsed.push({ num: parseInt(m[1], 10), content: m[2] });
    } else {
      parsed.push({ num: null, content: ln });
    }
  }
  return { numbered: matched / raw.length > 0.6, lines: parsed };
}

function highlight(code: string, language: string): string {
  if (!language) return escapeHtml(code);
  const grammar = Prism.languages[language];
  if (!grammar) return escapeHtml(code);
  try {
    return Prism.highlight(code, grammar, language);
  } catch {
    return escapeHtml(code);
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export default function CodeBlock({
  text,
  language,
}: {
  text: string;
  language: string;
}) {
  const { numbered, lines } = parseLineNumbered(text);
  if (numbered) {
    const gutterWidth = String(lines[lines.length - 1]?.num ?? 1).length;
    return (
      <pre className="code-hl whitespace-pre text-[12px] leading-[1.5]">
        {lines.map((ln, i) => (
          <div key={i} className="flex">
            <span
              className="shrink-0 select-none text-right text-n-disabled"
              style={{ width: `${gutterWidth + 1}ch`, paddingRight: "1ch" }}
            >
              {ln.num ?? ""}
            </span>
            <span
              className="min-w-0 flex-1 whitespace-pre-wrap break-all text-n-primary"
              dangerouslySetInnerHTML={{ __html: highlight(ln.content, language) }}
            />
          </div>
        ))}
      </pre>
    );
  }
  return (
    <pre
      className="code-hl whitespace-pre-wrap text-[12px] leading-[1.5] text-n-primary"
      dangerouslySetInnerHTML={{ __html: highlight(text, language) }}
    />
  );
}
