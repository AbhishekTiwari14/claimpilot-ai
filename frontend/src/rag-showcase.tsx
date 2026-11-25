import { useQuery } from '@tanstack/react-query';
import { BookOpen, Download, FileUp, Search } from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { client, type Answer, type DocumentInput, type DocumentRow } from './api';
import { Button } from './components/ui/button';
import { sampleFile } from './demo';
import { AnswerView, Badge, Empty, Failure, Loading, Notice, Panel, useAction } from './shared';
import { Guide, Tip, useWalkthrough } from './walkthrough';

const sampleName = 'visitor-policy.md';
const hints = [
  ['Coverage', 'What does Coverage for sudden appliance leaks say?'],
  ['Exclusions', 'What does Exclusion for long-term seepage say?'],
  ['Insufficient evidence', 'Does this policy cover stolen bicycles?'],
];

export function RagShowcase({ token, connect }: { token: string; connect: () => void }) {
  const api = client(token);
  const [params] = useSearchParams();
  const journey = useWalkthrough();
  const claim = params.get('claim');
  const returnPath = claim && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(claim)
    ? `/claims/${claim}?${new URLSearchParams({ scenario: params.get('scenario') || 'dishwasher', experience: 'full' })}` : null;
  const [file, setFile] = useState<File | null>(null);
  const [selectedID, setSelectedID] = useState('');
  const [importedIDs, setImportedIDs] = useState<string[]>([]);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [stage, setStage] = useState('');
  const [observed, setObserved] = useState({ cited: false, abstained: false });
  const action = useAction();
  const library = useQuery({ queryKey: ['showcase-documents'], enabled: !!token,
    queryFn: () => api.get<DocumentRow[]>('/knowledge/documents?limit=100') });
  const selected = useQuery({ queryKey: ['showcase-document', selectedID], enabled: !!token && !!selectedID,
    queryFn: () => api.get<DocumentRow>(`/knowledge/documents/${selectedID}`) });
  const document = selected.data;
  const isSample = document?.policy_id === 'SYN-LAB' && document.filename === sampleName;

  function select(id: string) {
    setSelectedID(id);
    setAnswer(null);
    setQuestion('');
    setObserved({ cited: false, abstained: false });
  }

  function addDocument(input: File | 'sample') {
    const tag = input === 'sample' ? input : `${input.name}:${input.size}:${input.lastModified}`;
    void action.run(`showcase-upload:${tag}`, async key => {
      setStage('Reading and indexing your document…');
      try {
        let upload: File;
        if (input === 'sample') {
          const response = await fetch(sampleFile(sampleName));
          if (!response.ok) throw new Error('Could not read the sample policy.');
          upload = new File([await response.blob()], sampleName, { type: 'text/markdown' });
        } else upload = input;
        const metadata: DocumentInput = input === 'sample'
          ? { title: 'Synthetic Visitor Home Policy', document_type: 'policy', policy_id: 'SYN-LAB', policy_version: 'v1' }
          : { title: upload.name.slice(0, 150), document_type: 'operations' };
        const form = new FormData();
        form.set('document', upload);
        form.set('metadata', JSON.stringify(metadata));
        const saved = await api.post<DocumentRow>('/knowledge/documents', form, key);
        setImportedIDs(ids => ids.includes(saved.id) ? ids : [...ids, saved.id]);
        select(saved.id);
      } finally { setStage(''); }
    });
  }

  return <div className="stack">
    <div className="page-heading"><div><h1>Explore the knowledge base</h1><p>Import a document and see exactly which passages support an answer.</p></div>
      {returnPath && <Button asChild variant="outline"><Link to={returnPath} onClick={() => journey?.record('knowledge', 'skipped')}>Return to Claim</Link></Button>}</div>
    {returnPath && <Notice>This is a separate sample. The claim keeps its own insurance policy and saved progress.</Notice>}
    {!token ? <Panel title="Import a document"><Empty icon={<BookOpen />} title="Connect an operator session"><Button onClick={connect}>Connect operator session</Button></Empty></Panel> :
      <>
        <Panel title="1. Import a document" caption="The sample contains fictional coverage and exclusions and starts outside the preloaded policy collection.">
          {!document && <Guide step="import-source">Import the sample to watch the system index an actual document.</Guide>}
          <div className="row wrap">
            <Button onClick={() => addDocument('sample')} disabled={action.pending}><FileUp /> Import sample policy</Button>
            <Button asChild variant="outline"><a href={sampleFile(sampleName)} download><Download /> Download sample policy</a></Button>
          </div>
          <form className="dropzone row wrap" onDragOver={event => event.preventDefault()} onDrop={event => {
            event.preventDefault();
            if (!action.pending && event.dataTransfer.files[0]) setFile(event.dataTransfer.files[0]);
          }} onSubmit={event => { event.preventDefault(); if (file) addDocument(file); }}>
            <span>{file ? file.name : 'Drop a PDF, DOCX, TXT or Markdown file here, or browse.'}</span>
            <input aria-label="Your PDF, DOCX, TXT or Markdown document" type="file" accept=".pdf,.docx,.txt,.md,.markdown"
              onChange={event => setFile(event.target.files?.[0] ?? null)} />
            <Button variant="outline" disabled={!file || action.pending}>Upload my document</Button>
          </form>
          {stage && <Notice>{stage}</Notice>}
          <Failure error={action.error || library.error || selected.error} />
          {library.data?.length ? <label>Document to query<select value={selectedID} onChange={event => select(event.target.value)} disabled={action.pending}>
            <option value="">Choose a document</option>
            {library.data.map(doc => <option key={doc.id} value={doc.id}>{doc.title} · {importedIDs.includes(doc.id) ? 'Imported here' : 'Existing in workspace'}</option>)}
          </select></label> : !stage && <p className="muted">No documents in this showcase yet.</p>}
          {selectedID && selected.isPending && <Loading />}
          {document && <div className="inset stack tight">
            <div className="row spread wrap"><strong>{document.filename}</strong><Badge value={document.status} /></div>
            {document.status === 'ready' && <p>Parsed and indexed{document.chunk_count != null ? ` · ${document.chunk_count} chunks` : ''}</p>}
            {document.indexing_config?.embedding && <small className="row wrap">Embedding: {document.indexing_config.embedding} · {document.indexing_config.distance ?? 'configured'} distance
              <Tip text="An embedding is a numerical representation used to find passages with related meaning." /></small>}
            {document.error_code && <Notice kind="error">Indexing failed: {document.error_code.replaceAll('_', ' ')}.</Notice>}
          </div>}
        </Panel>
        <div className="split">
          <Panel title="2. Ask the selected document" caption={document ? `Source: ${document.filename}. Search is limited to this document.` : 'Choose an indexed document before asking a question.'}>
            {isSample && document.status === 'ready' && !observed.abstained && <Guide key={observed.cited ? 'abstain' : 'grounded-query'}
              step={observed.cited ? 'abstain' : 'grounded-query'}>{observed.cited
                ? 'Try the insufficient-evidence question. The assistant should explain that the selected text is insufficient.'
                : 'Try the coverage question and expand a citation to inspect the exact text.'}</Guide>}
            {isSample && document.status === 'ready' && <div className="row wrap" aria-label="Suggested questions">
              {hints.map(([name, sample]) => <Button key={name} size="sm" variant="outline" disabled={action.pending} onClick={() => setQuestion(sample)}>{name}</Button>)}
            </div>}
            <form className="stack" onSubmit={event => {
              event.preventDefault();
              if (!document || document.status !== 'ready') return;
              setAnswer(null);
              const payload = { query: question.trim(), document_id: document.id,
                document_type: document.document_type, policy_id: document.policy_id, policy_version: document.policy_version };
              void action.run(`ask:${JSON.stringify(payload)}`, async () => {
                const result = await api.post<Answer>('/knowledge/playground/query', payload);
                setAnswer(result);
                if (isSample) {
                  const next = { cited: observed.cited || result.citations.length > 0,
                    abstained: observed.abstained || result.insufficient_evidence };
                  setObserved(next);
                  if (next.cited && next.abstained) journey?.record('knowledge');
                }
              });
            }}>
              <label>Question<textarea rows={4} value={question} maxLength={600} onChange={event => setQuestion(event.target.value)} /></label>
              <Button disabled={document?.status !== 'ready' || question.trim().length < 3 || action.pending}><Search /> Ask this document</Button>
            </form>
          </Panel>
          <Panel title="3. Inspect the evidence">
            {action.pending && !stage ? <Loading /> : answer ? <AnswerView answer={answer} /> :
              <Empty icon={<BookOpen />} title="No answer yet">Citations, exact passages, and retrieval distances will appear here.</Empty>}
          </Panel>
        </div>
      </>}
  </div>;
}
