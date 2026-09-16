import { useState } from 'react';

type Feature = { id: string; type: string; view: string; count: number | null; box: number[]; description: string };
export type Finding = { code: string; subject: string; status: string; detail: string };
export type RevisionBResult = {
  id: string;
  inventory?: { features: Feature[] } | null;
  findings?: Finding[];
  context?: { status: string; detail: string };
};

export default function RevisionBReview({ result }: { result: RevisionBResult }) {
  const features = result.inventory?.features ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const feature = features.find(f => f.id === selected);
  const findings = result.findings ?? [];
  const ordered = [...findings].sort((a,b) => Number(b.status === 'FAIL') - Number(a.status === 'FAIL'));
  const visible = showAll ? ordered : ordered.slice(0, 8);
  return <section className="revision-review" aria-label="Revision B completeness review">
    <div className="revision-heading"><div><span className="eyebrow">REVISION B · EVIDENCE REVIEW</span><h2>Every feature stays on the checklist.</h2></div><span className="review-status">Review required</span></div>
    <p>{result.context?.status === 'FAIL' ? result.context.detail : 'The independent inventory and source readings are proposals. The draft remains available while you resolve these findings. Manufacturing release requires validated associations and the paired-data accuracy gate.'}</p>
    <div className="review-summary"><span><strong>{features.length}</strong> feature observations</span><span><strong>{findings.length}</strong> open findings</span><span>Manufacturing release blocked</span></div>
    <p className="field-help">Counts are shown per view. The same physical feature may appear in more than one view; these observations must not be added together.</p>
    {features.length > 0 ? <>
      <div className="review-source"><img src={`/api/drawings/${result.id}/files/drawing`} alt="Drawing with the selected feature highlighted" /><svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">{feature && <rect x={feature.box[1]} y={feature.box[0]} width={feature.box[3]-feature.box[1]} height={feature.box[2]-feature.box[0]} />}</svg></div>
      {feature && <p className="selected-feature"><strong>{feature.type.replaceAll('_',' ')} · {feature.view}</strong><br />{feature.description}</p>}
      <div className="feature-list">{features.map(f => <button key={f.id} className={selected === f.id ? 'selected' : ''} onClick={() => setSelected(f.id)} aria-pressed={selected === f.id}><span>{f.type.replaceAll('_',' ')}<small>{f.view}</small></span><strong>{f.count ?? '?'}</strong><span>{f.description}</span></button>)}</div>
    </> : <p className="coverage-warning">Visual inventory unavailable or empty. Completeness cannot be established.</p>}
    <h3>Unresolved requirements</h3><ol className="finding-list">{visible.map((f,i) => <li key={`${f.code}-${f.subject}-${i}`}><span className={f.status === 'FAIL' ? 'finding-fail' : 'finding-unknown'}>{f.status}</span><div><strong>{f.subject} · {f.code.replaceAll('_',' ').toLowerCase()}</strong><p>{f.detail}</p></div></li>)}</ol>
    {ordered.length > 8 && <button className="review-expand" onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer findings' : `Show all ${ordered.length} findings`}</button>}
    <div className="audit-downloads"><a href={`/api/drawings/${result.id}/files/audit`} target="_blank" rel="noreferrer">Annotated report</a><a href={`/api/drawings/${result.id}/files/ledger`}>Requirement ledger</a><a href={`/api/drawings/${result.id}/files/report`}>Audit JSON</a></div>
  </section>;
}
