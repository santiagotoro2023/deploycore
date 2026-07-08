import { ReactNode } from "react";

export default function AuthBackground({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-neutral-50 dark:bg-neutral-950">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="auth-bg-blob-a absolute -left-24 -top-24 h-96 w-96 rounded-full bg-blue-300/40 blur-3xl dark:bg-blue-800/30" />
        <div className="auth-bg-blob-b absolute -right-32 top-1/3 h-[28rem] w-[28rem] rounded-full bg-sky-300/30 blur-3xl dark:bg-sky-900/25" />
        <div className="auth-bg-blob-c absolute -bottom-32 left-1/4 h-80 w-80 rounded-full bg-blue-200/40 blur-3xl dark:bg-blue-950/40" />
      </div>
      <div className="relative">{children}</div>
    </div>
  );
}
