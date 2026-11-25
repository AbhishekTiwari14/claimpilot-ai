import { useQuery } from '@tanstack/react-query';
import { BookOpen, Database, FileText, LibraryBig, Play, RefreshCw, Trash2, Upload } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { client, type Answer, type DocumentInput, type DocumentRow, type Scope } from './api';
import { Button } from './components/ui/button';
import { AnswerView, Badge, Empty, Failure, Loading, Notice, Panel, label, localTime, useAction } from './shared';
import manifest from '../../data/knowledge/manifest.json';
import policyA from '../../data/knowledge/homeowners-a-v1.md?raw';
import policyA2 from '../../data/knowledge/homeowners-a-v2.md?raw';
import policyB from '../../data/knowledge/homeowners-b-v1.md?raw';
import operations from '../../data/knowledge/restoration-operations.md?raw';

const synthetic: Record<string, string> = {
  'homeowners-a-v1.md': policyA, 'homeowners-a-v2.md': policyA2,
  'homeowners-b-v1.md': policyB, 'restoration-operations.md': operations,
};
const makeUpload = (file: File, metadata: DocumentInput) => {
  const form = new FormData();
  form.set('document', file);
  form.set('metadata', JSON.stringify(metadata));
  return form;
};

export async function seedDemoDocuments(token: string, key: string) {
  const api = client(token);
  const results = [];
  for (const { filename, ...metadata } of manifest) {
    results.push(await api.post<DocumentRow>('/knowledge/documents',
      makeUpload(new File([synthetic[filename]], filename, { type: 'text/markdown' }), metadata as DocumentInput),
      `${key}-${filename}`));
  }
  return results;
}

export function Knowledge({ token, connect }: { token: string; connect: () => void }) {
  const api = client(token);
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [formID, nextForm] = useState(0);
  const [kind, setKind] = useState<DocumentInput['document_type']>('operations');
  const [success, setSuccess] = useState('');
  const action = useAction();
  const docs = useQuery({ queryKey: ['documents', offset], enabled: !!token,
    queryFn: () => api.get<DocumentRow[]>(`/knowledge/documents?limit=100&offset=${offset}`) });
  const seed = () => {
    setSuccess('');
    void action.run('seed-workspace', async key => {
      const results = await seedDemoDocuments(token, key);
      const ready = results.filter(doc => doc.status === 'ready').length;
      setSuccess(`${ready} of ${results.length} synthetic documents indexed. Review the library for any failed items.`);
    });
  };
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setSuccess('');
    const data = new FormData(event.currentTarget);
    const metadata: DocumentInput = {
      title: String(data.get('title')).trim(), document_type: kind, document_version: Number(data.get('document_version')),
      ...(kind === 'policy' ? { policy_id: String(data.get('policy_id')).trim(), policy_version: String(data.get('policy_version')).trim(),
        subject_claimant_id: String(data.get('owner')).trim() || null } : {}),
    };
    void action.run(`upload:${file.name}:${file.size}:${file.lastModified}:${JSON.stringify(metadata)}`, async key => {
      const result = await api.post<DocumentRow>('/knowledge/documents', makeUpload(file, metadata), key);
      setSuccess(`${result.filename} · ${label(result.status)}`);
      setFile(null); nextForm(formID + 1);
    });
  }
  const manage = (request: string, doc: DocumentRow) => {
    setSuccess('');
    void action.run(`${request}:${doc.id}`, async key => {
      if (request === 'delete') {
        await api.delete(`/knowledge/documents/${doc.id}`, key); setSuccess('Document removed.');
      } else {
        const updated = await api.post<DocumentRow>(`/knowledge/documents/${doc.id}/index`, undefined, key);
        setSuccess(`${updated.filename} · ${label(updated.status)}`);
      }
    });
  };
  return <div className="stack">
    <div className="page-heading"><div><h1>Knowledge base</h1><p>Manage policy and operational sources for this workspace.</p></div>
      {token && <Button variant="outline" disabled={action.pending} onClick={seed}><LibraryBig /> Load synthetic documents</Button>}</div>
    {!token ? <Panel title="Document library"><Empty icon={<BookOpen />} title="Connect your operator session"><Button onClick={connect}>Connect session</Button></Empty></Panel> : <>
      <Failure error={docs.error || action.error} />{success && <Notice kind="success">{success}</Notice>}{action.pending && <Loading />}
      <Panel title="Upload a document" caption="Accepted formats are PDF, DOCX, TXT and Markdown.">
        <form className="form-grid" key={formID} onSubmit={submit}>
          <label>Document file<input required type="file" accept=".pdf,.docx,.txt,.md,.markdown" onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
          <label>Title<input required name="title" maxLength={150} /></label>
          <label>Collection<select value={kind} onChange={event => setKind(event.target.value as DocumentInput['document_type'])}><option value="operations">Operations</option><option value="policy">Policy</option></select></label>
          <label>Document version<input required name="document_version" type="number" min={1} max={1000000} defaultValue={1} /></label>
          {kind === 'policy' && <>
            <label>Policy ID<input required name="policy_id" maxLength={80} /></label>
            <label>Policy version<input required name="policy_version" maxLength={80} /></label>
            <label>Claimant ID (optional)<input name="owner" maxLength={80} /></label></>}
          <div className="form-button"><Button disabled={action.pending || !file}><Upload /> Upload &amp; index</Button></div>
        </form>
      </Panel>
      <Panel title="Document library" action={<Button variant="outline" disabled={action.pending} onClick={() => {
        if (!window.confirm('Rebuild the workspace knowledge index?')) return;
        setSuccess('');
        void action.run('rebuild', async key => {
          const rows = await api.post<DocumentRow[]>('/knowledge/rebuild', undefined, key);
          setSuccess(`${rows.filter(row => row.status === 'ready').length} of ${rows.length} documents indexed.`);
        });
      }}><RefreshCw /> Rebuild index</Button>}>
        {docs.isPending ? <Loading /> : docs.data?.length ? <div className="scroll-table">
          <table><thead><tr><th>Document</th><th>Scope</th><th>Status</th><th>Indexed generation</th><th>Actions</th></tr></thead>
            <tbody>{docs.data.map(doc => <tr key={doc.id}>
              <td><strong>{doc.title}</strong><small className="block">{doc.filename} · {localTime(doc.created_at)}</small></td>
              <td>{doc.document_type === 'policy' ? `${doc.policy_id} · ${doc.policy_version}` : 'Operations'}{doc.subject_claimant_id && <small className="block">Claimant: {doc.subject_claimant_id}</small>}</td>
              <td><Badge value={doc.status} />{doc.error_code && <small className="block">{label(doc.error_code)}</small>}</td><td>{doc.generation}</td>
              <td><div className="row"><Button size="sm" variant="outline" disabled={action.pending} onClick={() => manage('index', doc)}>
                <RefreshCw /> {doc.status === 'failed' ? 'Retry index' : 'Reindex'}</Button>
                <Button size="icon" variant="ghost" aria-label={`Delete ${doc.title}`} disabled={action.pending}
                  onClick={() => { if (window.confirm(`Delete “${doc.title}”?`)) manage('delete', doc); }}><Trash2 /></Button></div></td>
            </tr>)}</tbody></table>
          </div> : !docs.error && <Empty icon={<FileText />} title="No documents indexed">Upload a document or load the synthetic policy set.</Empty>}
        {(offset > 0 || docs.data?.length === 100) && <div className="row spread"><Button variant="outline" disabled={!offset} onClick={() => setOffset(offset - 100)}>Previous</Button>
          <Button variant="outline" disabled={docs.data?.length !== 100} onClick={() => setOffset(offset + 100)}>Next</Button></div>}
      </Panel>
    </>}
  </div>;
}

export function Playground({ token, connect }: { token: string; connect: () => void }) {
  const api = client(token);
  const [question, setQuestion] = useState('');
  const [scope, setScope] = useState('operations');
  const [answer, setAnswer] = useState<Answer | null>(null);
  const action = useAction();
  const docs = useQuery({ queryKey: ['playground-docs'], enabled: !!token, queryFn: async () => {
    const all: DocumentRow[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await api.get<DocumentRow[]>(`/knowledge/documents?limit=100&offset=${offset}`);
      all.push(...page);
      if (page.length < 100) return all;
    }
  }});
  const policies = new Map<string, Scope>();
  for (const doc of docs.data ?? []) {
    if (doc.status === 'ready' && doc.policy_id && doc.policy_version && !doc.subject_claimant_id) {
      policies.set(`${doc.policy_id}|${doc.policy_version}`, { document_type: 'policy', policy_id: doc.policy_id, policy_version: doc.policy_version });
    }
  }
  return <div className="stack">
    <div className="page-heading"><div><h1>RAG playground</h1><p>Ask a question and inspect the evidence behind the answer.</p></div></div>
    {!token ? <Panel title="Ask a grounded question"><Empty icon={<Database />} title="Connect your operator session"><Button onClick={connect}>Connect session</Button></Empty></Panel> :
      <div className="split">
        <Panel title="Question" caption="Choose the applicable policy version or the operations collection.">
          <form className="stack" onSubmit={event => {
            event.preventDefault();
            setAnswer(null);
            const payload = { ...(policies.get(scope) ?? { document_type: 'operations' }), query: question.trim(), top_k: 5 };
            void action.run(`ask:${JSON.stringify(payload)}`, async () => {
              setAnswer(await api.post<Answer>('/knowledge/playground/query', payload));
            });
          }}>
            <label>Collection<select value={scope} onChange={event => { setScope(event.target.value); setAnswer(null); }}>
              <option value="operations">Operations</option>{[...policies].map(([id, item]) =>
                <option key={id} value={id}>{item.policy_id} · {item.policy_version}</option>)}</select></label>
            <label>Question<textarea rows={5} minLength={3} maxLength={600} required placeholder="What does the selected source say?" value={question}
              onChange={event => setQuestion(event.target.value)} /></label>
            <Button disabled={action.pending || question.trim().length < 3 || (scope !== 'operations' && !policies.has(scope))}><Play /> {action.pending ? 'Retrieving sources…' : 'Ask a question'}</Button>
            <Failure error={docs.error || action.error} />
          </form>
        </Panel>
        <Panel title="Grounded answer" caption="Citations include the exact source excerpts and retrieval distances.">
          {action.pending ? <Loading /> : answer ? <AnswerView answer={answer} /> :
            <Empty icon={<BookOpen />} title="No answer yet">Submit a question to see the retrieved evidence.</Empty>}
        </Panel>
      </div>}
  </div>;
}
