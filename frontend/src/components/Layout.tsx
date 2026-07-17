import {
  BookOpen,
  Building2,
  ChevronLeft,
  ChevronRight,
  Disc,
  FileText,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Moon,
  MonitorSmartphone,
  Package,
  Rocket,
  ScrollText,
  Server,
  Settings as SettingsIcon,
  Shield,
  Sun,
  Users as UsersIcon,
  Webhook as WebhookIcon,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import Avatar from "./Avatar";
import BrandMark from "./BrandMark";
import NotificationBell from "./NotificationBell";
import Select from "./Select";
import { useAuth, roleAtLeast } from "../state/auth";
import { useInstanceInfo } from "../state/instance";
import { useOrg } from "../state/org";
import { useTheme } from "../state/theme";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true, icon: LayoutDashboard },
  { to: "/wiki", label: "Documentation", icon: BookOpen },
  { to: "/organizations", label: "Organizations", icon: Building2 },
  { to: "/deployments", label: "Deployments", icon: Rocket },
  { to: "/remote-management", label: "Remote Management", icon: MonitorSmartphone },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/disk-layouts", label: "Disk Layouts", icon: HardDrive },
  { to: "/hypervisors", label: "Hypervisors", icon: Server },
  { to: "/iso-assets", label: "ISO Assets", icon: Disc },
  { to: "/app-assets", label: "App Assets", icon: Package },
  { to: "/webhooks", label: "Webhooks", icon: WebhookIcon },
  { to: "/users", label: "Users", icon: UsersIcon, globalAdminOnly: true },
  { to: "/account", label: "Account", icon: Shield },
  { to: "/audit-log", label: "Audit Log", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: SettingsIcon, adminOnly: true },
];

export default function Layout() {
  const { user, logout, effectiveRole } = useAuth();
  const { organizations, selectedOrgId, selectOrg, loaded: orgLoaded } = useOrg();
  const { name: instanceName, hasLogo } = useInstanceInfo();
  const { theme, toggle } = useTheme();

  // Persisted per-browser, not per-user/server - purely a screen-space
  // preference, not worth a settings row or an API round-trip. Collapsing
  // is a real width change (not just a visual scale), so anything on the
  // current page watching its own container size (RemoteSession's
  // ResizeObserver, for the embedded remote screen) picks this up on its
  // own with no extra wiring here.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar-collapsed") === "true");
  function toggleCollapsed() {
    setCollapsed((c) => {
      localStorage.setItem("sidebar-collapsed", String(!c));
      return !c;
    });
  }

  // Settings (instance cards + org-scoped "Deployment settings") is
  // admin-only end to end, not just disabled controls for lower roles -
  // readonly/operator shouldn't even know it exists. Their own Account
  // page (separate nav item) stays open to everyone regardless of role.
  const isEffectiveAdmin = roleAtLeast(effectiveRole(selectedOrgId), "admin");
  const navItems = NAV_ITEMS.filter((item) => {
    if (item.globalAdminOnly) return !!user && roleAtLeast(user.global_role, "admin");
    if (item.adminOnly) return isEffectiveAdmin;
    return true;
  });

  return (
    <div className="flex h-screen overflow-hidden bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <aside
        className={`relative flex shrink-0 flex-col border-r border-neutral-200 bg-white transition-[width] duration-150 dark:border-neutral-800 dark:bg-neutral-900 ${
          collapsed ? "w-14" : "w-60"
        }`}
      >
        {/* Floating on the border rather than inside the nav flow - stays
            reachable and in the same spot whether the sidebar is expanded
            or collapsed, the common pattern for this (VSCode, Notion, etc.)
            rather than a full-width row that would itself need its own
            collapsed-vs-expanded layout. */}
        <button
          className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-neutral-300 bg-white text-neutral-500 shadow-sm hover:text-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
          onClick={toggleCollapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight size={14} strokeWidth={1.75} /> : <ChevronLeft size={14} strokeWidth={1.75} />}
        </button>

        <div className="border-b border-neutral-200 p-4 dark:border-neutral-800">
          <div className={`flex items-center gap-2 ${collapsed ? "justify-center" : ""}`}>
            {hasLogo ? (
              <img src="/api/instance/logo" alt="" className="h-9 w-9 shrink-0 object-contain" />
            ) : (
              <BrandMark size={42} />
            )}
            {!collapsed && <div className="truncate text-lg font-semibold tracking-tight">{instanceName}</div>}
          </div>
          {!collapsed && (
            <Select
              className="mt-2 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1.5 text-sm dark:bg-neutral-900"
              value={selectedOrgId ?? ""}
              onChange={(e) => selectOrg(e.target.value)}
            >
              {organizations.length === 0 && <option value="">{orgLoaded ? "No organizations" : "Loading..."}</option>}
              {organizations.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </Select>
          )}
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto overflow-x-hidden p-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm ${collapsed ? "justify-center" : ""} ${
                  isActive
                    ? "bg-blue-600 text-white"
                    : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
                }`
              }
            >
              <item.icon size={16} strokeWidth={1.75} />
              {!collapsed && item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-neutral-200 p-4 text-sm dark:border-neutral-800">
          <div className={`flex items-center gap-2.5 ${collapsed ? "justify-center" : ""}`}>
            {user && <Avatar userId={user.id} displayName={user.display_name} hasAvatar={user.has_avatar} size={32} />}
            {!collapsed && (
              <div className="min-w-0">
                <div className="truncate text-neutral-700 dark:text-neutral-300">{user?.display_name}</div>
                <div className="truncate text-xs text-neutral-400">@{user?.username}</div>
              </div>
            )}
          </div>
          <button
            className={`mt-2 flex items-center gap-1.5 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100 ${
              collapsed ? "w-full justify-center" : ""
            }`}
            onClick={logout}
            title={collapsed ? "Sign out" : undefined}
          >
            <LogOut size={14} strokeWidth={1.75} />
            {!collapsed && "Sign out"}
          </button>
        </div>
      </aside>
      <main className="flex flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center justify-end gap-3 border-b border-neutral-200 bg-white px-8 py-2 dark:border-neutral-800 dark:bg-neutral-900">
          <button
            className="rounded-md p-1.5 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
            onClick={toggle}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
          </button>
          <NotificationBell />
        </div>
        <div className="flex-1 overflow-y-auto p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
