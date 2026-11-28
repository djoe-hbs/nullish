import { LEGAL_ORDER, type LegalDocId } from "../legal/legalContent";
import { useLegal } from "../legal/useLegal";

export default function LegalFooter({
  active,
  variant = "app",
}: {
  active?: LegalDocId | null;
  variant?: "app" | "login";
}) {
  const { meta, docs } = useLegal();
  return (
    <footer className={variant === "login" ? "login-legal" : "app-footer"} aria-label="Legal links">
      <nav className="legal-links" aria-label="Legal documents">
        {LEGAL_ORDER.map((id) => (
          <a
            key={id}
            href={`#${id}`}
            aria-current={active === id ? "page" : undefined}
          >
            {docs[id].title}
          </a>
        ))}
      </nav>
      <span className="legal-operator">
        {meta.service_name} · {meta.operator_name}
      </span>
    </footer>
  );
}
