import { Database, Globe, LibraryBig } from "lucide-react";
import { useState } from "react";
import Databases from "./Databases";
import Sites from "./Sites";

type Section = "sites" | "databases";
const KEY = "smish.sources.section";

/** Everything Smish answers from: websites and databases. */
export default function Sources() {
  const [section, setSection] = useState<Section>(() => {
    try { return localStorage.getItem(KEY) === "databases" ? "databases" : "sites"; }
    catch { return "sites"; }
  });
  const pick = (s: Section) => {
    setSection(s);
    try { localStorage.setItem(KEY, s); } catch { /* remembered for this visit only */ }
  };
  return (
    <section className="space-y-4">
      <div className="page-heading">
        <span className="page-icon"><LibraryBig /></span>
        <div>
          <h1>Sources</h1>
          <p>The websites and databases chat answers from. Tap one for details.</p>
        </div>
      </div>
      <div className="source-tabs" role="tablist" aria-label="Kind of source">
        <button role="tab" aria-selected={section === "sites"} onClick={() => pick("sites")}>
          <Globe size={15} /> Websites
        </button>
        <button role="tab" aria-selected={section === "databases"} onClick={() => pick("databases")}>
          <Database size={15} /> Databases
        </button>
      </div>
      {section === "sites" ? <Sites /> : <Databases />}
    </section>
  );
}
