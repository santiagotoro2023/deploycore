import ConnectedDots from "./ConnectedDots";

/** The decorative blurred-blob + connected-dots layer shared by
 * AuthBackground (login/setup, full intensity) and any page that wants a
 * quieter version of the same effect behind real working content (the
 * Dashboard). Always pointer-events-none and absolutely positioned to
 * fill its nearest `relative` ancestor. */
export default function AmbientBackground({ subtle = false }: { subtle?: boolean }) {
  const blobOpacity = subtle ? "opacity-50" : "";
  return (
    <div className={`pointer-events-none absolute inset-0 overflow-hidden ${blobOpacity}`}>
      <div className="auth-bg-blob-a absolute -left-24 -top-24 h-96 w-96 rounded-full bg-blue-300/40 blur-3xl dark:bg-blue-800/30" />
      <div className="auth-bg-blob-b absolute -right-32 top-1/3 h-[28rem] w-[28rem] rounded-full bg-sky-300/30 blur-3xl dark:bg-sky-900/25" />
      <div className="auth-bg-blob-c absolute -bottom-32 left-1/4 h-80 w-80 rounded-full bg-blue-200/40 blur-3xl dark:bg-blue-950/40" />
      {!subtle && <ConnectedDots />}
      {subtle && <ConnectedDots opacity={0.35} />}
    </div>
  );
}
