import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Sidebar, Toasts, Topbar } from "./components/Layout";
import { api } from "./lib/api";
import { EventsProvider } from "./lib/events";
import Analytics from "./pages/Analytics";
import Candidates from "./pages/Candidates";
import Dashboard from "./pages/Dashboard";
import Editor from "./pages/Editor";
import Models from "./pages/Models";
import Onboarding from "./pages/Onboarding";
import Published from "./pages/Published";
import Queue from "./pages/Queue";
import Schedule from "./pages/Schedule";
import Settings from "./pages/Settings";
import ShortDetail from "./pages/ShortDetail";
import Shorts from "./pages/Shorts";
import Sources from "./pages/Sources";
import Templates from "./pages/Templates";
import VideoDetail from "./pages/VideoDetail";
import Videos from "./pages/Videos";

function Booting({ error }: { error?: boolean }) {
  return (
    <div className="center" style={{ height: "100vh", background: "var(--bg)" }}>
      <div className="col" style={{ alignItems: "center", gap: 14 }}>
        <div className="brand-logo" style={{ width: 54, height: 54, borderRadius: 16 }}>
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none"><rect x="6" y="2" width="12" height="20" rx="3.5" stroke="#fff" strokeWidth="2.2" /><path d="M10.5 8.5l4.5 3.5-4.5 3.5z" fill="#fff" /></svg>
        </div>
        <div className="row"><Loader2 size={15} className="spin" /><span className="muted">{error ? "Waiting for the ShortForge engine…" : "Starting ShortForge…"}</span></div>
        {error && <div className="tiny faint" style={{ maxWidth: 380, textAlign: "center" }}>
          The local engine (Python backend) is not reachable at 127.0.0.1:8756 yet. The desktop app starts it automatically;
          when running the UI on its own, start it with <span className="kbd">shortforge-server</span>.
        </div>}
      </div>
    </div>
  );
}

export default function App() {
  const loc = useLocation();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: true, retryDelay: 1200, refetchInterval: (q) => (q.state.data ? 30000 : 1500) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, enabled: !!health.data });
  if (!health.data) return <Booting error={health.failureCount > 2} />;
  if (!settings.data) return <Booting />;
  if (!settings.data.general.first_run_complete && loc.pathname !== "/welcome") return <Navigate to="/welcome" replace />;
  if (loc.pathname === "/welcome") {
    return (
      <EventsProvider>
        <Onboarding />
        <Toasts />
      </EventsProvider>
    );
  }
  return (
    <EventsProvider>
      <div className="app">
        <Sidebar />
        <Topbar />
        <main className="main">
          <AnimatePresence mode="wait">
            <motion.div key={loc.pathname.split("/").slice(0, 2).join("/")} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
              <Routes location={loc}>
                <Route path="/" element={<Dashboard />} />
                <Route path="/sources" element={<Sources />} />
                <Route path="/videos" element={<Videos />} />
                <Route path="/videos/:id" element={<VideoDetail />} />
                <Route path="/candidates" element={<Candidates />} />
                <Route path="/shorts" element={<Shorts />} />
                <Route path="/shorts/:id" element={<ShortDetail />} />
                <Route path="/editor" element={<Editor />} />
                <Route path="/editor/:id" element={<Editor />} />
                <Route path="/queue" element={<Queue />} />
                <Route path="/schedule" element={<Schedule />} />
                <Route path="/published" element={<Published />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/templates" element={<Templates />} />
                <Route path="/models" element={<Models />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toasts />
    </EventsProvider>
  );
}
