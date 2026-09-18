import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlow, Background, Controls, Panel, Handle, Position } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StatusDot } from "../../components/ui/Badge.jsx";

const ORIGIN_X = -320;
const LAYER_X = { bronze: 40, silver: 760, gold: 1100 };
const LAYER_COLOR = { bronze: "#a9702f", silver: "#5b7a94", gold: "#c98a1c" };
const TEST_COLOR = { passed: "#2f9e6e", failed: "#c53d3d", none: "#c7cdd3" };
const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };
// Module 18 §7 UX — deliberately reduced (for now) to just the two stages the guided "+"
// popup actually configures (see StructurationPanel's WIZARD_STAGES). 03/04/05
// (standardized/annotated/validated/quarantine) are dropped from the canvas, not the
// platform: still fully configurable from the bronze dataset's own "Structuration" tab
// (DatasetPanel, unconditional full view) — this only trims what shows up here.
const PREVIEW_X = { unpacked: 220, typed: 460 };

function OriginNode({ data }) {
  return (
    <div
      className="card"
      style={{
        padding: "10px 14px", minWidth: 190, border: "1.5px dashed var(--text-muted)",
        boxShadow: data.selected ? "0 0 0 2px var(--ember)" : "none", cursor: "pointer", background: "var(--bg)",
      }}
      onClick={data.onClick}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>
          {data.sourceTypeLabel}
        </div>
        {data.provenance?.watched && (
          <span className="badge badge-accent" style={{ fontSize: 9.5, padding: "1px 6px" }} title={data.watchedTitle}>{data.watchedLabel}</span>
        )}
      </div>
      <div style={{ fontWeight: 600, fontSize: 13.5, marginTop: 2, fontFamily: "var(--font-m)" }}>{data.name}</div>
      {data.provenance && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
          {data.provenance.file} · {data.provenance.row_count ?? "—"} {data.rowsWord}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

function DatasetNode({ data }) {
  const { t } = useTranslation();
  return (
    <div
      className="card"
      style={{
        padding: "10px 14px", minWidth: 190, borderLeft: `3px solid ${LAYER_COLOR[data.layer]}`,
        boxShadow: data.selected ? "0 0 0 2px var(--ember)" : "0 1px 3px rgba(0,0,0,.08)", cursor: "pointer",
        position: "relative",
      }}
      onClick={data.onClick}
    >
      {data.layer === "bronze" && data.payloadBacked && (
        // Module 6 extension (payload & structuration) — a shortcut straight to profiling,
        // available the moment the bronze node exists. Module 18 §7 UX — its guided popup is
        // what actually produces the "unpacked"/"typed" preview nodes below, once saved.
        <button
          type="button"
          title="Application data quality"
          onClick={(e) => { e.stopPropagation(); data.onOpenStructuration?.(); }}
          style={{
            position: "absolute", top: -9, right: -9, width: 20, height: 20, borderRadius: "50%",
            border: "1.5px solid var(--ember)", background: "var(--ember-soft)", color: "var(--ember)",
            fontSize: 14, lineHeight: "17px", fontWeight: 700, cursor: "pointer", padding: 0,
          }}
        >
          +
        </button>
      )}
      {data.onCreateStandardized && (
        // UX ask — a shortcut straight from "02 typed" into authoring 03_standardized_<name>
        // as a real silver dataset (DatasetPanel, pre-seeded: upstream = this bronze, a
        // starter SELECT off {{ ref('02_typed_<name>') }}) — the SQL editor's own "Tables
        // disponibles" column-click-to-insert already works the moment that upstream is
        // checked, no extra wiring needed here.
        <button
          type="button"
          title={t("medallion.panel.createStandardizedHint")}
          onClick={(e) => { e.stopPropagation(); data.onCreateStandardized(); }}
          style={{
            position: "absolute", top: -9, right: -9, width: 20, height: 20, borderRadius: "50%",
            border: "1.5px solid var(--ember)", background: "var(--ember-soft)", color: "var(--ember)",
            fontSize: 14, lineHeight: "17px", fontWeight: 700, cursor: "pointer", padding: 0,
          }}
        >
          +
        </button>
      )}
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>{data.layer}</div>
        {data.previewStageLabel && (
          // Module 18 §7 UX — silver.01_unpacked_<name>/silver.02_typed_<name>
          // (materialize_unpacked_typed_sync): a real silver node, same design as any other,
          // just flagged ember/orange since it's an instant preview, not a registered dataset.
          // "standardized" is the one exception — a real, registered 03_standardized_<name>
          // dataset (built normally, never synchronously previewed), tagged the same way purely
          // for visual continuity along the 01->02->03 chain, so it gets its own tooltip text.
          <span
            className="badge"
            title={t(data.previewStageLabel === "standardized" ? "medallion.structuration.standardizedBadgeHint" : "medallion.structuration.instantPreviewHint")}
            style={{ fontSize: 9.5, padding: "1px 6px", border: "1px solid var(--ember)", color: "var(--ember)", background: "var(--ember-soft)" }}
          >
            {data.previewStageLabel}
          </span>
        )}
        {data.transformType === "python" && (
          <span className="badge badge-accent" style={{ fontSize: 9.5, padding: "1px 6px" }}>{data.mlObjectiveLabel}</span>
        )}
        {data.layer === "gold" && data.published && (
          <span className="badge badge-accent" style={{ fontSize: 9.5, padding: "1px 6px" }} title={data.publishedLabel}>{data.publishedLabel}</span>
        )}
        {data.layer === "gold" && data.dashboard && (
          <span className="badge badge-neutral" style={{ fontSize: 9.5, padding: "1px 6px" }} title={data.dashboardTitle}>{data.dashboardLabel}</span>
        )}
      </div>
      <div style={{ fontWeight: 600, fontSize: 13.5, marginTop: 2 }}>{data.name}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, fontSize: 11.5, color: "var(--text-muted)" }}>
        <span>{data.lastRowCount != null ? data.rowsLabel : "—"}</span>
        {data.layer !== "bronze" && !data.isInstantPreview && (
          <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <StatusDot color={TEST_COLOR[data.testStatus] || TEST_COLOR.none} /> {data.testsLabel}
          </span>
        )}
        {data.layer === "gold" && data.qualityDegraded && (
          <span style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--danger)" }}>
            <StatusDot color="var(--danger)" /> {data.qualityLabel}
          </span>
        )}
      </div>
      {data.layer === "gold" && data.dashboard && (
        <div style={{ fontSize: 10.5, color: "var(--text-muted)", marginTop: 4, fontFamily: "var(--font-m)" }}>{data.dashboardDate}</div>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { dataset: DatasetNode, origin: OriginNode };

// UX ask — the auto-layout (fixed columns per layer) gets cramped once a project has more
// than a couple of datasets per layer; dragging a node now sticks (previously any re-render —
// e.g. just selecting a different node — recomputed every position from scratch and snapped
// dragged nodes right back). Kept per-project in localStorage, a per-viewer convenience: never
// synced, never read by anything else, safe to lose (private window, cleared storage, …).
const positionsStorageKey = (projectId) => `medallion.canvasPositions.${projectId}`;
function loadPositionOverrides(projectId) {
  if (!projectId) return {};
  try {
    const raw = localStorage.getItem(positionsStorageKey(projectId));
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}
function savePositionOverrides(projectId, overrides) {
  if (!projectId) return;
  try {
    localStorage.setItem(positionsStorageKey(projectId), JSON.stringify(overrides));
  } catch {
    // private window, quota exceeded, etc. — a lost manual layout tweak is harmless
  }
}

// A real edge's source can only ever be another MedallionDataset's id (upstream_dataset_ids),
// never a synthetic 01_unpacked/02_typed preview node — so a hand-written silver dataset whose
// SQL actually reads {{ ref('02_typed_<name>') }} still only records its bronze as upstream.
// Purely for display, an edge from a structured bronze is rerouted through whichever synthetic
// stage node the target's own SQL text references, closest stage first — the real
// upstream_dataset_ids this reads from (sqlByDataset, passed down from ProjectDetail) never
// changes; a target with no match (e.g. reading `source('bronze', ...)` or `05_validated_...`
// directly) keeps the plain bronze->target edge exactly as before.
function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function rerouteThroughStage(bronzeNode, targetSql) {
  if (!targetSql) return null;
  const name = escapeRegExp(bronzeNode.name);
  if (new RegExp(`ref\\(['"]02_typed_${name}['"]\\)`).test(targetSql)) return `silver-typed-${bronzeNode.id}`;
  if (new RegExp(`ref\\(['"]01_unpacked_${name}['"]\\)`).test(targetSql)) return `silver-unpacked-${bronzeNode.id}`;
  return null;
}

export default function LineageCanvas({ nodes: rawNodes, edges: rawEdges, onSelect, selectedId, projectId, onOpenStructuration, onOpenSilverPreview, onCreateStandardized, qualityByDataset = {}, publishedByDataset = {}, dashboardByDataset = {}, sqlByDataset = {} }) {
  const { t, i18n } = useTranslation();
  const ML_OBJECTIVE_LABEL = t("medallion.mlObjectives", { returnObjects: true });
  const publishedLabel = t("medallion.publish.badge");
  const dashboardLabel = t("medallion.suggest.dashboardBadge");

  const [positionOverrides, setPositionOverrides] = useState(() => loadPositionOverrides(projectId));
  useEffect(() => { setPositionOverrides(loadPositionOverrides(projectId)); }, [projectId]);

  const handleNodeDragStop = (_evt, node) => {
    setPositionOverrides((prev) => {
      const next = { ...prev, [node.id]: { x: node.position.x, y: node.position.y } };
      savePositionOverrides(projectId, next);
      return next;
    });
  };
  const resetLayout = () => {
    setPositionOverrides({});
    savePositionOverrides(projectId, {});
  };

  const { nodes, edges } = useMemo(() => {
    const origins = rawNodes.filter((n) => n.node_type === "origin");
    const byLayer = { bronze: [], silver: [], gold: [] };
    for (const n of rawNodes) if (n.node_type !== "origin") byLayer[n.layer]?.push(n);

    const flowNodes = [];
    origins.forEach((n, i) => {
      flowNodes.push({
        id: String(n.id),
        type: "origin",
        position: { x: ORIGIN_X, y: 20 + i * 100 },
        data: {
          name: n.name, sourceTypeLabel: SOURCE_TYPE_LABEL[n.source_type] || n.source_type,
          provenance: n.provenance, rowsWord: t("medallion.rowsWord"),
          watchedLabel: t("medallion.watched"),
          watchedTitle: n.provenance?.last_triggered_at
            ? t("medallion.watchedLastTrigger", { date: new Date(n.provenance.last_triggered_at).toLocaleString(i18n.language) })
            : t("medallion.watchedNoTriggerYet"),
          selected: selectedId === n.id, onClick: () => onSelect(n.id),
        },
      });
    });
    for (const layer of ["bronze", "silver", "gold"]) {
      byLayer[layer].forEach((n, i) => {
        flowNodes.push({
          id: String(n.id),
          type: "dataset",
          position: { x: LAYER_X[layer], y: 20 + i * 100 },
          data: {
            layer: n.layer, name: n.name, lastRowCount: n.last_row_count, testStatus: n.last_test_status,
            transformType: n.transform_type, mlObjectiveLabel: ML_OBJECTIVE_LABEL[n.ml_objective] || "ML",
            rowsLabel: t("medallion.rows", { count: n.last_row_count }), testsLabel: t("medallion.tests"), qualityLabel: t("medallion.quality.degraded"),
            qualityDegraded: qualityByDataset[n.id]?.degraded,
            published: !!publishedByDataset[n.id], publishedLabel,
            dashboard: !!dashboardByDataset[n.id], dashboardLabel,
            dashboardTitle: dashboardByDataset[n.id]?.last_generated_at ? new Date(dashboardByDataset[n.id].last_generated_at).toLocaleString(i18n.language) : undefined,
            dashboardDate: dashboardByDataset[n.id]?.last_generated_at ? new Date(dashboardByDataset[n.id].last_generated_at).toLocaleDateString(i18n.language) : "",
            payloadBacked: n.payload_backed, onOpenStructuration: () => onOpenStructuration?.(n.id, null, true),
            // UX ask — same orange "stage" badge as the 01_unpacked/02_typed synthetic preview
            // nodes, for visual continuity along the whole 01->02->03 chain: unlike those two,
            // this is a real, registered silver dataset (the "+" on 02_typed), not an instant
            // preview — detected by name, the same "03_standardized_<bronze>" convention
            // dbt_project.py itself keys off to resolve which model 04_annotated reads.
            previewStageLabel: n.layer === "silver" && n.name.startsWith("03_standardized_") ? "standardized" : undefined,
            selected: selectedId === n.id, onClick: () => onSelect(n.id),
          },
        });
      });
    }
    const edgeStyle = (dashed) => (dashed
      ? { stroke: "var(--text-muted)", strokeWidth: 1.5, strokeDasharray: "4 3" }
      : { stroke: "var(--border)", strokeWidth: 1.5 });
    const flowEdges = [];

    // Module 18 §7 UX — nothing shows up here until a contract is actually saved (n.structured,
    // set once materialize_unpacked_typed_sync has run at least once) — never merely because
    // the bronze happens to be payload-backed, and never gated on a downstream silver existing
    // either: the "+" popup's own save is what creates silver.01_unpacked_<name>/silver.02_typed_<name>,
    // so that's the only thing this waits on. Purely additive, client-side only — doesn't
    // touch or replace the real bronze->silver edges below.
    const structuredBronzeById = {};
    for (const n of byLayer.bronze) {
      if (!n.structured) continue;
      structuredBronzeById[n.id] = n;
      const bronzeNode = flowNodes.find((fn) => fn.id === String(n.id));
      const y = bronzeNode.position.y;
      const silverUnpackedId = `silver-unpacked-${n.id}`;
      const silverTypedId = `silver-typed-${n.id}`;
      flowNodes.push({
        id: silverUnpackedId,
        type: "dataset",
        position: { x: PREVIEW_X.unpacked, y },
        data: {
          layer: "silver", name: `01_unpacked_${n.name}`, lastRowCount: null, testsLabel: t("medallion.tests"),
          previewStageLabel: "unpacked", isInstantPreview: true,
          selected: false, onClick: () => onOpenSilverPreview?.(n.id, "unpacked"),
        },
      });
      flowNodes.push({
        id: silverTypedId,
        type: "dataset",
        position: { x: PREVIEW_X.typed, y },
        data: {
          layer: "silver", name: `02_typed_${n.name}`, lastRowCount: null, testsLabel: t("medallion.tests"),
          previewStageLabel: "typed", isInstantPreview: true,
          selected: false, onClick: () => onOpenSilverPreview?.(n.id, "typed"),
          onCreateStandardized: onCreateStandardized ? () => onCreateStandardized(n.id, n.name) : undefined,
        },
      });
      flowEdges.push({ id: `${n.id}-${silverUnpackedId}`, source: String(n.id), target: silverUnpackedId, animated: false, style: edgeStyle(true) });
      flowEdges.push({ id: `${silverUnpackedId}-${silverTypedId}`, source: silverUnpackedId, target: silverTypedId, animated: false, style: edgeStyle(true) });
    }

    rawEdges.forEach((e) => {
      const structuredBronze = structuredBronzeById[e.source];
      const reroutedSource = structuredBronze ? rerouteThroughStage(structuredBronze, sqlByDataset[e.target]) : null;
      flowEdges.push({
        id: `${e.source}-${e.target}`, source: reroutedSource || String(e.source), target: String(e.target),
        animated: false, style: edgeStyle(e.source < 0),
      });
    });

    // Manually-dragged positions win over the computed layout, applied as a final pass so
    // every node-pushing branch above stays oblivious to it.
    const positionedNodes = flowNodes.map((n) => (positionOverrides[n.id] ? { ...n, position: positionOverrides[n.id] } : n));
    return { nodes: positionedNodes, edges: flowEdges };
  }, [rawNodes, rawEdges, selectedId, onOpenStructuration, onOpenSilverPreview, onCreateStandardized, qualityByDataset, publishedByDataset, dashboardByDataset, sqlByDataset, publishedLabel, dashboardLabel, i18n.language, t, positionOverrides]);

  const hasCustomLayout = Object.keys(positionOverrides).length > 0;

  return (
    <div style={{ height: 480, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView proOptions={{ hideAttribution: true }} onNodeDragStop={handleNodeDragStop}>
        <Background color="var(--border)" gap={18} />
        <Controls showInteractive={false} />
        {hasCustomLayout && (
          <Panel position="top-right">
            <button type="button" className="btn-ghost" style={{ padding: "5px 10px", fontSize: 11.5, background: "var(--surface)" }} onClick={resetLayout}>
              {t("medallion.canvasResetLayout")}
            </button>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}
