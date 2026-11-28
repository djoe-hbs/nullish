import {
  ArrowRight,
  BookOpen,
  Database,
  Globe,
  MessageCircle,
  Moon,
  Quote,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  Sun,
} from "lucide-react";
import { motion } from "motion/react";
import LegalFooter from "../components/LegalFooter";

const FEATURES = [
  {
    icon: Quote,
    title: "Every answer is cited",
    text: "Each claim points to the page or query it came from, so you can check it yourself.",
  },
  {
    icon: RefreshCw,
    title: "Verified live",
    text: "Smish indexes to find the right pages, then re-reads them live before it answers. No stale facts.",
  },
  {
    icon: Globe,
    title: "Your websites",
    text: "Add the sites you trust. Pages that load slowly or build themselves in the browser are handled too.",
  },
  {
    icon: Database,
    title: "Your databases",
    text: "Connect Postgres or MySQL and ask in plain language. Smish writes the query and shows it to you.",
  },
  {
    icon: ShieldCheck,
    title: "You control edits",
    text: "Per database: never edit, ask before editing, or edit automatically. Schema changes always need your approval.",
  },
  {
    icon: ServerCog,
    title: "Open-source models",
    text: "Runs on open models you host yourself. Your questions and data stay on your own servers.",
  },
];

const STEPS = [
  { n: "1", title: "Add your sources", text: "Websites to read, databases to query." },
  { n: "2", title: "Ask a question", text: "In plain words, the way you would ask a colleague." },
  { n: "3", title: "Get a checked answer", text: "With citations, and the exact queries when a database was used." },
];

export default function Home({
  signedIn,
  dark,
  onToggleTheme,
}: {
  signedIn: boolean;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const open = signedIn ? "#chat" : "#login";
  return (
    <div className="home">
      <header className="home-bar">
        <a href="#home" className="wordmark" aria-label="Smish home">
          <span className="brand-tile"><BookOpen /></span>Smish
        </a>
        <div className="home-bar-right">
          <button className="theme-switch" aria-label="Dark theme" aria-pressed={dark} onClick={onToggleTheme}>
            {dark ? <Moon size={18} /> : <Sun size={18} />}
            <span>{dark ? "Dark" : "Light"}</span>
          </button>
          {signedIn ? (
            <a href="#chat" className="home-cta small">Open Smish</a>
          ) : (
            <>
              <a href="#login" className="home-link">Sign in</a>
              <a href="#register" className="home-cta small">Get started</a>
            </>
          )}
        </div>
      </header>

      <main>
        <section className="home-hero">
          <motion.div
            className="home-hero-copy"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
          >
            <span className="home-eyebrow">Answers only from your own sources</span>
            <h1>Ask once. Get answers you can check.</h1>
            <p>
              Smish reads the websites and databases you choose, and nothing else. Every answer
              comes with its sources, and websites are re-checked live before it replies.
            </p>
            <div className="home-actions">
              <a href={open} className="home-cta">
                {signedIn ? "Open Smish" : "Sign in"} <ArrowRight size={17} />
              </a>
              {!signedIn && <a href="#register" className="home-ghost">Create an account</a>}
            </div>
          </motion.div>
          <div className="home-art" aria-hidden="true" />
        </section>

        <section className="home-section" aria-labelledby="how">
          <h2 id="how">How it works</h2>
          <ol className="home-steps">
            {STEPS.map((s) => (
              <li key={s.n}>
                <span className="home-step-n">{s.n}</span>
                <strong>{s.title}</strong>
                <span>{s.text}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="home-section" aria-labelledby="why">
          <h2 id="why">Built to be trusted</h2>
          <div className="home-grid">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <article key={title} className="home-feature">
                <span className="home-icon"><Icon size={20} /></span>
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="home-final">
          <MessageCircle size={26} />
          <h2>Ready to ask your first question?</h2>
          <a href={open} className="home-cta">
            {signedIn ? "Open Smish" : "Sign in to start"} <ArrowRight size={17} />
          </a>
        </section>
      </main>

      <LegalFooter />
    </div>
  );
}
