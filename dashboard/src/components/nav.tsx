"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, SlidersHorizontal, Bot, ArrowLeftRight } from "lucide-react";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Übersicht", icon: LayoutDashboard },
  { href: "/trades", label: "Trades", icon: ArrowLeftRight },
  { href: "/settings", label: "Einstellungen", icon: SlidersHorizontal },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/75 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-3 sm:gap-6 sm:px-4">
        <Link
          href="/"
          className="group flex shrink-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:gap-2.5"
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-[0_0_16px_-2px] shadow-primary/50 transition-shadow group-hover:shadow-[0_0_22px_0px] group-hover:shadow-primary/60">
            <Bot className="h-4 w-4" />
          </span>
          <span className="whitespace-nowrap font-heading text-sm font-bold tracking-tight sm:text-[15px]">
            Bot-Battle
            <span className="ml-1.5 hidden font-mono text-[10px] font-normal uppercase tracking-[0.18em] text-muted-foreground sm:inline">
              Papier
            </span>
          </span>
        </Link>
        <nav className="flex items-center gap-0.5 sm:gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            const Icon = link.icon;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                aria-label={link.label}
                className={cn(
                  "relative flex h-11 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors sm:px-3",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                  active
                    ? "text-foreground after:absolute after:inset-x-3 after:-bottom-[13px] after:h-0.5 after:rounded-full after:bg-primary"
                    : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="hidden sm:inline">{link.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
