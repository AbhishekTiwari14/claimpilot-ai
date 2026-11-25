import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Check, ChevronDown, LoaderCircle } from 'lucide-react';
import { useRef, type ReactNode } from 'react';
import type { Analysis, Answer, Fact } from './api';

export const label = (value: string | null | undefined) => value
  ? value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase()) : '—';
export const shortID = (value: string) => value.slice(0, 8).toUpperCase();
export const localTime = (value: string | null) => value ? new Date(value).toLocaleString() : '—';
export const valueText = (value: string | string[] | null | undefined) => Array.isArray(value) ? value.join(', ') : value || '—';
export const providerName = (provider: string) => provider === 'groq' ? 'Live · Groq' : 'Offline fixture';

export function Panel({ title, caption, action, children }: { title: string; caption?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel"><header className="panel-head"><div><h2>{title}</h2>{caption && <p>{caption}</p>}</div>{action}</header>{children}</section>;
}
export function Badge({ value }: { value: string }) {
  const tone = /fail|reject|intervention|emergency|breach|conflict/.test(value) ? 'red' :
    /await|collect|pending|indexing/.test(value) ? 'amber' :
      /ready|approved|accept|complete|available|covered|answered|met/.test(value) ? 'green' : 'neutral';
  return <span className={`badge ${tone}`}><i />{label(value)}</span>;
}
export function Notice({ children, kind = 'info' }: { children: ReactNode; kind?: 'info' | 'error' | 'success' }) {
  const Icon = kind === 'success' ? Check : AlertCircle;
  return <div className={`notice ${kind}`} role={kind === 'error' ? 'alert' : 'status'}><Icon size={16} /> <span>{children}</span></div>;
}
export function Loading() {
  return <p className="loading" role="status"><LoaderCircle size={17} className="animate-spin" /> Loading…</p>;
}
export function Failure({ error }: { error: unknown }) {
  return error ? <Notice kind="error">{error instanceof Error ? error.message : 'The request failed.'}</Notice> : null;
}
export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><div>{children}</div></div>;
}
export function Facts({ facts, confirmedOnly = false }: { facts: Record<string, Fact>; confirmedOnly?: boolean }) {
  const entries = Object.entries(facts).filter(([, fact]) => !confirmedOnly || fact.status === 'customer_confirmed');
  return entries.length ? <dl className="facts">{entries.map(([field, fact]) =>
    <div key={field}><dt>{label(field)}</dt><dd>{valueText(fact.value)}{!confirmedOnly && fact.status === 'candidate' && <small>To confirm</small>}</dd></div>
  )}</dl> : <p className="muted">No {confirmedOnly ? 'confirmed ' : ''}facts yet.</p>;
}
export function Observations({ analyses }: { analyses: Analysis[] }) {
  return analyses.length ? <div className="stack">{analyses.map(analysis => <article className="inset" key={analysis.id}>
    <div className="row spread"><span className="mono">{shortID(analysis.id)}</span><Badge value={analysis.status} /></div>
    <p className="muted">{providerName(analysis.provider)} · {analysis.model}</p>
    {analysis.error_code && <Notice kind="error">{label(analysis.error_code)}</Notice>}
    {analysis.result?.observations.map(obs => <div className="observation" key={obs.image_id}>
      <h3>Photo {shortID(obs.image_id)} <span className="muted">· {label(obs.image_quality)}</span></h3>
      <p><strong>Visible:</strong> {valueText([...obs.visible.observed_damage_types.map(label), ...obs.visible.visible_indicators])}</p>
      <p><strong>Areas and materials:</strong> {valueText([...obs.visible.affected_areas, ...obs.visible.materials])}</p>
      <p><strong>Interpretation:</strong> {label(obs.interpretation.apparent_severity)} · {valueText(obs.interpretation.potential_safety_concerns.map(label))}</p>
      <p className="muted">{[...obs.limitations, ...obs.follow_up_questions].join(' ')}</p>
    </div>)}
    {analysis.result?.batch_limitations.map(item => <p key={item} className="muted">{label(item)}</p>)}
  </article>)}</div> : <p className="muted">No photo observations yet.</p>;
}
export function AnswerView({ answer }: { answer: Answer }) {
  return <div className="stack">
    <div className="row spread"><Badge value={answer.outcome} /><small>{providerName(answer.provider)}</small></div>
    {answer.insufficient_evidence ? <Notice>The available sources do not contain enough evidence to answer.</Notice> : <p className="preserve">{answer.answer}</p>}
    {answer.uncertainties.length > 0 && <ul className="plain-list">{answer.uncertainties.map((item, i) => <li key={i}>{item}</li>)}</ul>}
    {answer.citations.length > 0 && <div><h3 className="section-label">Citations</h3>
      {answer.citations.map(({ source, excerpt, category }, i) => <details key={`${source.chunk_id}-${i}`}>
        <summary>{source.section || source.filename} · {label(category)} <ChevronDown size={14} /></summary>
        <p className="muted">{source.filename}{source.page !== null ? ` · Page ${source.page}` : ''}</p><blockquote>{excerpt}</blockquote>
      </details>)}</div>}
    {answer.retrieved.length > 0 && <div><h3 className="section-label">Retrieved evidence</h3>
      {answer.retrieved.map(source => <details key={source.chunk_id}><summary>{source.section || source.filename}<small>Distance {source.distance.toFixed(4)}</small><ChevronDown size={14} /></summary><blockquote>{source.text}</blockquote></details>)}</div>}
  </div>;
}

export function useAction() {
  const queries = useQueryClient();
  const keys = useRef(new Map<string, string>());
  const busy = useRef(false);
  const mutation = useMutation({
    mutationFn: async ({ fingerprint, perform }: { fingerprint: string; perform: (key: string) => Promise<void> }) => {
      const key = keys.current.get(fingerprint) ?? crypto.randomUUID();
      keys.current.set(fingerprint, key);
      await perform(key);
      keys.current.delete(fingerprint);
    },
    onSettled: () => { void queries.invalidateQueries(); },
  });
  const run = async (fingerprint: string, perform: (key: string) => Promise<void>) => {
    if (busy.current) return;
    busy.current = true;
    try { await mutation.mutateAsync({ fingerprint, perform }); } catch { /* Render the mutation error. */ }
    finally { busy.current = false; }
  };
  return { run, error: mutation.error, pending: mutation.isPending };
}
