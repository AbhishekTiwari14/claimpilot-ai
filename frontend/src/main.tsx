import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { Activity, BookOpen, ChevronDown, ChevronRight, ClipboardList, FlaskConical, LayoutDashboard, LogIn, Menu, MessageSquare, RotateCcw, Shield, Truck, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { ApiError, client, onDemoSessionExpired, type History, type Turn } from './api';
import { experienceFor, Landing, sampleMessages, scenarioFor, type Experience } from './demo';
import Customer from './customer';
import { Knowledge, Playground, demoDocumentCount, seedDemoDocuments } from './knowledge';
import { RagShowcase } from './rag-showcase';
import { Operations, OperatorClaim } from './operations';
import { Contractors, Evaluations } from './resources';
import { Button } from './components/ui/button';
import { Failure, Notice, providerName } from './shared';
import { resetWalkthrough, Walkthrough, WalkthroughProgress } from './walkthrough';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 15000, refetchOnWindowFocus: true }, mutations: { retry: false } },
});
type Role = 'operator' | 'claimant';
const navigation = [
  { to: '/operator', name: 'Claims Dashboard', icon: LayoutDashboard },
  { to: '/contractors', name: 'Contractors', icon: Truck },
  { to: '/knowledge', name: 'Knowledge Base', icon: BookOpen },
  { to: '/playground', name: 'RAG Playground', icon: FlaskConical },
  { to: '/evaluations', name: 'AI Evaluations', icon: Activity },
];
const customerNav = [
  { to: '/customer', name: 'Report Damage', icon: MessageSquare },
  { to: '/customer/claims', name: 'My Claims', icon: ClipboardList },
];
const tokenKey = (role: Role) => `claimpilot.session.${role}`;
const setupStorageKey = 'claimpilot.demo.setup';
const portalLaunchKey = 'claimpilot.demo.portal-launch';
const portalPath = /^\/(?:customer(?:\/claims)?|operator|claims\/[^/]+|knowledge|playground|showcase\/rag|contractors)\/?$/;
type DemoSetup = {
  key: string;
  sample?: { scenario: string; conversation: string; claim: string; version: number; nextMessage: number };
};
function readSetup(): DemoSetup | null {
  try { return JSON.parse(sessionStorage.getItem(setupStorageKey) ?? 'null'); }
  catch { return null; }
}
const setupQueryOptions = { staleTime: 'static', retryOnMount: false } as const;

function App() {
  const location = useLocation();
  const navigate = useNavigate();
  useLayoutEffect(() => { window.scrollTo(0, 0); }, [location.pathname]);
  const [sessions, setSessions] = useState<Record<Role, string>>(() => ({
    operator: sessionStorage.getItem(tokenKey('operator')) || '', claimant: sessionStorage.getItem(tokenKey('claimant')) || '',
  }));
  const [dialog, setDialog] = useState<Role | null>(null);
  const [menu, setMenu] = useState(false);
  const [revision, setRevision] = useState(0);
  const [demoSession, setDemoSession] = useState(() => sessionStorage.getItem('claimpilot.demo') === 'true');
  const [setup, setSetup] = useState(readSetup);
  const [indexed, setIndexed] = useState({ key: '', count: 0 });
  const [phase, setPhase] = useState('');
  const [demoError, setDemoError] = useState<unknown>(null);
  const launchKey = useRef<{ target: string; key: string } | null>(null);
  const demoAvailability = useQuery({ queryKey: ['demo-availability'],
    queryFn: async () => {
      try { return await client('').get<{ enabled: boolean; hosted?: boolean; provider?: 'groq' | 'offline' }>('/demo/status'); }
      catch (error) {
        if (error instanceof ApiError && (error.status === 404 || error.code === 'demo_local_only')) return { enabled: false, hosted: false };
        throw error;
      }
    },
  });
  const hosted = demoAvailability.data?.hosted === true;
  const hasWorkspace = demoSession && !!sessions.claimant && !!sessions.operator;
  const needsWorkspace = hosted && portalPath.test(location.pathname) && !hasWorkspace;
  const portalSession = useQuery({
    ...setupQueryOptions,
    queryKey: ['portal-session', revision],
    enabled: needsWorkspace && !phase,
    queryFn: async () => {
      const key = sessionStorage.getItem(portalLaunchKey) || crypto.randomUUID();
      sessionStorage.setItem(portalLaunchKey, key);
      try {
        return await client('').post<Record<Role, string>>('/demo/session', undefined, key);
      } catch (error) {
        if (error instanceof ApiError && error.code === 'demo_session_required') sessionStorage.removeItem(portalLaunchKey);
        throw error;
      }
    },
  });
  const saveDemoSession = useCallback((session: Record<Role, string>, nextSetup: DemoSetup | null) => {
    if (nextSetup) sessionStorage.setItem(setupStorageKey, JSON.stringify(nextSetup));
    else sessionStorage.removeItem(setupStorageKey);
    sessionStorage.removeItem(portalLaunchKey);
    for (const role of ['operator', 'claimant'] as const) sessionStorage.setItem(tokenKey(role), session[role]);
    sessionStorage.setItem('claimpilot.demo', 'true');
    resetWalkthrough();
    setSetup(nextSetup);
    setSessions(session);
    setDemoSession(true);
    setRevision(value => value + 1);
  }, []);
  useEffect(() => {
    if (needsWorkspace && !phase && portalSession.data) {
      queryClient.removeQueries({ predicate: ({ queryKey }) => !['demo-availability', 'portal-session'].includes(String(queryKey[0])) });
      saveDemoSession(portalSession.data, null);
    }
  }, [needsWorkspace, phase, portalSession.data, saveDemoSession]);
  useLayoutEffect(() => {
    if (!hosted || !hasWorkspace) return;
    return onDemoSessionExpired(token => {
      if (!Object.values(sessions).includes(token) ||
          !(['claimant', 'operator'] as const).some(role => sessionStorage.getItem(tokenKey(role)) === token)) return;
      for (const role of ['operator', 'claimant'] as const) sessionStorage.removeItem(tokenKey(role));
      sessionStorage.removeItem('claimpilot.demo');
      sessionStorage.removeItem(setupStorageKey);
      setSetup(null);
      setSessions({ claimant: '', operator: '' });
      setDemoSession(false);
      setRevision(value => value + 1);
      if (location.pathname.startsWith('/claims/')) navigate('/operator', { replace: true });
      else if (location.pathname.startsWith('/customer') && location.search) navigate(location.pathname, { replace: true });
    });
  }, [hasWorkspace, hosted, location.pathname, location.search, navigate, sessions]);
  const role = location.pathname.startsWith('/customer') ? 'claimant' : 'operator';
  const params = new URLSearchParams(location.search);
  const experience = experienceFor(params.get('experience'));
  const policies = useQuery({
    ...setupQueryOptions,
    queryKey: ['demo-policies', setup?.key],
    enabled: hasWorkspace && !!setup && location.pathname !== '/',
    queryFn: async ({ signal }) => {
      const key = setup!.key;
      await seedDemoDocuments(sessions.operator, key, {
        signal, onProgress: count => setIndexed({ key, count }),
      });
      await queryClient.invalidateQueries({ queryKey: ['documents'] });
      return true;
    },
  });
  const sampleIntake = useQuery({
    ...setupQueryOptions,
    queryKey: ['demo-intake', setup?.key],
    enabled: hasWorkspace && !!setup?.sample && location.pathname !== '/',
    queryFn: async ({ signal }) => {
      const { key } = setup!;
      const saved = readSetup();
      const sample = { ...(saved?.key === key ? saved.sample! : setup!.sample!) };
      const claimant = client(sessions.claimant);
      const messages = sampleMessages(scenarioFor(sample.scenario));
      try {
        for (; sample.nextMessage < messages.length;) {
          signal.throwIfAborted();
          const turn = await claimant.post<Turn>(`/conversations/${sample.conversation}/messages`,
            { message: messages[sample.nextMessage], expected_version: sample.version }, `${key}-${sample.nextMessage}`);
          signal.throwIfAborted();
          sample.version = turn.claim.version;
          sample.nextMessage += 1;
          sessionStorage.setItem(setupStorageKey, JSON.stringify({ key, sample }));
        }
      } finally {
        if (!signal.aborted) await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['conversation', sample.conversation] }),
          queryClient.invalidateQueries({ queryKey: ['customer-claim', sample.claim] }),
        ]);
      }
      return true;
    },
  });
  const preparingPolicies = demoSession && !!setup && !policies.isSuccess;
  const preparingSample = demoSession && !!setup?.sample && params.get('conversation') === setup.sample.conversation && !sampleIntake.isSuccess;
  const items = role === 'claimant' ? customerNav : navigation;
  const heading = location.pathname === '/customer' && experience === 'vision' ? 'Damage Analysis' :
    location.pathname === '/customer' && experience === 'chat' ? 'Conversational AI' :
    location.pathname.startsWith('/claims/') ? 'Claim Review' :
    location.pathname === '/showcase/rag' ? 'Explore the Knowledge Base' :
    items.find(item => item.to === location.pathname)?.name ?? 'Page Not Found';
  useEffect(() => {
    document.title = location.pathname === '/' ? 'ClaimPilot AI | Property Claims' : `${heading} | ClaimPilot AI`;
  }, [heading, location.pathname]);
  const connect = () => { if (demoAvailability.isSuccess && !hosted) setDialog(role); };
  const update = (selected: Role, token: string) => {
    if (demoSession) {
      for (const role of ['operator', 'claimant'] as const) sessionStorage.removeItem(tokenKey(role));
      sessionStorage.removeItem('claimpilot.demo');
      sessionStorage.removeItem(setupStorageKey);
      setSetup(null);
      setSessions({ operator: '', claimant: '', [selected]: token });
      setDemoSession(false);
    } else setSessions(prev => ({ ...prev, [selected]: token }));
    if (token) sessionStorage.setItem(tokenKey(selected), token);
    else sessionStorage.removeItem(tokenKey(selected));
    queryClient.removeQueries({ predicate: ({ queryKey }) => queryKey[0] !== 'demo-availability' });
    setRevision(prev => prev + 1);
    setDialog(null);
  };
  const launch = async (scenario: string, experience: Experience = 'full') => {
    if (phase || !demoAvailability.data?.enabled) return;
    setDemoError(null);
    const target = `${scenario}:${experience}`;
    const current = launchKey.current?.target === target ? launchKey.current : { target, key: crypto.randomUUID() };
    launchKey.current = current;
    try {
      setPhase('Creating your demo…');
      const session = await client('').post<Record<Role, string>>('/demo/session', undefined, current.key);
      const search = new URLSearchParams({ scenario, experience });
      const turn = experience === 'rag' ? null : await client(session.claimant).post<Turn>('/conversations', undefined, current.key);
      const nextSetup: DemoSetup | null = experience === 'full' || experience === 'workflow' ? { key: current.key } : null;
      queryClient.removeQueries({ predicate: ({ queryKey }) => queryKey[0] !== 'demo-availability' });
      if (turn) {
        const { conversation_id: conversation, claim_id: claim, version } = turn.claim;
        search.set('conversation', conversation);
        queryClient.setQueryData<History>(['conversation', conversation], { claim_id: claim, messages: [turn.reply], next_after: null });
        queryClient.setQueryData(['customer-claim', claim], turn.claim);
        if (nextSetup && experience === 'workflow') nextSetup.sample = { scenario, conversation, claim, version, nextMessage: 0 };
      }
      saveDemoSession(session, nextSetup);
      launchKey.current = null;
      navigate(`${experience === 'rag' ? '/showcase/rag' : '/customer'}?${search}`);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'demo_session_required') launchKey.current = null;
      setDemoError(error);
    }
    finally { setPhase(''); }
  };
  if (location.pathname === '/') return <Landing enabled={demoAvailability.data?.enabled === true}
    hosted={hosted} loading={demoAvailability.isPending} phase={phase} error={demoError || demoAvailability.error} launch={(id, experience) => void launch(id, experience)} />;
  return <div className="app">
    {menu && <button className="mobile-overlay" aria-label="Close navigation" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? 'open' : ''}`}>
      <Link to="/" className="brand" onClick={() => setMenu(false)}><span className="brand-icon"><Shield size={21} /><i /></span>
        <span>ClaimPilot <b>AI</b><small>INSURANCE OPERATIONS</small></span></Link>
      <div className="sidebar-workspace"><span className="workspace-icon"><ClipboardList size={17} /></span><div><strong>{role === 'claimant' ? 'Customer portal' : 'Claims operator'}</strong><small>{demoSession ? (demoAvailability.data?.provider ? providerName(demoAvailability.data.provider) : 'Interactive demo') : 'Claims workspace'}</small></div></div>
      <nav aria-label={role === 'claimant' ? 'Customer navigation' : 'Operator navigation'}><div className="nav-title">WORKSPACE</div>
        {items.map(({ to, name, icon: Icon }) => <NavLink to={to} end className={({ isActive }) =>
          `nav-item ${isActive || (to === '/operator' && location.pathname.startsWith('/claims/')) ? 'active' : ''}`}
          key={to} onClick={() => setMenu(false)}><Icon size={18} />{name}</NavLink>)}
        {hosted && role === 'claimant' && <Link to="/operator" className="nav-item" onClick={() => setMenu(false)}><LayoutDashboard size={18} /> Claims Operator</Link>}
        <Link to="/" className="nav-item" onClick={() => setMenu(false)}><Shield size={18} /> Back to home</Link>
      </nav>
      <div className="sidebar-footer"><span className="footer-dot" /> <div><strong>{hosted ? 'Demo workspace' : 'Local workspace'}</strong><small>Simulated contractor dispatch</small></div></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="row"><Button className="menu-toggle" variant="ghost" size="icon" onClick={() => setMenu(!menu)}
          aria-label={menu ? 'Close navigation' : 'Open navigation'} aria-expanded={menu}>{menu ? <X /> : <Menu />}</Button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={13} /><strong>{heading}</strong></div></div>
        <div className="row wrap">{demoSession && demoAvailability.data?.enabled &&
          <Button variant="ghost" size="sm" disabled={!!phase} onClick={() => void launch(scenarioFor(params.get('scenario')).id, experienceFor(params.get('experience')))}>
            <RotateCcw /> Restart demo</Button>}
          {demoAvailability.isSuccess && !hosted &&
            <button className="session-button" onClick={connect}><span className="session-avatar">{sessions[role] ? (role === 'operator' ? 'OP' : 'CL') : <LogIn size={15} />}</span>
              <span>{sessions[role] ? (role === 'operator' ? 'Operator session' : 'Claimant session') : 'Connect session'}</span><ChevronDown size={15} /></button>}</div>
      </header>
      <Walkthrough key={revision} enabled={demoSession && params.get('experience') === 'full'}>
      <main className="main-content">
        {hosted && <Notice>Use synthetic information only. Use sample or fictional documents, photos, and claim details.</Notice>}
        {phase && <Notice>{phase}</Notice>}<Failure error={demoError} />
        {preparingPolicies && (policies.isError && !policies.isFetching ? <div className="stack tight">
          <Failure error={policies.error} /><Button variant="outline" onClick={() => void policies.refetch({ cancelRefetch: false })}>Retry sample indexing</Button>
        </div> : <Notice>Indexing sample documents ({indexed.key === setup?.key ? indexed.count : 0} of {demoDocumentCount}). Policy review will be available when indexing finishes.</Notice>)}
        {preparingSample && (sampleIntake.isError && !sampleIntake.isFetching ? <div className="stack tight">
          <Failure error={sampleIntake.error} /><Button variant="outline" onClick={() => void sampleIntake.refetch({ cancelRefetch: false })}>Retry sample claim setup</Button>
        </div> : <Notice>Preparing a sample claim for your confirmation…</Notice>)}
        <WalkthroughProgress />
        {demoAvailability.isError ? <div className="stack tight"><Failure error={demoAvailability.error} />
          <Button variant="outline" onClick={() => void demoAvailability.refetch()}>Retry connection</Button></div> :
          demoAvailability.isPending || needsWorkspace ?
            portalSession.isError && needsWorkspace ? <div className="stack tight"><Failure error={portalSession.error} />
              <Button variant="outline" onClick={() => void portalSession.refetch({ cancelRefetch: false })}>Retry demo access</Button></div> :
              <Notice>{needsWorkspace ? 'Opening your demo workspace…' : 'Checking demo access…'}</Notice> :
        <Routes>
          <Route path="/operator" element={<Operations token={sessions.operator} connect={connect} />} />
          <Route path="/claims/:claimID" element={<OperatorClaim token={sessions.operator} connect={connect} guided={demoSession} preparingPolicies={preparingPolicies} />} />
          <Route path="/knowledge" element={<Knowledge token={sessions.operator} connect={connect} preparingSamples={preparingPolicies} />} />
          <Route path="/playground" element={<Playground token={sessions.operator} connect={connect} />} />
          <Route path="/showcase/rag" element={<RagShowcase token={sessions.operator} connect={connect} />} />
          <Route path="/contractors" element={<Contractors token={sessions.operator} connect={connect} />} />
          <Route path="/evaluations" element={<Evaluations />} />
          <Route path="/customer" element={<Customer token={sessions.claimant} connect={connect} guided={demoSession && params.has('experience')} preparingSample={preparingSample} />} />
          <Route path="/customer/claims" element={<Customer token={sessions.claimant} connect={connect} guided={demoSession && params.has('experience')} list preparingSample={preparingSample} />} />
          <Route path="*" element={<><h1>Page not found</h1><Link className="inline-link" to="/">Return home</Link></>} />
        </Routes>}
      </main>
      </Walkthrough>
    </div>
    {demoAvailability.isSuccess && !hosted && dialog && <SessionDialog role={dialog} connected={!!sessions[dialog]} close={() => setDialog(null)} update={update} />}
  </div>;
}

function SessionDialog({ role, connected, close, update }: { role: Role; connected: boolean; close: () => void; update: (role: Role, token: string) => void }) {
  const [token, setToken] = useState('');
  const submit = (event: FormEvent) => { event.preventDefault(); if (token.trim()) update(role, token.trim()); };
  return <div className="dialog-fade" onClick={close}>
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="session-heading" onClick={event => event.stopPropagation()}
      onKeyDown={event => { if (event.key === 'Escape') close(); }}>
      <div className="row spread"><h2 id="session-heading">Connect {role} session</h2><span className="icon-tip" data-tooltip="Close session dialog"><Button aria-label="Close" size="icon" variant="ghost" onClick={close}><X /></Button></span></div>
      <p className="muted">Enter the {role} bearer token configured on your local backend. The session is stored in this browser tab.</p>
      <form className="stack" onSubmit={submit}><label>Bearer token<input type="password" autoFocus autoComplete="off" value={token}
        onChange={event => setToken(event.target.value)} /></label>
        <div className="row spread">{connected ? <Button variant="outline" type="button" onClick={() => update(role, '')}>Disconnect</Button> : <span />}
          <Button disabled={!token.trim()}>Connect</Button></div>
      </form>
    </section>
  </div>;
}

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>,
);
