import { ReactNode } from "react";
import AmbientBackground from "./AmbientBackground";

export default function AuthBackground({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <AmbientBackground />
      <div className="relative">{children}</div>
    </div>
  );
}
