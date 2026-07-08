import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "../api/client";
import { Organization } from "../api/types";
import { useAuth } from "./auth";

interface OrgState {
  organizations: Organization[];
  selectedOrgId: string | null;
  selectedOrg: Organization | null;
  selectOrg: (id: string) => void;
  refresh: () => Promise<void>;
}

const OrgContext = createContext<OrgState | null>(null);

const SELECTED_ORG_KEY_PREFIX = "deploycore_selected_org_";

export function OrgProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);

  function storageKey(): string | null {
    return user ? `${SELECTED_ORG_KEY_PREFIX}${user.id}` : null;
  }

  async function refresh() {
    const orgs = await api.get<Organization[]>("/organizations");
    setOrganizations(orgs);

    // The last-selected org is remembered per user (keyed by user id), never
    // shared across accounts on the same browser. Also re-validated against
    // this user's actual accessible list every time: a stale id (left over
    // from a previous user, or an org role that's since been revoked) must
    // never stick around silently, it would make every org-scoped page look
    // broken or empty for a perfectly valid user.
    const key = storageKey();
    const stored = key ? localStorage.getItem(key) : null;
    const stillValid = stored && orgs.some((o) => o.id === stored);
    const next = stillValid ? stored : (orgs[0]?.id ?? null);

    setSelectedOrgId(next);
    if (key) {
      if (next) localStorage.setItem(key, next);
      else localStorage.removeItem(key);
    }
  }

  useEffect(() => {
    if (user) {
      refresh();
    } else {
      setOrganizations([]);
      setSelectedOrgId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  function selectOrg(id: string) {
    setSelectedOrgId(id);
    const key = storageKey();
    if (key) localStorage.setItem(key, id);
  }

  const selectedOrg = organizations.find((o) => o.id === selectedOrgId) ?? null;

  return (
    <OrgContext.Provider value={{ organizations, selectedOrgId, selectedOrg, selectOrg, refresh }}>
      {children}
    </OrgContext.Provider>
  );
}

export function useOrg(): OrgState {
  const ctx = useContext(OrgContext);
  if (!ctx) throw new Error("useOrg must be used within OrgProvider");
  return ctx;
}
