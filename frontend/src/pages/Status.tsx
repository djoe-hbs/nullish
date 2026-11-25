import { Activity, Cpu, Server } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchHealth, type ServiceStatus } from "../api/client";

const LABELS: Record<string, { title: string; where: string }> = {
  database: { title: "Database", where: "local" },
  redis: { title: "Queue / cache", where: "local" },
  chat: { title: "Chat LLM", where: "rented GPU" },
  embedding: { title: "Embedding model", where: "rented GPU" },
  reranker: { title: "Reranker", where: "rented GPU" },
};

const GPU_SERVICES = new Set(["chat", "embedding", "reranker"]);

function Row({ s }: { s: ServiceStatus }) {
  const label = LABELS[s.name] ?? { title: s.name, where: "" };
  // GPU offline is an expected state (switched off to save cost): show it neutrally.
  const isGpu = GPU_SERVICES.has(s.name);
  const dot = s.ok ? "bg-emerald-500" : isGpu ? "bg-zinc-400" : "bg-red-500";
  const state = s.ok ? "Online" : isGpu ? "Offline (GPU off?)" : "Down";
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium">
          {label.title} <span className="text-xs text-zinc-500">· {label.where}</span>
        </div>
        <div className="truncate text-xs text-zinc-500">
          {s.model ?? s.detail ?? ""}
          {s.model && s.detail ? ` · ${s.detail}` : ""}
        </div>
      </div>
      <div className="text-right text-sm">
        <div>{state}</div>
        {s.latency_ms != null && <div className="text-xs text-zinc-500">{s.latency_ms} ms</div>}
      </div>
    </li>
  );
}

export default function Status() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });

  if (isLoading) return <p className="text-zinc-500">Checking services…</p>;
  if (error) return <p className="text-red-600">API unreachable: {String(error)}</p>;
  if (!data) return null;

  return (
    <section>
      <div className="page-heading"><span className="page-icon"><Activity/></span><div><h1>System status</h1>
      <p className="mt-1 text-sm text-zinc-500">
        {data.status === "ok" ? "Local services are healthy." : "Some local services are down."}{" "}
        {data.gpu_available
          ? "GPU models are reachable."
          : "GPU is offline: crawling still works; chat and embedding wait. Start the Vast instance and run gpu/tunnel.ps1."}
      </p>
      </div></div>
      <div className="service-groups">{[false,true].map(gpu => <section key={String(gpu)} className="service-group">
        <h2 className="flex items-center gap-2">{gpu ? <Cpu size={19}/> : <Server size={19}/>}
          {gpu ? "Model services" : "Local services"}</h2>
        <ul>{Object.values(data.services).filter(s => GPU_SERVICES.has(s.name) === gpu)
          .map(s => <Row key={s.name} s={s}/>)}</ul>
      </section>)}</div>
      {data.worker_last_check && (
        <p className="mt-3 text-xs text-zinc-500">
          Worker last checked endpoints at{" "}
          {new Date(data.worker_last_check.checked_at).toLocaleTimeString()}
        </p>
      )}
    </section>
  );
}
