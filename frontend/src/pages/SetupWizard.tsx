import { FormEvent, useState } from "react";
import { api, ApiError, setToken } from "../api/client";

const STEPS = ["Instance", "Admin account"];

export default function SetupWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0);
  const [instanceName, setInstanceName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function finish(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      const { access_token } = await api.post<{ access_token: string }>("/setup", {
        instance_name: instanceName,
        admin_display_name: displayName,
        admin_email: email,
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
    <div className="flex min-h-screen items-center justify-center bg-neutral-50">
      <div className="w-96 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
        <div className="mb-1 text-center text-base font-semibold tracking-tight">Set up your instance</div>
        <p className="mb-6 text-center text-xs text-neutral-500">
          This runs once. You can rename the instance later from Settings.
        </p>

        <div className="mb-6 flex gap-2 text-xs">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`flex-1 rounded-full px-2 py-1 text-center ${
                i === step ? "bg-neutral-900 text-white" : i < step ? "bg-emerald-100 text-emerald-700" : "bg-neutral-100 text-neutral-500"
              }`}
            >
              {i + 1}. {s}
            </div>
          ))}
        </div>

        <form onSubmit={step === 0 ? (e) => { e.preventDefault(); setStep(1); } : finish}>
          {step === 0 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600">MSP / instance name</label>
              <input
                required
                autoFocus
                placeholder="Acme Managed Services"
                className="mb-4 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                value={instanceName}
                onChange={(e) => setInstanceName(e.target.value)}
              />
              <p className="mb-4 text-xs text-neutral-500">
                This is your own organization — it manages every customer organization you add afterward. It is
                shown in the sidebar and on the sign-in screen.
              </p>
              <button
                type="submit"
                disabled={!instanceName}
                className="w-full rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
              >
                Continue
              </button>
            </div>
          )}

          {step === 1 && (
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600">Your name</label>
              <input
                required
                autoFocus
                className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600">Email</label>
              <input
                required
                type="email"
                className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600">Password</label>
              <input
                required
                type="password"
                className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <label className="mb-1 block text-xs font-medium text-neutral-600">Confirm password</label>
              <input
                required
                type="password"
                className="mb-4 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
              {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded-md border border-neutral-300 px-3 py-2 text-sm"
                  onClick={() => setStep(0)}
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex-1 rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
                >
                  {submitting ? "Setting up..." : "Finish setup"}
                </button>
              </div>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
