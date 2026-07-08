import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, getToken, setToken } from "../api/client";
import { Role, User } from "../api/types";

interface AuthState {
  user: User | null;
  orgRoles: Record<string, Role>;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  effectiveRole: (orgId: string | null) => Role;
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

  async function login(email: string, password: string) {
    const { access_token } = await api.post<{ access_token: string }>("/auth/login", { email, password });
    setToken(access_token);
    await refreshMe();
  }

  function logout() {
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
    <AuthContext.Provider value={{ user, orgRoles, loading, login, logout, effectiveRole }}>
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
