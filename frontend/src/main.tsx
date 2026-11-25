import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { Activity, BookOpen, ChevronDown, ChevronRight, ClipboardList, FlaskConical, LayoutDashboard, LogIn, Menu, MessageSquare, RotateCcw, Shield, Truck, X } from 'lucide-react';
import { useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { client, type Turn } from './api';
import { experienceFor, Landing, sampleMessages, scenarioFor, type Experience } from './demo';
import Customer from './customer';
import { Knowledge, Playground, seedDemoDocuments } from './knowledge';
import { RagShowcase } from './rag-showcase';
import { Operations, OperatorClaim } from './operations';
import { Contractors, Evaluations } from './resources';
import { Button } from './components/ui/button';
import { Failure, Notice, providerName } from './shared';
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

function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<Record<Role, string>>(() => ({
    operator: sessionStorage.getItem(tokenKey('operator')) || '', claimant: sessionStorage.getItem(tokenKey('claimant')) || '',
  }));
  const [dialog, setDialog] = useState<Role | null>(null);
  const [menu, setMenu] = useState(false);
  const [revision, setRevision] = useState(0);
  const [demoSession, setDemoSession] = useState(() => sessionStorage.getItem('claimpilot.demo') === 'true');
  const [phase, setPhase] = useState('');
  const [demoError, setDemoError] = useState<unknown>(null);
  const launchKey = useRef<{ target: string; key: string } | null>(null);
  const demoAvailability = useQuery({ queryKey: ['demo-availability'], enabled: location.pathname === '/' || demoSession,
    queryFn: async () => {
      try { return await client('').get<{ enabled: boolean; provider?: 'groq' | 'offline' }>('/demo/status'); }
      catch { return { enabled: false, provider: undefined }; }
    },
  });
  const role = location.pathname.startsWith('/customer') ? 'claimant' : 'operator';
  const items = role === 'claimant' ? customerNav : navigation;
  const heading = role === 'claimant' ? items.find(item => item.to === location.pathname)?.name ?? 'Customer portal' : location.pathname.startsWith('/claims/') ? 'Claim detail' :
    navigation.find(item => item.to === location.pathname)?.name || 'Workspace';
  const connect = () => setDialog(role);
  const update = (selected: Role, token: string) => {
    if (demoSession) {
      for (const role of ['operator', 'claimant'] as const) sessionStorage.removeItem(tokenKey(role));
      sessionStorage.removeItem('claimpilot.demo');
      setSessions({ operator: '', claimant: '', [selected]: token });
      setDemoSession(false);
    } else setSessions(prev => ({ ...prev, [selected]: token }));
    if (token) sessionStorage.setItem(tokenKey(selected), token);
    else sessionStorage.removeItem(tokenKey(selected));
    queryClient.clear();
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
      if (experience === 'full' || experience === 'workflow') {
        setPhase('Indexing sample policies…');
        const docs = await seedDemoDocuments(session.operator, current.key);
        if (docs.some(doc => doc.status !== 'ready')) {
          launchKey.current = null;
          throw new Error('A sample document did not index. Try starting the demo again.');
        }
      }
      const search = new URLSearchParams({ scenario, experience });
      if (experience !== 'rag') {
        const claimant = client(session.claimant);
        let turn = await claimant.post<Turn>('/conversations', undefined, current.key);
        search.set('conversation', turn.claim.conversation_id);
        if (experience === 'workflow') {
          setPhase('Preparing a sample claim for your confirmation…');
          for (const [index, message] of sampleMessages(scenarioFor(scenario)).entries()) {
            turn = await claimant.post<Turn>(`/conversations/${turn.claim.conversation_id}/messages`,
              { message, expected_version: turn.claim.version }, `${current.key}-${index}`);
          }
        }
      }
      queryClient.clear();
      for (const role of ['operator', 'claimant'] as const) sessionStorage.setItem(tokenKey(role), session[role]);
      sessionStorage.setItem('claimpilot.demo', 'true');
      setSessions(session);
      setDemoSession(true);
      setRevision(value => value + 1);
      launchKey.current = null;
      navigate(`${experience === 'rag' ? '/showcase/rag' : '/customer'}?${search}`);
    } catch (error) { setDemoError(error); }
    finally { setPhase(''); }
  };
  if (location.pathname === '/') return <Landing enabled={demoAvailability.data?.enabled === true}
    loading={demoAvailability.isPending} phase={phase} error={demoError} launch={(id, experience) => void launch(id, experience)} />;
  const params = new URLSearchParams(location.search);
  return <div className="app">
    {menu && <button className="mobile-overlay" aria-label="Close navigation" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? 'open' : ''}`}>
      <Link to="/" className="brand" onClick={() => setMenu(false)}><span className="brand-icon"><Shield size={21} /><i /></span>
        <span>ClaimPilot <b>AI</b><small>INSURANCE OPERATIONS</small></span></Link>
      <div className="sidebar-workspace"><span className="workspace-icon"><ClipboardList size={17} /></span><div><strong>{role === 'claimant' ? 'Customer portal' : 'Claims operator'}</strong><small>{demoSession ? 'Interactive demo' : 'Claims workspace'}</small></div></div>
      <nav aria-label={role === 'claimant' ? 'Customer navigation' : 'Operator navigation'}><div className="nav-title">WORKSPACE</div>
        {items.map(({ to, name, icon: Icon }) => <NavLink to={to} end className={({ isActive }) =>
          `nav-item ${isActive || (to === '/operator' && location.pathname.startsWith('/claims/')) ? 'active' : ''}`}
          key={to} onClick={() => setMenu(false)}><Icon size={18} />{name}</NavLink>)}
        <Link to="/" className="nav-item" onClick={() => setMenu(false)}><Shield size={18} /> Back to home</Link>
      </nav>
      <div className="sidebar-footer"><span className="footer-dot" /> <div><strong>Local workspace</strong><small>Simulated contractor dispatch</small></div></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="row"><Button className="menu-toggle" variant="ghost" size="icon" onClick={() => setMenu(!menu)}
          aria-label={menu ? 'Close navigation' : 'Open navigation'} aria-expanded={menu}>{menu ? <X /> : <Menu />}</Button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={13} /><strong>{heading}</strong></div></div>
        <div className="row wrap">{demoSession && demoAvailability.data?.enabled &&
          <Button variant="ghost" size="sm" disabled={!!phase} onClick={() => void launch(scenarioFor(params.get('scenario')).id, experienceFor(params.get('experience')))}>
            <RotateCcw /> Restart demo</Button>}
          <button className="session-button" onClick={connect}><span className="session-avatar">{sessions[role] ? (role === 'operator' ? 'OP' : 'CL') : <LogIn size={15} />}</span>
            <span>{sessions[role] ? (role === 'operator' ? 'Operator session' : 'Claimant session') : 'Connect session'}</span><ChevronDown size={15} /></button></div>
      </header>
      <main className="main-content" key={revision}>
        {phase && <Notice>{phase}</Notice>}<Failure error={demoError} />
        {demoSession && demoAvailability.data?.provider &&
          <p className="demo-mode">{providerName(demoAvailability.data.provider)} · Isolated demo workspace</p>}
        <Routes>
          <Route path="/operator" element={<Operations token={sessions.operator} connect={connect} />} />
          <Route path="/claims/:claimID" element={<OperatorClaim token={sessions.operator} connect={connect} guided={demoSession} />} />
          <Route path="/knowledge" element={<Knowledge token={sessions.operator} connect={connect} />} />
          <Route path="/playground" element={<Playground token={sessions.operator} connect={connect} />} />
          <Route path="/showcase/rag" element={<RagShowcase token={sessions.operator} connect={connect} />} />
          <Route path="/contractors" element={<Contractors token={sessions.operator} connect={connect} />} />
          <Route path="/evaluations" element={<Evaluations />} />
          <Route path="/customer" element={<Customer token={sessions.claimant} connect={connect} guided={demoSession} />} />
          <Route path="/customer/claims" element={<Customer token={sessions.claimant} connect={connect} guided={demoSession} list />} />
          <Route path="*" element={<><h1>Page not found</h1><Link className="inline-link" to="/">Return home</Link></>} />
        </Routes>
      </main>
    </div>
    {dialog && <SessionDialog role={dialog} connected={!!sessions[dialog]} close={() => setDialog(null)} update={update} />}
  </div>;
}

function SessionDialog({ role, connected, close, update }: { role: Role; connected: boolean; close: () => void; update: (role: Role, token: string) => void }) {
  const [token, setToken] = useState('');
  const submit = (event: FormEvent) => { event.preventDefault(); if (token.trim()) update(role, token.trim()); };
  return <div className="dialog-fade" onClick={close}>
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="session-heading" onClick={event => event.stopPropagation()}
      onKeyDown={event => { if (event.key === 'Escape') close(); }}>
      <div className="row spread"><h2 id="session-heading">Connect {role} session</h2><Button aria-label="Close" size="icon" variant="ghost" onClick={close}><X /></Button></div>
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
