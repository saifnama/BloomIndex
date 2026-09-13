/**
 * Standalone offline HTML export for knowledge graphs.
 *
 * Inlines vis-network and Google Sans fonts without network calls.
 * Replicates interactive graph features: search, layout reset,
 * legend filtering, and node neighbor inspection.
 */
import {
  GOOGLE_SANS_REGULAR_B64,
  GOOGLE_SANS_MEDIUM_B64,
  GOOGLE_SANS_BOLD_B64,
} from './embeddedFonts';

export interface GraphLegendItem {
  typeKey?: string;
  color: string;
  name: string;
  count?: number;
}

export interface ExportNodeInfo {
  type: string;
  name: string;
  freq: number;
  color: string;
  isPaper: boolean;
}

const safeJson = (value: unknown) =>
  JSON.stringify(value).replace(/</g, '\\u003c');
// Formats graph data with indentation for offline text inspection.
const prettyJson = (value: unknown) =>
  JSON.stringify(value, null, 2).replace(/</g, '\\u003c');

const findLocal = (bundle: string, exported: string): string => {
  const matches = [...bundle.matchAll(/export\s*\{([^}]*)\}/g)];
  const last = matches.length > 0 ? matches[matches.length - 1] : null;
  if (last) {
    for (const part of last[1].split(',')) {
      const seg = part.trim().split(/\s+as\s+/);
      const local = seg[0].trim();
      const alias = (seg[1] ?? seg[0]).trim();
      if (alias === exported && local) return local;
    }
  }
  return exported;
};

export async function downloadGraphHtml(opts: {
  nodes: unknown[];
  edges: unknown[];
  filename: string;
  title?: string;
  subtitle?: string;
  legend?: GraphLegendItem[];
  nodeInfo?: Record<string, ExportNodeInfo>;
  /** Types filtered out in the app at export time. */
  inactiveTypes?: string[];
  /** Live starting coordinates for initial layout stabilization. */
  positions?: Record<string, { x: number; y: number }>;
  /** Live camera zoom and pan coordinates. */
  view?: { scale: number; position: { x: number; y: number } };
}): Promise<void> {
  const { default: visBundle } =
    await import('vis-network/standalone/esm/vis-network.min.js?raw');
  const safeBundle = (visBundle as string).replace(
    /<\/script>/g,
    '<\\/script>',
  );
  const networkCtor = findLocal(visBundle as string, 'Network');
  const dataSetCtor = findLocal(visBundle as string, 'DataSet');
  const dataSetDecl =
    dataSetCtor !== 'DataSet' ? `const DataSet=${dataSetCtor};\n` : '';

  const nodesWithPos = opts.positions
    ? (opts.nodes as Record<string, unknown>[]).map((n) => {
        const p = n.id != null ? opts.positions![String(n.id)] : undefined;
        return p ? { ...n, x: p.x, y: p.y } : n;
      })
    : opts.nodes;
  // Restores exact camera viewport saved during export.
  const postInit = opts.view
    ? `net.moveTo({ position: ${safeJson(opts.view.position)}, scale: ${opts.view.scale} });`
    : `net.on('stabilizationIterationsDone', () => {
  const scale = net.getScale();
  net.moveTo({ scale: scale * 0.8 });
});`;

  const legendItems = opts.legend || [];
  const nodeInfoMap = opts.nodeInfo || {};
  const inactive = new Set(opts.inactiveTypes || []);
  const typeKeyOf = (l: GraphLegendItem) =>
    l.typeKey || l.name.toUpperCase().replace(/\s+/g, '_');
  const activeTypeKeys = legendItems
    .map(typeKeyOf)
    .filter((k) => !inactive.has(k));
  const escHtml = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const legendHtml = legendItems
    .map((item) => {
      const key = typeKeyOf(item);
      const dimmed = inactive.has(key);
      return (
        `<div class="legend-item" data-type="${key}" style="opacity: ${dimmed ? '0.3' : '1'};">` +
        `<div class="legend-dot" style="background-color: ${item.color};"></div>` +
        `<span class="legend-name">${escHtml(item.name)}</span>` +
        `<span class="legend-count">${item.count ?? ''}</span></div>`
      );
    })
    .join('');

  const html = `<!DOCTYPE html>
<!--
  Live offline knowledge graph export.
  Node labels, edge relationships, and properties can be inspected
  or modified in GRAPH_DATA below using standard text editors.
-->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${opts.title || 'Knowledge Graph'}</title>
<style>
@font-face {
  font-family: 'Google Sans';
  font-style: normal;
  font-weight: 400;
  src: url(data:font/woff2;base64,${GOOGLE_SANS_REGULAR_B64}) format('woff2');
}
@font-face {
  font-family: 'Google Sans';
  font-style: normal;
  font-weight: 500;
  src: url(data:font/woff2;base64,${GOOGLE_SANS_MEDIUM_B64}) format('woff2');
}
@font-face {
  font-family: 'Google Sans';
  font-style: normal;
  font-weight: 700;
  src: url(data:font/woff2;base64,${GOOGLE_SANS_BOLD_B64}) format('woff2');
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  margin: 0;
  padding: 0;
  overflow: hidden;
  font-family: 'Google Sans', Inter, system-ui, -apple-system, sans-serif;
  background-color: #ffffff;
  color: #1e293b;
  width: 100vw;
  height: 100vh;
}

#mynetwork {
  width: 100vw;
  height: 100vh;
  cursor: grab;
}
#mynetwork:active {
  cursor: grabbing;
}

/* Control toolbar */
.toolbar {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-input {
  font-family: 'Google Sans', Inter, sans-serif;
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background-color: rgba(255, 255, 255, 0.95);
  color: #475569;
  outline: none;
  width: 144px;
  transition: border-color 0.15s ease;
}
.search-input:focus {
  border-color: #94a3b8;
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border-radius: 8px;
  background: transparent;
  border: none;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-icon:hover {
  color: #334155;
  background-color: #f1f5f9;
}

.btn-label {
  font-family: 'Google Sans', Inter, sans-serif;
  font-size: 12px;
  font-weight: 500;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid #1e293b;
  background-color: #1e293b;
  color: #ffffff;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-label.off {
  background-color: #ffffff;
  color: #475569;
  border-color: #e2e8f0;
}
.btn-label.off:hover {
  background-color: #f1f5f9;
}

/* Legend with counts - exact replica */
.legend-panel {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 10;
  min-width: 240px;
  padding: 8px;
  font-family: 'Google Sans', Inter, sans-serif;
  pointer-events: auto;
  user-select: none;
}

.legend-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #64748b;
  margin-bottom: 8px;
  padding: 0 8px;
}

.legend-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.legend-item:hover {
  background-color: rgba(241, 245, 249, 0.7);
}

.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  font-size: 14px;
  font-weight: 500;
  color: #1e293b;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.legend-count {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  font-variant-numeric: tabular-nums;
}

/* Node detail tooltip */
.graph-tooltip {
  position: absolute;
  pointer-events: none;
  z-index: 30;
  background: #ffffff;
  border: 1px solid #e2e2e2;
  border-radius: 8px;
  padding: 9px 12px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.1);
  min-width: 155px;
  font-family: 'Google Sans', Inter, sans-serif;
  display: none;
}
.tooltip-type {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 3px;
}
.tooltip-name {
  font-size: 13.5px;
  font-weight: 600;
  color: #111111;
  margin-bottom: 3px;
  line-height: 1.3;
}
.tooltip-freq {
  font-size: 11px;
  color: #64748b;
}
</style>
</head>
<body>

<div class="legend-panel">
  <div class="legend-title">Entities</div>
  <div class="legend-list" id="legendList">
${legendHtml}
  </div>
</div>

<div class="toolbar">
  <input type="text" id="searchInput" class="search-input" placeholder="Search node…" />
  <button type="button" id="resetBtn" class="btn-icon" title="Reset layout" aria-label="Reset layout">
    <svg width="18" height="18" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true">
      <path d="M228,128a100,100,0,0,1-98.66,100H128a99.39,99.39,0,0,1-68.62-27.29,12,12,0,0,1,16.48-17.45,76,76,0,1,0-1.57-109c-.13.13-.25.25-.39.37L54.89,92H72a12,12,0,0,1,0,24H24a12,12,0,0,1-12-12V56a12,12,0,0,1,24,0V76.72L57.48,57.06A100,100,0,0,1,228,128Z"/>
    </svg>
  </button>
  <button type="button" id="labelsBtn" class="btn-label" aria-label="Toggle labels">Labels</button>
</div>

<div id="tooltip" class="graph-tooltip">
  <div id="tooltipType" class="tooltip-type"></div>
  <div id="tooltipName" class="tooltip-name"></div>
  <div id="tooltipFreq" class="tooltip-freq"></div>
</div>

<div id="mynetwork"></div>

<script type="module">
${safeBundle}

const GRAPH_DATA = {
  // NODES - edit label / size / color freely. "from"/"to" below refer to these ids.
  nodes: ${prettyJson(nodesWithPos)},
  // EDGES - { from: "<node id>", to: "<node id>" }.
  edges: ${prettyJson(opts.edges)}
};
const initialNodes = GRAPH_DATA.nodes;
const initialEdges = GRAPH_DATA.edges;
const nodeInfoMap = ${safeJson(nodeInfoMap)};

${dataSetDecl}const nodesDS = new DataSet(initialNodes);
const edgesDS = new DataSet(initialEdges);

const livePhysics = {
  enabled: true,
  solver: 'forceAtlas2Based',
  forceAtlas2Based: {
    gravitationalConstant: -100,
    centralGravity: 0.015,
    springLength: 130,
    springConstant: 0.04,
    damping: 0.85,
    avoidOverlap: 0.6
  },
  stabilization: { enabled: true, iterations: 300 },
  minVelocity: 0.3
};

const options = {
  nodes: {
    shape: 'dot',
    borderWidth: 0,
    font: {
      face: 'Google Sans, Inter, sans-serif'
    }
  },
  edges: {
    smooth: false,
    color: {
      color: '#e2e8f0',
      highlight: '#64748b',
      hover: '#64748b',
      opacity: 1
    },
    width: 0.8,
    hoverWidth: 1.5,
    selectionWidth: 1.5
  },
  interaction: {
    hover: true,
    tooltipDelay: 9999,
    zoomView: true,
    dragView: true
  },
  layout: { randomSeed: 42 },
  physics: livePhysics
};

const container = document.getElementById('mynetwork');
const net = new ${networkCtor}(container, { nodes: nodesDS, edges: edgesDS }, options);

${postInit}

let showLabels = true;
let selectedId = null;
const activeTypes = new Set(${safeJson(activeTypeKeys)});

const lightConnectedEdges = (nodeId) => {
  edgesDS.get().forEach((e) => {
    const isConn = e.from === nodeId || e.to === nodeId;
    edgesDS.update({
      id: e.id,
      width: isConn ? 1.5 : 0.8,
      color: { color: isConn ? '#64748b' : '#e2e8f0', opacity: 1 }
    });
  });
};

const restoreAllEdges = () => {
  edgesDS.get().forEach((e) => {
    edgesDS.update({
      id: e.id,
      width: 0.8,
      color: { color: '#e2e8f0', opacity: 1 }
    });
  });
};

const applyHalo = (nodeId) => {
  initialNodes.forEach((n) => {
    if (n.id === nodeId) {
      nodesDS.update({
        id: n.id,
        shadow: {
          enabled: true,
          color: n._color || '#10B981',
          size: 22,
          x: 0,
          y: 0
        }
      });
    } else {
      nodesDS.update({
        id: n.id,
        shadow: n._isPaper
          ? { enabled: true, color: 'rgba(0,0,0,0.08)', size: 8, x: 0, y: 2 }
          : false
      });
    }
  });
};

const clearHalo = () => {
  initialNodes.forEach((n) => {
    nodesDS.update({
      id: n.id,
      shadow: n._isPaper
        ? { enabled: true, color: 'rgba(0,0,0,0.08)', size: 8, x: 0, y: 2 }
        : false
    });
  });
};

// Tooltip logic
const tooltipEl = document.getElementById('tooltip');
const tooltipTypeEl = document.getElementById('tooltipType');
const tooltipNameEl = document.getElementById('tooltipName');
const tooltipFreqEl = document.getElementById('tooltipFreq');

net.on('hoverNode', (params) => {
  const nodeId = params.node;
  if (selectedId === null) {
    const connected = new Set(net.getConnectedNodes(nodeId));
    connected.add(nodeId);
    nodesDS.get().forEach((n) => {
      nodesDS.update({ id: n.id, opacity: connected.has(n.id) ? 1 : 0.12 });
    });
    lightConnectedEdges(nodeId);
  }

  const info = nodeInfoMap[nodeId];
  if (info) {
    const pos = params.pointer.DOM;
    tooltipEl.style.left = (pos.x + 14) + 'px';
    tooltipEl.style.top = (pos.y - 10) + 'px';

    if (info.isPaper) {
      tooltipTypeEl.style.display = 'none';
      tooltipFreqEl.style.display = 'none';
    } else {
      tooltipTypeEl.style.display = 'block';
      tooltipTypeEl.style.color = info.color;
      tooltipTypeEl.textContent = info.type;
      tooltipFreqEl.style.display = 'block';
      tooltipFreqEl.textContent = 'frequency · ' + info.freq;
    }
    tooltipNameEl.textContent = info.name;
    tooltipEl.style.display = 'block';
  }
});

net.on('blurNode', () => {
  if (selectedId === null) {
    nodesDS.get().forEach((n) => nodesDS.update({ id: n.id, opacity: 1 }));
    restoreAllEdges();
  }
  tooltipEl.style.display = 'none';
});

// Click: isolate connected nodes & halo
net.on('click', (params) => {
  if (params.nodes.length === 0) {
    selectedId = null;
    initialNodes.forEach((n) => {
      nodesDS.update({
        id: n.id,
        opacity: 1,
        hidden: n._isPaper ? false : !activeTypes.has(n._type)
      });
    });
    restoreAllEdges();
    clearHalo();
    return;
  }
  const nodeId = params.nodes[0];
  const connected = new Set(net.getConnectedNodes(nodeId));
  connected.add(nodeId);
  selectedId = nodeId;
  initialNodes.forEach((n) => {
    nodesDS.update({
      id: n.id,
      opacity: 1,
      hidden: !connected.has(n.id)
    });
  });
  lightConnectedEdges(nodeId);
  applyHalo(nodeId);
});

// Double click: collapse / expand neighbours
net.on('doubleClick', (params) => {
  if (params.nodes.length === 0) return;
  const nodeId = params.nodes[0];
  if (String(nodeId).startsWith('paper-')) return;
  const neighbours = net.getConnectedNodes(nodeId);
  neighbours.forEach((id) => {
    if (String(id).startsWith('paper-')) return;
    const node = nodesDS.get(id);
    if (!node) return;
    nodesDS.update({ id, hidden: !node.hidden });
  });
});

// Labels Toggle
const labelsBtn = document.getElementById('labelsBtn');
labelsBtn.addEventListener('click', () => {
  showLabels = !showLabels;
  labelsBtn.classList.toggle('off', !showLabels);
  initialNodes.forEach((n) => {
    nodesDS.update({ id: n.id, label: showLabels ? (n._label || '') : '' });
  });
});

// Reset Layout
const resetBtn = document.getElementById('resetBtn');
resetBtn.addEventListener('click', () => {
  net.setOptions({ physics: livePhysics });
  net.stabilize(150);
  document.getElementById('searchInput').value = '';
  selectedId = null;
  activeTypes.clear();
  ${safeJson(activeTypeKeys)}.forEach((k) => activeTypes.add(k));
  document.querySelectorAll('.legend-item').forEach((el) => {
    el.style.opacity = '1';
  });
  nodesDS.get().forEach((n) => {
    nodesDS.update({ id: n.id, opacity: 1, hidden: false });
  });
  restoreAllEdges();
  clearHalo();
});

// Search node filtering
const searchInput = document.getElementById('searchInput');
searchInput.addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) {
    if (selectedId === null) {
      nodesDS.get().forEach((n) => nodesDS.update({ id: n.id, opacity: 1 }));
    }
    return;
  }
  const matches = [];
  initialNodes.forEach((n) => {
    if (n._label && n._label.toLowerCase().includes(q)) {
      matches.push(n.id);
    }
  });
  nodesDS.get().forEach((n) => {
    nodesDS.update({ id: n.id, opacity: matches.includes(n.id) ? 1 : 0.1 });
  });
  if (matches.length > 0) {
    net.focus(matches[0], {
      scale: 1.6,
      animation: { duration: 600, easingFunction: 'easeInOutQuad' }
    });
  }
});

// Interactive legend category toggling
document.querySelectorAll('.legend-item').forEach((item) => {
  item.addEventListener('click', () => {
    const type = item.getAttribute('data-type');
    if (activeTypes.has(type)) {
      activeTypes.delete(type);
      item.style.opacity = '0.3';
    } else {
      activeTypes.add(type);
      item.style.opacity = '1';
    }

    initialNodes.forEach((n) => {
      if (!n._isPaper) {
        nodesDS.update({ id: n.id, hidden: !activeTypes.has(n._type) });
      }
    });
  });
});
</script>
</body>
</html>`;

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = opts.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
