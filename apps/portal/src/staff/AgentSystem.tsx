// OPS-1: the "Agent system" view (docs/design/staff/, the first two screenshots). The map is drawn
// from what the gateway generates from the tool scopes and the model constants; nothing here names
// an agent, a tool or a model. The traces are sample data and say so.
import { useState } from "react";
import { KIND_LABEL } from "./format";
import { OutcomePill, SampleBadge } from "./parts";
import type { AgentMap, MapLane, MapNode, SampleTrace, SystemView } from "./types";

const BADGE: Record<MapNode["kind"], string | null> = {
  entry: null,
  ai: null,
  mcp: "MCP",
  code: "Code",
  fake: "Fake",
  eval: null,
};

function Badges({ node }: { node: MapNode }) {
  const fixed = BADGE[node.kind];
  return (
    <div className="node-badges">
      {node.models.map((model) => (
        <span key={model} className="node-badge">
          AI · {model}
        </span>
      ))}
      {fixed && <span className={`node-badge node-badge-${node.kind}`}>{fixed}</span>}
    </div>
  );
}

function Node({ node, lit }: { node: MapNode; lit: boolean | null }) {
  const state = lit === null ? "" : lit ? " is-lit" : " is-dim";
  return (
    <div className={`map-node map-node-${node.kind}${state}`} data-node={node.id} aria-current={lit ? "step" : undefined}>
      <p className="map-node-title">{node.title}</p>
      {node.subtitle && <p className="map-node-sub">{node.subtitle}</p>}
      {node.tools.length > 0 && <p className="map-node-tools">{node.tools.join(", ")}</p>}
      <Badges node={node} />
    </div>
  );
}

function callers(map: AgentMap, byId: Map<string, MapNode>, server: string): string {
  const names = map.edges
    .filter((edge) => edge.to === server)
    .map((edge) => byId.get(edge.from)?.title)
    .filter((name): name is string => name !== undefined);
  return names.length > 0 ? `from ${names.join(" · ")}` : "";
}

function Lane({ lane, map, byId, lit }: { lane: MapLane; map: AgentMap; byId: Map<string, MapNode>; lit: Set<string> | null }) {
  const state = (id: string) => (lit === null ? null : lit.has(id));
  return (
    <section className="map-lane" aria-label={lane.title ?? "Shopper chat"}>
      {lane.title && <h3 className="map-lane-title">{lane.title}</h3>}
      <div className="map-flow">
        {lane.steps.map((column, index) => (
          <div className="map-step" key={column.join("+")}>
            {index > 0 && (
              <span className="map-arrow" aria-hidden="true">
                →
              </span>
            )}
            <div className="map-column">
              {column.map((id) => {
                const node = byId.get(id);
                return node ? <Node key={id} node={node} lit={state(id)} /> : null;
              })}
            </div>
          </div>
        ))}
      </div>
      {lane.servers.length > 0 && (
        <div className="map-servers">
          {lane.servers.map((id) => {
            const node = byId.get(id);
            if (!node) return null;
            return (
              <div key={id} className="map-server">
                <p className="map-server-from">{callers(map, byId, id)}</p>
                <Node node={node} lit={state(id)} />
              </div>
            );
          })}
        </div>
      )}
    </section>
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
      <span className="trace-meta">
        {[...trace.refs, `${trace.steps.length} steps`].join(" · ")}
      </span>
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
          <span className="muted">Full architecture</span>
        </div>
        {map.lanes.map((lane) => (
          <Lane key={lane.id} lane={lane} map={map} byId={byId} lit={lit} />
        ))}
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
