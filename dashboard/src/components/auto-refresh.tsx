"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";

export function AutoRefresh({ intervalMs = 10000 }: { intervalMs?: number }) {
  const router = useRouter();
  const [ticking, setTicking] = useState(false);

  useEffect(() => {
    const id = setInterval(() => {
      setTicking(true);
      router.refresh();
      const t = setTimeout(() => setTicking(false), 600);
      return () => clearTimeout(t);
    }, intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);

  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-up" />
      </span>
      Live
      <RefreshCw className={`h-3 w-3 ${ticking ? "animate-spin" : ""}`} />
    </span>
  );
}
