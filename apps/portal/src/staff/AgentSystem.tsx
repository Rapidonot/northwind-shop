// OPS-1: the "Agent system" view (docs/design/staff/staff-01-agent-system.png and staff-02-agent-system-traces.png):
// one compact SVG. The nodes and edges are what the gateway generates from the tool scopes and the
// model constants; each box and each elbow route is the layout table's (layout.json, measured from
// the reference). Nothing here names an agent, a tool or a model. The traces are sample data.
import { useState } from "react";
import { KIND_LABEL } from "./format";
import { type Box, chipWidth, edgeKey, LANE_TITLES, MCP_BAR, MIN_WIDTH, nodeText, pathOf, ROUTES, SEPARATORS, SLOTS, TYPE, VIEW_BOX } from "./mapLayout";
import { OutcomePill, SampleBadge } from "./parts";
import type { AgentMap, MapEdge, MapNode, SampleTrace, SystemView } from "./types";

const CHIP: Record<MapNode["kind"], string | null> = {
  entry: null,
  ai: null,
  mcp: "MCP",
  code: "Code",
  fake: "Fake",
  eval: null,
  end: null,
};

/** Lit, dimmed, or neither: only a selected trace dims anything. */
type Light = "lit" | "dim" | null;

const lightClass = (light: Light) => (light === "lit" ? " is-lit" : light === "dim" ? " is-dim" : "");


function Lines({ lines, x, y, size, step, className }: { lines: string[]; x: number; y: number; size: number; step: number; className: string }) {
  return (
    <text className={className} x={x} y={y} fontSize={size}>
      {lines.map((line, i) => (
        <tspan key={i} x={x} dy={i === 0 ? 0 : step}>
          {line}
        </tspan>
      ))}
    </text>
  );
}

export function chipLabels(node: MapNode): string[] {
  return [...node.models.map((m) => `AI · ${m}`), ...(CHIP[node.kind] ? [CHIP[node.kind]!] : [])];
}

function Chips({ node, box, y, right }: { node: MapNode; box: Box; y: number; right: boolean }) {
  const labels = chipLabels(node);
  const widths = labels.map(chipWidth);
  const row = widths.reduce((sum, w) => sum + w + 6, -6);
  const left = right ? box.x + box.w - TYPE.pad - row : box.x + TYPE.pad + (node.kind === "mcp" ? MCP_BAR : 0);
  const xs = widths.map((_, i) => left + widths.slice(0, i).reduce((sum, w) => sum + w + 6, 0));
  return (
    <>
      {labels.map((label, i) => (
        <g key={label} className={`map-chip map-chip-${label.startsWith("AI") ? "ai" : node.kind}`}>
          <rect x={xs[i]} y={y} width={widths[i]} height={TYPE.chipHeight} rx={2} />
          <text x={xs[i]! + widths[i]! / 2} y={y + 13} fontSize={TYPE.chip} textAnchor="middle">
            {label}
          </text>
        </g>
      ))}
    </>
  );
}

function Node({ node, box, light }: { node: MapNode; box: Box; light: Light }) {
  const t = nodeText(node, box);
  const names = node.tools.length > 0 ? t.tools.length - node.collapsed.length : 0;
  return (
    <g className={`map-node map-node-${node.kind}${lightClass(light)}`} data-node={node.id} aria-current={light === "lit" ? "step" : undefined}>
      <rect className="map-node-box" x={box.x} y={box.y} width={box.w} height={box.h} />
      {node.kind === "mcp" && <rect className="map-node-bar" x={box.x} y={box.y} width={MCP_BAR} height={box.h} />}
      <Lines className={node.kind === "mcp" ? "map-node-title is-mono" : "map-node-title"} lines={t.title} x={t.x} y={t.titleY} size={t.titleSize} step={t.titleSize + 3} />
      {t.sub.length > 0 && <Lines className="map-node-sub" lines={t.sub} x={t.x} y={t.subY} size={TYPE.description} step={TYPE.description + 3} />}
      {t.tools.length > 0 && (
        <text className="map-node-tools" x={t.x} y={t.toolsY} fontSize={TYPE.tools}>
          {t.tools.slice(0, names).map((line, i) => (
            <tspan key={i} x={t.x} dy={i === 0 ? 0 : TYPE.tools + 3}>
              {line}
            </tspan>
          ))}
          {node.collapsed.map((group, i) => (
            <tspan key={group.label} className="map-node-more" x={t.x} dy={names === 0 && i === 0 ? 0 : TYPE.tools + 3}>
              <title>{group.tools.join(", ")}</title>+ {group.label}
            </tspan>
          ))}
        </text>
      )}
      <Chips node={node} box={box} y={t.chipY} right={t.chipsRight} />
    </g>
  );
}

/** A label shared by several edges from one source ("code port") is drawn once, where the layout
 * table gives it a place; every edge keeps its label as its line's tooltip. */
function visibleLabels(edges: readonly MapEdge[]): Set<string> {
  const shown = new Set<string>();
  const groups = new Set<string>();
  for (const edge of edges) {
    if (!edge.label || !ROUTES[edgeKey(edge)]?.label) continue;
    const group = `${edge.from}|${edge.label}`;
    if (groups.has(group)) continue;
    groups.add(group);
    shown.add(edgeKey(edge));
  }
  return shown;
}

function Canvas({ map, lit }: { map: AgentMap; lit: Set<string> | null }) {
  const [width, height] = VIEW_BOX;
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  const labelled = visibleLabels(map.edges);
  const nodeLight = (id: string): Light => (lit === null ? null : lit.has(id) ? "lit" : "dim");
  const edgeLight = (e: MapEdge): Light => (lit === null ? null : lit.has(e.from) && lit.has(e.to) ? "lit" : "dim");
  return (
    <>
      <svg className="map-svg" viewBox={`0 0 ${width} ${height}`} width="100%" style={{ minWidth: MIN_WIDTH }} role="img" aria-label="Agent system map">
        <defs>
          <marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        {SEPARATORS.map((y) => (
          <line key={y} className="map-separator" x1={0} x2={width} y1={y} y2={y} />
        ))}
        {LANE_TITLES.map((t) => (
          <text key={t.text} className="map-lane-title" x={t.x} y={t.y} fontSize={TYPE.description}>
            {t.text}
          </text>
        ))}
        {map.edges.map((edge) => {
          const key = edgeKey(edge);
          const route = ROUTES[key];
          if (!route) return null;
          return (
            <g key={key} className={`map-edge${lightClass(edgeLight(edge))}`} data-edge={key}>
              <title>
                {byId.get(edge.from)?.title ?? edge.from} → {byId.get(edge.to)?.title ?? edge.to}
                {edge.label ? ` (${edge.label})` : ""}
              </title>
              <path d={pathOf(route.points)} fill="none" strokeDasharray={edge.dashed ? "5 4" : undefined} markerEnd="url(#map-arrow)" />
              {edge.label && route.label && labelled.has(key) && (
                <text className="map-edge-label" x={route.label.x} y={route.label.y} textAnchor={route.label.anchor} fontSize={TYPE.tools}>
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}
        {map.nodes.map((node) => {
          const box = SLOTS[node.id];
          return box ? <Node key={node.id} node={node} box={box} light={nodeLight(node.id)} /> : null;
        })}
      </svg>
      <ul className="visually-hidden" aria-label="Connections">
        {map.edges.map((edge) => (
          <li key={edgeKey(edge)}>
            {byId.get(edge.from)?.title ?? edge.from} → {byId.get(edge.to)?.title ?? edge.to}
            {edge.label ? ` (${edge.label})` : ""}
          </li>
        ))}
      </ul>
    </>
  );
}

export function Legend({ kinds }: { kinds: AgentMap["legend"] }) {
  return (
    <ul className="legend" aria-label="Legend">
      {kinds.map((kind) => (
        <li key={kind}>
          <span className={`legend-key legend-key-${kind}`} aria-hidden="true" />
          {KIND_LABEL[kind]}
        </li>
      ))}
    </ul>
  );
}

function TraceCard({ trace, selected, onSelect }: { trace: SampleTrace; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={selected ? "trace-card is-selected" : "trace-card"} aria-pressed={selected} onClick={onSelect}>
      <span className="trace-card-head">
        <span className="trace-kind">{trace.kind}</span>
        <span className="trace-pills">
          {trace.no_ai && <span className="pill pill-strong">No AI on this path</span>}
          <OutcomePill outcome={trace.outcome} />
        </span>
      </span>
      <span className="trace-title">{trace.title}</span>
      <span className="trace-meta">{[...trace.refs, `${trace.steps.length} steps`].join(" · ")}</span>
    </button>
  );
}

function StepList({ trace, byId }: { trace: SampleTrace; byId: Map<string, MapNode> }) {
  return (
    <div className="trace-steps">
      <h3>
        {trace.kind} <SampleBadge />
      </h3>
      <ol>
        {trace.steps.map((step, index) => (
          <li key={`${step.node}-${index}`}>
            <span className="trace-step-node">{byId.get(step.node)?.title ?? step.node}</span>
            <span className="trace-step-label">{step.label}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AgentSystem({ view }: { view: SystemView }) {
  const { map, traces } = view;
  const [selected, setSelected] = useState<string | null>(null);
  const byId = new Map(map.nodes.map((node) => [node.id, node]));
  const trace = traces.find((t) => t.id === selected) ?? null;
  const lit = trace ? new Set(trace.steps.map((step) => step.node)) : null;

  return (
    <>
      <header className="view-head">
        <div>
          <h1>Agent system</h1>
          <p className="muted">Read-only architecture: AI agents, MCP servers, deterministic code and fake services, generated from the code.</p>
        </div>
      </header>
      <div className="map">
        <div className="map-legend-bar">
          <Legend kinds={map.legend} />
          {trace ? (
            <button type="button" className="btn btn-outline" onClick={() => setSelected(null)}>
              Full architecture
            </button>
          ) : (
            <span className="muted">Full architecture</span>
          )}
        </div>
        <div className="map-scroll">
          <Canvas map={map} lit={lit} />
        </div>
      </div>
      <p className="callout">Money and stock are decided by code, never by a model.</p>
      <section className="traces" aria-label="Trace one action">
        <h2>
          Trace one action <SampleBadge />
        </h2>
        <div className="trace-grid">
          {traces.map((t) => (
            <TraceCard key={t.id} trace={t} selected={t.id === selected} onSelect={() => setSelected(t.id === selected ? null : t.id)} />
          ))}
        </div>
        {trace ? (
          <StepList trace={trace} byId={byId} />
        ) : (
          <p className="trace-hint">Select a sample action above to highlight its path on the diagram and inspect its step list.</p>
        )}
      </section>
    </>
  );
}
