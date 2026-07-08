import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import AuthBackground from "../components/AuthBackground";
import BrandMark from "../components/BrandMark";
import { useAuth } from "../state/auth";
import { useInstanceInfo } from "../state/instance";

export default function Login() {
  const { login, loginTotp } = useAuth();
  const navigate = useNavigate();
  const { name: instanceName, hasLogo } = useInstanceInfo();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username || !password) {
      setError("Enter your username and password.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await login(username, password);
      if (result.requiresTotp) {
        setTicket(result.ticket ?? null);
      } else {
        navigate("/");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign in.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onSubmitTotp(e: FormEvent) {
    e.preventDefault();
    if (!ticket) return;
    setError(null);
    if (!code) {
      setError("Enter the 6-digit code from your authenticator app.");
      return;
    }
    setSubmitting(true);
    try {
      await loginTotp(ticket, code);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthBackground>
      <form
        noValidate
        onSubmit={ticket ? onSubmitTotp : onSubmit}
        className="w-80 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-6 shadow-sm"
      >
        <div className="mb-6 flex flex-col items-center gap-2">
          {hasLogo ? (
            <img src="/api/instance/logo" alt="" className="max-h-16 max-w-full object-contain" />
          ) : (
            <BrandMark size={56} />
          )}
          <div className="text-center text-base font-semibold tracking-tight text-neutral-900 dark:text-neutral-100">{instanceName}</div>
        </div>
        {!ticket && (
          <>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Username</label>
            <input
              type="text"
              autoFocus
              className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Password</label>
            <input
              type="password"
              className="mb-4 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </>
        )}
        {ticket && (
          <>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Authenticator code</label>
            <input
              type="text"
              inputMode="numeric"
              autoFocus
              className="mb-4 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </>
        )}
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? "Signing in..." : ticket ? "Verify" : "Sign in"}
        </button>
      </form>
    </AuthBackground>
  );
}
