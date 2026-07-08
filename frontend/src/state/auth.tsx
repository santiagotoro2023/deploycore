import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, getToken, setToken } from "../api/client";
import { Role, User } from "../api/types";

interface LoginResult {
  requiresTotp: boolean;
  ticket?: string;
}

interface AuthState {
  user: User | null;
  orgRoles: Record<string, Role>;
  loading: boolean;
  login: (username: string, password: string) => Promise<LoginResult>;
  loginTotp: (ticket: string, code: string) => Promise<void>;
  logout: () => void;
  logoutEverywhere: () => Promise<void>;
  effectiveRole: (orgId: string | null) => Role;
  refreshUser: () => Promise<void>;
}

const ROLE_ORDER: Record<Role, number> = { none: 0, readonly: 1, operator: 2, admin: 3 };

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [orgRoles, setOrgRoles] = useState<Record<string, Role>>({});
  const [loading, setLoading] = useState(true);

  async function refreshMe() {
    try {
      const me = await api.get<{ user: User; org_roles: Record<string, Role> }>("/auth/me");
      setUser(me.user);
      setOrgRoles(me.org_roles);
    } catch {
      setToken(null);
      setUser(null);
      setOrgRoles({});
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (getToken()) {
      refreshMe();
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function login(username: string, password: string): Promise<LoginResult> {
    const res = await api.post<{ access_token?: string; requires_totp?: boolean; ticket?: string }>(
      "/auth/login",
      { username, password },
    );
    if (res.requires_totp) {
      return { requiresTotp: true, ticket: res.ticket };
    }
    setToken(res.access_token as string);
    await refreshMe();
    return { requiresTotp: false };
  }

  async function loginTotp(ticket: string, code: string) {
    const { access_token } = await api.post<{ access_token: string }>("/auth/login/totp", { ticket, code });
    setToken(access_token);
    await refreshMe();
  }

  async function logout() {
    try {
      await api.post("/auth/logout");
    } catch {
      // best effort, clear local state regardless
    }
    setToken(null);
    setUser(null);
    setOrgRoles({});
  }

  async function logoutEverywhere() {
    await api.post("/auth/logout-all");
    setToken(null);
    setUser(null);
    setOrgRoles({});
  }

  function effectiveRole(orgId: string | null): Role {
    if (!user) return "none";
    const global = ROLE_ORDER[user.global_role];
    const org = orgId ? ROLE_ORDER[orgRoles[orgId] ?? "none"] : 0;
    return global >= org ? user.global_role : orgRoles[orgId as string];
  }

  return (
    <AuthContext.Provider
      value={{ user, orgRoles, loading, login, loginTotp, logout, logoutEverywhere, effectiveRole, refreshUser: refreshMe }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function roleAtLeast(role: Role, min: Role): boolean {
  return ROLE_ORDER[role] >= ROLE_ORDER[min];
}
