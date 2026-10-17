"use client";

import {
  Activity,
  ExternalLink,
  FileText,
  Inbox,
  LayoutDashboard,
  Loader2,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Plug,
  RotateCcw,
  ServerCrash,
  ShieldCheck,
  Sun,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import * as React from "react";
import { toast } from "sonner";

import { Logo } from "@/components/app/logo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { ApiError, api } from "@/lib/api";
import { refreshAll, useActions, useHealth, useSession } from "@/lib/hooks";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  pill?: boolean;
}

const NAV: NavItem[] = [
  { href: "/app", label: "Dashboard", icon: LayoutDashboard },
  { href: "/app/chat", label: "Assistant", icon: MessageSquare },
  { href: "/app/approvals", label: "Approvals", icon: Inbox, pill: true },
  { href: "/app/invoices", label: "Invoices", icon: FileText },
  { href: "/app/integrations", label: "Integrations", icon: Plug },
  { href: "/app/activity", label: "Activity", icon: Activity },
  { href: "/app/settings", label: "Safety rules", icon: ShieldCheck },
];

function isActive(pathname: string, href: string) {
  return href === "/app" ? pathname === "/app" : pathname === href || pathname.startsWith(`${href}/`);
}

// --------------------------------------------------------------------------- nav
function NavLinks({ pending, onNavigate }: { pending: number; onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon, pill }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-sidebar-foreground/80 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              active && "bg-sidebar-accent text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4" aria-hidden="true" />
            <span className="flex-1">{label}</span>
            {pill && pending > 0 ? (
              <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                {pending}
                <span className="sr-only"> waiting for approval</span>
              </span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

function SafetyCard({ version }: { version?: string }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-sidebar-border bg-background/60 p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ShieldCheck className="size-4 text-success" aria-hidden="true" />
          Safety gate on
        </div>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Nothing is sent, charged or refunded until you approve it.
        </p>
      </div>
      <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
        <span>{version ? `v${version}` : ""}</span>
        <a href="/api/docs" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
          API <ExternalLink className="size-3" aria-hidden="true" />
        </a>
      </div>
    </div>
  );
}

function SidebarBody({ pending, version, onNavigate }: { pending: number; version?: string; onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-6 p-4">
      <Link href="/app" onClick={onNavigate} className="rounded-md px-2 py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Logo subtitle="Payments assistant" />
      </Link>
      <NavLinks pending={pending} onNavigate={onNavigate} />
      <div className="mt-auto">
        <SafetyCard version={version} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------ top bar
const subscribeNever = () => () => {};

function ThemeMenu() {
  const { theme, setTheme } = useTheme();
  // The theme is only known in the browser; render the neutral icon on the server and during hydration.
  const mounted = React.useSyncExternalStore(subscribeNever, () => true, () => false);
  const Icon = !mounted ? Monitor : theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <Icon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {(
          [
            ["light", "Light", Sun],
            ["dark", "Dark", Moon],
            ["system", "System", Monitor],
          ] as const
        ).map(([value, label, ItemIcon]) => (
          <DropdownMenuItem key={value} onSelect={() => setTheme(value)} aria-checked={theme === value}>
            <ItemIcon /> {label}
            {mounted && theme === value ? <span className="ml-auto text-xs text-muted-foreground">Active</span> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function TopBar({
  pending,
  business,
  demo,
  canSignOut,
  onOpenMenu,
}: {
  pending: number;
  business?: string;
  demo?: boolean;
  canSignOut: boolean;
  onOpenMenu: React.ReactNode;
}) {
  const { mutate: refreshSession } = useSession();
  const [resetting, setResetting] = React.useState(false);

  async function resetDemo() {
    if (!window.confirm("Reset this demo workspace? Your drafts, approvals and changes will be cleared.")) return;
    setResetting(true);
    try {
      await api.resetDemo();
      await refreshAll();
      toast.success("Demo workspace reset to the sample data.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reset the demo.");
    } finally {
      setResetting(false);
    }
  }

  async function signOut() {
    await api.logout().catch(() => undefined);
    await refreshSession();
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-5">
      {onOpenMenu}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate font-medium">{business ?? "PayPilot"}</span>
        {demo === undefined ? null : demo ? (
          <Badge variant="secondary" className="hidden sm:inline-flex">Demo workspace</Badge>
        ) : (
          <Badge variant="success" className="hidden sm:inline-flex">Live sandbox</Badge>
        )}
      </div>
      <Button asChild variant={pending > 0 ? "default" : "outline"} size="sm">
        <Link href="/app/approvals" aria-label={`${pending} to review`}>
          <Inbox />
          <span>{pending}</span>
          <span className="hidden sm:inline">to review</span>
        </Link>
      </Button>
      {demo ? (
        <Button variant="ghost" size="sm" onClick={resetDemo} disabled={resetting} aria-label="Reset demo">
          {resetting ? <Loader2 className="animate-spin" /> : <RotateCcw />}
          <span className="hidden md:inline">Reset demo</span>
        </Button>
      ) : null}
      <ThemeMenu />
      {canSignOut ? (
        <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
          <LogOut />
        </Button>
      ) : null}
    </header>
  );
}

// --------------------------------------------------------------------- gate states
function CenteredCard({ children }: { children: React.ReactNode }) {
  return <main className="bg-grid flex min-h-screen items-center justify-center p-4">{children}</main>;
}

function SignIn({ loginAvailable }: { loginAvailable: boolean }) {
  const { mutate } = useSession();
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      await mutate();
      await refreshAll();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That password is not right." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <CenteredCard>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <Logo className="mb-2" />
          <CardTitle className="flex items-center gap-2 text-lg">
            <LockKeyhole className="size-4" aria-hidden="true" /> Owner sign in
          </CardTitle>
          <CardDescription>This workspace is private. Sign in to review and approve payments.</CardDescription>
        </CardHeader>
        {loginAvailable ? (
          <form onSubmit={submit}>
            <CardContent className="space-y-2">
              <Label htmlFor="owner-password">Password</Label>
              <Input
                id="owner-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "owner-password-error" : undefined}
                required
                autoFocus
              />
              {error ? (
                <p id="owner-password-error" role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              ) : null}
            </CardContent>
            <CardFooter className="mt-5">
              <Button type="submit" className="w-full" disabled={busy || !password}>
                {busy ? <Loader2 className="animate-spin" /> : null} Sign in
              </Button>
            </CardFooter>
          </form>
        ) : (
          <CardContent className="text-sm text-muted-foreground">
            Owner sign-in is not configured. Set <code className="font-mono">PAYPILOT_OWNER_PASSWORD</code> on the backend,
            or set <code className="font-mono">PAYPILOT_OPEN_ACCESS=true</code> for a private network.
          </CardContent>
        )}
      </Card>
    </CenteredCard>
  );
}

function Unreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <CenteredCard>
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <ServerCrash className="size-5 text-destructive" aria-hidden="true" /> Can&apos;t reach the PayPilot API
          </CardTitle>
          <CardDescription>The website is running, but the backend it talks to is not answering.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>Start the backend from the repository root:</p>
          <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
            uvicorn api.index:app --reload --port 8000
          </pre>
          <p className="text-muted-foreground">
            If it runs somewhere else, set <code className="font-mono">BACKEND_URL</code> in{" "}
            <code className="font-mono">web/.env.local</code> and restart <code className="font-mono">npm run dev</code>.
          </p>
        </CardContent>
        <CardFooter>
          <Button onClick={onRetry}>
            <RotateCcw /> Try again
          </Button>
        </CardFooter>
      </Card>
    </CenteredCard>
  );
}

function Loader() {
  return (
    <CenteredCard>
      <div role="status" className="flex items-center gap-3 text-sm text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-hidden="true" /> Loading PayPilot…
      </div>
    </CenteredCard>
  );
}

// ------------------------------------------------------------------------- shell
function Workspace({ children, openAccess }: { children: React.ReactNode; openAccess: boolean }) {
  const { data: health } = useHealth();
  const { actions: pendingActions } = useActions("pending");
  const pending = pendingActions?.length ?? 0;
  const [menuOpen, setMenuOpen] = React.useState(false);
  const navigatedFromMenu = React.useRef(false);

  const menuButton = (
    <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent
        className="bg-sidebar p-0"
        onCloseAutoFocus={(e) => {
          // Escape or the close button return focus to the menu button; picking a page leaves it on the page.
          if (navigatedFromMenu.current) e.preventDefault();
          navigatedFromMenu.current = false;
        }}
      >
        <SheetTitle className="sr-only">Navigation</SheetTitle>
        <SheetDescription className="sr-only">Go to a section of PayPilot</SheetDescription>
        <SidebarBody
          pending={pending}
          version={health?.version}
          onNavigate={() => {
            navigatedFromMenu.current = true;
            setMenuOpen(false);
          }}
        />
      </SheetContent>
    </Sheet>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 border-r border-sidebar-border bg-sidebar lg:block">
        <div className="sticky top-0 h-screen overflow-y-auto">
          <SidebarBody pending={pending} version={health?.version} />
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          pending={pending}
          business={health?.business}
          demo={health?.demo_mode}
          canSignOut={!openAccess}
          onOpenMenu={menuButton}
        />
        <main className="mx-auto w-full min-w-0 max-w-6xl flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: session, error, mutate } = useSession();

  // The loader only shows before the FIRST session response; later revalidations keep the current screen.
  if (!session && !error) return <Loader />;
  if (!session) {
    const unreachable = !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
    if (unreachable) return <Unreachable onRetry={() => void mutate()} />;
    return <SignIn loginAvailable={false} />;
  }
  if (!session.authenticated) return <SignIn loginAvailable={session.login_available} />;
  return <Workspace openAccess={session.open_access}>{children}</Workspace>;
}
