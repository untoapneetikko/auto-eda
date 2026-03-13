// ── Modal helpers ────────────────────────────────────────────
function openModal(id){document.getElementById(id).classList.add('open');}
function closeModal(id){document.getElementById(id).classList.remove('open');}

// ═══════════════════════════════════════════════════════════════
// PCB 3D VIEWER
// ═══════════════════════════════════════════════════════════════
class PCB3DViewer {
  constructor(containerId) {
    this.cid = containerId;
    this.renderer = null; this.scene = null; this.camera = null;
    this.animId = null; this.board = null;
    this.theta = 0.5; this.phi = 0.95; this.radius = 80;
    this.pivotX = 0; this.pivotY = 0; this.pivotZ = 0;
    this._initialized = false;
  }

  init() {
    if (!window.THREE) { console.error('3D Viewer: Three.js not loaded'); return; }
    const container = document.getElementById(this.cid);
    if (!container) return;
    const w = Math.max(container.clientWidth, 400);
    const h = Math.max(container.clientHeight, 300);

    // Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0b0b14);
    this.scene.fog = new THREE.Fog(0x0b0b14, 300, 800);

    // Camera
    this.camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 2000);
    this._updateCam();

    // Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const el = this.renderer.domElement;
    el.style.cssText = 'display:block;position:absolute;top:0;left:0;width:100%;height:100%;';
    container.appendChild(el);

    this._addLights();
    this._bindMouse(container);
    this._loop();
    this._ro = new ResizeObserver(() => this._onResize());
    this._ro.observe(container);
    this._initialized = true;
  }

  _addLights() {
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xfff8e0, 1.0);
    sun.position.set(30, 60, 20); sun.castShadow = true;
    sun.shadow.camera.near = 1; sun.shadow.camera.far = 400;
    sun.shadow.camera.left = sun.shadow.camera.bottom = -150;
    sun.shadow.camera.right = sun.shadow.camera.top = 150;
    sun.shadow.mapSize.width = sun.shadow.mapSize.height = 2048;
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0x7080ff, 0.4);
    fill.position.set(-20, 10, -30);
    this.scene.add(fill);
    const rim = new THREE.DirectionalLight(0xffffff, 0.15);
    rim.position.set(0, -10, 0);
    this.scene.add(rim);
  }

  _bindMouse(el) {
    let drag = false, pan = false, lx = 0, ly = 0;
    el.addEventListener('mousedown', e => {
      drag = true; pan = e.button !== 0;
      lx = e.clientX; ly = e.clientY; e.preventDefault();
    });
    window.addEventListener('mouseup', () => { drag = false; });
    window.addEventListener('mousemove', e => {
      if (!drag) return;
      const dx = e.clientX - lx, dy = e.clientY - ly;
      lx = e.clientX; ly = e.clientY;
      if (pan) {
        const s = this.radius * 0.0012;
        const ct = Math.cos(this.theta), st = Math.sin(this.theta);
        this.pivotX -= ct * dx * s;
        this.pivotZ -= st * dx * s;
        this.pivotY += dy * s;
      } else {
        this.theta -= dx * 0.007;
        this.phi = Math.max(0.05, Math.min(Math.PI / 2 - 0.01, this.phi + dy * 0.007));
      }
      this._updateCam();
    });
    el.addEventListener('wheel', e => {
      this.radius = Math.max(3, Math.min(600, this.radius * (1 + e.deltaY * 0.001)));
      this._updateCam(); e.preventDefault();
    }, { passive: false });
    el.addEventListener('contextmenu', e => e.preventDefault());
    el.addEventListener('dblclick', () => this.resetView());
  }

  _updateCam() {
    const sp = Math.sin(this.phi), cp = Math.cos(this.phi);
    const st = Math.sin(this.theta), ct = Math.cos(this.theta);
    this.camera.position.set(
      this.pivotX + this.radius * sp * st,
      this.pivotY + this.radius * cp,
      this.pivotZ + this.radius * sp * ct
    );
    this.camera.lookAt(this.pivotX, this.pivotY, this.pivotZ);
  }

  resetView() {
    if (this.board) {
      this.pivotX = 0; this.pivotY = 0; this.pivotZ = 0;
      const diag = Math.sqrt(this.board.board.width ** 2 + this.board.board.height ** 2);
      this.radius = diag * 1.15;
    }
    this.theta = 0.45; this.phi = 0.9;
    this._updateCam();
  }

  _clearScene() {
    const objs = [];
    this.scene.traverse(o => { if (o !== this.scene) objs.push(o); });
    for (const o of objs) {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
      }
    }
    while (this.scene.children.length) this.scene.remove(this.scene.children[0]);
    this._addLights();
  }

  // Convert PCB mm coords → Three.js world coords (centered on board)
  _px(x) { return x - this.board.board.width / 2; }
  _pz(y) { return y - this.board.board.height / 2; }

  load(board) {
    this.board = board;
    // ── Layer thickness constants (visually exaggerated for clarity) ──────────
    this.PCB_T = 1.6;    // FR4 substrate
    this.CU_T  = 0.20;   // copper (real: 0.035 — exaggerated 6× so it reads clearly)
    this.SM_T  = 0.10;   // solder mask
    this.SP_T  = 0.06;   // solder paste (in pad openings only)
    // Derived Y positions (Y=0 is top of FR4, Y=-PCB_T is bottom of FR4)
    this.Y_CU_TOP    =  this.CU_T / 2;                          // top copper centre
    this.Y_SM_TOP    =  this.CU_T + this.SM_T / 2;              // top soldermask centre
    this.Y_SP_TOP    =  this.CU_T + this.SM_T + this.SP_T / 2;  // top paste centre
    this.Y_CU_BOT    = -this.PCB_T - this.CU_T / 2;             // bottom copper centre
    this.Y_SM_BOT    = -this.PCB_T - this.CU_T - this.SM_T / 2; // bottom soldermask centre
    this.Y_SP_BOT    = -this.PCB_T - this.CU_T - this.SM_T - this.SP_T / 2;
    this._clearScene();

    // Ground grid
    const gs = Math.max(board.board.width, board.board.height) * 3;
    const grid = new THREE.GridHelper(gs, Math.round(gs / 5), 0x181828, 0x181828);
    grid.position.y = -2.1;
    this.scene.add(grid);

    this._buildBoard(board);
    this._buildTraces(board);
    this._buildVias(board);
    this._buildComponents(board);
    this.resetView();
  }

  _buildBoard(board) {
    const { width, height } = board.board;
    const { PCB_T, CU_T, SM_T } = this;

    // FR4 substrate
    const geo = new THREE.BoxGeometry(width, PCB_T, height);
    const substrate = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: 0x1a6b2a }));
    substrate.position.y = -PCB_T / 2;
    substrate.receiveShadow = true;
    this.scene.add(substrate);

    // Top copper fill (bare board surface — shows under semi-transparent mask)
    const cuFillMat = new THREE.MeshLambertMaterial({ color: 0xb87333 });
    const cuTop = new THREE.Mesh(new THREE.BoxGeometry(width, CU_T, height), cuFillMat);
    cuTop.position.y = this.Y_CU_TOP;
    this.scene.add(cuTop);

    const cuBot = new THREE.Mesh(new THREE.BoxGeometry(width, CU_T, height), cuFillMat.clone());
    cuBot.position.y = this.Y_CU_BOT;
    this.scene.add(cuBot);

    // Top solder mask — semi-transparent dark green, sits above copper
    const smMat = new THREE.MeshLambertMaterial({ color: 0x0e4f1a, transparent: true, opacity: 0.82, depthWrite: false });
    const smTop = new THREE.Mesh(new THREE.BoxGeometry(width, SM_T, height), smMat);
    smTop.position.y = this.Y_SM_TOP;
    this.scene.add(smTop);

    // Bottom solder mask
    const smBot = new THREE.Mesh(
      new THREE.BoxGeometry(width, SM_T, height),
      smMat.clone()
    );
    smBot.position.y = this.Y_SM_BOT;
    this.scene.add(smBot);

    // Board outline
    const edgeLine = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: 0xddcc00 })
    );
    edgeLine.position.y = -PCB_T / 2;
    this.scene.add(edgeLine);
  }

  _buildTraces(board) {
    if (!board.traces || !board.traces.length) return;
    const { CU_T, SM_T } = this;
    // Traces sit ON TOP of the solder mask so they are always visible.
    // Height is exaggerated beyond the mask thickness for clear readability.
    const TR_H  = 0.30;   // visual trace height above solder mask surface
    const topSurface = CU_T + SM_T; // top of solder mask
    const botSurface = -(this.PCB_T + CU_T + SM_T); // bottom of solder mask
    const ty_top =  topSurface + TR_H / 2;
    const ty_bot =  botSurface - TR_H / 2;
    const matTop = new THREE.MeshLambertMaterial({ color: 0xd4881a }); // warm copper
    const matBot = new THREE.MeshLambertMaterial({ color: 0x4488ff }); // blue for bottom
    for (const trace of board.traces) {
      const isBot = trace.layer === 'B.Cu';
      const mat = isBot ? matBot : matTop;
      const ty  = isBot ? ty_bot : ty_top;
      for (const seg of (trace.segments || [])) {
        if (!seg || !seg.start || !seg.end) continue;
        const dx = seg.end.x - seg.start.x, dz = seg.end.y - seg.start.y;
        const len = Math.sqrt(dx * dx + dz * dz);
        if (len < 0.001) continue;
        const w = Math.max(trace.width || 0.25, 0.15); // minimum visual width
        const geo = new THREE.BoxGeometry(len, TR_H, w);
        const m = new THREE.Mesh(geo, mat);
        m.position.set(
          this._px((seg.start.x + seg.end.x) / 2),
          ty,
          this._pz((seg.start.y + seg.end.y) / 2)
        );
        m.rotation.y = -Math.atan2(dz, dx);
        this.scene.add(m);
      }
    }
  }

  _buildVias(board) {
    if (!board.vias || !board.vias.length) return;
    const { PCB_T, CU_T, SM_T } = this;
    const barrelH = PCB_T + CU_T * 2;
    const mat = new THREE.MeshLambertMaterial({ color: 0xd4aa00 });
    for (const via of board.vias) {
      const r  = (via.size  || 1.0) / 2;
      const dr = (via.drill || 0.6) / 2;
      // Barrel through entire stack
      const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, barrelH, 16), mat.clone());
      m.position.set(this._px(via.x), -(PCB_T / 2), this._pz(via.y));
      this.scene.add(m);
      // Top annular ring — above solder mask
      const ringY_top = CU_T + SM_T;
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(Math.min(dr, r * 0.9), r, 16),
        new THREE.MeshLambertMaterial({ color: 0xd4aa00, side: THREE.DoubleSide })
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.set(this._px(via.x), ringY_top, this._pz(via.y));
      this.scene.add(ring);
      // Bottom annular ring
      const ringY_bot = -(PCB_T + CU_T + SM_T);
      const ringB = ring.clone();
      ringB.rotation.x = Math.PI / 2;
      ringB.position.set(this._px(via.x), ringY_bot, this._pz(via.y));
      this.scene.add(ringB);
    }
  }

  _buildComponents(board) {
    if (!board.components || !board.components.length) return;
    for (const comp of board.components) {
      const isBack = comp.layer === 'B';
      const fp = (comp.footprint || '').toLowerCase();
      const ref = comp.ref || '';
      let color = 0x111118, h = 1.5, cw = 3, cd = 3;

      // Shape/height from footprint name
      if (fp.includes('to-220') || fp.includes('to220')) { h = 10; cw = 10; cd = 4.5; color = 0x111111; }
      else if (fp.includes('to-92') || fp.includes('to92'))  { h = 5;  cw = 4.5; cd = 4.5; color = 0x111111; }
      else if (fp.includes('pinheader') || fp.includes('pin_header') || fp.includes('conn_01x')) { h = 9; cw = 2.5; cd = 2.5; color = 0x1a1600; }
      else if (fp.includes('dip'))  { h = 4;   color = 0x0d0d0d; }
      else if (fp.includes('soic') || fp.includes('sop'))  { h = 1.7; color = 0x0d0d0d; }
      else if (fp.includes('qfn')  || fp.includes('qfp')  || fp.includes('lqfp') || fp.includes('tqfp')) { h = 1.0; color = 0x0d0d0d; }
      else if (fp.includes('0201')) { h = 0.6; cw = 0.6;  cd = 0.3; }
      else if (fp.includes('0402')) { h = 0.5; cw = 1.0;  cd = 0.5; }
      else if (fp.includes('0603')) { h = 0.8; cw = 1.6;  cd = 0.8; }
      else if (fp.includes('0805')) { h = 1.25;cw = 2.0;  cd = 1.25;}
      else if (fp.includes('1206')) { h = 1.25;cw = 3.2;  cd = 1.6; }

      // Auto-size from pad bounding box when cw/cd still default
      if (cw === 3 && comp.pads && comp.pads.length > 0) {
        let mnx=Infinity, mxx=-Infinity, mny=Infinity, mxy=-Infinity;
        for (const p of comp.pads) {
          mnx = Math.min(mnx, p.x - (p.size_x||1)/2); mxx = Math.max(mxx, p.x + (p.size_x||1)/2);
          mny = Math.min(mny, p.y - (p.size_y||1)/2); mxy = Math.max(mxy, p.y + (p.size_y||1)/2);
        }
        if (isFinite(mxx)) { cw = Math.max(1.5, mxx - mnx + 0.6); cd = Math.max(1.5, mxy - mny + 0.6); }
      }

      // Color by reference designator prefix
      if      (ref.startsWith('R'))             color = 0x3a1a00;
      else if (ref.startsWith('C'))             color = 0x001838;
      else if (ref.startsWith('L'))             color = 0x00180c;
      else if (ref.startsWith('U') || ref.startsWith('IC')) color = 0x0a0a0a;
      else if (ref.startsWith('D'))             color = 0x200020;
      else if (ref.startsWith('Q'))             color = 0x1a0000;
      else if (ref.startsWith('J') || ref.startsWith('P')) color = 0x1a1400;

      const geo = new THREE.BoxGeometry(cw, h, cd);
      const mesh = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color }));
      const yBase = isBack ? (-1.6 - h / 2) : (h / 2);
      mesh.position.set(this._px(comp.x), yBase, this._pz(comp.y));
      const rotRad = -(comp.rotation || 0) * Math.PI / 180;
      mesh.rotation.y = rotRad;
      mesh.castShadow = true;
      this.scene.add(mesh);

      // Wireframe outline
      const outline = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: 0x3333aa })
      );
      outline.position.copy(mesh.position);
      outline.rotation.y = rotRad;
      this.scene.add(outline);

      // Pads
      this._buildPads(comp);

      // Label sprite
      this._mkLabel(ref, this._px(comp.x), yBase + h / 2 + 0.4, this._pz(comp.y));
    }
  }

  _buildPads(comp) {
    const rot = (comp.rotation || 0) * Math.PI / 180;
    const cosR = Math.cos(rot), sinR = Math.sin(rot);
    const isBack = comp.layer === 'B';
    const { CU_T, SM_T, SP_T, PCB_T } = this;
    const matGold  = new THREE.MeshLambertMaterial({ color: 0xd4a800 });
    const matPaste = new THREE.MeshLambertMaterial({ color: 0xaaaaaa }); // solder paste — silver

    for (const pad of (comp.pads || [])) {
      // Rotate pad position with component
      const wx = comp.x + pad.x * cosR - pad.y * sinR;
      const wy = comp.y + pad.x * sinR + pad.y * cosR;
      const px3 = this._px(wx), pz3 = this._pz(wy);

      if (pad.type === 'thru_hole') {
        const outerR = Math.max(pad.size_x || 1.5, pad.size_y || 1.5) / 2;
        const innerR = Math.max(0.1, (pad.drill || outerR * 0.6) / 2);
        const ringY_top = CU_T + SM_T;          // exposed above solder mask
        const ringY_bot = -(PCB_T + CU_T + SM_T);
        // Top annular ring (exposed through mask)
        const rg = new THREE.RingGeometry(innerR, outerR, 16);
        const rt = new THREE.Mesh(rg, new THREE.MeshLambertMaterial({ color: 0xd4a800, side: THREE.DoubleSide }));
        rt.rotation.x = -Math.PI / 2; rt.position.set(px3, ringY_top, pz3);
        this.scene.add(rt);
        // Bottom annular ring
        const rb = new THREE.Mesh(rg.clone(), new THREE.MeshLambertMaterial({ color: 0xd4a800, side: THREE.DoubleSide }));
        rb.rotation.x = Math.PI / 2; rb.position.set(px3, ringY_bot, pz3);
        this.scene.add(rb);
        // Plated through-hole barrel
        const bg = new THREE.CylinderGeometry(innerR * 0.98, innerR * 0.98, PCB_T + CU_T * 2, 16, 1, true);
        const brl = new THREE.Mesh(bg, new THREE.MeshLambertMaterial({ color: 0xd4a800, side: THREE.BackSide }));
        brl.position.set(px3, -(PCB_T / 2), pz3);
        this.scene.add(brl);
      } else {
        // SMD pad — gold lands sit just above copper, poking through solder mask
        const padY  = isBack ? this.Y_CU_BOT : this.Y_CU_TOP;
        const pw = pad.size_x || 1.5, pd_s = pad.size_y || 1.5;
        const isCirc = pad.shape === 'circle';
        const padGeo = isCirc
          ? new THREE.CylinderGeometry(Math.min(pw, pd_s) / 2, Math.min(pw, pd_s) / 2, CU_T, 16)
          : new THREE.BoxGeometry(pw, CU_T, pd_s);
        const m = new THREE.Mesh(padGeo, matGold.clone());
        m.position.set(px3, padY, pz3);
        this.scene.add(m);

        // Solder paste — thin silver layer on top of pad opening (top side only by default)
        const pasteY = isBack ? this.Y_SP_BOT : this.Y_SP_TOP;
        const shrink = 0.9; // paste is slightly smaller than the pad
        const pasteGeo = isCirc
          ? new THREE.CylinderGeometry(Math.min(pw, pd_s) * shrink / 2, Math.min(pw, pd_s) * shrink / 2, SP_T, 16)
          : new THREE.BoxGeometry(pw * shrink, SP_T, pd_s * shrink);
        const paste = new THREE.Mesh(pasteGeo, matPaste.clone());
        paste.position.set(px3, pasteY, pz3);
        this.scene.add(paste);
      }
    }
  }

  _mkLabel(text, x, y, z) {
    if (!text || !window.THREE) return;
    const c = document.createElement('canvas');
    c.width = 128; c.height = 32;
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, 128, 32);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 20px monospace';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text.substring(0, 8), 64, 16);
    const tex = new THREE.CanvasTexture(c);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
    sp.scale.set(4, 1, 1);
    sp.position.set(x, y + 0.5, z);
    this.scene.add(sp);
  }

  _loop() {
    this.animId = requestAnimationFrame(() => this._loop());
    if (this.renderer) this.renderer.render(this.scene, this.camera);
  }

  _onResize() {
    const container = document.getElementById(this.cid);
    if (!container || !this.renderer) return;
    const w = container.clientWidth, h = container.clientHeight;
    if (w > 0 && h > 0) {
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    }
  }

  destroy() {
    if (this.animId) cancelAnimationFrame(this.animId);
    if (this._ro) this._ro.disconnect();
    this._clearScene();
    if (this.renderer) this.renderer.dispose();
    const c = document.getElementById(this.cid);
    if (c && this.renderer && this.renderer.domElement.parentNode === c)
      c.removeChild(this.renderer.domElement);
  }
}

let viewer3d = null;

function open3DView() {
  switchPcbSection('3d');
  setTimeout(() => {
    if (!window.THREE) {
      document.getElementById('viewer-3d-empty').innerHTML =
        '<span style="color:var(--red)">Error: Three.js failed to load. Check your internet connection.</span>';
      return;
    }
    if (!viewer3d) {
      viewer3d = new PCB3DViewer('viewer-3d-canvas');
      viewer3d.init();
    } else {
      viewer3d._onResize();
    }
    refresh3DView();
  }, 50);
}

function refresh3DView() {
  if (!viewer3d || !viewer3d._initialized) return;
  const empty = document.getElementById('viewer-3d-empty');
  const title = document.getElementById('viewer-3d-title');
  if (editor && editor.board) {
    viewer3d.load(editor.board);
    if (empty) empty.style.display = 'none';
    if (title) title.textContent = editor.board.title || 'Untitled Board';
  } else {
    if (empty) empty.style.display = 'flex';
    if (title) title.textContent = '';
  }
}
