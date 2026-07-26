// Privacy mode: hide every monetary amount (screen sharing, someone nearby).
// The masking itself is CSS (`[data-privacy="on"]` blurs the amount surfaces —
// layout untouched); this context exists for the few spots where money leaks
// through ATTRIBUTES (hover `title`s), which CSS cannot blur.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const KEY = "cashato.privacy.v1";

const Ctx = createContext<{ hidden: boolean; toggle: () => void }>({
  hidden: false,
  toggle: () => {},
});

export function PrivacyProvider({ children }: { children: ReactNode }) {
  const [hidden, setHidden] = useState(() => localStorage.getItem(KEY) === "on");
  useEffect(() => {
    document.documentElement.setAttribute("data-privacy", hidden ? "on" : "off");
    localStorage.setItem(KEY, hidden ? "on" : "off");
  }, [hidden]);
  return (
    <Ctx.Provider value={{ hidden, toggle: () => setHidden((v) => !v) }}>{children}</Ctx.Provider>
  );
}

export function usePrivacy() {
  return useContext(Ctx);
}
