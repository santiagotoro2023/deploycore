import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { WIKI_CATEGORIES, WikiArticle } from "../wiki/content";

const ALL_ARTICLES = WIKI_CATEGORIES.flatMap((c) => c.articles.map((a) => ({ ...a, category: c.label })));

export default function Wiki() {
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState(ALL_ARTICLES[0]?.id ?? "");

  const filteredCategories = useMemo(() => {
    if (!query.trim()) return WIKI_CATEGORIES;
    const q = query.trim().toLowerCase();
    return WIKI_CATEGORIES.map((c) => ({
      ...c,
      articles: c.articles.filter((a) => a.title.toLowerCase().includes(q)),
    })).filter((c) => c.articles.length > 0);
  }, [query]);

  const active: WikiArticle | undefined = ALL_ARTICLES.find((a) => a.id === activeId);

  return (
    <div className="flex h-[calc(100vh-6rem)] gap-6">
      <aside className="w-64 shrink-0 overflow-y-auto">
        <h1 className="mb-3 text-lg font-semibold">Documentation</h1>
        <div className="relative mb-3">
          <Search size={14} strokeWidth={1.75} className="pointer-events-none absolute left-2.5 top-2.5 text-neutral-400" />
          <input
            className="w-full rounded-md border border-neutral-300 py-1.5 pl-8 pr-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
            placeholder="Search articles..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <nav className="space-y-4">
          {filteredCategories.map((c) => (
            <div key={c.id}>
              <div className="mb-1 px-1 text-xs font-semibold uppercase tracking-wide text-neutral-400 dark:text-neutral-500">
                {c.label}
              </div>
              <div className="space-y-0.5">
                {c.articles.map((a) => (
                  <button
                    key={a.id}
                    onClick={() => setActiveId(a.id)}
                    className={`block w-full rounded-md px-2 py-1.5 text-left text-sm ${
                      activeId === a.id
                        ? "bg-blue-600 text-white"
                        : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
                    }`}
                  >
                    {a.title}
                  </button>
                ))}
              </div>
            </div>
          ))}
          {filteredCategories.length === 0 && (
            <p className="px-1 text-sm text-neutral-400">No articles match "{query}".</p>
          )}
        </nav>
      </aside>

      <div className="min-w-0 flex-1 overflow-y-auto pb-8">
        {active ? (
          <article className="max-w-3xl space-y-6">
            <h2 className="text-xl font-semibold">{active.title}</h2>

            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-400">
                Quick overview
              </div>
              <div className="space-y-2">{active.overview}</div>
            </div>

            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                Deep dive
              </div>
              <div className="space-y-3">{active.deepDive}</div>
            </div>
          </article>
        ) : (
          <p className="text-sm text-neutral-500">Select an article.</p>
        )}
      </div>
    </div>
  );
}
