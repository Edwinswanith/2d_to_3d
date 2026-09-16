import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Expand, RotateCcw, Grid2X2 } from 'lucide-react';

export type MeshData = { positions: number[]; indices: number[]; units: string };

export default function Viewer({ mesh, overlay }: { mesh: MeshData | null; overlay?: ReactNode }) {
  const host = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  const [error, setError] = useState('');
  const [wireframe, setWireframe] = useState(false);
  useEffect(() => { setWireframe(false); }, [mesh]);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    setError('');
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
    catch { setError('Your browser could not start the 3D viewer. STEP downloads are still available.'); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor('#e9efec', 0);
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const geometry = new THREE.BufferGeometry();
    let material: THREE.Material;
    let shape: THREE.Mesh;
    if (mesh) {
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3));
      geometry.setIndex(mesh.indices);
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.center();
      material = new THREE.MeshStandardMaterial({ color: '#b5c0b8', metalness: 0.58, roughness: 0.34, side: THREE.DoubleSide, polygonOffset: wireframe, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      shape = new THREE.Mesh(geometry, material);
      if (wireframe) {
        const edges = new THREE.LineSegments(
          new THREE.WireframeGeometry(geometry),
          new THREE.LineBasicMaterial({ color: '#50635d', transparent: true, opacity: 0.35 }),
        );
        shape.add(edges);
      }
      shape.rotation.x = -Math.PI / 2;
    } else {
      material = new THREE.MeshStandardMaterial({ color: '#82998e', metalness: 0.3, roughness: 0.5, wireframe: true, transparent: true, opacity: 0.32 });
      // An illustrative ring before upload; never presented as a generated model.
      const placeholder = new THREE.TorusGeometry(45, 9, 16, 70);
      shape = new THREE.Mesh(placeholder, material);
      shape.rotation.x = -Math.PI / 2;
    }
    scene.add(shape);
    const box = new THREE.Box3().setFromObject(shape);
    const size = box.getSize(new THREE.Vector3());
    const extent = Math.max(size.x, size.y, size.z, 10);
    const fit = () => {
      camera.position.set(extent * 1.5, extent * 1.2, extent * 1.7);
      camera.near = extent / 1000; camera.far = extent * 100;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0); controls.update();
    };
    fit(); reset.current = fit;
    scene.add(new THREE.HemisphereLight('#ffffff', '#50635d', 2.8));
    const key = new THREE.DirectionalLight('#ffffff', 4); key.position.set(extent, extent * 2, extent); scene.add(key);
    const fill = new THREE.DirectionalLight('#e3f7e9', 2); fill.position.set(-extent, extent, -extent); scene.add(fill);
    const grid = new THREE.GridHelper(extent * 3, 24, '#b9c8be', '#d6dfd8');
    grid.position.y = -size.y / 2 - extent * 0.02; scene.add(grid);
    const observer = new ResizeObserver(() => {
      const width = element.clientWidth, height = element.clientHeight;
      renderer.setSize(width, height); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
    }); observer.observe(element);
    let frame = 0;
    function animate() { frame = requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }
    animate();
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); controls.dispose();
      shape.children.forEach(child => {
        if (child instanceof THREE.LineSegments) {
          child.geometry.dispose(); (child.material as THREE.Material).dispose();
        }
      });
      geometry.dispose(); shape.geometry.dispose(); material.dispose(); grid.geometry.dispose();
      (grid.material as THREE.Material).dispose(); renderer.dispose(); renderer.domElement.remove();
    };
  }, [mesh, wireframe]);

  return <div className="viewport">
    <div className="viewport-header"><span><i className={mesh ? 'live-dot' : 'muted-dot'} />{mesh ? 'BODY PREVIEW' : '3D WORKSPACE'}</span><span className="mono">{mesh ? 'MODEL UNITS / MM' : 'ILLUSTRATION'}</span></div>
    <div ref={host} className="canvas-host" aria-label={mesh ? 'Interactive generated 3D body model' : 'Illustrative ring; upload a drawing to generate your model'} />
    {!mesh && <div className="viewport-copy"><span className="eyebrow">FROM SHEET TO SOLID</span><h2>Your drawing.<br />Another dimension.</h2><p>Upload a gland-ring drawing to see its body take shape here.</p></div>}
    {overlay}
    {error && <div className="viewer-error" role="alert">{error}</div>}
    <div className="viewport-bottom"><span className="mono">DRAG TO ORBIT · SCROLL TO ZOOM</span><div className="viewer-tools">
      <button type="button" disabled={!mesh} onClick={() => setWireframe(!wireframe)} aria-label="Toggle mesh edges" aria-pressed={wireframe} title={wireframe ? 'Hide mesh edges' : 'Show mesh edges'}><Grid2X2 size={16} /></button>
      <button type="button" onClick={() => reset.current()} aria-label="Reset model view" title="Reset view"><RotateCcw size={16} /></button>
      <button type="button" onClick={() => host.current?.parentElement?.requestFullscreen().catch(() => {})} aria-label="Expand viewer" title="Expand"><Expand size={16} /></button>
    </div></div>
    <div className="axis-label mono"><span className="axis-x">X</span><span className="axis-y">Y</span><span className="axis-z">Z</span></div>
  </div>;
}
