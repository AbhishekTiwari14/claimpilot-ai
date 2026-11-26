import { ArrowRight, BookOpen, Camera, ClipboardCheck, GitBranch, House, MessageSquare, ShieldCheck, Truck } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from './components/ui/button';
import { Failure } from './shared';

export const scenarios = [
  { id: 'dishwasher', title: 'Dishwasher leak', detail: 'Follow a Seattle claim from report to simulated dispatch.', zip: '98101', policy: 'SYN-123' },
  { id: 'exclusion', title: 'Policy exclusion', detail: 'See how an exclusion sends a claim to human review.', zip: '98101', policy: 'SYN-456' },
  { id: 'unavailable', title: 'No contractor available', detail: 'See what happens when no contractor serves the property ZIP.', zip: '99501', policy: 'SYN-123' },
] as const;
export const scenarioFor = (id: string | null) => scenarios.find(scenario => scenario.id === id) ?? scenarios[0];
const experiences = [
  { id: 'chat', title: 'Conversational AI', description: 'Start with a short report and watch the assistant ask for what is missing.', technical: 'Multi-turn extraction · validation', icon: MessageSquare },
  { id: 'vision', title: 'Multimodal Damage Analysis', description: 'Add a photo to see what is visible, what the model interprets, and where it is uncertain.', technical: 'Vision · structured observations', icon: Camera },
  { id: 'rag', title: 'Custom Knowledge Base & RAG', description: 'Import a policy, ask it a question, and see the exact text behind the answer.', technical: 'Embeddings · retrieval · citations', icon: BookOpen },
  { id: 'workflow', title: 'Agentic Workflow', description: 'Inspect the tools that check policy and contractors. Approve a proposal or try reassignment.', technical: 'LangGraph · tool calls · checkpoints', icon: GitBranch },
] as const;
export type Experience = 'full' | typeof experiences[number]['id'];
export const experienceFor = (id: string | null): Experience => experiences.find(item => item.id === id)?.id ?? 'full';
export const sampleFile = (name: string) => `${import.meta.env.BASE_URL}samples/${name}`;
export const photoAttribution = 'https://commons.wikimedia.org/wiki/File:FEMA_-_17647_-_Photograph_by_Patsy_Lynch_taken_on_10-18-2005_in_Louisiana.jpg';

export function sampleMessages(scenario: ReturnType<typeof scenarioFor>) {
  return [
    'There is water damage in the kitchen.',
    `The damaged property is 12 Example Lane, ZIP ${scenario.zip}.`,
    'I noticed it yesterday morning.',
    `My policy ID is ${scenario.policy}.`,
    'There are no ongoing hazards. I think the dishwasher leaked.',
  ];
}

export function Landing({ enabled, hosted, loading, phase, error, launch }: {
  enabled: boolean; hosted: boolean; loading: boolean; phase: string; error: unknown; launch: (scenario: string, experience?: Experience) => void;
}) {
  const [selected, setSelected] = useState<string>(scenarios[0].id);
  return <div className="landing">
    <header className="landing-header"><Link to="/" className="landing-brand"><span className="brand-icon"><ShieldCheck size={22} /></span> ClaimPilot AI</Link>
      <nav aria-label="Portal entry points"><Link to="/customer">Customer Portal</Link><Link to="/operator">Claims Operator <ArrowRight size={15} /></Link></nav>
    </header>
    <main className="landing-body">
      <section className="landing-hero">
        <div className="stack">
          <p className="eyebrow"><span /> AI FOR PROPERTY CLAIMS</p>
          <h1>From property damage to a clear next step.</h1>
          <p className="hero-copy">Report damage in your own words. ClaimPilot gathers the facts, checks the policy, and suggests a contractor. A person reviews the recommendation before any simulated dispatch.</p>
          <div className="row wrap"><Button size="default" disabled={!enabled || !!phase} onClick={() => launch(selected)}>
            {phase || 'Try Interactive Demo'}<ArrowRight /></Button><small>About 2–3 minutes · Sample information only</small></div>
          {!enabled && !loading && !error && <p className="muted">Interactive demo access is disabled here. Use a portal to sign in manually.</p>}
          {hosted && <p className="muted">Use synthetic information only. Demo workspaces expire, and AI requests are processed by Groq.</p>}
          <Failure error={error} />
        </div>
        <aside className="landing-preview">
          <img src={sampleFile('fema-wall.jpg')} alt="Sample interior wall with visible water damage" />
          <div><span className="eyebrow">THE WALKTHROUGH</span><h2>Follow a claim through both portals.</h2>
            <ul><li><House /> Report the damage</li><li><ClipboardCheck /> Review the policy evidence</li><li><Truck /> Approve a simulated dispatch</li></ul>
          </div>
          <a className="photo-credit" href={photoAttribution}>Sample photograph · Patsy Lynch / FEMA</a>
        </aside>
      </section>
      <section className="capabilities" aria-labelledby="capability-title">
        <h2 id="capability-title">Explore a capability</h2>
        <div className="capability-grid">{experiences.map(({ id, title, description, technical, icon: Icon }) =>
          <article className="panel capability" key={id}>
            <span className="metric-icon teal"><Icon size={18} /></span>
            <div><h3>{title}</h3><p>{description}</p></div>
            <small>{technical}</small>
            <Button variant="outline" disabled={!enabled || !!phase} onClick={() => launch(selected, id)}>Try {title}<ArrowRight /></Button>
          </article>)}</div>
      </section>
      <section className="scenario-section" aria-labelledby="scenario-title">
        <h2 id="scenario-title">Choose a sample claim</h2>
        <div className="scenario-grid">{scenarios.map(scenario => <label className="scenario-card" key={scenario.id}>
          <input type="radio" name="scenario" value={scenario.id} checked={selected === scenario.id}
            disabled={!!phase} onChange={() => setSelected(scenario.id)} /><span><strong>{scenario.title}</strong><small>{scenario.detail}</small></span>
        </label>)}</div>
      </section>
    </main>
  </div>;
}
