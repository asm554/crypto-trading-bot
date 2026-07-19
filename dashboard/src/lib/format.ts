export function eur(n: number, digits = 2): string {
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

export function signedEur(n: number): string {
  const s = eur(n);
  return n > 0 ? `+${s}` : s;
}

export function signedPct(n: number): string {
  return `${n > 0 ? "+" : ""}${n.toFixed(1)} %`;
}

export function pnlTone(n: number): "up" | "down" | "flat" {
  if (n > 0.0001) return "up";
  if (n < -0.0001) return "down";
  return "flat";
}

export function pnlToneClass(n: number): string {
  const t = pnlTone(n);
  return t === "up" ? "text-up" : t === "down" ? "text-down" : "text-muted-foreground";
}

export function relTime(ts: number | null): string {
  if (!ts) return "noch keine Aktivität";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  return `vor ${Math.floor(diff / 86400)} Tagen`;
}

export function clockTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
