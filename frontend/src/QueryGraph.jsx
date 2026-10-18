import { useState } from "react";
import { Network } from "lucide-react";

export default function QueryGraph({ result }) {
  const [selected, setSelected] = useState("");
  const drugs = result.ingredients.map(d => ({id: String(d.rxcui), label: d.name}));
  const height = Math.max(310, drugs.length * 100 + 40);
  const positions = new Map(drugs.map((n, i) => [n.id, {x: i % 2 ? 280 : 100, y: 65 + i * 100}]));
  const colors = {BLOCK: '#c84b5c', FLAG: '#bd842f', NOTE: '#7554cb'};
  const toggle = id => setSelected(selected === id ? '' : id);
  const shownPairs = selected ? result.pairs.filter(p => p.rxcuis.map(String).includes(selected)) : [];
  return <><div className="graph-title"><h3>Interaction map</h3><Network size={20}/></div><p className="muted small">{drugs.length} ingredients. {result.pairs.length} pairs checked. Select a medicine to explore its connections.</p><div className="graph-scroll"><svg className="query-graph" viewBox={`0 0 380 ${height}`} role="img" aria-label="Graph of analyzed medicines and returned interaction evidence">
    {result.pairs.map((pair, i) => {const a = positions.get(String(pair.rxcuis[0])), b = positions.get(String(pair.rxcuis[1])); if (!a || !b) return null; const active = !selected || pair.rxcuis.map(String).includes(selected); const color = colors[pair.verdict] || '#c7c9d8'; const x = a.x === b.x ? a.x + 45 : (a.x+b.x)/2; const y = (a.y+b.y)/2; return <g key={pair.pair_id} opacity={active ? 1 : .12}><title>{pair.drugs.join(' + ')}: {pair.summary}</title><path d={`M ${a.x} ${a.y} Q ${x} ${y} ${b.x} ${b.y}`} fill="none" stroke={color} strokeWidth="1.7" strokeDasharray={colors[pair.verdict] ? undefined : '4 4'}/>{pair.paths.length > 0 && <><circle cx={a.x === b.x ? a.x + 22.5 : x} cy={y} r="11" fill="white" stroke={color}/><text x={a.x === b.x ? a.x + 22.5 : x} y={y+3} textAnchor="middle" fontSize="8" fill={color}>{String(i+1).padStart(2,'0')}</text></>}</g>;})}
    {drugs.map(n => {const p = positions.get(n.id); return <g key={n.id} role="button" tabIndex="0" aria-label={`Highlight ${n.label}`} aria-pressed={selected === n.id} onClick={() => toggle(n.id)} onKeyDown={e => {if(e.key === "Enter" || e.key === " ") {e.preventDefault(); toggle(n.id);}}}><title>{n.label}</title><rect x={p.x-76} y={p.y-30} width="152" height="60" rx="10" fill={selected === n.id ? 'var(--accent)' : 'white'} stroke={selected === n.id ? 'var(--accent)' : 'var(--border)'}/><text x={p.x-62} y={p.y-2} fontWeight="500" fontSize="12" fill={selected === n.id ? 'white' : 'var(--text)'}>{n.label.length > 19 ? `${n.label.slice(0,17)}…` : n.label}</text><text x={p.x-62} y={p.y+16} fontSize="9" fill={selected === n.id ? '#e3d9fa' : 'var(--muted)'}>RxNorm {n.id}</text></g>;})}
  </svg></div><ul className="graph-legend"><li><span className="legend-major"/>Major finding</li><li><span className="legend-moderate"/>Moderate finding</li><li><span className="legend-mechanism"/>Mechanism / minor finding</li><li><span className="legend-unknown"/>Unknown severity / no evidence · not a safety finding</li></ul>
  {shownPairs.map(pair => <details key={pair.pair_id}><summary>{pair.drugs.join(' + ')}</summary><p className="muted small">{pair.summary}</p>{pair.paths.filter(e => e.target).map(e => <p className="mechanism-line" key={e.evidence_id}>{e.actor_drug} → {e.relationship} → {e.target} ← SUBSTRATE_OF ← {e.substrate_drug}</p>)}</details>)}
  <p className="muted small">Connections show returned pair evidence, not your personal likelihood of harm. {result.evidence.length} source records.</p></>;
}
