import { useEffect, useId, useState } from 'react';
import { Check } from 'lucide-react';
import './progress.css';

/**
 * Plain-language progress for one drawing. Every stage maps to a real backend status;
 * nothing here invents progress, percentages or time estimates.
 */
type Stage = { key: string; step: number; title: string; detail: string };

const STEPS = ['Upload', 'Prepare sheet', 'Read dimensions', 'Check numbers', 'Build solid', '3D preview'];

const STAGES: Stage[] = [
  { key: 'uploading', step: 0, title: 'Sending your drawing', detail: 'Uploading the sheet to your workspace.' },
  { key: 'queued', step: 0, title: 'Lining up your drawing', detail: 'Waiting for the local worker to become available.' },
  { key: 'rendering', step: 1, title: 'Preparing the sheet', detail: 'Turning your file into a clear, upright image so every number is easy to read.' },
  { key: 'reading', step: 2, title: 'Reading the dimensions', detail: 'Finding the sizes, units and notes printed on the drawing. Reading a busy sheet can take time.' },
  { key: 'checking', step: 3, title: 'Checking the numbers', detail: 'Checking the source citations, unit conversions and profile inputs before building.' },
  { key: 'building', step: 4, title: 'Shaping the ring', detail: 'Spinning the side profile into a solid, the way a lathe turns a bar, then saving and reopening it to check its size and volume.' },
  { key: 'previewing', step: 5, title: 'Preparing your 3D view', detail: 'Getting the model ready for you to spin and inspect.' },
];

const FALLBACK: Stage = { key: 'working', step: 0, title: 'Working on your drawing', detail: 'This page updates on its own.' };

export function stageFor(status: string): Stage {
  return STAGES.find(stage => stage.key === status) ?? FALLBACK;
}

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

type Props = { status: string; drawingUrl?: string; startedAt: number };

export default function GenerationProgress({ status, drawingUrl, startedAt }: Props) {
  const stage = stageFor(status);
  const [now, setNow] = useState(() => Date.now());
  const [stageStartedAt, setStageStartedAt] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => setStageStartedAt(Date.now()), [stage.key]);

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  const inStage = Math.max(0, Math.floor((now - stageStartedAt) / 1000));

  // Reassurance only; never a promise about remaining time.
  let note = '';
  if (stage.key === 'queued' && inStage >= 10) note = 'Your drawing is still queued. It starts automatically when the worker is available.';
  if (stage.key === 'reading' && inStage >= 30) note = 'Busy sheets take longer to read. Hang tight.';
  if (elapsed >= 90) note = 'Still processing. This page updates automatically with the result or an error.';

  const showSheetImage = stage.step >= 2 && !!drawingUrl;
  const solid = stage.step >= 4;

  return (
    <div className="gp" data-stage={stage.key}>
      <span className="visually-hidden" role="status" aria-live="polite">
        {`Step ${stage.step + 1} of ${STEPS.length}. ${stage.title}. ${stage.detail} ${note}`}
      </span>

      <div className="gp-visual" aria-hidden="true">
        {solid
          ? <ProfileToSolid finishing={stage.key === 'previewing'} />
          : <Sheet url={showSheetImage ? drawingUrl : undefined} stage={stage.key} />}
      </div>

      <div className="gp-copy" aria-hidden="true">
        <p className="gp-count mono">Step {stage.step + 1} of {STEPS.length}</p>
        <h3 className="gp-title" key={stage.key}>{stage.title}</h3>
        <p className="gp-detail">{stage.detail}</p>

        <ol className="gp-steps">
          {STEPS.map((label, i) => (
            <li key={label} className={i < stage.step ? 'is-done' : i === stage.step ? 'is-current' : ''}>
              <span className="gp-mark">{i < stage.step && <Check size={10} strokeWidth={3} />}</span>
              {label}
            </li>
          ))}
        </ol>

        <div className="gp-segments">
          {STEPS.map((label, i) => (
            <i key={label} className={i < stage.step ? 'is-done' : i === stage.step ? 'is-current' : ''} />
          ))}
        </div>

        <div className="gp-foot">
          <span className="gp-clock mono">{clock(elapsed)}</span>
          {note && <span className="gp-note">{note}</span>}
        </div>
      </div>
    </div>
  );
}

function Sheet({ url, stage }: { url?: string; stage: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  return (
    <div className={`gp-sheet gp-sheet--${stage}`}>
      {url && !failed
        ? <img src={url} alt="" onError={() => setFailed(true)} />
        : <SheetSketch />}
      {stage === 'reading' && <span className="gp-scan" />}
      {stage === 'checking' && <span className="gp-verify" />}
      <i className="gp-corner gp-corner--tl" />
      <i className="gp-corner gp-corner--tr" />
      <i className="gp-corner gp-corner--bl" />
      <i className="gp-corner gp-corner--br" />
    </div>
  );
}

/** A generic sheet outline shown until the real render exists. */
function SheetSketch() {
  return (
    <svg className="gp-sketch" viewBox="0 0 282 200" preserveAspectRatio="xMidYMid meet">
      <rect x="8" y="8" width="266" height="184" />
      <rect x="176" y="150" width="98" height="42" />
      <line x1="176" y1="164" x2="274" y2="164" />
      <circle cx="206" cy="78" r="40" />
      <circle cx="206" cy="78" r="18" />
      <rect x="38" y="42" width="30" height="80" />
      <rect x="68" y="56" width="24" height="52" />
      <line x1="38" y1="136" x2="92" y2="136" className="gp-dim" />
      <line x1="26" y1="42" x2="26" y2="122" className="gp-dim" />
      <line x1="120" y1="110" x2="160" y2="96" className="gp-dim" />
    </svg>
  );
}

/**
 * A gland-ring half section is drawn, then the solid is revolved around its axis
 * and the section fades back. This mirrors what the builder does: profile first,
 * solid second.
 */
const LEVELS = [
  { y: 40, r: 76, ry: 13 },
  { y: 66, r: 92, ry: 16 },
  { y: 134, r: 92, ry: 16 },
  { y: 160, r: 66, ry: 12 },
];
const front = (y: number, r: number, ry: number) => `M${120 - r} ${y} A${r} ${ry} 0 0 0 ${120 + r} ${y}`;
const back = (y: number, r: number, ry: number) => `M${120 - r} ${y} A${r} ${ry} 0 0 1 ${120 + r} ${y}`;

function ProfileToSolid({ finishing }: { finishing: boolean }) {
  const id = useId().replace(/:/g, '');
  const profile = 'M136 40 H196 V66 H212 V134 H186 V160 H136 Z';
  const silhouettes = ['M44 40 V66 H28 V134 H54 V160', 'M196 40 V66 H212 V134 H186 V160'];
  return (
    <svg className={`gp-solid ${finishing ? 'is-finishing' : ''}`} viewBox="0 0 240 200">
      <defs>
        <pattern id={`${id}-hatch`} patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" />
        </pattern>
      </defs>
      <line className="gp-axis" x1="120" y1="8" x2="120" y2="192" />
      <g className="gp-section">
        <path className="gp-fill" d={profile} fill={`url(#${id}-hatch)`} />
        <path className="gp-profile" pathLength={1} d={profile} />
      </g>
      <g className="gp-exterior">
        {LEVELS.map(l => <path key={`b${l.y}`} className="gp-back" d={back(l.y, l.r, l.ry)} />)}
        {silhouettes.map(d => <path key={d} className="gp-edge" pathLength={1} d={d} />)}
        {LEVELS.map(l => <path key={`f${l.y}`} className="gp-edge" pathLength={1} d={front(l.y, l.r, l.ry)} />)}
        <path className="gp-sweep" pathLength={1} d={front(134, 92, 16)} />
      </g>
    </svg>
  );
}
