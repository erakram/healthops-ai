"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "sonner";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true, retry: 1 } } }),
  );
  return (
    <QueryClientProvider client={client}>
      {children}
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{ className: "!bg-ink-850/95 !backdrop-blur-xl !border !border-white/10 !text-slate-100" }}
      />
    </QueryClientProvider>
  );
}
