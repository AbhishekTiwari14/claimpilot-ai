import { useQuery } from '@tanstack/react-query';
import { Camera, Check, CircleDashed, ClipboardCheck, MessageSquare, Paperclip, Plus, Send, Shield, Trash2 } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type Ref } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { client, type Analysis, type Claim, type ClaimRow, type History, type Photo, type Turn } from './api';
import { Badge, Empty, Facts, Failure, Loading, Notice, Observations, Panel, fieldLabel, label, shortID, useAction, valueText } from './shared';
import { Button } from './components/ui/button';
import { experienceFor, photoAttribution, sampleFile, sampleMessages, scenarioFor, type Experience } from './demo';
import { Guide, Milestone, useWalkthrough } from './walkthrough';

const suggestedFields: Record<string, [number, string]> = {
  damage_type: [0, 'I noticed damage to '],
  affected_areas: [0, 'The damaged rooms are '],
  property_location: [1, 'The damaged property is at [street and city], ZIP [five digits].'],
  incident_time: [2, 'I first noticed the damage on '],
  policy_id: [3, 'My policy ID is '],
  ongoing_hazards: [4, 'The current safety conditions are '],
};

export default function Customer({ token, connect, guided, list = false, preparingSample = false }: {
  token: string; connect: () => void; guided: boolean; list?: boolean; preparingSample?: boolean;
}) {
  const api = client(token);
  const [params, setParams] = useSearchParams();
  const conversation = params.get('conversation') || '';
  const scenario = scenarioFor(params.get('scenario'));
  const experience = experienceFor(params.get('experience'));
  const mutation = useAction();
  const claims = useQuery({ queryKey: ['customer-claims'], enabled: !!token, queryFn: () => api.get<ClaimRow[]>('/customer/claims') });
  return <div className="stack">
    <div className="page-heading"><div><h1>{list ? 'My Claims' : experience === 'vision' ? 'Multimodal Damage Analysis' : experience === 'chat' ? 'Conversational AI' : 'Report Damage'}</h1>
      {guided && conversation && !list && experience !== 'vision' && <p>Sample claim · {scenario.title}</p>}</div>
      {token && <Button onClick={() => void mutation.run('new-conversation', async key => {
        const result = await api.post<Turn>('/conversations', undefined, key);
        setParams({ conversation: result.claim.conversation_id, ...(guided ? { scenario: scenario.id, experience } : {}) });
      })} disabled={mutation.pending}><Plus /> New conversation</Button>}</div>
    <Failure error={mutation.error || claims.error} />
    {!token ? <Panel title="Connect as a claimant"><Empty icon={<MessageSquare />} title="Your claims appear here"><Button onClick={connect}>Connect claimant session</Button></Empty></Panel> :
      <div className="stack">
        {!list && !guided && <div className="row wrap"><label htmlFor="conversation">Resume a conversation</label>
          <select id="conversation" value={conversation} onChange={event => setParams(event.target.value
            ? { conversation: event.target.value, ...(guided ? { scenario: scenario.id } : {}) } : {})}>
            <option value="">Choose a conversation</option>
            {claims.data?.map(claim => <option key={claim.claim_id} value={claim.conversation_id}>
              {shortID(claim.claim_id)} · {label(claim.status)}
            </option>)}
          </select>
          {claims.isPending && <Loading />}
        </div>}
        {list && <Panel title="Your claims">{claims.isPending ? <Loading /> : claims.data?.length ?
          <div className="stack tight">{claims.data.map(claim => <div className="inset row spread wrap" key={claim.claim_id}>
            <Link className="inline-link" to={`?conversation=${claim.conversation_id}&scenario=${scenario.id}`}>Claim {shortID(claim.claim_id)}</Link>
            <div className="row wrap"><Badge value={claim.status} />{claim.dispatch_status && <Badge value={claim.dispatch_status} />}</div>
          </div>)}</div> : <Empty icon={<MessageSquare />} title="No claims yet">Report damage to start a claim.</Empty>}</Panel>}
        {conversation ? <Conversation token={token} conversation={conversation} key={conversation} guided={guided} scenario={scenario} experience={experience} preparingSample={preparingSample} /> :
          <Panel title="Claim intake"><Empty icon={<MessageSquare />} title="Start or resume a conversation">Your conversation and claim details are saved as you go.</Empty></Panel>}
      </div>}
  </div>;
}

function Conversation({ token, conversation, guided, scenario, experience, preparingSample }: {
  token: string; conversation: string; guided: boolean; scenario: ReturnType<typeof scenarioFor>; experience: Experience; preparingSample: boolean;
}) {
  const api = client(token);
  const [text, setText] = useState('');
  const [retry, setRetry] = useState<{ message: string; expected_version: number; replace_failed_message: boolean } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const chat = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const journey = useWalkthrough();
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
  useLayoutEffect(() => {
    if (chat.current && nearBottom.current) chat.current.scrollTop = chat.current.scrollHeight;
  }, [history.data?.messages.length, claim?.version]);
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
  if (experience === 'vision') return <Photos token={token} claimID={claimID} guided={guided} />;
  const complete = claim.status === 'intake_completed';
  const samples = sampleMessages(scenario);
  const firstMessage = !history.data?.messages.some(message => message.role === 'customer');
  const field = claim.pending_field || Object.keys(claim.conflicts)[0] || claim.missing_fields[0];
  const suggestion = suggestedFields[field];
  const fill = (message: string) => { setText(message); composer.current?.focus({ preventScroll: true }); };
  const ready = claim.status === 'awaiting_customer_confirmation' && !claim.missing_fields.length;
  return <>
    <Milestone step="report" when={ready || complete} />
    <Milestone step="confirm" when={complete} />
    <div className="customer-grid">
      <Panel title="Damage report" action={<Badge value={claim.status} />}>
        <div ref={chat} className="chat-log" role="log" aria-label="Claim conversation" onScroll={event => {
          const element = event.currentTarget;
          nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}>{history.data?.messages.map(message => <div className={`bubble ${message.role}`} key={message.id}>
          {message.role === 'assistant' && <span className="assistant-mark"><Shield size={14} /></span>}
          <div><small>{message.role === 'assistant' ? 'ClaimPilot' : 'You'}</small><p>{message.text}</p></div>
        </div>)}</div>
        <Failure error={mutation.error} />
        {claim.processing_error && !preparingSample && <Notice>Intake paused while processing your last message. Retry it or submit a replacement.</Notice>}
        {retry && mutation.error && <Button variant="outline" disabled={mutation.pending} onClick={() => send(retry)}>Retry saved message</Button>}
        {!complete && !preparingSample && <form onSubmit={submit} className="chat-composer stack tight">
          {!ready && <Guide key={firstMessage ? 'opening' : field} step={firstMessage ? 'opening' : `reply-${field}`}>
            {firstMessage ? 'Start with the sample sentence or describe the damage in your own words. Leave some details for the assistant to ask about.' :
              `The assistant needs ${fieldLabel(field).toLowerCase()}. Use the suggested reply or enter your own.`}</Guide>}
          <label className="sr-only" htmlFor="message">Your message</label>
          <textarea ref={composer} id="message" rows={2} placeholder="Message ClaimPilot…" value={text} maxLength={4000} onChange={event => setText(event.target.value)} />
          <div className="row spread wrap">
            {guided && firstMessage ? <Button type="button" variant="outline" onClick={() => fill(samples[0])}>Use sample opening message</Button> :
              suggestion ? <Button type="button" variant="outline" onClick={() => fill(guided ? samples[suggestion[0]] : suggestion[1])}>
                {guided ? 'Use sample' : 'Start answer'} · {fieldLabel(field)}</Button> : <span />}
            <div className="row">{experience !== 'chat' && <span className="icon-tip" data-tooltip="Attach a damage photo">
              <Button type="button" size="icon" variant="ghost" aria-label="Attach a damage photo" onClick={() => photoInput.current?.click()}><Paperclip /></Button></span>}
              <Button disabled={mutation.pending || !text.trim()}><Send /> {mutation.pending ? 'Sending…' : 'Send message'}</Button></div></div>
        </form>}
        {experience !== 'chat' && !preparingSample && <Photos token={token} claimID={claimID} guided={guided} inputRef={complete ? undefined : photoInput} inline ready={ready} />}
      </Panel>
      <Panel title="Claim checklist" caption="This updates with the assistant’s latest reply.">
        <div className="stack">
          <details className="claim-details" open><summary>Collected details <small>{Object.keys(suggestedFields).length - new Set([...claim.missing_fields, ...Object.keys(claim.conflicts)].filter(item => item in suggestedFields)).size} of {Object.keys(suggestedFields).length}</small></summary>
          {!complete ? <ul className="claim-checklist" aria-label="Collected and missing claim information">
            {Object.keys(suggestedFields).map(item => {
              const missing = claim.missing_fields.includes(item) || !!claim.conflicts[item];
              return <li key={item} data-collected={!missing}><span>{missing ? <CircleDashed size={16} /> : <Check size={16} />}</span>
                <div><strong>{fieldLabel(item)}</strong><small>{missing ? (claim.conflicts[item] ? 'Needs clarification' : 'Still needed') : valueText(claim.facts[item]?.value)}</small></div></li>;
            })}
          </ul> : <Facts facts={claim.facts} />}
          </details>
          {!complete && claim.facts.suspected_cause && <p className="muted">Suspected cause: {valueText(claim.facts.suspected_cause.value)}</p>}
          {Object.entries(claim.conflicts).map(([field, values]) => <Notice key={field}>Clarify {label(field)}: {values.map(fact => String(fact.value)).join(' / ')}.</Notice>)}
          {ready && !preparingSample && <>
            {(!journey || journey.progress.photo) && <Guide step="confirm">Check the address and other details, then confirm this exact report.</Guide>}
            <p className="muted">To correct a detail, send the correction in the conversation before confirming.</p>
            <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I reviewed these details and confirm they are correct.</label>
            <Button disabled={!confirmed || mutation.pending} onClick={() => void mutation.run(`confirm:${claim.version}`, async key => {
              await api.post<Turn>(`/claims/${claimID}/confirm`, { expected_version: claim.version, confirmed: true }, key);
              if (!journey?.progress.photo) journey?.record('photo', 'skipped');
              setConfirmed(false);
            })}><ClipboardCheck /> Confirm claim details</Button>
          </>}
          {complete && <Notice kind="success">Your confirmed claim is available to the operations team.</Notice>}
          {complete && guided && (experience === 'chat' ? <Button asChild><Link to="/">Explore another capability</Link></Button> :
            <><Guide step="insurance-review">Continue with the same claim in the insurance team’s workspace.</Guide>
              <Button asChild><Link to={`/claims/${claimID}?scenario=${scenario.id}&experience=${experience}`}>Continue to Insurance Review</Link></Button></>)}
          {progress?.dispatch_status && <div className="row wrap">Simulated contractor status <Badge value={progress.dispatch_status} /></div>}
          {claim.photo_follow_ups.map(item => <Notice key={`${item.photo_id}-${item.question}`}>{item.question}</Notice>)}
        </div>
      </Panel>
    </div>
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

function Photos({ token, claimID, guided, inputRef, inline = false, ready = false }: {
  token: string; claimID: string; guided: boolean; inputRef?: Ref<HTMLInputElement>; inline?: boolean; ready?: boolean;
}) {
  const api = client(token);
  const path = `/claims/${claimID}`;
  const [task, setTask] = useState<{ input: File | string; key: string } | null>(null);
  const [stage, setStage] = useState('');
  const [names, setNames] = useState<Record<string, string>>({});
  const action = useAction();
  const photos = useQuery({ queryKey: ['photos', claimID], queryFn: () => api.get<Photo[]>(`${path}/photos`) });
  const analyses = useQuery({ queryKey: ['analyses', claimID], queryFn: () => api.get<Analysis[]>(`${path}/photo-analyses`) });
  function upload(input: File | string, operation: string = crypto.randomUUID()) {
    setTask({ input, key: operation });
    void action.run(operation, async key => {
      try {
        let file: File;
        if (typeof input === 'string') {
          setStage('Loading sample photo…');
          const response = await fetch(sampleFile(input));
          if (!response.ok) throw new Error('The sample photo could not be loaded.');
          file = new File([await response.blob()], input);
        } else file = input;
        setStage(`Uploading ${file.name}…`);
        const form = new FormData();
        form.set('photo', file);
        const photo = await api.post<Photo>(`${path}/photos`, form, `${key}-upload`);
        setNames(old => ({ ...old, [photo.id]: file.name }));
        setStage(`Analysing ${file.name}…`);
        await api.post<Analysis>(`${path}/photo-analyses`, { photo_ids: [photo.id] }, `${key}-analyse`);
        setTask(null);
      } finally { setStage(''); }
    });
  }
  const uploadControls = <>
      <input ref={inputRef} className={inline && inputRef ? 'sr-only' : ''} tabIndex={inline && inputRef ? -1 : undefined}
        aria-label="Damage photograph" type="file" accept="image/jpeg,image/png,image/webp" disabled={action.pending} onChange={event => {
        const file = event.target.files?.[0];
        if (file) upload(file);
        event.target.value = '';
      }} />
      {guided && <div className="row wrap">
        <Button variant="outline" disabled={action.pending} onClick={() => upload('synthetic-wall.png')}><Camera /> Use sample image</Button>
        <Button variant="ghost" disabled={action.pending} onClick={() => upload('fema-wall.jpg')}>Use FEMA sample photograph</Button>
        <a className="inline-link" href={photoAttribution}>Photo credit</a>
      </div>}
      {stage && <Notice>{stage}</Notice>}
      {photos.isPending && <Loading />}
      <Failure error={photos.error || action.error} />
      {task && action.error && <Button variant="outline" disabled={action.pending} onClick={() => upload(task.input, task.key)}>
        Retry {typeof task.input === 'string' ? task.input : task.input.name}</Button>}
      <div className="photo-grid">{photos.data?.map(photo => <div className="inset" key={photo.id}>
        <Thumbnail token={token} claimID={claimID} photo={photo} />
        <div className="row spread"><span>{names[photo.id] || `Photo ${shortID(photo.id)}`}</span>
          <Button aria-label={`Delete photo ${shortID(photo.id)}`} variant="ghost" size="icon" disabled={action.pending} onClick={() => {
            if (window.confirm('Delete this photo?')) void action.run(`delete:${photo.id}`, key => api.delete(`${path}/photos/${photo.id}`, key));
          }}><Trash2 /></Button></div>
      </div>)}</div>
  </>;
  const observations = <><Failure error={analyses.error} />
    {analyses.isPending ? <Loading /> : <Observations analyses={analyses.data ?? []} />}</>;
  return <>
    <Milestone step="photo" when={!!analyses.data?.some(analysis => analysis.status === 'completed')} />
    {inline ? <div className="inline-photos stack tight">
      {ready && !analyses.data?.some(analysis => analysis.status === 'completed') && <Guide step="photo">Try the sample photo to see visible damage and AI observations. You can also confirm without a photo.</Guide>}
      {uploadControls}
      {!!analyses.data?.length && <details open><summary>Damage observations</summary>{observations}</details>}
    </div> : <div className="split">
      <Panel title="Damage photos" caption="Selecting an image uploads and analyses it automatically.">{uploadControls}</Panel>
      <Panel title="Photo observations" caption="Image observations stay separate from confirmed claim facts.">{observations}</Panel>
    </div>}
  </>;
}
