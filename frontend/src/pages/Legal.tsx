import { AlertTriangle, ArrowLeft, FileText, Pencil, ShieldCheck } from "lucide-react";
import { Fragment } from "react";
import { LEGAL_ORDER, type LegalBlock, type LegalDocId } from "../legal/legalContent";
import { useLegal } from "../legal/useLegal";

const ICONS = {
  privacy: ShieldCheck,
  terms: FileText,
  disclaimer: AlertTriangle,
} as const;

function renderBlock(block: LegalBlock, key: number) {
  if (block.kind === "p") {
    return <p key={key}>{block.text}</p>;
  }
  if (block.kind === "ul") {
    return (
      <ul key={key}>
        {block.items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    );
  }
  return (
    <dl key={key} className="legal-dl">
      {block.items.map((item) => (
        <Fragment key={item.term}>
          <dt>{item.term}</dt>
          <dd>{item.detail}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export default function Legal({
  docId,
  backHref,
  backLabel,
  canEdit = false,
}: {
  docId: LegalDocId;
  backHref: string;
  backLabel: string;
  canEdit?: boolean;
}) {
  const { meta, docs } = useLegal();
  const doc = docs[docId];
  const Icon = ICONS[docId];

  return (
    <section className="space-y-4">
      <div className="legal-top">
        <a href={backHref} className="legal-back">
          <ArrowLeft size={15} /> {backLabel}
        </a>
        {canEdit && (
          <a
            href="#settings"
            className="legal-back"
            onClick={() => {
              window.setTimeout(() => {
                document
                  .getElementById("legal-settings")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" });
              }, 80);
            }}
          >
            <Pencil size={14} /> Edit legal pages
          </a>
        )}
      </div>

      <div className="page-heading">
        <span className="page-icon">
          <Icon />
        </span>
        <div>
          <h1>{doc.title}</h1>
          <p>{doc.subtitle}</p>
        </div>
      </div>

      <div className="source-tabs" role="tablist" aria-label="Legal documents">
        {LEGAL_ORDER.map((id) => {
          const TabIcon = ICONS[id];
          return (
            <a
              key={id}
              role="tab"
              href={`#${id}`}
              aria-selected={id === docId}
              className="legal-tab"
            >
              <TabIcon size={15} /> {docs[id].title}
            </a>
          );
        })}
      </div>

      <article className="site-card legal-card">
        <div className="legal-meta">
          <span>Effective: {meta.effective_date}</span>
          <span>Operator: {meta.operator_name}</span>
          <span>Jurisdiction: {meta.jurisdiction}</span>
          <span>Contact: {meta.contact_email}</span>
        </div>

        <p className="legal-summary">{doc.summary}</p>

        <div className="legal-sections">
          {doc.sections.map((sec) => (
            <section key={sec.heading} className="legal-section">
              <h2>{sec.heading}</h2>
              {sec.blocks.map((block, i) => renderBlock(block, i))}
            </section>
          ))}
        </div>
      </article>
    </section>
  );
}
