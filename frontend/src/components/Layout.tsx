import {
  BookOpen,
  Building2,
  Disc,
  FileText,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Moon,
  Rocket,
  ScrollText,
  Server,
  Settings as SettingsIcon,
  Shield,
  Sun,
  Users as UsersIcon,
  Webhook as WebhookIcon,
} from "lucide-react";
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
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/disk-layouts", label: "Disk Layouts", icon: HardDrive },
  { to: "/hypervisors", label: "Hypervisors", icon: Server },
  { to: "/iso-assets", label: "ISO Assets", icon: Disc },
  { to: "/webhooks", label: "Webhooks", icon: WebhookIcon },
  { to: "/users", label: "Users", icon: UsersIcon, globalAdminOnly: true },
  { to: "/account", label: "Account", icon: Shield },
  { to: "/audit-log", label: "Audit Log", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const { organizations, selectedOrgId, selectOrg } = useOrg();
  const { name: instanceName, hasLogo } = useInstanceInfo();
  const { theme, toggle } = useTheme();

  const navItems = NAV_ITEMS.filter(
    (item) => !item.globalAdminOnly || (user && roleAtLeast(user.global_role, "admin")),
  );

  return (
    <div className="flex min-h-screen bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <aside className="flex w-60 shrink-0 flex-col border-r border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <div className="border-b border-neutral-200 p-4 dark:border-neutral-800">
          <div className="flex items-center gap-2">
            {hasLogo ? (
              <img src="/api/instance/logo" alt="" className="h-9 w-9 shrink-0 object-contain" />
            ) : (
              <BrandMark size={34} />
            )}
            <div className="truncate text-sm font-semibold tracking-tight">{instanceName}</div>
          </div>
          <Select
            className="mt-2 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1.5 text-sm dark:bg-neutral-900"
            value={selectedOrgId ?? ""}
            onChange={(e) => selectOrg(e.target.value)}
          >
            {organizations.length === 0 && <option value="">No organizations</option>}
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </Select>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm ${
                  isActive
                    ? "bg-blue-600 text-white"
                    : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
                }`
              }
            >
              <item.icon size={16} strokeWidth={1.75} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-neutral-200 p-4 text-sm dark:border-neutral-800">
          <div className="flex items-center gap-2.5">
            {user && <Avatar userId={user.id} displayName={user.display_name} hasAvatar={user.has_avatar} size={32} />}
            <div className="min-w-0">
              <div className="truncate text-neutral-700 dark:text-neutral-300">{user?.display_name}</div>
              <div className="truncate text-xs text-neutral-400">@{user?.username}</div>
            </div>
          </div>
          <button
            className="mt-2 flex items-center gap-1.5 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
            onClick={logout}
          >
            <LogOut size={14} strokeWidth={1.75} />
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="flex items-center justify-end gap-3 border-b border-neutral-200 bg-white px-8 py-2 dark:border-neutral-800 dark:bg-neutral-900">
          <button
            className="rounded-md p-1.5 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
            onClick={toggle}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
          </button>
          <NotificationBell />
        </div>
        <div className="min-h-full p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
