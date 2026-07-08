import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

interface Notification {
  id: string;
  deployment_id: string | null;
  message: string;
  read: boolean;
  created_at: string;
}

const POLL_INTERVAL_MS = 20000;

export default function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  async function refreshCount() {
    const { count } = await api.get<{ count: number }>("/notifications/unread-count");
    setUnreadCount(count);
  }

  useEffect(() => {
    refreshCount();
    const interval = setInterval(refreshCount, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function toggleOpen() {
    if (!open) {
      setNotifications(await api.get<Notification[]>("/notifications"));
    }
    setOpen(!open);
  }

  async function openNotification(n: Notification) {
    if (!n.read) {
      await api.post(`/notifications/${n.id}/read`);
      setUnreadCount((c) => Math.max(0, c - 1));
    }
    setOpen(false);
    if (n.deployment_id) navigate(`/deployments/${n.deployment_id}`);
  }

  async function markAllRead() {
    await api.post("/notifications/read-all");
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnreadCount(0);
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
        onClick={toggleOpen}
      >
        <Bell size={16} strokeWidth={1.75} />
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-600 px-1 text-[10px] font-medium text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-50 w-80 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 shadow-sm">
          <div className="flex items-center justify-between border-b border-neutral-100 px-3 py-2 dark:border-neutral-800">
            <span className="text-xs font-semibold text-neutral-700 dark:text-neutral-300">Notifications</span>
            {unreadCount > 0 && (
              <button className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={markAllRead}>
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-80 overflow-y-auto">
            {notifications.length === 0 && (
              <div className="p-4 text-center text-xs text-neutral-400">No notifications yet.</div>
            )}
            {notifications.map((n) => (
              <button
                key={n.id}
                onClick={() => openNotification(n)}
                className={`flex w-full items-start gap-2 border-b border-neutral-50 px-3 py-2.5 text-left text-xs last:border-0 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800 ${
                  n.read ? "text-neutral-500" : "text-neutral-800 dark:text-neutral-200"
                }`}
              >
                {!n.read && <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-600" />}
                <span>
                  {n.message}
                  <span className="mt-0.5 block text-[10px] text-neutral-400">
                    {new Date(n.created_at).toLocaleString()}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
