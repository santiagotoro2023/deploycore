function initialsFor(displayName: string): string {
  const parts = displayName.trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}

export default function Avatar({
  userId,
  displayName,
  hasAvatar,
  size = 32,
  cacheBust,
}: {
  userId: string;
  displayName: string;
  hasAvatar: boolean;
  size?: number;
  cacheBust?: number;
}) {
  if (!hasAvatar) {
    return (
      <div
        style={{ width: size, height: size, fontSize: size * 0.4 }}
        className="flex shrink-0 items-center justify-center rounded-full bg-blue-100 font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300"
      >
        {initialsFor(displayName)}
      </div>
    );
  }
  const src = `/api/users/${userId}/avatar${cacheBust ? `?v=${cacheBust}` : ""}`;
  return (
    <img
      src={src}
      alt={displayName}
      style={{ width: size, height: size }}
      className="shrink-0 rounded-full object-cover"
    />
  );
}
