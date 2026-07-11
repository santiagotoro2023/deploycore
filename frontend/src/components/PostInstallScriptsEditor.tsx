import { Plus, Trash2 } from "lucide-react";

export interface PostInstallScriptForm {
  name: string;
  script_text: string;
}

/** Named, ordered PowerShell scripts (run over WinRM in list order - see
 * WinRMClient.run_ps / provision.py's _run_post_install_scripts), shared
 * by DeploymentTemplate and DiskLayout, both of which store the same
 * {name, script_text} shape. A monospace textarea per script rather than
 * a single shared box: each one is its own independent command run in
 * its own PowerShell invocation, and pasting several unrelated scripts
 * into one text area would blur where one ends and the next begins. */
export default function PostInstallScriptsEditor({
  scripts,
  onChange,
}: {
  scripts: PostInstallScriptForm[];
  onChange: (scripts: PostInstallScriptForm[]) => void;
}) {
  function addScript() {
    onChange([...scripts, { name: `Script ${scripts.length + 1}`, script_text: "" }]);
  }

  function updateScript(index: number, patch: Partial<PostInstallScriptForm>) {
    onChange(scripts.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  function removeScript(index: number) {
    onChange(scripts.filter((_, i) => i !== index));
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="block text-xs font-medium text-neutral-600 dark:text-neutral-400">Post-install scripts</label>
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
          onClick={addScript}
        >
          <Plus size={13} strokeWidth={2} />
          Add script
        </button>
      </div>
      {scripts.length === 0 && <p className="text-xs text-neutral-400">No post-install scripts.</p>}
      {scripts.map((s, i) => (
        <div key={i} className="mb-2 rounded-md border border-neutral-200 dark:border-neutral-700">
          <div className="flex items-center gap-2 border-b border-neutral-200 p-1.5 dark:border-neutral-700">
            <input
              placeholder="Name"
              className="flex-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-sm dark:bg-neutral-900"
              value={s.name}
              onChange={(e) => updateScript(i, { name: e.target.value })}
            />
            <button
              type="button"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800 hover:text-red-600"
              onClick={() => removeScript(i)}
            >
              <Trash2 size={14} strokeWidth={1.75} />
            </button>
          </div>
          <textarea
            spellCheck={false}
            placeholder={"PowerShell, run over WinRM.\nPaste one or more separate commands, one per line."}
            className="h-28 w-full resize-y rounded-b-md bg-transparent px-2 py-1.5 font-mono text-xs outline-none dark:bg-neutral-900"
            value={s.script_text}
            onChange={(e) => updateScript(i, { script_text: e.target.value })}
          />
        </div>
      ))}
    </div>
  );
}
