import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  HardDrive,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { setStorageMode, type StorageMode, type User } from "../api/client";

export default function StorageSetup({ user }: { user: User }) {
  const qc = useQueryClient();
  const [choice, setChoice] = useState<StorageMode | null>(null);

  const confirm = useMutation({
    mutationFn: (mode: StorageMode) => setStorageMode(mode),
    onSuccess: (updated) => {
      qc.setQueryData(["me"], updated);
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });

  const displayName = user.name || user.username || user.email;

  return (
    <section className="setup-wrap">
      <div className="setup-card site-card">
        <div className="setup-head">
          <span className="page-icon">
            <ShieldCheck />
          </span>
          <div>
            <h1>Choose how your data is stored</h1>
            <p>
              Welcome, <strong>{displayName}</strong>. Before entering Smish, choose where your chat
              history and database credentials should live. You can change this anytime in Settings.
            </p>
          </div>
        </div>

        <div className="setup-grid" role="radiogroup" aria-label="Storage security mode">
          <button
            type="button"
            role="radio"
            aria-checked={choice === "db"}
            className={`setup-option${choice === "db" ? " selected" : ""}`}
            onClick={() => setChoice("db")}
          >
            <div className="setup-option-top">
              <span className="setup-option-icon db">
                <Database size={20} />
              </span>
              <div>
                <strong>Secure Server Database</strong>
                <span className="tag ok">Recommended · Persistent</span>
              </div>
              {choice === "db" && <CheckCircle2 size={18} className="setup-check" />}
            </div>
            <p>
              Store your chat conversations, rolling memory, and encrypted database credentials in
              our PostgreSQL database.
            </p>
            <ul>
              <li>Database passwords are encrypted at rest with Fernet AES.</li>
              <li>Never lose your chat history or database configs when switching devices.</li>
              <li>Safe if you clear your browser cache or cookies.</li>
            </ul>
          </button>

          <button
            type="button"
            role="radio"
            aria-checked={choice === "local"}
            className={`setup-option${choice === "local" ? " selected" : ""}`}
            onClick={() => setChoice("local")}
          >
            <div className="setup-option-top">
              <span className="setup-option-icon local">
                <HardDrive size={20} />
              </span>
              <div>
                <strong>Browser Local Storage Only</strong>
                <span className="tag">On this device only</span>
              </div>
              {choice === "local" && <CheckCircle2 size={18} className="setup-check" />}
            </div>
            <p>
              Keep your chat conversations, database credentials, schemas, and query logs strictly
              inside this browser&apos;s storage.
            </p>
            <ul>
              <li>Nothing is saved to our server database for your chats or DB connections.</li>
              <li>Sent to the server only transiently while running a chat or SQL query.</li>
            </ul>
            <div className="setup-warn">
              <AlertTriangle size={15} />
              <span>
                <strong>Important:</strong> Clearing browser site data/cache, using private
                browsing, or switching to another device or browser will{" "}
                <strong>permanently wipe</strong> all local chats and database configs.
              </span>
            </div>
          </button>
        </div>

        {confirm.isError && (
          <p className="form-error" style={{ margin: 0 }}>
            {String((confirm.error as Error).message)}
          </p>
        )}

        <div className="actions setup-actions">
          <button
            type="button"
            className="primary"
            disabled={!choice || confirm.isPending}
            onClick={() => choice && confirm.mutate(choice)}
          >
            {confirm.isPending ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <ArrowRight size={15} />
            )}
            {confirm.isPending ? "Saving preference…" : "Continue with selected security"}
          </button>
        </div>
      </div>
    </section>
  );
}

