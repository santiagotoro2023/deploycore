import { useEffect, useState } from "react";
import { api } from "../api/client";

const DEFAULT_NAME = "DeployCore";

interface InstanceInfo {
  name: string;
  hasLogo: boolean;
}

export function useInstanceInfo(): InstanceInfo {
  const [info, setInfo] = useState<InstanceInfo>({ name: DEFAULT_NAME, hasLogo: false });
  useEffect(() => {
    api
      .get<{ name: string; has_logo: boolean }>("/instance")
      .then((r) => setInfo({ name: r.name, hasLogo: r.has_logo }))
      .catch(() => undefined);
  }, []);
  return info;
}
