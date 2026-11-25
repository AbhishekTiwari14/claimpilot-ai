import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Activity, BookOpen, ChevronDown, ChevronRight, ClipboardList, FlaskConical, LayoutDashboard, LogIn, Menu, MessageSquare, Shield, Truck, X } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import Customer from './customer';
import { Knowledge, Playground } from './knowledge';
import { Operations, OperatorClaim } from './operations';
import { Contractors, Evaluations } from './resources';
import { Button } from './components/ui/button';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 15000, refetchOnWindowFocus: true }, mutations: { retry: false } },
});
type Role = 'operator' | 'claimant';
const navigation = [
  { to: '/', name: 'Operations overview', icon: LayoutDashboard },
  { to: '/knowledge', name: 'Knowledge base', icon: BookOpen },
  { to: '/playground', name: 'RAG playground', icon: FlaskConical },
  { to: '/contractors', name: 'Contractors', icon: Truck },
  { to: '/evaluations', name: 'Evaluations', icon: Activity },
];
const tokenKey = (role: Role) => `claimpilot.session.${role}`;

function App() {
  const location = useLocation();
  const [sessions, setSessions] = useState<Record<Role, string>>(() => ({
    operator: sessionStorage.getItem(tokenKey('operator')) || '', claimant: sessionStorage.getItem(tokenKey('claimant')) || '',
  }));
  const [dialog, setDialog] = useState<Role | null>(null);
  const [menu, setMenu] = useState(false);
  const [revision, setRevision] = useState(0);
  const role = location.pathname.startsWith('/customer') ? 'claimant' : 'operator';
  const heading = role === 'claimant' ? 'Customer portal' : location.pathname.startsWith('/claims/') ? 'Claim detail' :
    navigation.find(item => item.to === location.pathname)?.name || 'Workspace';
  const connect = () => setDialog(role);
  const update = (selected: Role, token: string) => {
    if (token) sessionStorage.setItem(tokenKey(selected), token);
    else sessionStorage.removeItem(tokenKey(selected));
    queryClient.clear();
    setSessions(prev => ({ ...prev, [selected]: token }));
    setRevision(prev => prev + 1);
    setDialog(null);
  };
  return <div className="app">
    {menu && <button className="mobile-overlay" aria-label="Close navigation" onClick={() => setMenu(false)} />}
    <aside className={`sidebar ${menu ? 'open' : ''}`}>
      <Link to="/" className="brand" onClick={() => setMenu(false)}><span className="brand-icon"><Shield size={21} /><i /></span>
        <span>ClaimPilot <b>AI</b><small>INSURANCE OPERATIONS</small></span></Link>
      <div className="sidebar-workspace"><span className="workspace-icon"><ClipboardList size={17} /></span><div><strong>Claims workspace</strong><small>Property operations</small></div></div>
      <nav aria-label="Workspace"><div className="nav-title">WORKSPACE</div>
        {navigation.map(({ to, name, icon: Icon }) => <NavLink to={to} end={to === '/'} className={({ isActive }) =>
          `nav-item ${isActive || (to === '/' && location.pathname.startsWith('/claims/')) ? 'active' : ''}`}
          key={to} onClick={() => setMenu(false)}><Icon size={18} />{name}</NavLink>)}
        <div className="nav-title portal-nav">PORTAL</div>
        <NavLink to="/customer" className="nav-item" onClick={() => setMenu(false)}><MessageSquare size={18} /> Customer portal</NavLink>
      </nav>
      <div className="sidebar-footer"><span className="footer-dot" /> <div><strong>Local workspace</strong><small>Simulated contractor dispatch</small></div></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="row"><Button className="menu-toggle" variant="ghost" size="icon" onClick={() => setMenu(!menu)}
          aria-label={menu ? 'Close navigation' : 'Open navigation'} aria-expanded={menu}>{menu ? <X /> : <Menu />}</Button>
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={13} /><strong>{heading}</strong></div></div>
        <button className="session-button" onClick={connect}><span className="session-avatar">{sessions[role] ? (role === 'operator' ? 'OP' : 'CL') : <LogIn size={15} />}</span>
          <span>{sessions[role] ? (role === 'operator' ? 'Operator session' : 'Claimant session') : 'Connect session'}</span><ChevronDown size={15} /></button>
      </header>
      <main className="main-content" key={revision}>
        <Routes>
          <Route path="/" element={<Operations token={sessions.operator} connect={connect} />} />
          <Route path="/claims/:claimID" element={<OperatorClaim token={sessions.operator} connect={connect} />} />
          <Route path="/knowledge" element={<Knowledge token={sessions.operator} connect={connect} />} />
          <Route path="/playground" element={<Playground token={sessions.operator} connect={connect} />} />
          <Route path="/contractors" element={<Contractors token={sessions.operator} connect={connect} />} />
          <Route path="/evaluations" element={<Evaluations />} />
          <Route path="/customer" element={<Customer token={sessions.claimant} connect={connect} />} />
          <Route path="*" element={<><h1>Page not found</h1><Link className="inline-link" to="/">Return to operations</Link></>} />
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
