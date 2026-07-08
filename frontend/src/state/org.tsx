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

const SELECTED_ORG_KEY = "deploycore_selected_org";

export function OrgProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(localStorage.getItem(SELECTED_ORG_KEY));

  async function refresh() {
    const orgs = await api.get<Organization[]>("/organizations");
    setOrganizations(orgs);
    if (!selectedOrgId && orgs.length > 0) {
      selectOrg(orgs[0].id);
    }
  }

  useEffect(() => {
    if (user) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  function selectOrg(id: string) {
    setSelectedOrgId(id);
    localStorage.setItem(SELECTED_ORG_KEY, id);
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
