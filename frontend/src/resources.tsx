import { useQuery } from '@tanstack/react-query';
import { Activity, Search, Truck } from 'lucide-react';
import { useState } from 'react';
import { client, type Contractor } from './api';
import { Badge, Empty, Failure, Loading, Panel, label } from './shared';
import { Button } from './components/ui/button';
import snapshot from './benchmark.json';

export function Contractors({ token, connect }: { token: string; connect: () => void }) {
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const records = useQuery({ queryKey: ['contractors'], queryFn: () => client(token).get<Contractor[]>('/contractors'), enabled: !!token });
  const contractors = records.data?.filter(c => [c.id, c.name, ...c.specializations.map(label), ...c.zip_codes].some(text => text.toLowerCase().includes(search.toLowerCase())));
  const detail = records.data?.find(c => c.id === selected);
  return <div className="stack">
    <div className="page-heading"><div><h1>Contractors</h1><p>Review the workspace’s synthetic contractor records and eligibility inputs.</p></div></div>
    <Panel title="Contractor directory" action={<Truck className="panel-icon" size={20} />}>
      {!token ? <Empty icon={<Truck />} title="Connect your operator session"><Button onClick={connect}>Connect session</Button></Empty> : <>
        <label className="search"><Search size={16} /><input aria-label="Filter contractors" placeholder="Search by name, trade or ZIP" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <Failure error={records.error} />
        {records.isPending ? <Loading /> : contractors?.length ? <div className="scroll-table">
          <table><thead><tr><th>Name</th><th>Trades</th><th>Status</th><th>Capacity</th><th>Rating</th><th>ETA</th></tr></thead>
            <tbody>{contractors.map(c => <tr key={c.id}><td><button type="button" className="table-link" onClick={() => setSelected(c.id)}>{c.name}</button>
              <small className="block mono">{c.id}</small></td><td>{c.specializations.map(label).join(', ')}</td>
              <td><Badge value={!c.active ? 'inactive' : c.available ? 'available' : 'not available'} /></td>
              <td>{c.capacity}</td><td>{c.rating.toFixed(1)}</td><td>{c.arrival_minutes} min</td></tr>)}</tbody></table>
        </div> : !records.error && <Empty icon={<Truck />} title="No matching contractors" />}
      </>}
    </Panel>
    {detail && <Panel title={detail.name} caption={detail.id} action={<Button variant="ghost" onClick={() => setSelected('')}>Close details</Button>}>
      <dl className="facts"><div><dt>Service areas</dt><dd>{detail.service_areas.join(', ')}</dd></div><div><dt>ZIP codes</dt><dd>{detail.zip_codes.join(', ')}</dd></div>
        <div><dt>Operating hours</dt><dd>{detail.opens_utc}:00–{detail.closes_utc}:00 UTC</dd></div>
        <div><dt>Credentials</dt><dd>{detail.credentialed ? 'Credentialed' : 'Uncredentialed'} · {detail.insured ? 'Insured' : 'Uninsured'}</dd></div>
        <div><dt>Emergency capability</dt><dd>{detail.emergency_capable ? 'Supported' : 'Unavailable'}</dd></div></dl>
    </Panel>}
  </div>;
}

export function Evaluations() {
  return <div className="stack">
    <div className="page-heading"><div><h1>Evaluations</h1><p>Measured benchmark results from the ClaimPilot evaluation runner.</p></div></div>
    <Panel title="Generated benchmark snapshot" caption={`Captured ${snapshot.captured_on}. Static results from synthetic fixtures and deterministic tests.`}
      action={<Activity className="panel-icon" size={20} />}>
      <div className="scroll-table"><table><thead><tr><th>Metric</th><th>Evaluation mode</th><th>Result</th><th>Sample size</th></tr></thead>
        <tbody>{snapshot.results.map(row => <tr key={row.metric}><td><strong>{row.metric}</strong></td><td>{row.mode}</td>
          <td>{row.passed}</td><td>{row.total}</td></tr>)}</tbody></table></div>
      <p className="muted">Citation validity checks that returned excerpts match the retrieved source chunks.</p>
    </Panel>
  </div>;
}
