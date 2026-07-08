import {
  Building2,
  Disc,
  FileText,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Rocket,
  ScrollText,
  Server,
  Settings as SettingsIcon,
  Users as UsersIcon,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth, roleAtLeast } from "../state/auth";
import { useInstanceName } from "../state/instance";
import { useOrg } from "../state/org";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true, icon: LayoutDashboard },
  { to: "/organizations", label: "Organizations", icon: Building2 },
  { to: "/deployments", label: "Deployments", icon: Rocket },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/disk-layouts", label: "Disk Layouts", icon: HardDrive },
  { to: "/hypervisors", label: "Hypervisors", icon: Server },
  { to: "/iso-assets", label: "ISO Assets", icon: Disc },
  { to: "/users", label: "Users", icon: UsersIcon, globalAdminOnly: true },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/audit-log", label: "Audit Log", icon: ScrollText },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const { organizations, selectedOrgId, selectOrg } = useOrg();
  const instanceName = useInstanceName();

  const navItems = NAV_ITEMS.filter(
    (item) => !item.globalAdminOnly || (user && roleAtLeast(user.global_role, "admin")),
  );

  return (
    <div className="flex min-h-screen bg-neutral-50 text-neutral-900">
      <aside className="flex w-60 shrink-0 flex-col border-r border-neutral-200 bg-white">
        <div className="border-b border-neutral-200 p-4">
          <div className="truncate text-sm font-semibold tracking-tight">{instanceName}</div>
          <select
            className="mt-2 w-full rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm"
            value={selectedOrgId ?? ""}
            onChange={(e) => selectOrg(e.target.value)}
          >
            {organizations.length === 0 && <option value="">No organizations</option>}
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        </div>
        <nav className="flex-1 space-y-0.5 p-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm ${
                  isActive ? "bg-neutral-900 text-white" : "text-neutral-600 hover:bg-neutral-100"
                }`
              }
            >
              <item.icon size={16} strokeWidth={1.75} />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-neutral-200 p-4 text-sm">
          <div className="truncate text-neutral-700">{user?.display_name}</div>
          <div className="truncate text-xs text-neutral-400">{user?.email}</div>
          <button
            className="mt-2 flex items-center gap-1.5 text-xs text-neutral-500 hover:text-neutral-900"
            onClick={logout}
          >
            <LogOut size={14} strokeWidth={1.75} />
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto p-8">
        <Outlet />
      </main>
    </div>
  );
}
