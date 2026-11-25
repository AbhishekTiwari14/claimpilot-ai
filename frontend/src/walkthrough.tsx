import { CircleHelp, X } from 'lucide-react';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from './components/ui/button';

const steps = [
  ['report', 'Report'], ['photo', 'Photo'], ['confirm', 'Confirm'], ['knowledge', 'Explore sources'],
  ['policy', 'Review policy'], ['approval', 'Approve'], ['dispatch', 'Dispatch'],
] as const;
type Step = typeof steps[number][0];
type Status = 'done' | 'skipped';
type Progress = Partial<Record<Step, Status>>;
const storageKey = 'claimpilot.walkthrough';
type Saved = { progress: Progress; seen: string[]; disabled: boolean };
function read(): Saved {
  try { return JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') ?? { progress: {}, seen: [], disabled: false }; }
  catch { return { progress: {}, seen: [], disabled: false }; }
}
export function resetWalkthrough() { sessionStorage.removeItem(storageKey); }
const Context = createContext<{
  progress: Progress; disabled: boolean; record(step: Step, status?: Status): void; toggle(): void;
} | null>(null);
export const useWalkthrough = () => useContext(Context);

export function Walkthrough({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [saved, setSaved] = useState(read);
  const change = (update: (current: Saved) => Saved) => {
    const next = update(read());
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    setSaved(next);
  };
  return <Context.Provider value={enabled ? {
    progress: saved.progress, disabled: saved.disabled,
    record: (step, status = 'done') => {
      const current = read().progress[step];
      if (current !== status && current !== 'done') change(value => ({ ...value, progress: { ...value.progress, [step]: status } }));
    },
    toggle: () => change(value => ({ ...value, disabled: !value.disabled })),
  } : null}>{children}</Context.Provider>;
}

export function WalkthroughProgress() {
  const state = useWalkthrough();
  if (!state) return null;
  const next = steps.find(([id]) => !state.progress[id])?.[0];
  return <div className="walkthrough-bar">
    <ol aria-label="Walkthrough progress">{steps.map(([id, title]) => <li key={id} data-status={state.progress[id] || (id === next ? 'current' : 'next')}
      aria-current={id === next ? 'step' : undefined}>{title}{state.progress[id] === 'skipped' && <span className="sr-only"> skipped</span>}</li>)}</ol>
    <Button variant="ghost" size="sm" onClick={state.toggle}>{state.disabled ? 'Show guidance' : 'Hide guidance'}</Button>
  </div>;
}

export function Milestone({ step, when }: { step: Step; when: boolean }) {
  const state = useWalkthrough();
  useEffect(() => { if (when) state?.record(step); }, [step, when, state]);
  return null;
}

export function Guide({ step, children }: { step: string; children: ReactNode }) {
  const state = useWalkthrough();
  const target = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const enabled = !!state && !state.disabled;
  useEffect(() => {
    if (!enabled) { setOpen(false); return; }
    if (!target.current || read().seen.includes(step)) return;
    let revealed = false;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !revealed) {
        const saved = read();
        if (saved.disabled || saved.seen.includes(step)) { observer.disconnect(); return; }
        sessionStorage.setItem(storageKey, JSON.stringify({ ...saved, seen: [...saved.seen, step] }));
        revealed = true;
        setOpen(true);
      } else if (!entry.isIntersecting && revealed) {
        setOpen(false);
        observer.disconnect();
      }
    });
    observer.observe(target.current);
    return () => observer.disconnect();
  }, [enabled, step]);
  if (!state) return null;
  return <div className="guide-anchor" data-guide-step={step} ref={target}>
    {enabled && open && <div className="guide-note" role="note"><CircleHelp size={16} /><p>{children}</p>
      <Button size="icon" variant="ghost" title="Dismiss guidance" aria-label="Dismiss guidance" onClick={() => setOpen(false)}><X size={14} /></Button></div>}
  </div>;
}

export function Tip({ text }: { text: string }) {
  return <span className="term-tip" tabIndex={0} aria-label={text}><CircleHelp size={14} /><span role="tooltip">{text}</span></span>;
}
