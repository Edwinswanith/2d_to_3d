import { useState } from 'react';

type ModelJob = {
  id: string; model_version?: number; sections_available?: boolean; model_available: boolean; message: string; completion?: string;
  model_features?: {id: string; kind: string; status: string; detail?: string}[];
  assumptions?: {id: string; item: string; value: string; unit: string; reason: string}[];
};

export default function ModelReview({job, onBuild, onSelectFeature}: {job: ModelJob; onBuild: () => void; onSelectFeature: (id: string | null) => void}) {
  const [editing, setEditing] = useState(false);
  const [spec, setSpec] = useState('');
  const [reason, setReason] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  async function build(correct = false) {
    setPending(true); setError('');
    try {
      const response = await fetch(`/api/drawings/${job.id}/build`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_version:job.model_version ?? 0, ...(correct ? {spec:JSON.parse(spec), reason, reviewer} : {})})});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check the specification format and required fields.');
      setEditing(false); onBuild();
    } catch(e) {setError((e as Error).message);}
    finally {setPending(false);}
  }
  async function edit() {
    try {
      const response = await fetch(`/api/drawings/${job.id}/files/spec`);
      if (!response.ok) throw new Error('Build a draft first to create an editable specification.');
      setSpec(JSON.stringify(await response.json(), null, 2)); setEditing(true);
    } catch(e) {setError((e as Error).message);}
  }
  return <section className="revision-review" aria-label="Model construction and corrections">
    <div className="revision-heading"><div><span className="eyebrow">FEATURE CONSTRUCTION · VERSION {job.model_version ?? 0}</span><h2>{job.completion === 'PARTIAL_DRAFT_REQUIRES_REVIEW' ? 'Partial draft: some features need correction.' : job.model_available ? 'Your draft is ready to inspect.' : 'Build from the drawing evidence.'}</h2></div><span className="review-status">Not released</span></div>
    <p>{job.message}</p>
    <div className="audit-downloads"><button disabled={pending} onClick={() => build()}>{job.model_available ? 'Generate another draft' : 'Build 3D draft'}</button>{job.model_available && <><button onClick={edit}>Correct specification</button><a href={`/api/drawings/${job.id}/files/spec`}>Specification</a><a href={`/api/drawings/${job.id}/files/checks`}>Geometry checks</a><a href={`/api/drawings/${job.id}/files/model-manifest`}>Version manifest</a></>}</div>
    {error && <p className="error-message" role="alert">{error}</p>}
    {job.model_features && <><h3>Built features and omissions</h3><ul className="model-features">{job.model_features.map(f => <li key={f.id}><button onClick={() => onSelectFeature(f.id)} title="Highlight this feature in the 3D model">{f.id} · {f.kind.replaceAll('_', ' ')}</button><span className={f.status === 'FAILED' ? 'finding-fail' : 'finding-unknown'}>{f.status.replaceAll('_', ' ')}</span>{f.detail && <p>{f.detail}</p>}</li>)}</ul></>}
    {job.sections_available && <details><summary>Compare reimported STEP sections with the drawing</summary><p>These are measured sections through the model axes. The physical drawing plane and its topology still require review.</p><div className="section-previews">{['XZ','YZ'].map(plane => <figure key={plane}><img src={`/api/drawings/${job.id}/files/section-${plane}`} alt={`${plane} section from the reimported STEP`} /><figcaption>{plane} · Model section</figcaption></figure>)}</div></details>}
    {!!job.assumptions?.length && <details><summary>{job.assumptions.length} registered assumptions need engineer review</summary><ol className="finding-list">{job.assumptions.map(a => <li key={a.id}><div><strong>{a.item}: {a.value} {a.unit}</strong><p>{a.reason}</p></div></li>)}</ol></details>}
    {editing && <div className="spec-editor"><h3>Correct the feature specification</h3><p>Save creates a new version and reruns construction and measurements. Numbers must cite the ledger or a registered assumption.</p><label>Your name<input value={reviewer} onChange={e => setReviewer(e.target.value)} /></label><label>Reason for correction<input value={reason} onChange={e => setReason(e.target.value)} /></label><label>Specification JSON<textarea spellCheck={false} value={spec} onChange={e => setSpec(e.target.value)} /></label><button disabled={pending || !reviewer || reason.length < 3} onClick={() => build(true)}>Save correction and rebuild</button><button onClick={() => setEditing(false)}>Cancel</button></div>}
  </section>;
}
