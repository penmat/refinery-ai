import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  type NodeProps,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import {
  Check,
  Circle,
  AlertCircle,
  ArrowUpRight,
  GitBranch,
  HelpCircle,
} from "lucide-react";
import { label, nodeStatus, type State, type Topology } from "./state";
import "@xyflow/react/dist/style.css";
function WorkflowNode({ data }: NodeProps) {
  const status = String(data.status);
  const Icon =
    status === "completed"
      ? Check
      : status === "unconfirmed"
        ? HelpCircle
        : status === "not visited"
          ? Circle
          : AlertCircle;
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Inspect ${String(data.label)}`}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.currentTarget.click();
        }
      }}
      className={`workflow-node status-${status.replaceAll(" ", "-")} ${data.selected ? "is-selected" : ""}`}
    >
      <Handle type="target" position={Position.Top} />
      <div className="node-heading">
        <span className="node-icon">
          <Icon size={15} />
        </span>
        <strong>{String(data.label)}</strong>
        {data.childCount ? <ArrowUpRight size={14} /> : null}
      </div>
      <div className="node-meta">
        <span>
          {status === "unconfirmed" ? "Started · status unconfirmed" : status}
        </span>
        {Number(data.visits) > 0 && (
          <span>
            {String(data.visits)}{" "}
            {Number(data.visits) === 1 ? "visit" : "visits"}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
const nodeTypes = { workflow: WorkflowNode };
export function WorkflowGraph({
  topology,
  state,
  selected,
  onSelect,
  childrenByNode,
}: {
  topology: Topology;
  state: State;
  selected: string | null;
  onSelect: (id: string) => void;
  childrenByNode: Record<string, number>;
}) {
  const positions = useMemo(() => {
    const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
    graph.setGraph({
      rankdir: "TB",
      nodesep: 45,
      ranksep: 48,
      marginx: 30,
      marginy: 20,
    });
    topology.nodes.forEach((n) =>
      graph.setNode(n.id, { width: 220, height: 76 }),
    );
    topology.edges.forEach((e) => graph.setEdge(e.source, e.target));
    dagre.layout(graph);
    return Object.fromEntries(
      topology.nodes.map((n) => {
        const p = graph.node(n.id);
        return [n.id, { x: p.x - 110, y: p.y - 38 }];
      }),
    );
  }, [topology]);
  const nodes = topology.nodes.map((n) => ({
    id: n.id,
    type: "workflow",
    position: positions[n.id],
    data: {
      label:
        n.id === "__start__"
          ? "Start"
          : n.id === "__end__"
            ? "End"
            : label(n.id),
      status: nodeStatus(state, n.id),
      visits: state.events.filter(
        (e) => e.phase === n.id && e.lifecycle === "started",
      ).length,
      selected: n.id === selected,
      childCount: childrenByNode[n.id] ?? 0,
    },
  }));
  const edges = topology.edges.map((e, i) => {
    const observed = state.transitions.includes(`${e.source}→${e.target}`);
    return {
      id: `edge-${i}`,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      style: {
        stroke: observed ? "#437764" : "#c9cec7",
        strokeWidth: observed ? 2.1 : 1.2,
        strokeDasharray: !observed && e.conditional ? "4 5" : undefined,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: observed ? "#437764" : "#bbc2b9",
      },
    };
  });
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.18 }}
      minZoom={0.2}
      maxZoom={1.8}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      onNodeClick={(_, node) => onSelect(node.id)}
      proOptions={{ hideAttribution: false }}
    >
      <Background color="#cfd3ca" gap={22} size={1} />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        nodeColor="#c5d5cb"
        maskColor="rgba(245,247,242,.7)"
      />
    </ReactFlow>
  );
}
