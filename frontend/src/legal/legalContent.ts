import type { LegalDocId, LegalDocument, LegalMetaApi, LegalSettings } from "../api/client";

export type { LegalBlock, LegalDocId, LegalDocument, LegalSection } from "../api/client";

export const DEFAULT_LEGAL_META: LegalMetaApi = {
  service_name: "Smish",
  operator_name: "Harish Rohit Israel",
  operator_website: "https://smish.io/",
  contact_email: "reachus@smish.io",
  privacy_email: "reachus@smish.io",
  jurisdiction: "Tamil Nadu, India",
  effective_date: "Nov 28, 2026",
  inference_provider: "self-hosted GPU infrastructure",
  training_commitment:
    "We do not sell your personal data, and we do not use your configured websites, database records, or chat prompts to train public or third-party foundation models.",
  liability_cap:
    "THE TOTAL FEES ACTUALLY PAID BY YOU FOR THE SERVICE IN THE TWELVE (12) MONTHS IMMEDIATELY PRECEDING THE CLAIM, OR THE MINIMUM AMOUNT PERMITTED BY APPLICABLE LAW IF ACCESS IS PROVIDED FREE OF CHARGE",
};

export function buildLegalDocs(M: LegalMetaApi): Record<LegalDocId, LegalDocument> {
  return {
    privacy: {
      id: "privacy",
      title: "Privacy Policy",
      subtitle: `How ${M.operator_name} handles account, source, database, and chat data in ${M.service_name}.`,
      summary:
        `${M.service_name} is a hosted grounded-chat and database Q&A service. When you first enter, you choose whether to store your chat history and database credentials persistently in our encrypted PostgreSQL database or keep them locally in your browser's storage (where they remain until cleared or you switch devices).`,
      sections: [
        {
          heading: "1. Who We Are & Scope",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} is operated as a hosted service by ${M.operator_name} (${M.operator_website}). This Privacy Policy explains what information we collect and process when you sign in to ${M.service_name}, configure websites or databases as sources, or ask questions through the service.`,
            },
            {
              kind: "p",
              text:
                M.privacy_email === M.contact_email
                  ? `For privacy questions or data rights requests, contact us at ${M.privacy_email}.`
                  : `For privacy questions or data rights requests, contact us at ${M.privacy_email} (or ${M.contact_email}).`,
            },
          ],
        },
        {
          heading: "2. Information We Collect and Process",
          blocks: [
            {
              kind: "dl",
              items: [
                {
                  term: "Account & Authentication Data",
                  detail:
                    "Your email address, role, Argon2id-hashed password, failed-login lockout timestamps, and active session records (including a SHA-256 hash of your session token, IP address, user-agent string, and expiration time).",
                },
                {
                  term: "Configured Website Sources",
                  detail:
                    "Website root URLs, display names, crawl/scope rules, and the publicly accessible web pages crawled or verified live on your behalf—stored as extracted text, metadata, full-text search vectors, and embeddings.",
                },
                {
                  term: "Connected Database Metadata & Logs",
                  detail:
                    "Host, port, database name, username, TLS settings, edit permissions, encrypted passwords (stored using symmetric Fernet encryption), introspected table/column metadata, optional low-cardinality category samples (excluding primary/foreign keys and sensitive column names such as passwords, emails, phones, or tokens), pending change previews, and an audit log of SQL statements generated, executed, approved, or refused.",
                },
                {
                  term: "Chat Prompts & Outputs",
                  detail:
                    "When you send a message, recent conversation turns and your question are sent over HTTPS to our API and processed by our model endpoints to route intent, retrieve sources, generate SQL (if applicable), create conversation titles, and stream answers.",
                },
                {
                  term: "Chat Conversations & Storage Security Choice",
                  detail:
                    "You choose at first entry (and can change anytime in Settings) between Secure Server Database mode (conversations, rolling memory, Fernet-encrypted database credentials, schemas, and query audit logs stored privately per account in PostgreSQL) or Browser Local Storage mode (conversations, database credentials, schema cache, and query logs stored only in your browser's localStorage and sent to the server only transiently while answering or querying). Clearing browser data or changing devices wipes browser-local data.",
                },
              ],
            },
          ],
        },
        {
          heading: "3. Cookies & Local Technologies",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} uses strictly necessary authentication storage only: an HTTP-only, SameSite cookie holding your session token so you stay signed in securely. We do not use third-party advertising cookies or cross-site behavioral tracking cookies.`,
            },
          ],
        },
        {
          heading: "4. How We Use Information",
          blocks: [
            {
              kind: "ul",
              items: [
                "Authenticate users, enforce login rate limits/lockouts, and protect accounts and sessions.",
                "Crawl, extract, chunk, embed, and live-verify websites you configure so answers stay current and cited.",
                "Introspect database schemas, validate queries through automated safety rules, preview pending changes, and record an audit trail.",
                "Operate, monitor, and troubleshoot infrastructure, model availability, and service security.",
                "Comply with legal obligations and enforce our Terms & Conditions.",
              ],
            },
          ],
        },
        {
          heading: "5. AI Inference & Subprocessors",
          blocks: [
            {
              kind: "p",
              text: `To route questions, generate embeddings, rerank results, and stream answers, ${M.service_name} transmits relevant prompt text, retrieved source excerpts, or database schema/query context over encrypted tunnels to model servers hosted via ${M.inference_provider} (and our cloud infrastructure providers).`,
            },
            {
              kind: "p",
              text: M.training_commitment,
            },
          ],
        },
        {
          heading: "6. Data Security",
          blocks: [
            {
              kind: "ul",
              items: [
                "Transport encryption (HTTPS/TLS) for browser traffic and encrypted SSH tunnels to inference servers.",
                "Argon2id password hashing and server-side SHA-256 session hashing so raw passwords and tokens are not stored.",
                "Encrypted storage at rest for external database passwords (never returned via API or shown to models).",
                "SSRF protections on web crawlers, read-only database transactions for read queries, and AST-based SQL safety validation.",
              ],
            },
            {
              kind: "p",
              text: "No online system is 100% immune from risk. You are responsible for safeguarding your account password and using least-privilege credentials for connected databases.",
            },
          ],
        },
        {
          heading: "7. Retention & Deletion",
          blocks: [
            {
              kind: "ul",
              items: [
                "Deleting a website source immediately removes its indexed pages, chunks, vectors, and crawl jobs.",
                `Removing a database connection deletes stored credentials, schema items, pending changes, and query audit logs from ${M.service_name} (without modifying your external database).`,
                "Pending database change approvals expire automatically after their configured window (default 10 minutes).",
                "Conversations and database configs kept in Browser Local Storage stay on your device only until you delete them, clear browser site data, or switch browsers/devices. Conversations in Server Database mode remain until you delete them or request account deletion.",
                `To request account deletion or export/removal of server-side data, email ${M.privacy_email}.`,
              ],
            },
          ],
        },
        {
          heading: "8. Your Rights & International Transfers",
          blocks: [
            {
              kind: "p",
              text: `Depending on your location (${M.jurisdiction} or other applicable data protection laws), you may have rights to access, correct, delete, restrict, or object to processing of your personal data. Because our hosting and ${M.inference_provider} servers may operate across regions, data may be processed outside your jurisdiction under appropriate contractual and technical safeguards.`,
            },
          ],
        },
        {
          heading: "9. Updates to This Policy",
          blocks: [
            {
              kind: "p",
              text: `We may update this Privacy Policy as ${M.service_name} evolves. Material changes will be reflected by updating the effective date (${M.effective_date}) and notifying account holders where required.`,
            },
          ],
        },
      ],
    },

    terms: {
      id: "terms",
      title: "Terms & Conditions",
      subtitle: `Rules governing your access to and use of ${M.service_name}, operated by ${M.operator_name}.`,
      summary:
        `By signing in to or using ${M.service_name}, you agree to configure only websites and databases you are authorized to access, choose appropriate database edit permissions, and follow acceptable-use rules.`,
      sections: [
        {
          heading: "1. Agreement & Eligibility",
          blocks: [
            {
              kind: "p",
              text: `These Terms & Conditions ("Terms") govern your use of the hosted ${M.service_name} platform provided by ${M.operator_name} ("we", "us"). By accessing or using ${M.service_name}, you confirm that you have authority to bind yourself or the organization you represent to these Terms.`,
            },
          ],
        },
        {
          heading: "2. Accounts & Access Credentials",
          blocks: [
            {
              kind: "ul",
              items: [
                "You are responsible for maintaining the confidentiality of your login credentials and for all activity under your account.",
                "Notify us promptly if you suspect unauthorized access or a compromised session.",
                "We may enforce rate limits, lockouts, or suspend accounts to protect platform security or prevent abuse.",
              ],
            },
          ],
        },
        {
          heading: "3. Configured Websites & Crawling Authorization",
          blocks: [
            {
              kind: "p",
              text: `You may configure websites for ${M.service_name} to crawl, index, and re-verify live at query time. You represent and warrant that:`,
            },
            {
              kind: "ul",
              items: [
                "You own, control, or have sufficient legal authorization to crawl, index, and query every website URL you add.",
                "Your configuration of crawl rules (including disabling robots.txt respect or setting aggressive crawl delays) will not violate applicable laws or third-party website terms.",
                `You will not use ${M.service_name} to probe internal/private networks, bypass authentication walls, or conduct denial-of-service traffic against third-party sites.`,
              ],
            },
          ],
        },
        {
          heading: "4. Connected Databases & Edit Permissions",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} allows you to connect external PostgreSQL, MySQL, or MariaDB databases and select an edit policy ("Never edit", "Ask before editing", or "Edit automatically"). By connecting a database, you agree that:`,
            },
            {
              kind: "ul",
              items: [
                `You are authorized to connect the database, expose its schema (except items you mark hidden), and permit ${M.service_name} to run queries against it.`,
                "You are solely responsible for selecting the appropriate edit policy and hiding sensitive tables or columns before use.",
                "We strongly recommend supplying least-privilege or read-only database credentials (e.g., GRANT SELECT) when write access is not required.",
                `In "Ask before editing" or "Edit automatically" mode, you authorize ${M.service_name} to preview or execute AI-generated INSERT, UPDATE, DELETE, or approved schema statements on your behalf.`,
              ],
            },
          ],
        },
        {
          heading: "5. Acceptable Use",
          blocks: [
            {
              kind: "p",
              text: "You agree not to:",
            },
            {
              kind: "ul",
              items: [
                "Use the service for unlawful, fraudulent, defamatory, or infringing activities.",
                "Attempt to circumvent SQL safety gates, SSRF protections, session controls, or multi-tenant/network boundaries.",
                "Upload or process regulated sensitive data (such as payment card data or protected health records) unless covered by a separate written agreement with us.",
                "Reverse engineer, overload, or interfere with the availability of the service or underlying model servers.",
              ],
            },
          ],
        },
        {
          heading: "6. Service Availability & AI Infrastructure",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} relies on GPU model servers for chat, routing, embeddings, and reranking. When inference servers are offline or undergoing maintenance, background crawling and keyword indexing may continue while AI chat and vector embedding pause until models return.`,
            },
            {
              kind: "p",
              text: "We may modify, update, or discontinue features from time to time and will make reasonable efforts to communicate major changes.",
            },
          ],
        },
        {
          heading: "7. Ownership & Intellectual Property",
          blocks: [
            {
              kind: "p",
              text: `As between you and ${M.operator_name}, you retain all rights to your proprietary database content, credentials, and prompts. Third-party website content remains the property of its respective owners. ${M.operator_name} retains all rights, title, and interest in the ${M.service_name} software, design, and branding.`,
            },
          ],
        },
        {
          heading: "8. Warranties & Limitation of Liability",
          blocks: [
            {
              kind: "p",
              text: `TO THE MAXIMUM EXTENT PERMITTED BY LAW, ${M.service_name.toUpperCase()} IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING ACCURACY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT.`,
            },
            {
              kind: "p",
              text: `TO THE EXTENT PERMITTED BY LAW, ${M.operator_name.toUpperCase()} SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR LOSS OF DATA, REVENUE, OR PROFITS. OUR AGGREGATE LIABILITY ARISING OUT OF OR RELATED TO THE SERVICE SHALL NOT EXCEED ${M.liability_cap}.`,
            },
          ],
        },
        {
          heading: "9. Indemnification & Termination",
          blocks: [
            {
              kind: "p",
              text: `You agree to indemnify and hold ${M.operator_name} harmless from claims, damages, or expenses arising from your unauthorized crawling of third-party websites, unauthorized database access, or database modifications approved or enabled by you.`,
            },
            {
              kind: "p",
              text: `You may stop using ${M.service_name} and delete your sources at any time. We may suspend or terminate access if you materially breach these Terms or create security/legal risk.`,
            },
          ],
        },
        {
          heading: "10. Governing Law & Contact",
          blocks: [
            {
              kind: "p",
              text: `These Terms are governed by the laws of ${M.jurisdiction}, without regard to conflict-of-law principles. Questions about these Terms may be sent to ${M.contact_email}.`,
            },
          ],
        },
      ],
    },

    disclaimer: {
      id: "disclaimer",
      title: "Disclaimer",
      subtitle: `Important limits on AI-generated answers, live website verification, and automated SQL actions in ${M.service_name}.`,
      summary:
        `${M.service_name} verifies website citations live and gates database SQL for safety, but AI outputs can still be incomplete or wrong. Always confirm critical facts and inspect database edits before relying on them.`,
      sections: [
        {
          heading: "1. Automated AI Output — Verify Before Relying",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} uses open-source large language models to interpret questions, rewrite queries, generate SQL, and synthesize answers. Even when grounded in cited web pages ([1], [2]) or database queries ([Q1], [Q2]), automated models can misread context, omit caveats, miscalculate, or produce inaccurate summaries.`,
            },
            {
              kind: "p",
              text: `Do not rely solely on ${M.service_name} answers for high-stakes decisions without checking the linked original web pages or inspecting the underlying SQL query results shown under the answer.`,
            },
          ],
        },
        {
          heading: "2. Website Freshness & " + '"Not Verified"' + " Sources",
          blocks: [
            {
              kind: "ul",
              items: [
                "While \"Your sources\" mode re-checks top candidate pages against live sites, target websites may time out, block automated browsers, return errors, or change immediately after a check.",
                "Pages that fail live verification may be included from the existing index and labeled as not verified live.",
                "Pages confirmed recently within the re-verification cache window may reuse that recent check rather than re-fetching on every consecutive question.",
                "JavaScript-heavy pages, pages behind logins, or content excluded by robots.txt/scope rules may be partially extracted or omitted.",
              ],
            },
          ],
        },
        {
          heading: "3. \"Model chat\" Mode Is Ungrounded",
          blocks: [
            {
              kind: "p",
              text: "When you switch Chat to \"Model chat\" mode, responses come directly from the language model without searching your configured websites, without live verification, and without querying your databases. Outputs in that mode may reflect outdated training data or model hallucinations.",
            },
          ],
        },
        {
          heading: "4. Database Queries & Data/Schema Changes",
          blocks: [
            {
              kind: "ul",
              items: [
                "Safety gates (SQL AST validation, read-only transactions, row limits, and timeouts) reduce risk but do not guarantee semantic correctness of generated SQL—always inspect the SQL and query results panel for critical reporting.",
                `In "Ask before editing" mode, ${M.service_name} attempts a rolled-back trial run to preview affected rows where supported, but database triggers, concurrent writes, or subsequent state changes between preview and approval can alter the final outcome.`,
                "In \"Edit automatically\" mode, INSERT, UPDATE, and DELETE statements execute immediately without waiting for confirmation.",
                "Schema changes (DDL) and operations on non-transactional storage engines (such as MySQL MyISAM) cannot be rolled back for a trial preview and may commit permanently once approved or run.",
              ],
            },
          ],
        },
        {
          heading: "5. No Professional Advice & External Sites",
          blocks: [
            {
              kind: "p",
              text: `${M.service_name} does not provide legal, financial, tax, medical, accounting, or security advice. Links and citations pointing to external websites are provided for verification only; ${M.operator_name} does not endorse or guarantee the accuracy, legality, or availability of third-party website content.`,
            },
            {
              kind: "p",
              text: `For questions about this Disclaimer, contact ${M.contact_email}.`,
            },
          ],
        },
      ],
    },
  };
}

export const LEGAL_META = DEFAULT_LEGAL_META;
export const LEGAL_DOCS = buildLegalDocs(DEFAULT_LEGAL_META);
export const LEGAL_ORDER: LegalDocId[] = ["privacy", "terms", "disclaimer"];

export function resolveLegal(settings?: LegalSettings) {
  const meta = settings?.meta ?? DEFAULT_LEGAL_META;
  const generated = buildLegalDocs(meta);
  const docs: Record<LegalDocId, LegalDocument> = {
    privacy: settings?.documents?.privacy ?? generated.privacy,
    terms: settings?.documents?.terms ?? generated.terms,
    disclaimer: settings?.documents?.disclaimer ?? generated.disclaimer,
  };
  return { meta, docs };
}
