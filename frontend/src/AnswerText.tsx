import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { visit, SKIP } from "unist-util-visit";
import type { Root, PhrasingContent } from "mdast";
import type { QueryRecord, Source } from "./api/client";

const CITE = /\[(\d{1,2})\]/g;
const QCITE = /\[Q(\d{1,2})\]/g;

/** [Q1] markers (database answers) become small "Q1" chips that name the query. */
function queryCitations(queries: QueryRecord[]) {
  const byNumber = new Map(queries.map(q => [q.n, q]));
  return () => (tree: Root) => {
    visit(tree, (node, index, parent) => {
      if (node.type !== "text" || !parent || index == null) return;
      const parts: PhrasingContent[] = [];
      let offset = 0;
      for (const match of node.value.matchAll(QCITE)) {
        parts.push({ type: "text", value: node.value.slice(offset, match.index) });
        const q = byNumber.get(Number(match[1]));
        if (q) parts.push({ type: "emphasis",
          data: { hProperties: { className: ["cite", "cite-q"], title: q.purpose || q.sql } },
          children: [{ type: "text", value: `Q${match[1]}` }] });
        offset = match.index! + match[0].length;
      }
      if (offset) {
        parts.push({ type: "text", value: node.value.slice(offset) });
        parent.children.splice(index, 1, ...parts);
        return index + parts.length;
      }
    });
  };
}

function citations(sources: Source[]) {
  const byNumber = new Map(sources.map(s => [s.n, s]));
  return () => (tree: Root) => {
    visit(tree, (node, index, parent) => {
      if (node.type === "link" || node.type === "linkReference") return SKIP;
      if (node.type !== "text" || !parent || index == null) return;
      const parts: PhrasingContent[] = [];
      let offset = 0;
      for (const match of node.value.matchAll(CITE)) {
        parts.push({ type: "text", value: node.value.slice(offset, match.index) });
        const source = byNumber.get(Number(match[1]));
        // Rendered as a small numbered chip (".cite") instead of a bracketed "[1]".
        if (source) parts.push({ type: "link", url: source.url,
          title: source.title ? `${source.title} · ${source.site}` : source.site,
          data: { hProperties: { className: ["cite"] } },
          children: [{ type: "text", value: match[1] }] });
        offset = match.index! + match[0].length;
      }
      if (offset) {
        parts.push({ type: "text", value: node.value.slice(offset) });
        parent.children.splice(index, 1, ...parts);
        return index + parts.length;
      }
    });
  };
}

export default function AnswerText({grounded, sources, queries = [], content}: {
  grounded: boolean; sources: Source[]; queries?: QueryRecord[]; content: string;
}) {
  const plugins = grounded ? [remarkGfm, citations(sources)] : [remarkGfm];
  if (queries.length) plugins.push(queryCitations(queries));
  return <div className="answer"><Markdown skipHtml disallowedElements={["img"]}
            remarkPlugins={plugins}
            components={{ a: ({children, href, title, className}) => <a href={href} title={title}
              className={className} target="_blank" rel="noopener noreferrer">{children}</a> }}>
            {content}
          </Markdown></div>;
}
