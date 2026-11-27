// OPS-1: the "Agent system" view (docs/design/staff/, the 8.25.57 PM and 8.26.08 PM screenshots).
// The nodes and edges are what the gateway generates from the tool scopes and the model constants;
// where each box sits is the layout table (layout.json), and every line is routed from it
// (mapLayout.ts). Nothing here names an agent, a tool or a model. The traces are sample data.
import { useState } from "react";
import { KIND_LABEL } from "./format";
import { type Box, pathOf, place, route, type Placed } from "./mapLayout";
import { OutcomePill, SampleBadge } from "./parts";
import type { AgentMap, MapNode, SampleTrace, SystemView } from "./types";

const BADGE: Record<MapNode["kind"], string | null> = {
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

function Node({ node, box, light }: { node: MapNode; box: Box; light: Light }) {
  const fixed = BADGE[node.kind];
  return (
    <div
      className={`map-node map-node-${node.kind}${lightClass(light)}`}
      data-node={node.id}
      aria-current={light === "lit" ? "step" : undefined}
      style={{ left: box.x, top: box.y, width: box.w, height: box.h }}
    >
      <p className="map-node-title">{node.title}</p>
      {node.subtitle && <p className="map-node-sub">{node.subtitle}</p>}
      {(node.tools.length > 0 || node.collapsed.length > 0) && (
        <p className="map-node-tools">
          {node.tools.join(", ")}
          {node.collapsed.map((group) => (
            <span key={group.label} className="map-node-more" title={group.tools.join(", ")}>
              {" "}
              + {group.label}
            </span>
          ))}
        </p>
      )}
      {(node.models.length > 0 || fixed) && (
        <div className="node-badges">
          {node.models.map((model) => (
            <span key={model} className="node-badge">
              AI · {model}
            </span>
          ))}
          {fixed && <span className={`node-badge node-badge-${node.kind}`}>{fixed}</span>}
        </div>
      )}
    </div>
  );
}

function Canvas({ map, placed, lit }: { map: AgentMap; placed: Placed; lit: Set<string> | null }) {
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  const routes = route(map.edges, placed);
  const nodeLight = (id: string): Light => (lit === null ? null : lit.has(id) ? "lit" : "dim");
  const edgeLight = (from: string, to: string): Light => (lit === null ? null : lit.has(from) && lit.has(to) ? "lit" : "dim");
  return (
    <div className="map-canvas" style={{ width: placed.canvasWidth, height: placed.height }}>
      {placed.rows
        .filter((row) => row.title !== null)
        .map((row) => (
          <p key={row.id} className="map-row-title" style={{ top: row.titleY }}>
            {row.title}
          </p>
        ))}
      <svg className="map-lines" width={placed.canvasWidth} height={placed.height} aria-hidden="true">
        <defs>
          <marker id="map-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        {routes.map(({ edge, points, label, route: kind }) => (
          <g key={`${edge.from}->${edge.to}`} className={`map-edge map-edge-${kind}${lightClass(edgeLight(edge.from, edge.to))}`} data-edge={`${edge.from}->${edge.to}`}>
            <path d={pathOf(points)} fill="none" strokeDasharray={edge.dashed ? "5 4" : undefined} markerEnd="url(#map-arrow)" />
            {edge.label && label && (
              <text x={label.x} y={label.y} textAnchor={label.anchor}>
                {edge.label}
              </text>
            )}
          </g>
        ))}
      </svg>
      {map.nodes.map((node) => {
        const box = placed.boxes.get(node.id);
        return box ? <Node key={node.id} node={node} box={box} light={nodeLight(node.id)} /> : null;
      })}
      <ul className="visually-hidden" aria-label="Connections">
        {map.edges.map((edge) => (
          <li key={`${edge.from}->${edge.to}`}>
            {byId.get(edge.from)?.title ?? edge.from} → {byId.get(edge.to)?.title ?? edge.to}
            {edge.label ? ` (${edge.label})` : ""}
          </li>
        ))}
      </ul>
    </div>
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
  const placed = place(map.nodes.map((n) => n.id));
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
          <Canvas map={map} placed={placed} lit={lit} />
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
