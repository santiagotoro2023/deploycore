import { FormEvent, useState } from "react";
import { api, ApiError, setToken } from "../api/client";
import AuthBackground from "../components/AuthBackground";
import BrandMark from "../components/BrandMark";

const STEPS = ["Instance", "Admin account"];

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0);
  const [instanceName, setInstanceName] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function finish(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!displayName || !username || !password || !confirmPassword) {
      setError("Fill in your name, username, and password.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      const { access_token } = await api.post<{ access_token: string }>("/setup", {
        instance_name: instanceName,
        admin_username: username,
        admin_display_name: displayName,
        admin_email: email || null,
        admin_password: password,
      });
      setToken(access_token);
      onComplete();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Setup failed.");
      setSubmitting(false);
    }
  }

  return (
    <AuthBackground>
      <div className="w-96 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-6 shadow-sm">
        <div className="mb-4 flex justify-center">
          <BrandMark size={56} />
        </div>
        <div className="mb-1 text-center text-base font-semibold tracking-tight text-neutral-900 dark:text-neutral-100">Set up your instance</div>
        <p className="mb-6 text-center text-xs text-neutral-500">
          This runs once. You can rename the instance later from Settings.
        </p>

        <div className="mb-6 flex gap-2 text-xs">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`flex-1 rounded-full px-2 py-1 text-center ${
                i === step
                  ? "bg-blue-600 text-white"
                  : i < step
                    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                    : "bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400"
              }`}
            >
              {i + 1}. {s}
            </div>
          ))}
        </div>

        <form noValidate onSubmit={step === 0 ? (e) => { e.preventDefault(); setStep(1); } : finish}>
          {step === 0 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">MSP / instance name</label>
              <input
                autoFocus
                placeholder="Acme Managed Services"
                className="mb-4 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={instanceName}
                onChange={(e) => setInstanceName(e.target.value)}
              />
              <p className="mb-4 text-xs text-neutral-500">
                This is your own organization: it manages every customer organization you add afterward. It is
                shown in the sidebar and on the sign-in screen.
              </p>
              <button
                type="submit"
                disabled={!instanceName}
                className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                Continue
              </button>
            </div>
          )}

          {step === 1 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Your name</label>
              <input
                autoFocus
                className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Username</label>
              <input
                className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Email (optional, for future notifications)</label>
              <input
                type="email"
                className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Password</label>
              <input
                type="password"
                className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Confirm password</label>
              <input
                type="password"
                className="mb-4 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
              {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-2 text-sm"
                  onClick={() => setStep(0)}
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex-1 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {submitting ? "Setting up..." : "Finish setup"}
                </button>
              </div>
            </div>
          )}
        </form>
      </div>
    </AuthBackground>
  );
}
