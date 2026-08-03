import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { Lang } from "../api/types";
import { setFormatLocale } from "./format";

// Language drives the API `lang` param (localized category labels), the i18n
// dictionary, and the date/number locale (pushed into format.ts). Persisted so
// the choice survives reloads.
const LANG_KEY = "cashato.lang.v1";

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
}
const Ctx = createContext<LangCtx | null>(null);

function readLang(): Lang {
  return localStorage.getItem(LANG_KEY) === "en" ? "en" : "it";
}

export function LangProvider({ children }: { children: ReactNode }) {
  // The locale must be set before the first child renders a date.
  const [lang, setLangState] = useState<Lang>(() => {
    const l = readLang();
    setFormatLocale(l);
    return l;
  });
  const setLang = useCallback((l: Lang) => {
    setFormatLocale(l);
    setLangState(l);
    localStorage.setItem(LANG_KEY, l);
  }, []);
  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang(): LangCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useLang must be used within LangProvider");
  return c;
}
