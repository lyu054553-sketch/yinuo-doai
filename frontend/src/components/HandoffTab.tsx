import { useEffect, useState } from "react";
import type { Session } from "../api/types";
import { getHandoff } from "../api/client";
import { readableError } from "../api/errors";

interface Props {
  session: Session;
}

type Block =
  | { kind: "h1" | "h2" | "quote" | "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "table"; rows: string[][] };

function parseMarkdown(markdown: string): Block[] {
  const lines = markdown.split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push({ kind: "h2", text: line.slice(3) });
      i += 1;
      continue;
    }
    if (line.startsWith("# ")) {
      blocks.push({ kind: "h1", text: line.slice(2) });
      i += 1;
      continue;
    }
    if (line.startsWith("> ")) {
      blocks.push({ kind: "quote", text: line.slice(2) });
      i += 1;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i]
          .split("|")
          .slice(1, -1)
          .map((cell) => cell.trim());
        if (!cells.every((cell) => /^:?-{2,}:?$/.test(cell))) rows.push(cells);
        i += 1;
      }
      blocks.push({ kind: "table", rows });
      continue;
    }
    if (line.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) {
        items.push(lines[i].slice(2));
        i += 1;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }
    blocks.push({ kind: "p", text: line });
    i += 1;
  }
  return blocks;
}

export default function HandoffTab({ session }: Props) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getHandoff(session.id)
      .then((result) => {
        if (alive) setMarkdown(result.markdown);
      })
      .catch((err) => {
        if (alive) setError(readableError(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [session.id, session.spec_versions.length, tick]);

  async function copyAll() {
    if (!markdown) return;
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("复制没有成功，请手动选中复制。");
    }
  }

  if (loading && !markdown) {
    return (
      <div className="tab-panel">
        <p className="hint-line">正在整理交接单…</p>
      </div>
    );
  }

  if (error && !markdown) {
    return (
      <div className="tab-panel">
        <div className="alert alert-error">{error}</div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTick((t) => t + 1)}>
          重试
        </button>
      </div>
    );
  }

  const blocks = markdown ? parseMarkdown(markdown) : [];

  return (
    <div className="tab-panel" role="tabpanel">
      <div className="handoff-bar">
        <span className="hint-line">设计师拿到就能接手的一份说明。</span>
        <button type="button" className="btn btn-primary btn-sm" onClick={copyAll}>
          {copied ? "已复制" : "复制交接单"}
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <article className="handoff-doc">
        {blocks.map((block, index) => {
          switch (block.kind) {
            case "h1":
              return <h1 key={index}>{block.text}</h1>;
            case "h2":
              return <h2 key={index}>{block.text}</h2>;
            case "quote":
              return <blockquote key={index}>{block.text}</blockquote>;
            case "ul":
              return (
                <ul key={index}>
                  {block.items.map((item, k) => (
                    <li key={k}>{item}</li>
                  ))}
                </ul>
              );
            case "table":
              return (
                <table key={index}>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) =>
                          r === 0 ? <th key={c}>{cell}</th> : <td key={c}>{cell}</td>,
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            default:
              return <p key={index}>{block.text}</p>;
          }
        })}
      </article>
    </div>
  );
}