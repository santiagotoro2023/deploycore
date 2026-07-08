import { useEffect, useState } from "react";
import { api } from "../api/client";

const DEFAULT_NAME = "DeployCore";

export function useInstanceName(): string {
  const [name, setName] = useState(DEFAULT_NAME);
  useEffect(() => {
    api
      .get<{ name: string }>("/instance")
      .then((r) => setName(r.name))
      .catch(() => undefined);
  }, []);
  return name;
}
