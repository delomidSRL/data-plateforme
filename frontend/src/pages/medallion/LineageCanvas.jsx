import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlow, Background, Controls, Handle, Position } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as structurationApi from "../../api/structuration.js";

const ORIGIN_X = -320;
const LAYER_X = { bronze: 40, silver: 380, gold: 720 };
const STRUCTURATION_X = (LAYER_X.bronze + LAYER_X.silver) / 2;
const LAYER_COLOR = { bronze: "#a9702f", silver: "#5b7a94", gold: "#c98a1c" };
const TEST_COLOR = { passed: "#2f9e6e", failed: "#c53d3d", none: "#c7cdd3" };
const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

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
        // available the moment the bronze node exists (unlike the synthetic "structuration"
        // node spliced into a bronze->silver edge below, which needs a silver to already
        // reference this bronze as upstream).
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
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--text-muted)", fontFamily: "var(--font-m)" }}>{data.layer}</div>
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
        {data.layer !== "bronze" && (
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

// Module 6 extension (payload & structuration) — a small synthetic node the canvas inserts
// between a payload-backed bronze and any silver reading it (never persisted — same "derived,
// computed client-side" spirit as OriginNode). Opens the profiling/quarantine popup on click;
// the quarantine badge is fetched lazily, once, straight from the endpoint the panel itself
// uses — best-effort, never blocks rendering the node.
function StructurationNode({ data }) {
  const { t } = useTranslation();
  const [quarantineCount, setQuarantineCount] = useState(null);

  useEffect(() => {
    let alive = true;
    structurationApi.getQuarantineSummary(data.projectId, data.bronzeId)
      .then((s) => { if (alive) setQuarantineCount(s.total_quarantined); })
      .catch(() => { if (alive) setQuarantineCount(null); });
    return () => { alive = false; };
  }, [data.projectId, data.bronzeId]);

  return (
    <div
      className="card"
      style={{
        padding: "8px 12px", minWidth: 130, textAlign: "center", cursor: "pointer",
        border: "1.5px solid var(--ember)", background: "var(--ember-soft)",
        boxShadow: data.selected ? "0 0 0 2px var(--ember)" : "none",
      }}
      onClick={data.onClick}
      title={t("medallion.structuration.canvasHint")}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 5, color: "var(--ember)" }}>
        {Icon.wand({ width: 13, height: 13 })}
        <span style={{ fontSize: 11.5, fontWeight: 600 }}>{data.label}</span>
      </div>
      {quarantineCount > 0 && (
        <span className="badge badge-danger" style={{ fontSize: 9.5, padding: "1px 6px", marginTop: 4, display: "inline-block" }}>
          {t("medallion.structuration.quarantineBadge", { count: quarantineCount })}
        </span>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { dataset: DatasetNode, origin: OriginNode, structuration: StructurationNode };

export default function LineageCanvas({ nodes: rawNodes, edges: rawEdges, onSelect, selectedId, projectId, onOpenStructuration, qualityByDataset = {}, publishedByDataset = {}, dashboardByDataset = {} }) {
  const { t, i18n } = useTranslation();
  const ML_OBJECTIVE_LABEL = t("medallion.mlObjectives", { returnObjects: true });
  const publishedLabel = t("medallion.publish.badge");
  const dashboardLabel = t("medallion.suggest.dashboardBadge");

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
            payloadBacked: n.payload_backed, onOpenStructuration: () => onOpenStructuration?.(n.id),
            selected: selectedId === n.id, onClick: () => onSelect(n.id),
          },
        });
      });
    }
    // Module 6 extension (payload & structuration) — every bronze->silver edge whose bronze
    // is payload-backed gets a small synthetic "structuration" node spliced in between (never
    // persisted, derived purely client-side — same spirit as origin nodes). A bronze feeding
    // several silvers gets exactly one such node, reused for every one of those edges.
    const byId = new Map(rawNodes.map((n) => [n.id, n]));
    const structurationNodeIdByBronze = new Map();
    const edgeStyle = (dashed) => (dashed
      ? { stroke: "var(--text-muted)", strokeWidth: 1.5, strokeDasharray: "4 3" }
      : { stroke: "var(--border)", strokeWidth: 1.5 });

    const flowEdges = [];
    rawEdges.forEach((e, i) => {
      const source = byId.get(e.source);
      const target = byId.get(e.target);
      if (source?.node_type === "dataset" && source.layer === "bronze" && source.payload_backed && target?.layer === "silver") {
        let structId = structurationNodeIdByBronze.get(e.source);
        if (!structId) {
          structId = `structuration-${e.source}`;
          structurationNodeIdByBronze.set(e.source, structId);
          const bronzeNode = flowNodes.find((n) => n.id === String(e.source));
          flowNodes.push({
            id: structId,
            type: "structuration",
            position: { x: STRUCTURATION_X, y: bronzeNode ? bronzeNode.position.y : 20 + i * 100 },
            data: {
              label: t("medallion.structuration.tab"), projectId, bronzeId: e.source,
              selected: false, onClick: () => onOpenStructuration?.(e.source),
            },
          });
        }
        flowEdges.push({ id: `${e.source}-${structId}`, source: String(e.source), target: structId, animated: false, style: edgeStyle(false) });
        flowEdges.push({ id: `${structId}-${e.target}`, source: structId, target: String(e.target), animated: false, style: edgeStyle(false) });
        return;
      }
      flowEdges.push({
        id: `${e.source}-${e.target}`, source: String(e.source), target: String(e.target),
        animated: false, style: edgeStyle(e.source < 0),
      });
    });
    return { nodes: flowNodes, edges: flowEdges };
  }, [rawNodes, rawEdges, selectedId, projectId, onOpenStructuration, qualityByDataset, publishedByDataset, dashboardByDataset, publishedLabel, dashboardLabel, i18n.language, t]);

  return (
    <div style={{ height: 480, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView proOptions={{ hideAttribution: true }}>
        <Background color="var(--border)" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
