import { useQuery } from "@tanstack/react-query";
import { fetchLegalSettings } from "../api/client";
import { DEFAULT_LEGAL_META, resolveLegal } from "./legalContent";

export function useLegal() {
  const { data } = useQuery({
    queryKey: ["legal"],
    queryFn: fetchLegalSettings,
    staleTime: 60_000,
    retry: 1,
  });
  return resolveLegal(data ?? { meta: DEFAULT_LEGAL_META, documents: {} });
}

