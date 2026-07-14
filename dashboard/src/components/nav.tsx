"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, SlidersHorizontal, Bot } from "lucide-react";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Übersicht", icon: LayoutDashboard },
  { href: "/settings", label: "Einstellungen", icon: SlidersHorizontal },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Bot className="h-4 w-4" />
          </span>
          Trading-Bots
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
