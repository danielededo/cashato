import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Review } from "./pages/Review";
import { Transactions } from "./pages/Transactions";
import { Upload } from "./pages/Upload";
import { Investments } from "./pages/Investments";
import { Manage } from "./pages/Manage";
import { HeaderSlotContext } from "./lib/headerSlot";
import { useT } from "./lib/i18n";
import { useLang } from "./lib/lang";

const NAV = [
  { to: "/dashboard", key: "nav.dashboard" },
  { to: "/transactions", key: "nav.transactions" },
  { to: "/review", key: "nav.review" },
  { to: "/investments", key: "nav.investments" },
  { to: "/manage", key: "nav.manage" },
  { to: "/upload", key: "nav.upload" },
];

const THEME_KEY = "cashato.theme.v1";
type Theme = "dark" | "light";

function readTheme(): Theme {
  return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
}

export function App() {
  // lazy init so localStorage is read once, not on every render
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const { lang, setLang } = useLang();
  const { t } = useT();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  return (
    <HeaderSlotContext.Provider value={slot}>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            cashato<span className="dot">.</span>
          </div>
          <nav>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
                {t(n.key)}
              </NavLink>
            ))}
          </nav>
          <div className="topbar-right">
            {/* page-contextual controls (e.g. dashboard period) portal in here */}
            <div className="topbar-slot" ref={setSlot} />
            <div className="segmented" role="group" aria-label="Language">
              <button aria-pressed={lang === "it"} onClick={() => setLang("it")}>IT</button>
              <button aria-pressed={lang === "en"} onClick={() => setLang("en")}>EN</button>
            </div>
            <button
              className="icon-btn"
              onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light" : "Dark"}
            >
              {theme === "dark" ? "☀" : "☾"}
            </button>
          </div>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/review" element={<Review />} />
            <Route path="/investments" element={<Investments />} />
            <Route path="/manage" element={<Manage />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </main>
      </div>
    </HeaderSlotContext.Provider>
  );
}
