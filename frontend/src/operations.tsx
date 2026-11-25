import { useQuery } from '@tanstack/react-query';
import { Activity, ArrowDownRight, ArrowRight, BookOpen, CheckCircle2, ClipboardList, Clock3, GitBranch, ListFilter, Radio, Search, ShieldCheck, Truck } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { client, type Assessment, type ClaimDetail, type ClaimRow, type Contractor, type DispatchSummary,
  type DocumentRow, type Proposal, type RequirementSource, type Run, type Trace, type Workflow } from './api';
import { AnswerView, Badge, Empty, Facts, Failure, Loading, Notice, Observations, Panel, label,
  localTime, providerName, shortID, useAction, valueText } from './shared';
import { Button } from './components/ui/button';
import { ClaimProgress, scenarioFor } from './demo';

export function Operations({ token, connect }: { token: string; connect: () => void }) {
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const claims = useQuery({ queryKey: ['operator-claims', offset], enabled: !!token,
    queryFn: () => client(token).get<ClaimRow[]>(`/operator/claims?limit=50&offset=${offset}`) });
  const rows = claims.data;
  const displayed = rows?.filter(row => (filter === 'all' || row.status === filter) &&
    `${row.claim_id} ${valueText(row.damage_type)}`.toLowerCase().includes(search.toLowerCase()));
  const cards = [
    { label: 'Claims on this page', count: rows?.length, icon: ClipboardList, tone: 'teal', detail: 'Workspace intake records' },
    { label: 'Awaiting confirmation', count: rows?.filter(c => c.status === 'awaiting_customer_confirmation').length, icon: Clock3, tone: 'amber', detail: 'Customer review needed' },
    { label: 'Confirmed intake', count: rows?.filter(c => c.status === 'intake_completed').length, icon: CheckCircle2, tone: 'blue', detail: 'Available for assessment' },
    { label: 'Accepted dispatches', count: rows?.filter(c => c.dispatch_status === 'accepted').length, icon: Truck, tone: 'green', detail: 'Simulated contractor responses' },
  ];
  return <div className="stack dashboard">
    <div className="page-heading"><div><div className="eyebrow"><span /> CLAIMS OPERATIONS</div><h1>Claims Dashboard</h1>
      <p>Manage property claims from intake through contractor coordination.</p></div>
      <Button asChild variant="outline"><Link to="/customer"><ArrowDownRight /> Customer portal</Link></Button>
    </div>
    <div className="metric-grid">{cards.map(({ icon: Icon, ...card }) => <article className="metric" key={card.label}>
      <div className="row spread"><span>{card.label}</span><span className={`metric-icon ${card.tone}`}><Icon size={17} /></span></div>
      <strong>{card.count ?? '—'}</strong><small>{card.detail}</small>
    </article>)}</div>
    <div className="dashboard-grid">
      <Panel title="Claims queue" caption="Review intake status and open a claim to run its workflow." action={<ClipboardList className="panel-icon" size={19} />}>
        <div className="table-tools"><label className="search"><Search size={16} /><input aria-label="Search claims" placeholder="Search by claim ID or damage" value={search} onChange={event => setSearch(event.target.value)} /></label>
          <label className="filter"><ListFilter size={16} /><select aria-label="Claim status filter" value={filter} onChange={event => setFilter(event.target.value)}>
            <option value="all">All statuses</option>{['collecting_information', 'awaiting_clarification', 'awaiting_customer_confirmation', 'intake_completed', 'human_assistance_required'].map(status =>
              <option key={status} value={status}>{label(status)}</option>)}</select></label></div>
        <div className="scroll-table"><table><thead><tr><th>Claim</th><th>Damage</th><th>Intake status</th><th>Dispatch</th><th><span className="sr-only">Open claim</span></th></tr></thead>
          <tbody>{displayed?.map(row => <tr key={row.claim_id}><td><Link className="table-link mono" to={`/claims/${row.claim_id}`}>{shortID(row.claim_id)}</Link></td>
            <td>{valueText(row.damage_type)}</td><td><Badge value={row.status} /></td>
            <td>{row.dispatch_status ? <Badge value={row.dispatch_status} /> : '—'}</td>
            <td><Link className="icon-link" aria-label={`Open claim ${shortID(row.claim_id)}`} to={`/claims/${row.claim_id}`}><ArrowRight size={15} /></Link></td></tr>)}</tbody>
        </table></div>
        <Failure error={claims.error} />
        {token && claims.isPending ? <Loading /> : !token ?
          <Empty icon={<ShieldCheck />} title="Connect your operator session"><p>Sign in with a local operator token to open the claims queue.</p><Button onClick={connect}>Connect session <ArrowRight /></Button></Empty> :
          !displayed?.length && !claims.error ? <Empty icon={<ClipboardList />} title={search || filter !== 'all' ? 'No matching claims' : 'No claims in this workspace'}>
            <Link className="table-link" to="/customer">Start in the customer portal <ArrowRight size={14} /></Link></Empty> : null}
        {token && (offset > 0 || rows?.length === 50) && <div className="row spread"><Button variant="outline" disabled={!offset} onClick={() => setOffset(offset - 50)}>Previous</Button>
          <Button variant="outline" disabled={rows?.length !== 50} onClick={() => setOffset(offset + 50)}>Next</Button></div>}
      </Panel>
      <div className="stack">
        <Panel title="Dispatch workflow" caption="Operator controls" action={<GitBranch className="panel-icon" size={19} />}>
          <ol className="guide-list">
            <li><span><ClipboardList size={16} /></span><div><strong>Confirm intake</strong><p>Customer facts and damage observations stay separate.</p></div></li>
            <li><span><BookOpen size={16} /></span><div><strong>Review evidence</strong><p>Assess the bound policy and inspect contractor recommendations.</p></div></li>
            <li><span><ShieldCheck size={16} /></span><div><strong>Approve and dispatch</strong><p>Approve the exact proposal before sending a simulated dispatch.</p></div></li>
          </ol>
        </Panel>
        <Panel title="Knowledge base" caption="Manage the sources used to assess claims." action={<BookOpen className="panel-icon" size={19} />}>
          <Link className="inline-link" to="/knowledge">Open document library <ArrowRight size={15} /></Link>
        </Panel>
      </div>
    </div>
  </div>;
}

export function OperatorClaim({ token, connect, guided = false }: { token: string; connect: () => void; guided?: boolean }) {
  const { claimID = '' } = useParams();
  if (!token) return <Panel title="Claim workspace"><Empty icon={<ShieldCheck />} title="Operator credentials required"><Button onClick={connect}>Connect operator session</Button></Empty></Panel>;
  return <ClaimWorkspace claimID={claimID} token={token} guided={guided} key={claimID} />;
}

const interventions: Record<string, string> = {
  confirmed_intake_required: 'Ask the customer to finish and confirm the damage report, then resume.',
  policy_unbound: 'Bind an indexed policy version before resuming.',
  potentially_excluded: 'Review the cited exclusion. This needs a human coverage decision.',
  insufficient_evidence: 'Check the policy version and indexed sources. Add evidence before resuming.',
  conflicting_evidence: 'Review the conflicting policy evidence before resuming.',
  no_eligible_contractors: 'No eligible contractor serves this claim. Confirm the ZIP or availability before resuming.',
  immediate_hazard: 'An immediate hazard was reported. Route this claim to a person for safety review.',
  stale_proposal: 'The proposal changed or expired. Resume to generate a current proposal.',
  reassignment_limit: 'The automatic reassignment limit was reached. Review this claim manually.',
};

function ClaimWorkspace({ token, claimID, guided }: { token: string; claimID: string; guided: boolean }) {
  const api = client(token);
  const [params] = useSearchParams();
  const scenario = scenarioFor(params.get('scenario'));
  const base = `/claims/${claimID}`;
  const action = useAction();
  const [workflowChoice, setWorkflowChoice] = useState('');
  const [reason, setReason] = useState('');
  const [version, setVersion] = useState('');
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const detailQuery = useQuery({ queryKey: ['operator-claim', claimID], queryFn: () => api.get<ClaimDetail>(`/operator/claims/${claimID}`) });
  const docs = useQuery({ queryKey: ['documents', 0], queryFn: () => api.get<DocumentRow[]>('/knowledge/documents?limit=100') });
  const catalog = useQuery({ queryKey: ['contractors'], queryFn: () => api.get<Contractor[]>('/contractors') });
  const detail = detailQuery.data;
  const workflowID = workflowChoice || detail?.workflow_ids[0];
  const path = `${base}/workflows/${workflowID}`;
  const dispatchQuery = useQuery({ queryKey: ['dispatch', claimID, workflowID], enabled: !!workflowID,
    queryFn: () => api.get<DispatchSummary>(`${path}/dispatch`) });
  const workflow = dispatchQuery.data?.workflow;
  const sources = useQuery({ queryKey: ['policy-evidence', claimID, workflowID, workflow?.policy_citations],
    enabled: !!workflow?.policy_citations?.length,
    queryFn: () => api.get<RequirementSource[]>(`/operator/claims/${claimID}/workflows/${workflowID}/policy-evidence`) });
  const runID = workflow?.contractor_run_id;
  const runPath = `${base}/contractor-recommendations/${runID}`;
  const run = useQuery({ queryKey: ['recommendation', claimID, runID], enabled: !!runID, queryFn: () => api.get<Run>(runPath) });
  const trace = useQuery({ queryKey: ['trace', claimID, runID], enabled: !!runID, queryFn: () => api.get<Trace>(`${runPath}/trace`) });
  if (detailQuery.isPending) return <Loading />;
  if (!detail) return <Failure error={detailQuery.error} />;
  const { claim, urgency } = detail;
  const policy = valueText(claim.facts.policy_id?.value);
  const versions = [...new Set(docs.data?.filter(doc => doc.status === 'ready' && doc.policy_id === policy && doc.policy_version).map(doc => doc.policy_version!) ?? [])];
  const suggestedVersion = guided && policy === scenario.policy && versions.includes('v1') ? 'v1' : versions.length === 1 ? versions[0] : '';
  const selectedVersion = version || detail.policy_version || suggestedVersion;
  const canContinue = ['requires_intervention', 'awaiting_information'].includes(workflow?.status ?? '');
  const dispatching = ['approved', 'awaiting_acknowledgement', 'accepted'].includes(workflow?.status ?? '');
  const next = !detail.policy_version ? ['Bind the policy', 'Choose the matching version to let ClaimPilot check coverage.', '#policy-review'] :
    !workflow ? ['Run claim review', 'ClaimPilot will check the policy and look for eligible contractors.', '#workflow-review'] :
    canContinue ? ['Review required', interventions[workflow.reason ?? ''] || `Resolve ${label(workflow.reason).toLowerCase()} before retrying.`, '#workflow-review'] :
    workflow.status === 'awaiting_approval' ? ['Review the recommendation', 'Read the evidence and record your decision for this exact version.', '#workflow-review'] :
    workflow.status === 'approved' ? ['Send simulated dispatch', 'Approval is recorded. Send the request to the simulated contractor.', '#workflow-review'] :
    workflow.status === 'awaiting_acknowledgement' ? ['Try a contractor response', 'Accept to complete the demo or reject to see reassignment and fresh approval.', '#dispatch-review'] :
    ['Review the workflow', workflow.status === 'accepted' ? 'The simulated contractor accepted. Review the dispatch summary.' : 'Check the latest workflow decision below.', workflow.status === 'accepted' ? '#dispatch-review' : '#workflow-review'];
  const post = (endpoint: string, body: unknown, after?: (result: DispatchSummary) => void) => {
    void action.run(`post:${endpoint}:${JSON.stringify(body)}`, async key => {
      const result = await api.post<DispatchSummary>(endpoint, body, key);
      after?.(result);
    });
  };
  const decision = (choice: 'approve' | 'reject') => {
    if (!workflow?.pause_id || !reason.trim()) return;
    post(`${path}/resume`, { pause_id: workflow.pause_id, expected_version: workflow.claim_version,
      action: choice, proposal_id: workflow.proposal_id, proposal_version: workflow.proposal_version, reason: reason.trim() }, () => setReason(''));
  };
  const dispatchPost = (endpoint: string, body?: unknown) => {
    void action.run(`dispatch:${endpoint}:${JSON.stringify(body)}`, async key => { await api.post(endpoint, body, key); });
  };
  return <div className="stack">
    <Link className="inline-link" to="/operator">← All claims</Link>
    <div className="page-heading"><div><h1>{guided ? 'Agentic Workflow' : 'Claim review'}</h1><p>{valueText(claim.facts.damage_type?.value)} · {valueText(claim.facts.property_location?.value)}</p></div>
      <div className="row wrap"><Badge value={claim.status} /><Badge value={urgency.level} /></div></div>
    <Failure error={action.error || docs.error || catalog.error} />
    {action.pending && <Loading />}
    {urgency.mandatory_escalation && <Notice kind="error">Urgent human assistance required.</Notice>}
    <ClaimProgress stage={dispatching ? 3 : claim.status === 'intake_completed' ? 2 : claim.status === 'awaiting_customer_confirmation' ? 1 : 0} />
    <section className="next-action"><div><strong>{next[0]}</strong><p>{next[1]}</p></div>
      <Button asChild variant="outline"><a href={next[2]}>Go to step <ArrowRight /></a></Button></section>
    {workflow && <div className="split">
      <Panel title="Execution timeline" caption="Completed steps are saved as checkpoints. You can return to a paused workflow.">
        <ol className="timeline">{workflow.transitions.map((event, index) =>
          <li key={index}><span /><div><strong>{nodeLabels[event.node] || label(event.node)}</strong> · {label(event.status)}
            {event.reason && <p>{label(event.reason)}</p>}</div></li>)}</ol>
        {workflow.pause_id && <Notice>{workflow.status === 'awaiting_approval'
          ? 'Paused for human approval. The agent cannot approve its own proposal.'
          : 'A checkpoint is saved while the workflow waits for the next action.'}</Notice>}
      </Panel>
      <Panel title="Tools the agent used" caption="Recorded calls and results from the contractor recommendation.">
        <Failure error={trace.error} />
        {runID && trace.isPending ? <Loading /> : trace.data ? <TraceView trace={trace.data} /> : <p className="muted">No contractor tool calls recorded.</p>}
      </Panel>
    </div>}
    <div className="split">
      <Panel title="Confirmed facts" caption={urgency.reasons.map(label).join(' · ')}><Facts facts={claim.facts} confirmedOnly /></Panel>
      <Panel title="Photo observations" caption="Image evidence is displayed separately from confirmed facts."><Observations analyses={detail.analyses} /></Panel>
    </div>
    <Panel id="policy-review" title="Policy assessment" caption="Preliminary guidance for operator review.">
      <div className="row wrap"><span>Policy {policy}</span><label className="sr-only" htmlFor="policy-version">Policy version</label>
        <select id="policy-version" value={selectedVersion} onChange={event => setVersion(event.target.value)}>
          <option value="">Select version</option>{[...new Set([...versions, ...(detail.policy_version ? [detail.policy_version] : [])])].map(item => <option key={item}>{item}</option>)}</select>
        <Button variant="outline" disabled={action.pending || !selectedVersion || claim.status !== 'intake_completed' || selectedVersion === detail.policy_version}
          onClick={() => void action.run(`bind:${selectedVersion}`, async key => {
            await api.post(`${base}/policy-binding`, { policy_version: selectedVersion }, key); setAssessment(null);
          })}>Bind policy version</Button>
        <Button variant="outline" disabled={action.pending || !detail.policy_version} onClick={() => void action.run('policy-assessment', async () => {
          setAssessment(await api.post<Assessment>(`${base}/policy-assessment`));
        })}>Assess policy &amp; view citations</Button>
      </div>
      {!detail.policy_version && suggestedVersion && <p className="muted">Suggested for this claim: {policy} · {suggestedVersion}.</p>}
      {workflow?.policy_outcome && <div className="row wrap">Workflow assessment <Badge value={workflow.policy_outcome} /></div>}
      <Failure error={sources.error} />
      {assessment ? <AnswerView answer={assessment.assessment} /> : sources.data?.length ? <div>{sources.data.map(source =>
        <details key={source.chunk_id}><summary>{source.section || source.filename}<small>{source.filename}</small></summary><blockquote>{source.excerpt}</blockquote></details>)}</div> :
        workflow?.policy_citations?.length ? <p className="muted">{sources.isPending ? 'Loading workflow citations…' : 'The original citations are no longer available for this claim snapshot.'}</p> : null}
    </Panel>
    <Panel id="workflow-review" title="Claim workflow" caption="Review state and resume the current checkpoint.">
      <div className="row wrap"><label htmlFor="workflow-id">Workflow</label>
        <select id="workflow-id" value={workflowID || ''} onChange={event => setWorkflowChoice(event.target.value)}>
          <option value="">No workflow selected</option>{detail.workflow_ids.map((id, i) =>
            <option key={id} value={id}>{i === 0 ? 'Latest workflow' : `Earlier workflow ${i}`}{id === workflow?.workflow_id ? ` · ${label(workflow.status)}` : ''}</option>)}
        </select>
        <Button disabled={action.pending || (guided && !detail.policy_version) || (!!detail.workflow_ids.length && dispatchQuery.isPending)} onClick={() => {
          if (workflow?.pause_id && !window.confirm('This claim already has a paused workflow. Start a separate run instead of resuming it?')) return;
          void action.run(`start:${claim.version}`, async key => {
            const result = await api.post<Workflow>(`${base}/workflows`, { expected_version: claim.version }, key);
            setWorkflowChoice(result.workflow_id);
          });
        }}><Activity /> Start workflow</Button></div>
      <Failure error={dispatchQuery.error || run.error} />
      {workflowID && dispatchQuery.isPending && <Loading />}
      {workflow && <div className="stack">
        <div className="row wrap"><Badge value={workflow.status} />{workflow.reason && <span>{label(workflow.reason)}</span>}
          {workflow.updated_at && <small>Updated {localTime(workflow.updated_at)}</small>}</div>
        {workflow.execution_error && <Notice kind="error">{label(workflow.execution_error)}. Retry the prior request if it failed.</Notice>}
        {workflow.pause_id && ['requires_intervention', 'awaiting_information'].includes(workflow.status) &&
          <Button variant="outline" disabled={action.pending} onClick={() => void action.run(`retry:${workflow.pause_id}:${claim.version}`, async key => {
            await api.post(`${path}/resume`, { pause_id: workflow.pause_id, action: 'retry', expected_version: claim.version }, key);
          })}>Resume after review</Button>}
        {run.data && <ProposalCard run={run.data} catalog={catalog.data ?? []} />}
        {workflow.status === 'awaiting_approval' && workflow.pause_id && <div className="inset stack tight">
          <label htmlFor="approval-reason">Decision reason for proposal v{workflow.proposal_version}</label>
          <textarea id="approval-reason" rows={2} value={reason} maxLength={500} onChange={event => setReason(event.target.value)} />
          <div className="row wrap"><Button disabled={action.pending || !reason.trim() || !run.data?.proposal || run.data.proposal.stale}
            onClick={() => decision('approve')}><ShieldCheck /> Approve exact version</Button>
            <Button variant="destructive" disabled={action.pending || !reason.trim()} onClick={() => decision('reject')}>Reject proposal</Button></div>
        </div>}
        {workflow.status === 'approved' && <Button disabled={action.pending} onClick={() => dispatchPost(`${path}/dispatch`, {
          proposal_id: workflow.proposal_id, proposal_version: workflow.proposal_version,
        })}><Truck /> Send simulated dispatch</Button>}
      </div>}
    </Panel>
    {dispatchQuery.data && <Panel id="dispatch-review" title="Simulated dispatch summary" caption="Acknowledgement decisions, deadlines and SLA outcomes.">
      {!dispatchQuery.data.dispatches.length && <p className="muted">No simulated dispatch has been sent.</p>}
      {dispatchQuery.data.dispatches.map(row => <article className="inset stack tight" key={row.id}>
        <div className="row spread wrap"><strong>{catalog.data?.find(contractor => contractor.id === row.contractor_id)?.name || 'Contractor'} · Proposal v{row.proposal_version}</strong><Badge value={row.status} /></div>
        <p>Sent {localTime(row.sent_at)} · Due {localTime(row.acknowledgement_deadline)} · Resolved {localTime(row.resolved_at)}</p>
        <p>Approved by {row.reviewer_id} · {localTime(row.decided_at)} · {row.decision_reason}</p>
        <p>SLA: {label(row.sla_outcome)} · {label(row.sla.provenance)} · {row.sla.acknowledgement_limit_minutes} min acknowledgement</p>
        {row.sla.sources.map(source => <details key={source.chunk_id}><summary>{source.section || source.filename}</summary><blockquote>{source.excerpt}</blockquote></details>)}
        {row.status === 'awaiting_acknowledgement' && <div className="row wrap">
          <Button disabled={action.pending} onClick={() => dispatchPost(`${path}/dispatch/${row.id}/response`, { outcome: 'accepted' })}>Simulate acceptance</Button>
          <Button variant="outline" disabled={action.pending} onClick={() => dispatchPost(`${path}/dispatch/${row.id}/response`, { outcome: 'rejected' })}>Reject and find replacement</Button>
          <Button variant="outline" disabled={action.pending} onClick={() => dispatchPost(`${path}/dispatch/${row.id}/sla`)}><Clock3 /> Process SLA</Button>
        </div>}
      </article>)}
    </Panel>}
  </div>;
}

function ProposalCard({ run, catalog }: { run: Run; catalog: Contractor[] }) {
  const contractorName = (id: string) => catalog.find(item => item.id === id)?.name || id;
  const p: Proposal | null = run.proposal;
  if (!p) return <p className="muted">The contractor recommendation is in progress.</p>;
  return <div className="inset stack tight">
    <div className="row spread wrap"><strong>Proposal v{p.version}</strong><span className="muted">{providerName(run.provider)} · {run.model}</span></div>
    <p>{p.explanation}</p>
    {p.recommended_contractor_id && <p><strong>{contractorName(p.recommended_contractor_id)}</strong> · {p.estimated_arrival_minutes ?? '—'} min ETA · {label(p.required_specialization)}</p>}
    {[p.recommended_contractor_id, ...p.alternative_contractor_ids].map(id => {
      const contractor = catalog.find(item => item.id === id);
      return contractor && <details key={id}><summary>{contractor.name} · service and eligibility</summary>
        <p>Service ZIPs: {contractor.zip_codes.join(', ')}</p><p>{contractor.active && contractor.available ? 'Active and available' : 'Unavailable'} · {contractor.capacity} slots · {contractor.credentialed ? 'Credentialed' : 'Credentials missing'} · {contractor.insured ? 'Insured' : 'Insurance missing'}</p></details>;
    })}
    {p.alternative_contractor_ids.length > 0 && <p>Alternatives: {p.alternative_contractor_ids.map(contractorName).join(', ')}</p>}
    {p.stale && <Notice kind="error">This proposal is stale. It cannot be approved.</Notice>}
    {p.uncertainties.map(item => <p key={item} className="muted">{label(item)}</p>)}
    {p.requirements && <p className="muted">{p.requirements.acknowledgement_limit_minutes} min acknowledgement · {label(p.requirements.provenance)}</p>}
    <small>Proposed {localTime(p.created_at)}</small>
  </div>;
}

const nodeLabels: Record<string, string> = {
  intake: 'Validate the claim facts', policy: 'Read policy evidence', contractor: 'Find eligible contractors',
  approval: 'Human decision', dispatch: 'Simulate dispatch', acknowledgement: 'Record contractor response', review: 'Operator review',
};
const toolLabels: Record<string, string> = {
  search_contractors: 'Find contractors that serve this claim', check_availability: 'Check available capacity',
  get_estimated_arrival: 'Estimate time to arrive', get_contractor_performance: 'Check past contractor performance',
  get_dispatch_requirements: 'Read dispatch and approval rules', propose_dispatch: 'Prepare proposal for human approval',
};
function toolOutcome(tool: Trace['tools'][number]) {
  if (!tool.success) return label(typeof tool.result.error === 'string' ? tool.result.error : 'failed');
  const result = tool.result;
  if (Array.isArray(result.candidate_ids)) return `${result.candidate_ids.length} eligible candidates returned`;
  if (typeof result.capacity === 'number') return `${result.capacity} available slots · ${result.eligible ? 'Eligible' : 'Ineligible'}`;
  if (typeof result.minutes === 'number') return `${result.minutes} minute arrival estimate`;
  if (typeof result.rating === 'number') return `Recorded rating: ${result.rating}/5`;
  if (typeof result.acknowledgement_limit_minutes === 'number') return `${result.acknowledgement_limit_minutes} minute acknowledgement limit`;
  return tool.tool === 'propose_dispatch' ? 'Proposal recorded for review' : 'Result recorded';
}
function TraceView({ trace }: { trace: Trace }) {
  const totals = trace.model_turns.flatMap(turn => turn.usage.total_tokens === undefined ? [] : [turn.usage.total_tokens]);
  return <div className="stack tight">{trace.decision_summary && <p>{trace.decision_summary}</p>}
    <p className="muted"><Radio size={14} className="inline" /> {trace.model_turns.length} model turns · {totals.length ? `${totals.reduce((sum, count) => sum + count, 0)} reported tokens` : 'Token usage unavailable'}</p>
    {trace.tools.map((tool, index) => <details key={tool.call_id || index}>
      <summary><strong>{toolLabels[tool.tool] || label(tool.tool)}</strong><small>{tool.duration_ms.toFixed(0)} ms</small></summary>
      <p>{toolOutcome(tool)}</p><small>{tool.tool} · {tool.attempts} execution attempts</small>
      <pre>{JSON.stringify({ arguments: tool.arguments, result: tool.result }, null, 2)}</pre></details>)}
    {!!trace.model_turns.length && <details><summary>Model timing and token usage</summary>{trace.model_turns.map(turn =>
      <p key={turn.iteration}>Turn {turn.iteration} · {turn.duration_ms.toFixed(0)} ms
        {turn.usage.total_tokens != null ? ` · ${turn.usage.total_tokens} tokens` : ''}{turn.error ? ` · ${label(turn.error)}` : ''}</p>)}</details>}
  </div>;
}
