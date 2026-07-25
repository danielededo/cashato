// Frontend i18n. Two concerns:
//   1) CATEGORY labels — the category *codes* are language-neutral; the API
//      localizes labels for data it returns, but the editable selectors list ALL
//      codes (including ones absent from the data), so we keep a local dictionary.
//   2) UI chrome — menus, titles, buttons, table headers. `t(key)` resolves them
//      for the active language.
import { useCallback } from "react";
import type { Lang } from "../api/types";
import { useLang } from "./lang";

export const CATEGORY_LABELS: Record<string, { it: string; en: string }> = {
  groceries: { it: "Spesa", en: "Groceries" },
  dining: { it: "Ristorazione", en: "Dining" },
  transport: { it: "Trasporti", en: "Transport" },
  bills: { it: "Bollette", en: "Bills & utilities" },
  subscriptions: { it: "Abbonamenti", en: "Subscriptions" },
  salary: { it: "Stipendio", en: "Salary" },
  rent: { it: "Affitti", en: "Rent" },
  health: { it: "Salute", en: "Health" },
  shopping: { it: "Acquisti", en: "Shopping" },
  transfers: { it: "Trasferimenti", en: "Transfers" },
  investments: { it: "Investimenti", en: "Investments" },
  crypto: { it: "Crypto", en: "Crypto" },
  cash: { it: "Contanti", en: "Cash" },
  fees: { it: "Commissioni", en: "Fees" },
  other: { it: "Altro", en: "Other" },
};

export function catLabel(code: string | null | undefined, lang: Lang): string {
  if (!code) return lang === "it" ? "Altro" : "Other";
  return CATEGORY_LABELS[code]?.[lang] ?? code;
}

type Dict = Record<string, { it: string; en: string }>;

const UI: Dict = {
  // nav
  "nav.dashboard": { it: "Cruscotto", en: "Dashboard" },
  "nav.transactions": { it: "Movimenti", en: "Transactions" },
  "nav.review": { it: "Revisione", en: "Review" },
  "nav.investments": { it: "Investimenti", en: "Investments" },
  "nav.manage": { it: "Gestione", en: "Manage" },
  "nav.upload": { it: "Carica", en: "Upload" },

  // common
  "common.loading": { it: "Caricamento…", en: "Loading…" },
  "common.all": { it: "Tutte", en: "All" },
  "common.income": { it: "Entrate", en: "Income" },
  "common.expense": { it: "Uscite", en: "Expense" },
  "common.category": { it: "Categoria", en: "Category" },
  "common.date": { it: "Data", en: "Date" },
  "common.description": { it: "Descrizione", en: "Description" },
  "common.amount": { it: "Importo", en: "Amount" },
  "common.account": { it: "Conto", en: "Account" },
  "common.source": { it: "Fonte", en: "Source" },
  "common.prev": { it: "← Prec", en: "← Prev" },
  "common.next": { it: "Succ →", en: "Next →" },

  // dashboard
  "dash.compare": { it: "Confronta", en: "Compare" },
  "dash.compareTitle": {
    it: "Confronta ogni metrica col periodo precedente di pari durata",
    en: "Compare each metric to the equal-length period immediately before",
  },
  "dash.reconciling": { it: "Riconciliazione…", en: "Reconciling…" },
  "kpi.net": { it: "Flusso netto", en: "Net flow" },
  "kpi.income": { it: "Entrate", en: "Income" },
  "kpi.expense": { it: "Uscite", en: "Expense" },
  "kpi.savings": { it: "Tasso di risparmio", en: "Savings rate" },
  "kpi.avg": { it: "Media / mese", en: "Avg / month" },
  "kpi.movements": { it: "Movimenti", en: "Movements" },
  "panel.spendingOverTime": { it: "Spesa nel tempo", en: "Spending over time" },
  "panel.spendingOverTime.hint": { it: "Top {n} categorie · mensile, impilato", en: "Top {n} categories · monthly, stacked" },
  "panel.categories": { it: "Categorie", en: "Categories" },
  "panel.categories.hint": { it: "Clicca per esplorare i movimenti", en: "Click to drill into transactions" },
  "panel.incomeExpense": { it: "Entrate vs uscite", en: "Income vs expense" },
  "panel.intensity": { it: "Intensità di spesa", en: "Spending intensity" },
  "panel.intensity.hint": { it: "Categoria × mese · clicca una cella", en: "Category × month · click a cell" },

  // transactions
  "tx.search": { it: "Cerca nelle descrizioni…", en: "Search descriptions…" },
  "tx.allSources": { it: "Tutte le fonti", en: "All sources" },
  "tx.filters": { it: "Filtri", en: "Filters" },
  "tx.range": { it: "Intervallo", en: "Range" },
  "tx.from": { it: "Da", en: "From" },
  "tx.to": { it: "A", en: "To" },
  "tx.transfers": { it: "Trasferimenti", en: "Transfers" },
  "tx.include": { it: "includi", en: "include" },
  "tx.exclude": { it: "escludi", en: "exclude" },
  "tx.custom": { it: "Personalizzato", en: "Custom" },
  "tx.matchN": { it: "movimenti trovati", en: "movements match" },
  "tx.noFilters": { it: "nessun filtro", en: "no filters" },
  "tx.emptyBig": { it: "Nessun movimento", en: "No movements match" },
  "tx.emptySub": { it: "Prova a togliere un filtro o allargare l'intervallo.", en: "Try clearing a filter or widening the date range." },

  // review
  "rev.trust": { it: "Affidabilità categorie", en: "Categorization trust" },
  "rev.trust.hint": { it: "Come è stata assegnata ogni categoria · campione dei {n} più recenti", en: "How each category was assigned · sample of the {n} most recent" },
  "rev.estimating": { it: "Stima in corso…", en: "Estimating…" },
  "rev.sure": { it: "Assegnate con certezza", en: "Assigned with certainty" },
  "rev.sure.foot": { it: "MCC · regole · manuale", en: "MCC · rule · manual" },
  "rev.conf": { it: "Confidenza media del modello", en: "Model-guessed avg confidence" },
  "rev.conf.foot": { it: "più alta = più sicura", en: "higher is safer" },
  "rev.other": { it: "Ancora non categorizzate", en: "Still uncategorized" },
  "rev.other.foot": { it: "“other” — rivedi sotto", en: "“other” — review below" },
  "rev.queue": { it: "Coda di revisione · non categorizzate", en: "Review queue · uncategorized" },
  "rev.total": { it: "{n} totali “other”", en: "{n} total “other”" },
  "rev.labelled": { it: " · {n} etichettate in questa sessione", en: " · {n} labelled this session" },
  "rev.setCategory": { it: "Assegna categoria", en: "Set category" },
  "rev.choose": { it: "scegli…", en: "choose…" },
  "rev.cleared": { it: "Coda svuotata — bene.", en: "Queue cleared — nice." },
  "rev.nothing": { it: "Niente da rivedere", en: "Nothing to review" },
  "rev.nothingSub": { it: "Nessun movimento non categorizzato in questo lotto. Ricarica o carica nuovi estratti.", en: "No uncategorized movements in this batch. Reload for more, or upload new statements." },
  "rev.mode.other": { it: "Non categorizzate", en: "Uncategorized" },
  "rev.mode.lowconf": { it: "Bassa confidenza", en: "Low confidence" },

  // sources (trust provenance)
  "src.manual": { it: "Manuale (tu)", en: "Manual (you)" },
  "src.mcc": { it: "Codice MCC", en: "MCC code" },
  "src.rule": { it: "Regola keyword", en: "Keyword rule" },
  "src.model": { it: "Stima del modello", en: "Model guess" },
  "src.default": { it: "Ripiego", en: "Fallback" },
  "src.unknown": { it: "Sconosciuto", en: "Unknown" },

  // investments
  "inv.title": { it: "Investimenti", en: "Investments" },
  "inv.invested": { it: "Capitale netto investito", en: "Net invested" },
  "inv.contrib": { it: "Contributi (uscite)", en: "Contributions (out)" },
  "inv.returns": { it: "Rientri (entrate)", en: "Returns (in)" },
  "inv.split": { it: "Investimenti vs crypto", en: "Investments vs crypto" },
  "inv.flow": { it: "Flusso mensile", en: "Monthly flow" },
  "inv.byAccount": { it: "Per conto", en: "By account" },
  "inv.empty": { it: "Nessun movimento di investimento", en: "No investment activity" },
  "inv.emptySub": { it: "Le categorie investments/crypto appariranno qui una volta caricati gli estratti.", en: "Investments/crypto categories will appear here once statements are loaded." },

  // upload
  "up.source": { it: "Fonte", en: "Source" },
  "up.autodetect": { it: "Rileva automaticamente", en: "Detect automatically" },
  "up.drop": { it: "Trascina un estratto qui, o clicca per scegliere", en: "Drop a statement here, or click to choose" },
  "up.uploading": { it: "Caricamento…", en: "Uploading…" },
  "up.hint": { it: "Revolut · Trade Republic · Intesa Sanpaolo — PDF, CSV o XLSX", en: "Revolut · Trade Republic · Intesa Sanpaolo — PDF, CSV or XLSX" },
  "up.recent": { it: "File recenti", en: "Recent files" },
  "up.when": { it: "Quando", en: "When" },
  "up.file": { it: "File", en: "File" },
  "up.status": { it: "Stato", en: "Status" },
  "up.new": { it: "Nuovi", en: "New" },
  "up.dup": { it: "Dup", en: "Dup" },
  "up.note": { it: "Nota", en: "Note" },
  "up.emptyBig": { it: "Ancora niente caricato", en: "Nothing uploaded yet" },
  "up.emptySub": { it: "Trascina il primo estratto qui sopra per iniziare.", en: "Drop your first statement above to start building the ledger." },

  // manage
  "mng.title": { it: "Gestione dati", en: "Manage data" },
  "mng.reprocess": { it: "Rielabora file", en: "Reprocess files" },
  "mng.reprocess.hint": { it: "Rimetti in coda l'ETL sui file archiviati (per sha256), senza ricaricarli.", en: "Re-run the ETL over stored files (by sha256), without re-uploading." },
  "mng.reprocessBtn": { it: "Rielabora", en: "Reprocess" },
  "mng.retrain": { it: "Riaddestramento modello", en: "Retrain the model" },
  "mng.retrain.hint": { it: "Passi guidati (offline, sulla GPU host — non in cluster).", en: "Guided steps (offline, on the host GPU — not in-cluster)." },
  "mng.reset": { it: "Reset dati", en: "Reset data" },
  "mng.reset.hint": { it: "Operazione distruttiva. Scrivi RESET per confermare.", en: "Destructive. Type RESET to confirm." },
  "mng.reset.keep": { it: "Tieni le etichette apprese (consigliato)", en: "Keep learned labels (recommended)" },
  "mng.reset.all": { it: "Cancella tutto, training incluso", en: "Wipe everything, training included" },
  "mng.resetBtn": { it: "Cancella dati", en: "Reset data" },
  "mng.notDeployed": { it: "Endpoint non ancora disponibile (serve deploy del backend).", en: "Endpoint not available yet (needs a backend deploy)." },

  // home greeting (falls back to the impersonal form when no statement named a holder)
  "home.hello": { it: "Ciao, {name}", en: "Hi, {name}" },
  "home.helloAnon": { it: "I tuoi conti", en: "Your accounts" },
  "home.mixed": { it: "Attenzione: gli estratti caricati risultano intestati a {n} persone diverse.", en: "Heads up: the loaded statements name {n} different people." },
  "home.mixed.hint": { it: "normale per un conto cointestato, altrimenti controlla di non aver caricato estratti di qualcun altro.", en: "normal for a joint account, otherwise check you have not loaded someone else's statements." },
  "acc.title": { it: "Conti", en: "Accounts" },
  "acc.hint": { it: "Nome, banca e intestazione ricavati dagli estratti. Puoi rinominarli.", en: "Name, bank and holding read off the statements. You can rename them." },
  "acc.rename": { it: "Rinomina", en: "Rename" },
  "acc.reset": { it: "Ripristina", en: "Reset" },
  "acc.derived": { it: "nome ricavato", en: "derived name" },
  "acc.custom": { it: "rinominato", en: "renamed" },
  "acc.movements": { it: "movimenti", en: "movements" },
  "acc.noMeta": { it: "nessun estratto descrive questo conto", en: "no statement describes this account" },
  "home.subtitle": { it: "Ecco come sono andate le tue finanze.", en: "Here is how your finances are doing." },
};

export function t(key: string, lang: Lang, vars?: Record<string, string | number>): string {
  const entry = UI[key];
  let s = entry ? entry[lang] : key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v));
  return s;
}

/** Hook returning a bound translator for the active language. */
export function useT() {
  const { lang } = useLang();
  const tr = useCallback((key: string, vars?: Record<string, string | number>) => t(key, lang, vars), [lang]);
  return { t: tr, lang };
}
