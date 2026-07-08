import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../state/auth";
import { useInstanceName } from "../state/instance";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const instanceName = useInstanceName();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign in.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-50">
      <form onSubmit={onSubmit} className="w-80 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
        <div className="mb-6 text-center text-base font-semibold tracking-tight">{instanceName}</div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Email</label>
        <input
          type="email"
          required
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Password</label>
        <input
          type="password"
          required
          className="mb-4 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
