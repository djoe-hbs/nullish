import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ExternalLink,
  FileText,
  List,
  Loader2,
  Plus,
  RotateCcw,
  Scale,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  getAdminLegal,
  resetAdminLegal,
  updateAdminLegal,
  type LegalBlock,
  type LegalDocId,
  type LegalDocument,
  type LegalMetaApi,
  type LegalSection,
  type LegalSettings,
} from "../api/client";
import { buildLegalDocs, LEGAL_ORDER } from "../legal/legalContent";

const DOC_LABELS: Record<LegalDocId, string> = {
  privacy: "Privacy Policy",
  terms: "Terms & Conditions",
  disclaimer: "Disclaimer",
};

const DOC_ICONS = {
  privacy: ShieldCheck,
  terms: FileText,
  disclaimer: AlertTriangle,
} as const;

function cleanDocumentForSave(doc: LegalDocument): LegalDocument {
  const sections = doc.sections
    .map((sec) => {
      const blocks = sec.blocks
        .map((blk): LegalBlock | null => {
          if (blk.kind === "p") {
            const text = blk.text.trim();
            return text ? { kind: "p", text } : null;
          }
          if (blk.kind === "ul") {
            const items = blk.items.map((x) => x.trim()).filter(Boolean);
            return items.length ? { kind: "ul", items } : null;
          }
          const items = blk.items
            .map((it) => ({ term: it.term.trim(), detail: it.detail.trim() }))
            .filter((it) => it.term && it.detail);
          return items.length ? { kind: "dl", items } : null;
        })
        .filter((b): b is LegalBlock => Boolean(b));
      const heading = sec.heading.trim();
      return heading && blocks.length ? { heading, blocks } : null;
    })
    .filter((s): s is LegalSection => Boolean(s));

  return {
    id: doc.id,
    title: doc.title.trim() || DOC_LABELS[doc.id],
    subtitle: doc.subtitle.trim() || DOC_LABELS[doc.id],
    summary: doc.summary.trim() || DOC_LABELS[doc.id],
    sections: sections.length ? sections : [{ heading: "1. Overview", blocks: [{ kind: "p", text: doc.summary.trim() || "Updated terms." }] }],
  };
}

export default function LegalSettingsSection() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-legal"],
    queryFn: getAdminLegal,
  });

  const [draft, setDraft] = useState<LegalSettings | null>(null);
  const [activeDoc, setActiveDoc] = useState<LegalDocId>("privacy");
  const [saved, setSaved] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    if (data) {
      setDraft({
        meta: { ...data.meta },
        documents: { ...data.documents },
      });
    }
  }, [data]);

  const save = useMutation({
    mutationFn: (payload: LegalSettings) => {
      const cleanedDocs: Partial<Record<LegalDocId, LegalDocument>> = {};
      for (const id of LEGAL_ORDER) {
        const doc = payload.documents[id];
        if (doc) cleanedDocs[id] = cleanDocumentForSave(doc);
      }
      return updateAdminLegal({
        meta: payload.meta,
        documents: cleanedDocs,
      });
    },
    onSuccess: (updated) => {
      qc.setQueryData(["admin-legal"], updated);
      qc.setQueryData(["legal"], updated);
      qc.invalidateQueries({ queryKey: ["legal"] });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  const resetAll = useMutation({
    mutationFn: () => resetAdminLegal(),
    onSuccess: (defaults) => {
      setConfirmReset(false);
      setDraft({ meta: { ...defaults.meta }, documents: {} });
      qc.setQueryData(["admin-legal"], defaults);
      qc.setQueryData(["legal"], defaults);
      qc.invalidateQueries({ queryKey: ["legal"] });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  if (isLoading)
    return (
      <p id="legal-settings" className="text-sm text-zinc-500">
        Loading legal settings…
      </p>
    );
  if (error) return <p className="form-error">{String((error as Error).message)}</p>;
  if (!draft) return null;

  const setMeta = (key: keyof LegalMetaApi, val: string) => {
    setSaved(false);
    setDraft((prev) => (prev ? { ...prev, meta: { ...prev.meta, [key]: val } } : prev));
  };

  const generated = buildLegalDocs(draft.meta);
  const customDoc = draft.documents[activeDoc];
  const isCustom = Boolean(customDoc);
  const shownDoc = customDoc ?? generated[activeDoc];

  const setCustomDoc = (next: LegalDocument) => {
    setSaved(false);
    setDraft((prev) =>
      prev ? { ...prev, documents: { ...prev.documents, [activeDoc]: next } } : prev,
    );
  };

  const customizeCurrent = () => {
    setSaved(false);
    setCustomDoc(structuredClone(generated[activeDoc]));
  };

  const revertCurrent = () => {
    setSaved(false);
    setDraft((prev) => {
      if (!prev) return prev;
      const nextDocs = { ...prev.documents };
      delete nextDocs[activeDoc];
      return { ...prev, documents: nextDocs };
    });
  };

  const updateSection = (secIdx: number, updater: (sec: LegalSection) => LegalSection) => {
    if (!customDoc) return;
    setCustomDoc({
      ...customDoc,
      sections: customDoc.sections.map((s, idx) => (idx === secIdx ? updater(s) : s)),
    });
  };

  const moveSection = (secIdx: number, dir: -1 | 1) => {
    if (!customDoc) return;
    const nextIdx = secIdx + dir;
    if (nextIdx < 0 || nextIdx >= customDoc.sections.length) return;
    const copy = [...customDoc.sections];
    const [moved] = copy.splice(secIdx, 1);
    copy.splice(nextIdx, 0, moved);
    setCustomDoc({ ...customDoc, sections: copy });
  };

  const removeSection = (secIdx: number) => {
    if (!customDoc || customDoc.sections.length <= 1) return;
    setCustomDoc({
      ...customDoc,
      sections: customDoc.sections.filter((_, i) => i !== secIdx),
    });
  };

  const addSection = () => {
    if (!customDoc) return;
    setCustomDoc({
      ...customDoc,
      sections: [
        ...customDoc.sections,
        {
          heading: `${customDoc.sections.length + 1}. New Section`,
          blocks: [{ kind: "p", text: "Add section details here." }],
        },
      ],
    });
  };

  return (
    <form
      id="legal-settings"
      className="site-card add-db"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate(draft);
      }}
    >
      <div className="user-form-head">
        <Scale size={17} />
        <strong>Legal pages &amp; operator info (Admin)</strong>
        <small>
          Configure Privacy Policy, Terms &amp; Conditions, Disclaimer, and footer details live.
        </small>
      </div>

      <div className="add-options" style={{ padding: 0 }}>
        <label>
          Service name
          <input
            required
            maxLength={120}
            value={draft.meta.service_name}
            onChange={(e) => setMeta("service_name", e.target.value)}
          />
        </label>
        <label>
          Operator / company name
          <input
            required
            maxLength={200}
            value={draft.meta.operator_name}
            onChange={(e) => setMeta("operator_name", e.target.value)}
          />
        </label>
        <label>
          Operator website
          <input
            required
            maxLength={300}
            value={draft.meta.operator_website}
            onChange={(e) => setMeta("operator_website", e.target.value)}
          />
        </label>
        <label>
          Contact email
          <input
            type="email"
            required
            maxLength={320}
            value={draft.meta.contact_email}
            onChange={(e) => setMeta("contact_email", e.target.value)}
          />
        </label>
        <label>
          Privacy requests email
          <input
            type="email"
            required
            maxLength={320}
            value={draft.meta.privacy_email}
            onChange={(e) => setMeta("privacy_email", e.target.value)}
          />
        </label>
        <label>
          Effective date
          <input
            required
            maxLength={120}
            value={draft.meta.effective_date}
            onChange={(e) => setMeta("effective_date", e.target.value)}
          />
        </label>
        <label>
          Governing jurisdiction
          <input
            required
            maxLength={200}
            value={draft.meta.jurisdiction}
            onChange={(e) => setMeta("jurisdiction", e.target.value)}
          />
        </label>
        <label>
          GPU / inference hosting
          <input
            required
            maxLength={300}
            value={draft.meta.inference_provider}
            onChange={(e) => setMeta("inference_provider", e.target.value)}
          />
        </label>
      </div>

      <div className="settings-prompts">
        <label>
          AI training &amp; data-sharing commitment (Privacy Policy Section 5)
          <textarea
            rows={2}
            required
            minLength={5}
            maxLength={2000}
            value={draft.meta.training_commitment}
            onChange={(e) => setMeta("training_commitment", e.target.value)}
          />
        </label>
        <label>
          Liability cap (Terms Section 8 — &quot;...SHALL NOT EXCEED [cap]&quot;)
          <textarea
            rows={2}
            required
            minLength={2}
            maxLength={1000}
            value={draft.meta.liability_cap}
            onChange={(e) => setMeta("liability_cap", e.target.value)}
          />
        </label>
      </div>

      <div className="legal-editor-divider" />

      <div className="legal-editor-bar">
        <div className="source-tabs" role="tablist" aria-label="Edit legal document">
          {LEGAL_ORDER.map((id) => {
            const Icon = DOC_ICONS[id];
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={activeDoc === id}
                onClick={() => setActiveDoc(id)}
              >
                <Icon size={14} /> {DOC_LABELS[id]}
                {draft.documents[id] && <span className="tag">Custom</span>}
              </button>
            );
          })}
        </div>
        <div className="actions">
          <a href={`#${activeDoc}`} className="legal-preview-link">
            <ExternalLink size={13} /> Open page
          </a>
          {isCustom ? (
            <button type="button" onClick={revertCurrent}>
              <RotateCcw size={13} /> Revert page to template
            </button>
          ) : (
            <button type="button" onClick={customizeCurrent}>
              <FileText size={13} /> Customize {DOC_LABELS[activeDoc]} text
            </button>
          )}
        </div>
      </div>

      {!isCustom ? (
        <div className="storage-banner db" role="note">
          <ShieldCheck size={16} />
          <span>
            <strong>{shownDoc.title}</strong> is currently generated automatically from your
            operator details above ({shownDoc.sections.length} sections). Edit the fields above or
            click <strong>Customize {DOC_LABELS[activeDoc]} text</strong> to rewrite any section
            directly.
          </span>
        </div>
      ) : (
        <div className="legal-doc-editor">
          <div className="add-options" style={{ padding: 0 }}>
            <label>
              Document title
              <input
                required
                maxLength={120}
                value={shownDoc.title}
                onChange={(e) => setCustomDoc({ ...shownDoc, title: e.target.value })}
              />
            </label>
            <label>
              Subtitle
              <input
                required
                maxLength={500}
                value={shownDoc.subtitle}
                onChange={(e) => setCustomDoc({ ...shownDoc, subtitle: e.target.value })}
              />
            </label>
          </div>
          <div className="settings-prompts" style={{ marginTop: 0 }}>
            <label>
              Summary banner
              <textarea
                rows={2}
                required
                maxLength={2000}
                value={shownDoc.summary}
                onChange={(e) => setCustomDoc({ ...shownDoc, summary: e.target.value })}
              />
            </label>
          </div>

          <div className="legal-section-list">
            {shownDoc.sections.map((sec, sIdx) => (
              <div key={sIdx} className="legal-section-card">
                <div className="legal-section-head">
                  <input
                    aria-label={`Section ${sIdx + 1} heading`}
                    required
                    maxLength={300}
                    value={sec.heading}
                    onChange={(e) =>
                      updateSection(sIdx, (s) => ({ ...s, heading: e.target.value }))
                    }
                  />
                  <div className="actions">
                    <button
                      type="button"
                      title="Move section up"
                      disabled={sIdx === 0}
                      onClick={() => moveSection(sIdx, -1)}
                    >
                      <ArrowUp size={13} />
                    </button>
                    <button
                      type="button"
                      title="Move section down"
                      disabled={sIdx === shownDoc.sections.length - 1}
                      onClick={() => moveSection(sIdx, 1)}
                    >
                      <ArrowDown size={13} />
                    </button>
                    {shownDoc.sections.length > 1 && (
                      <button
                        type="button"
                        className="danger"
                        title="Remove section"
                        onClick={() => removeSection(sIdx)}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>

                <div className="legal-blocks-edit">
                  {sec.blocks.map((blk, bIdx) => (
                    <div key={bIdx} className="legal-block-box">
                      <div className="legal-block-head">
                        <span>
                          {blk.kind === "p"
                            ? "Paragraph"
                            : blk.kind === "ul"
                              ? "Bullet list (one item per line)"
                              : "Definition list"}
                        </span>
                        {sec.blocks.length > 1 && (
                          <button
                            type="button"
                            className="link-btn"
                            onClick={() =>
                              updateSection(sIdx, (s) => ({
                                ...s,
                                blocks: s.blocks.filter((_, j) => j !== bIdx),
                              }))
                            }
                          >
                            Remove block
                          </button>
                        )}
                      </div>

                      {blk.kind === "p" && (
                        <textarea
                          rows={3}
                          value={blk.text}
                          onChange={(e) =>
                            updateSection(sIdx, (s) => ({
                              ...s,
                              blocks: s.blocks.map((b, j) =>
                                j === bIdx ? { kind: "p", text: e.target.value } : b,
                              ),
                            }))
                          }
                        />
                      )}

                      {blk.kind === "ul" && (
                        <textarea
                          rows={Math.min(8, Math.max(3, blk.items.length))}
                          value={blk.items.join("\n")}
                          onChange={(e) =>
                            updateSection(sIdx, (s) => ({
                              ...s,
                              blocks: s.blocks.map((b, j) =>
                                j === bIdx
                                  ? { kind: "ul", items: e.target.value.split("\n") }
                                  : b,
                              ),
                            }))
                          }
                        />
                      )}

                      {blk.kind === "dl" && (
                        <div className="legal-dl-edit">
                          {blk.items.map((item, dIdx) => (
                            <div key={dIdx} className="legal-dl-row">
                              <input
                                placeholder="Term"
                                value={item.term}
                                onChange={(e) =>
                                  updateSection(sIdx, (s) => ({
                                    ...s,
                                    blocks: s.blocks.map((b, j) =>
                                      j === bIdx && b.kind === "dl"
                                        ? {
                                            kind: "dl",
                                            items: b.items.map((it, k) =>
                                              k === dIdx ? { ...it, term: e.target.value } : it,
                                            ),
                                          }
                                        : b,
                                    ),
                                  }))
                                }
                              />
                              <textarea
                                rows={2}
                                placeholder="Details"
                                value={item.detail}
                                onChange={(e) =>
                                  updateSection(sIdx, (s) => ({
                                    ...s,
                                    blocks: s.blocks.map((b, j) =>
                                      j === bIdx && b.kind === "dl"
                                        ? {
                                            kind: "dl",
                                            items: b.items.map((it, k) =>
                                              k === dIdx ? { ...it, detail: e.target.value } : it,
                                            ),
                                          }
                                        : b,
                                    ),
                                  }))
                                }
                              />
                              {blk.items.length > 1 && (
                                <button
                                  type="button"
                                  className="link-btn"
                                  onClick={() =>
                                    updateSection(sIdx, (s) => ({
                                      ...s,
                                      blocks: s.blocks.map((b, j) =>
                                        j === bIdx && b.kind === "dl"
                                          ? {
                                              kind: "dl",
                                              items: b.items.filter((_, k) => k !== dIdx),
                                            }
                                          : b,
                                      ),
                                    }))
                                  }
                                >
                                  Remove item
                                </button>
                              )}
                            </div>
                          ))}
                          <button
                            type="button"
                            className="link-btn"
                            onClick={() =>
                              updateSection(sIdx, (s) => ({
                                ...s,
                                blocks: s.blocks.map((b, j) =>
                                  j === bIdx && b.kind === "dl"
                                    ? {
                                        kind: "dl",
                                        items: [...b.items, { term: "New item", detail: "" }],
                                      }
                                    : b,
                                ),
                              }))
                            }
                          >
                            + Add definition row
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div className="actions">
                  <button
                    type="button"
                    onClick={() =>
                      updateSection(sIdx, (s) => ({
                        ...s,
                        blocks: [...s.blocks, { kind: "p", text: "" }],
                      }))
                    }
                  >
                    <Plus size={13} /> Paragraph
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      updateSection(sIdx, (s) => ({
                        ...s,
                        blocks: [...s.blocks, { kind: "ul", items: ["List item"] }],
                      }))
                    }
                  >
                    <List size={13} /> Bullet list
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="actions">
            <button type="button" onClick={addSection}>
              <Plus size={14} /> Add section
            </button>
          </div>
        </div>
      )}

      <div className="actions db-submit">
        {confirmReset ? (
          <>
            <button
              type="button"
              className="danger confirm"
              disabled={resetAll.isPending}
              onClick={() => resetAll.mutate()}
            >
              <RotateCcw size={14} /> Confirm reset all to defaults
            </button>
            <button type="button" onClick={() => setConfirmReset(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="danger"
            disabled={save.isPending || resetAll.isPending}
            onClick={() => setConfirmReset(true)}
          >
            <RotateCcw size={14} /> Reset all to defaults
          </button>
        )}
        <button type="submit" className="primary" disabled={save.isPending || resetAll.isPending}>
          {save.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {save.isPending ? "Saving…" : "Save legal settings"}
        </button>
      </div>

      {saved && (
        <p className="test-line ok">
          <Check size={14} /> Legal pages updated.
        </p>
      )}
      {(save.isError || resetAll.isError) && (
        <p className="form-error">{String((save.error ?? resetAll.error)?.message)}</p>
      )}
    </form>
  );
}
