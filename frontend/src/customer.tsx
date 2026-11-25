import { useQuery } from '@tanstack/react-query';
import { Camera, ClipboardCheck, MessageSquare, Plus, Send, Trash2 } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { client, type Analysis, type Claim, type ClaimRow, type History, type Photo, type Turn } from './api';
import { Badge, Empty, Facts, Failure, Loading, Notice, Observations, Panel, label, shortID, useAction } from './shared';
import { Button } from './components/ui/button';

const demo = ['There is water damage in the kitchen.', 'The damaged property is 12 Example Lane, ZIP 98101.',
  'I noticed it yesterday morning.', 'My policy ID is SYN-123.', 'There are no ongoing hazards. I think the dishwasher leaked.'];

export default function Customer({ token, connect }: { token: string; connect: () => void }) {
  const api = client(token);
  const [params, setParams] = useSearchParams();
  const conversation = params.get('conversation') || '';
  const mutation = useAction();
  const claims = useQuery({ queryKey: ['customer-claims'], enabled: !!token, queryFn: () => api.get<ClaimRow[]>('/customer/claims') });
  return <div className="stack">
    <div className="page-heading"><div><h1>Customer portal</h1><p>Report property damage and track your claim.</p></div>
      {token && <Button onClick={() => void mutation.run('new-conversation', async key => {
        const result = await api.post<Turn>('/conversations', undefined, key);
        setParams({ conversation: result.claim.conversation_id });
      })} disabled={mutation.pending}><Plus /> New conversation</Button>}</div>
    <Failure error={mutation.error || claims.error} />
    {!token ? <Panel title="Connect as a claimant"><Empty icon={<MessageSquare />} title="Your claims appear here"><Button onClick={connect}>Connect claimant session</Button></Empty></Panel> :
      <div className="stack">
        <div className="row wrap"><label htmlFor="conversation">Resume a conversation</label>
          <select id="conversation" value={conversation} onChange={event => setParams(event.target.value ? { conversation: event.target.value } : {})}>
            <option value="">Choose a conversation</option>
            {claims.data?.map(claim => <option key={claim.claim_id} value={claim.conversation_id}>
              {shortID(claim.claim_id)} · {label(claim.status)}
            </option>)}
          </select>
          {claims.isPending && <Loading />}
        </div>
        {conversation ? <Conversation token={token} conversation={conversation} key={conversation} /> :
          <Panel title="Claim intake"><Empty icon={<MessageSquare />} title="Start or resume a conversation">Your conversation and claim details are saved as you go.</Empty></Panel>}
      </div>}
  </div>;
}

function Conversation({ token, conversation }: { token: string; conversation: string }) {
  const api = client(token);
  const [text, setText] = useState('');
  const [retry, setRetry] = useState<{ message: string; expected_version: number; replace_failed_message: boolean } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const mutation = useAction();
  const history = useQuery({ queryKey: ['conversation', conversation], queryFn: async () => {
    const first = await api.get<History>(`/conversations/${conversation}?limit=100`);
    const messages = [...first.messages];
    let cursor = first.next_after;
    while (cursor != null) {
      const page = await api.get<History>(`/conversations/${conversation}?limit=100&after=${cursor}`);
      messages.push(...page.messages);
      cursor = page.next_after;
    }
    return { ...first, messages };
  } });
  const claimID = history.data?.claim_id;
  const claimQuery = useQuery({ queryKey: ['customer-claim', claimID], enabled: !!claimID,
    queryFn: () => api.get<Claim>(`/claims/${claimID}`) });
  const progress = useQuery({ queryKey: ['customer-claims'], queryFn: () => api.get<ClaimRow[]>('/customer/claims') })
    .data?.find(claim => claim.claim_id === claimID);
  const claim = claimQuery.data;
  const send = (body: NonNullable<typeof retry>) => {
    setRetry(body);
    void mutation.run(`message:${JSON.stringify(body)}`, async key => {
      await api.post<Turn>(`/conversations/${conversation}/messages`, body, key);
      setRetry(null); setText(''); setConfirmed(false);
    });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (claim && text.trim()) send(retry?.message === text.trim() && mutation.error ? retry : {
      message: text.trim(), expected_version: claim.version, replace_failed_message: !!claim.processing_error,
    });
  };
  if (history.isPending) return <Loading />;
  if (history.error || claimQuery.error) return <Failure error={history.error || claimQuery.error} />;
  if (!claim || !claimID) return <Loading />;
  const complete = claim.status === 'intake_completed';
  return <>
    <div className="split">
      <Panel title="Intake conversation" caption={`Claim ${shortID(claimID)}`} action={<Badge value={claim.status} />}>
        <div className="chat-log" aria-live="polite">{history.data?.messages.map(message => <div className={`bubble ${message.role}`} key={message.id}>
          <small>{message.role === 'assistant' ? 'ClaimPilot intake' : 'You'}</small><p>{message.text}</p>
        </div>)}</div>
        <Failure error={mutation.error} />
        {claim.processing_error && <Notice>Intake paused while processing your last message. Retry it or submit a replacement.</Notice>}
        {retry && mutation.error && <Button variant="outline" disabled={mutation.pending} onClick={() => send(retry)}>Retry saved message</Button>}
        {!complete && <form onSubmit={submit} className="stack tight">
          <label htmlFor="message">Your message</label>
          <textarea id="message" rows={3} value={text} maxLength={4000} onChange={event => setText(event.target.value)} />
          <div className="row spread wrap"><details className="demo-lines"><summary>Synthetic demo messages</summary>
            {demo.map(line => <button type="button" className="text-button" onClick={() => setText(line)} key={line}>{line}</button>)}
          </details><Button disabled={mutation.pending || !text.trim()}><Send /> {mutation.pending ? 'Sending…' : 'Send message'}</Button></div>
        </form>}
      </Panel>
      <Panel title="Claim details" caption="Review the information gathered during intake.">
        <div className="stack">
          <Facts facts={claim.facts} />
          {claim.missing_fields.length > 0 && <Notice>Still needed: {claim.missing_fields.map(label).join(', ')}.</Notice>}
          {Object.entries(claim.conflicts).map(([field, values]) => <Notice key={field}>Clarify {label(field)}: {values.map(fact => String(fact.value)).join(' / ')}.</Notice>)}
          {claim.status === 'awaiting_customer_confirmation' && <>
            <p className="muted">To correct a detail, send the correction in the conversation before confirming.</p>
            <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I reviewed these details and confirm they are correct.</label>
            <Button disabled={!confirmed || mutation.pending} onClick={() => void mutation.run(`confirm:${claim.version}`, async key => {
              await api.post<Turn>(`/claims/${claimID}/confirm`, { expected_version: claim.version, confirmed: true }, key);
              setConfirmed(false);
            })}><ClipboardCheck /> Confirm claim details</Button>
          </>}
          {complete && <Notice kind="success">Your confirmed claim is available to the operations team.</Notice>}
          {progress?.dispatch_status && <div className="row wrap">Simulated contractor status <Badge value={progress.dispatch_status} /></div>}
          {claim.photo_follow_ups.map(item => <Notice key={`${item.photo_id}-${item.question}`}>{item.question}</Notice>)}
        </div>
      </Panel>
    </div>
    <Photos token={token} claimID={claimID} />
  </>;
}

function Thumbnail({ token, claimID, photo }: { token: string; claimID: string; photo: Photo }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let active = true;
    let objectUrl = '';
    void client(token).blob(`/claims/${claimID}/photos/${photo.id}/content`).then(blob => {
      if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    }).catch(caught => { if (active) setError(caught); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [token, claimID, photo.id]);
  return url ? <img src={url} alt={`Claim photo ${shortID(photo.id)}`} /> : error ? <Failure error={error} /> : <Loading />;
}

function Photos({ token, claimID }: { token: string; claimID: string }) {
  const api = client(token);
  const path = `/claims/${claimID}`;
  const [file, setFile] = useState<File | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [inputKey, setInputKey] = useState(0);
  const action = useAction();
  const photos = useQuery({ queryKey: ['photos', claimID], queryFn: () => api.get<Photo[]>(`${path}/photos`) });
  const analyses = useQuery({ queryKey: ['analyses', claimID], queryFn: () => api.get<Analysis[]>(`${path}/photo-analyses`) });
  const ids = selected.filter(id => photos.data?.some(photo => photo.id === id));
  return <div className="split">
    <Panel title="Damage photos" caption="Upload JPEG, PNG or WebP files. Select up to three images per analysis.">
      <form className="row wrap" onSubmit={event => {
        event.preventDefault();
        if (!file) return;
        const form = new FormData(); form.set('photo', file);
        void action.run(`upload:${file.name}:${file.size}:${file.lastModified}`, async key => {
          const photo = await api.post<Photo>(`${path}/photos`, form, key);
          setSelected([photo.id]); setFile(null); setInputKey(key => key + 1);
        });
      }}>
        <input aria-label="Damage photograph" type="file" accept="image/jpeg,image/png,image/webp" key={inputKey} onChange={event => setFile(event.target.files?.[0] ?? null)} />
        <Button variant="outline" disabled={!file || action.pending}><Camera /> Upload photo</Button>
      </form>
      {photos.isPending && <Loading />}
      <Failure error={photos.error || action.error} />
      <div className="photo-grid">{photos.data?.map(photo => <div className="inset" key={photo.id}>
        <Thumbnail token={token} claimID={claimID} photo={photo} />
        <div className="row spread"><label className="check"><input type="checkbox" checked={ids.includes(photo.id)}
          disabled={ids.length >= 3 && !ids.includes(photo.id)} onChange={event => setSelected(event.target.checked ? [...ids, photo.id] : ids.filter(id => id !== photo.id))} /> {shortID(photo.id)}</label>
          <Button aria-label={`Delete photo ${shortID(photo.id)}`} variant="ghost" size="icon" disabled={action.pending} onClick={() => {
            if (window.confirm('Delete this photo?')) void action.run(`delete:${photo.id}`, key => api.delete(`${path}/photos/${photo.id}`, key));
          }}><Trash2 /></Button></div>
      </div>)}</div>
      <Button disabled={!ids.length || action.pending} onClick={() => void action.run(`analyse:${ids.join(',')}`, async key => {
        await api.post<Analysis>(`${path}/photo-analyses`, { photo_ids: ids }, key);
      })}>{action.pending ? 'Working…' : `Analyse selected photos (${ids.length})`}</Button>
    </Panel>
    <Panel title="Photo observations" caption="Visual observations and interpretations are separate from confirmed claim facts.">
      <Failure error={analyses.error} />{analyses.isPending ? <Loading /> : <Observations analyses={analyses.data ?? []} />}
    </Panel>
  </div>;
}
