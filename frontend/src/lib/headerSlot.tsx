import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

// A single app bar hosts page-contextual controls (e.g. the dashboard period /
// compare). Pages render those controls through this portal so there is exactly
// ONE top bar, not a shell bar plus a per-page bar.
export const HeaderSlotContext = createContext<HTMLElement | null>(null);

export function useHeaderSlot(): HTMLElement | null {
  return useContext(HeaderSlotContext);
}

export function HeaderPortal({ children }: { children: ReactNode }) {
  const el = useHeaderSlot();
  return el ? createPortal(children, el) : null;
}
