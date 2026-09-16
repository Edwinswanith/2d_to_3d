import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowDownToLine, ArrowUpRight, Box, Check, ChevronDown, FileText, Layers3, LoaderCircle, MoveRight, Plus, RotateCw, Upload, X } from 'lucide-react';
import '@fontsource/dm-sans/400.css';
import '@fontsource/dm-sans/500.css';
import '@fontsource/dm-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import Viewer, { type MeshData } from './Viewer';
import GenerationProgress, { stageFor } from './GenerationProgress';
import './style.css';

type UnitChoice = 'auto' | 'mm' | 'cm' | 'in';
type Job = { id: string; filename: string; status: string; message: string; model_available: boolean; download_available: boolean; unit?: string; unit_source?: string; units_statement?: string; drawing_number?: string; part_name?: string; partial?: boolean; warnings?: string[]; unsupported_features?: string[]; requirements?: { id: string; raw_text: string; resolved_unit: string; verified: boolean }[]; measurements?: { bbox: number[]; volume: number }; verification?: { step_integrity: string } };
const terminal = new Set(['ready', 'review', 'failed']);

function App() {
  const [file, setFile] = useState<File | null>(null);
  const [units, setUnits] = useState<UnitChoice>('auto');
  const [rotation, setRotation] = useState(0);
  const [job, setJob] = useState<Job | null>(() => { const id = new URLSearchParams(location.search).get("drawing"); return id && /^[a-f0-9]{32}$/.test(id) ? { id, filename: "Saved drawing", status: "loading", message: "Loading saved drawing", model_available: false, download_available: false } : null; });
  const [mesh, setMesh] = useState<MeshData | null>(null);
  const [error, setError] = useState('');
  const [posting, setPosting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [details, setDetails] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const startedAt = useRef(Date.now());
  const busy = posting || (!!job && !terminal.has(job.status));
  const previewUrl = job && ['reading', 'checking', 'building', 'previewing', 'ready', 'review'].includes(job.status) ? `/api/drawings/${job.id}/files/drawing` : '';
  const progressStatus = posting ? 'uploading' : job?.status ?? 'uploading';

  function choose(next?: File) {
    if (!next || busy) return;
    if (!/\.(pdf|png|jpe?g)$/i.test(next.name)) { setError('Please choose a PDF, PNG or JPG drawing.'); return; }
    if (next.size > 20 * 1024 * 1024) { setError('The drawing must be smaller than 20 MB.'); return; }
    history.replaceState(null, "", location.pathname); generation.current++; setFile(next); setJob(null); setMesh(null); setError('');
  }

  useEffect(() => {
    if (!job || terminal.has(job.status)) return;
    const id = job.id;
    const version = generation.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      try {
        const response = await fetch(`/api/drawings/${id}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Could not read this drawing’s progress. Refresh and try again.');
        const next = await response.json() as Job;
        if (version !== generation.current || stopped) return;
        if (next.status === 'ready' && next.model_available) {
          const meshResponse = await fetch(`/api/drawings/${id}/files/mesh`, { signal: controller.signal });
          if (!meshResponse.ok) throw new Error('The model preview could not be loaded.');
          const geometry = await meshResponse.json() as MeshData;
          if (version !== generation.current || stopped) return;
          setMesh(geometry);
        }
        setJob(next);
        if (next.status === 'failed') setError(next.message);
        if (!terminal.has(next.status)) timer = setTimeout(poll, 1200);
      } catch (e) {
        if (!stopped && (e as Error).name !== 'AbortError') {
          setError((e as Error).message); setJob(prev => prev ? { ...prev, status: 'failed' } : prev);
        }
      }
    }
    timer = setTimeout(poll, 500);
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status]);

  async function generate() {
    if (!file || busy) return;
    generation.current++; startedAt.current = Date.now(); setPosting(true); setError(''); setMesh(null); setJob(null);
    const form = new FormData(); form.append('file', file); form.append('units', units); form.append('rotation', String(rotation));
    try {
      const response = await fetch('/api/drawings', { method: 'POST', body: form });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The drawing could not be uploaded.');
      history.replaceState(null, "", `?drawing=${result.id}`);
      setJob({ id: result.id, filename: file.name, status: 'queued', message: 'Your drawing is queued', model_available: false, download_available: false });
    } catch (e) { setError((e as Error).message); }
    finally { setPosting(false); }
  }

  const phases = [{ key: 'Upload', done: !!job }, { key: 'Read drawing', done: !!job && ['checking', 'building', 'previewing', 'ready', 'review'].includes(job.status) }, { key: 'Build & inspect', done: job?.status === 'ready' }, { key: 'Download', done: false }];
  return <div className="app-shell">
    <aside className="rail"><a className="logo-icon" href="/" aria-label="Formwerk home"><Layers3 size={23} /></a><div className="rail-line" /><button className="rail-active" aria-label="Drawing workspace" title="Drawing workspace"><Box size={21} /></button><span className="rail-caption">FW</span></aside>
    <div className="workspace">
      <header className="topbar"><a href="/" className="wordmark">formwerk<span className="brand-dot">.</span></a><div className="breadcrumb"><span>Workspace</span><MoveRight size={13} /><strong>Drawing to 3D</strong></div><span className="local-badge"><i className="live-dot" />LOCAL WORKSPACE</span></header>
      <main>
        <div className="page-heading"><div><p className="eyebrow">DRAWING TO 3D / 01</p><h1>Make room for another dimension.</h1><p>Turn a gland-ring drawing into a body preview you can inspect and keep.</p></div><span className="version-badge mono">BODY MODELING · V0.1</span></div>
        <div className="work-grid">
          <section className="input-panel" aria-label="Drawing upload and settings">
            <div className="panel-title"><span className="step-number mono">01</span><h2>Source drawing</h2><FileText size={17} /></div>
            <input ref={input} type="file" accept=".pdf,.png,.jpg,.jpeg" className="visually-hidden" aria-label="Upload drawing" onChange={e => choose(e.target.files?.[0])} disabled={busy} />
            <button className={`dropzone ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`} disabled={busy} onClick={() => input.current?.click()} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files[0]); }}>
              <div className="upload-icon">{file ? <FileText size={25} /> : <Upload size={25} />}</div>
              <strong>{file ? file.name : 'Drop your drawing here'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · Click to replace` : 'or browse files to get started'}</span><small className="mono">PDF, PNG, JPG · SINGLE SHEET · MAX 20 MB</small>
            </button>
            {previewUrl && <div className="drawing-thumbnail"><img src={previewUrl} alt="Uploaded drawing rendered in the selected orientation" /><span className="mono">SOURCE SHEET</span></div>}
            <div className="settings-head"><span className="eyebrow">MODEL SETTINGS</span><span className="mono">02</span></div>
            <label className="field-label" htmlFor="units">Measurement units</label>
            <div className="select-wrap"><select id="units" value={units} onChange={e => setUnits(e.target.value as UnitChoice)} disabled={busy}><option value="auto">Auto detect · default mm</option><option value="mm">Millimetres (mm)</option><option value="cm">Centimetres (cm)</option><option value="in">Inches (in)</option></select><ChevronDown size={15} /></div>
            <p className="field-help">No units on the sheet? We use mm. Explicit cm or inch units take precedence. Use the selector to correct a misleading title block.</p>
            <label className="field-label" htmlFor="rotation">Drawing orientation</label>
            <div className="orientation"><select id="rotation" value={rotation} onChange={e => setRotation(Number(e.target.value))} disabled={busy}><option value={0}>As uploaded</option><option value={90}>Rotate 90° clockwise</option><option value={180}>Rotate 180°</option><option value={270}>Rotate 90° counterclockwise</option></select><RotateCw size={16} /></div>
            <div className="units-note"><span className="mono">CAD OUTPUT</span><strong>Millimetres</strong><p>Geometry is normalised to mm for consistent viewing and export.</p></div>
            {error && <p className="error-message" role="alert"><X size={16} />{error}</p>}
            <button className="primary-button" onClick={generate} disabled={!file || busy}>{busy ? <><LoaderCircle size={17} className="spin" />{stageFor(progressStatus).title}…</> : <><span>{job ? 'Generate again' : 'Generate 3D preview'}</span><ArrowUpRight size={19} /></>}</button>
            <p className="cloud-note">Generating sends this drawing to Gemini for reading. The API key stays on the server.</p>
          </section>
          <section className="model-panel" aria-label="Model preview and downloads">
            <Viewer mesh={mesh} overlay={busy ? <GenerationProgress status={progressStatus} startedAt={startedAt.current} drawingUrl={job ? `/api/drawings/${job.id}/files/drawing` : undefined} /> : null} />
            <div className="model-caption"><div><span className="eyebrow">{mesh ? 'YOUR MODEL' : 'THE NEXT DIMENSION'}</span><h2>{mesh ? job?.part_name || 'Gland ring body' : 'A better perspective starts here.'}</h2><p>{mesh ? `${job?.drawing_number || file?.name} · Source units ${job?.unit?.toUpperCase()} · ${job?.unit_source}` : 'Orbit the solid, inspect its form, and export a STEP file for future CAD work.'}</p></div><div className="caption-icon"><Box size={24} /></div></div>
            {mesh && <div className="result-strip"><span><Check size={15} />STEP integrity passed</span><span className="mono">{job?.measurements?.bbox.map(x => x.toFixed(2)).join(' × ')} MM</span></div>}
            <div className="download-bar"><div><ArrowDownToLine size={19} /><div><strong>{mesh ? 'Keep your body draft' : 'Your design, ready to keep'}</strong><p>{mesh ? 'Partial body preview · drawing conformance unverified' : 'STEP for CAD · STL for mesh workflows'}</p></div></div><div className="download-actions">{job?.download_available ? <><a className="download-step" href={`/api/drawings/${job.id}/files/step`} download>STEP <ArrowDownToLine size={15} /></a><a className="download-secondary" href={`/api/drawings/${job.id}/files/stl`} download>STL</a></> : <><button className="download-step" disabled>STEP <ArrowDownToLine size={15} /></button><button className="download-secondary" disabled>STL</button></>}</div></div>
            {job && terminal.has(job.status) && <div className="review-card"><button onClick={() => setDetails(!details)} aria-expanded={details}><span><FileText size={17} />{job.status === 'review' ? 'Profile needs review' : 'Drawing notes & feature coverage'}</span><Plus size={17} className={details ? 'rotate-plus' : ''} /></button><p>{mesh ? "Only the axial body is built. Holes, slots, pins and ports need further modeling and review." : job.status === "review" ? job.message : "No model was generated. Review the reading notes and profile before creating geometry."}</p>{details && <div className="review-details">{job.unsupported_features?.length ? <><strong>Features outside this preview</strong><ul>{job.unsupported_features.map((text, i) => <li key={i}>{text}</li>)}</ul></> : null}{job.warnings?.length ? <><strong>Reading notes</strong><ul>{job.warnings.map((text, i) => <li key={i}>{text}</li>)}</ul></> : null}<strong>Proposed callouts · {job.requirements?.length || 0}</strong><div className="ledger-table">{job.requirements?.map(r => <div key={r.id}><span className="mono">{r.id}</span><span>{r.raw_text}</span><small>{r.resolved_unit}</small></div>)}</div></div>}</div>}
          </section>
        </div>
        <div className="flow-footer">{phases.map((phase, i) => <React.Fragment key={phase.key}>{i > 0 && <span className="flow-connector" />}<div className={phase.done ? 'flow-step complete' : 'flow-step'}><span className="mono">{phase.done ? <Check size={12} /> : `0${i + 1}`}</span>{phase.key}</div></React.Fragment>)}<span className="flow-caption mono">GLAND RINGS FIRST.</span></div>
        <footer><span>Designed for the work between the drawing and the machine.</span><span className="mono">INSPECT BEFORE MANUFACTURING</span></footer>
      </main>
    </div>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
