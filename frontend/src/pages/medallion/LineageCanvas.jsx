import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReactFlow, Background, Controls, Handle, Position } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import * as structurationApi from "../../api/structuration.js";

const ORIGIN_X = -320;
const LAYER_X = { bronze: 40, silver: 760, gold: 1100 };
const LAYER_COLOR = { bronze: "#a9702f", silver: "#5b7a94", gold: "#c98a1c" };
const TEST_COLOR = { passed: "#2f9e6e", failed: "#c53d3d", none: "#c7cdd3" };
const SOURCE_TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };

// Module 18 §7 — the 01..05 declarative refinement chain, spliced client-side (never
// persisted) between a payload-backed bronze and each silver reading it. Same "derived,
// computed client-side" spirit as OriginNode / the old single structuration node it replaces.
// Names/order mirror the actual model files (01_unpacked_.. through 04_annotated_..); 04
// fans out into the two mirror-predicate terminals (05_validated_.., which silver reads, and
// 05_quarantine_.., a dead end kept for review — see render_validated_quarantine_models).
// `key` doubles as the stage id passed to onOpenStructuration — StructurationPanel uses it
// both to jump to the right popup block and to progressively reveal only the contract
// columns that stage has actually decided by then (see its STAGE_LEVEL).
const STRUCTURATION_STAGES = [
  { key: "unpacked", num: "01" },
  { key: "typed", num: "02" },
  { key: "standardized", num: "03" },
  { key: "annotated", num: "04" },
];
const STAGE_X = { unpacked: 160, typed: 280, standardized: 400, annotated: 520, validated: 640 };
const QUARANTINE_Y_OFFSET = 46;

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
        // available the moment the bronze node exists (unlike the 01..05 chain spliced into
        // a bronze->silver edge below, which needs a silver to already reference this bronze
        // as upstream).
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
        {data.previewStageLabel && (
          // Module 18 §7 UX — silver.typed_<name>/silver.structured_<name>
          // (materialize_typed_structured_sync): a real silver node, same design as any other,
          // just flagged ember/orange since it's an instant preview, not a registered dataset.
          <span
            className="badge" title={t("medallion.structuration.instantPreviewHint")}
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

// Module 18 §7 — one pill per 01..04 stage, plus the "05 validated" terminal that feeds
// silver (tone "success"). Static: no fetch, just a label and an explanatory tooltip: every
// stage in the chain opens the same structuration popup on click, so there's nothing stage-
// specific to load here.
function StructurationStageNode({ data }) {
  const success = data.tone === "success";
  const color = success ? TEST_COLOR.passed : "var(--ember)";
  return (
    <div
      className="card"
      style={{
        padding: "6px 10px", minWidth: 100, textAlign: "center", cursor: "pointer",
        border: `1.5px solid ${color}`, background: success ? "rgba(47,158,110,.08)" : "var(--ember-soft)",
        boxShadow: data.selected ? "0 0 0 2px var(--ember)" : "none",
      }}
      onClick={data.onClick}
      title={data.hint}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 4, color }}>
        {data.icon}
        <span style={{ fontSize: 11, fontWeight: 600, fontFamily: "var(--font-m)" }}>{data.label}</span>
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// Module 18 §7 — the "05 quarantine" terminal: the one dead end in the chain (no outgoing
// edge, rows just sit here for review/repair), and the only stage node that still fetches
// anything — the quarantine badge, lazily, straight from the endpoint the popup itself uses.
function StructurationQuarantineNode({ data }) {
  const { t } = useTranslation();
  const [quarantineCount, setQuarantineCount] = useState(null);

  useEffect(() => {
    let alive = true;
    structurationApi.getQuarantineSummary(data.projectId, data.bronzeId)
      .then((s) => { if (alive) setQuarantineCount(s.total_quarantined); })
      .catch(() => { if (alive) setQuarantineCount(null); });
    return () => { alive = false; };
  }, [data.projectId, data.bronzeId]);

  const flagged = quarantineCount > 0;
  return (
    <div
      className="card"
      style={{
        padding: "6px 10px", minWidth: 100, textAlign: "center", cursor: "pointer",
        border: `1.5px solid ${flagged ? "var(--danger)" : "var(--border)"}`,
        background: flagged ? "rgba(197,61,61,.07)" : "var(--bg)",
        boxShadow: data.selected ? "0 0 0 2px var(--ember)" : "none",
      }}
      onClick={data.onClick}
      title={data.hint}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 4, color: flagged ? "var(--danger)" : "var(--text-muted)" }}>
        {Icon.warn({ width: 12, height: 12 })}
        <span style={{ fontSize: 11, fontWeight: 600, fontFamily: "var(--font-m)" }}>{data.label}</span>
      </div>
      {flagged && (
        <span className="badge badge-danger" style={{ fontSize: 9.5, padding: "1px 6px", marginTop: 4, display: "inline-block" }}>
          {t("medallion.structuration.quarantineBadge", { count: quarantineCount })}
        </span>
      )}
    </div>
  );
}

const nodeTypes = {
  dataset: DatasetNode, origin: OriginNode,
  structurationStage: StructurationStageNode, structurationQuarantine: StructurationQuarantineNode,
};

export default function LineageCanvas({ nodes: rawNodes, edges: rawEdges, onSelect, selectedId, projectId, onOpenStructuration, onOpenSilverPreview, qualityByDataset = {}, publishedByDataset = {}, dashboardByDataset = {} }) {
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
            payloadBacked: n.payload_backed, onOpenStructuration: () => onOpenStructuration?.(n.id, null, true),
            selected: selectedId === n.id, onClick: () => onSelect(n.id),
          },
        });
      });
    }
    // Module 18 §7 — every bronze->silver edge whose bronze is payload-backed gets the full
    // 01..05 chain spliced in between (never persisted, derived purely client-side — same
    // spirit as origin nodes). A bronze feeding several silvers gets exactly one such chain,
    // its "05 validated" terminal reused as the source for every one of those edges.
    const byId = new Map(rawNodes.map((n) => [n.id, n]));
    const structurationChainByBronze = new Map();
    const edgeStyle = (dashed, danger) => (danger
      ? { stroke: "var(--danger)", strokeWidth: 1.5, strokeDasharray: "4 3", opacity: 0.8 }
      : dashed
        ? { stroke: "var(--text-muted)", strokeWidth: 1.5, strokeDasharray: "4 3" }
        : { stroke: "var(--border)", strokeWidth: 1.5 });

    const flowEdges = [];
    rawEdges.forEach((e, i) => {
      const source = byId.get(e.source);
      const target = byId.get(e.target);
      if (source?.node_type === "dataset" && source.layer === "bronze" && source.payload_backed && target?.layer === "silver") {
        let validatedId = structurationChainByBronze.get(e.source);
        if (!validatedId) {
          const bronzeNode = flowNodes.find((n) => n.id === String(e.source));
          const y = bronzeNode ? bronzeNode.position.y : 20 + i * 100;
          const openPopup = (stageId) => () => onOpenStructuration?.(e.source, stageId);

          const stageIds = STRUCTURATION_STAGES.map((stage) => `structuration-${stage.key}-${e.source}`);
          STRUCTURATION_STAGES.forEach((stage, idx) => {
            flowNodes.push({
              id: stageIds[idx],
              type: "structurationStage",
              position: { x: STAGE_X[stage.key], y },
              data: {
                label: `${stage.num} ${stage.key}`, hint: t(`medallion.structuration.stageHint_${stage.key}`),
                selected: false, onClick: openPopup(stage.key),
              },
            });
          });

          validatedId = `structuration-validated-${e.source}`;
          flowNodes.push({
            id: validatedId,
            type: "structurationStage",
            position: { x: STAGE_X.validated, y },
            data: {
              label: "05 validated", hint: t("medallion.structuration.stageHint_validated"), tone: "success",
              icon: Icon.check({ width: 11, height: 11 }), selected: false, onClick: openPopup("validated"),
            },
          });

          const quarantineId = `structuration-quarantine-${e.source}`;
          flowNodes.push({
            id: quarantineId,
            type: "structurationQuarantine",
            position: { x: STAGE_X.validated, y: y + QUARANTINE_Y_OFFSET },
            data: {
              label: "05 quarantine", hint: t("medallion.structuration.stageHint_quarantine"),
              projectId, bronzeId: e.source, selected: false, onClick: openPopup("quarantine"),
            },
          });

          const chainIds = [String(e.source), ...stageIds, validatedId];
          for (let k = 0; k < chainIds.length - 1; k++) {
            flowEdges.push({ id: `${chainIds[k]}-${chainIds[k + 1]}`, source: chainIds[k], target: chainIds[k + 1], animated: false, style: edgeStyle(false) });
          }
          flowEdges.push({ id: `${stageIds[stageIds.length - 1]}-${quarantineId}`, source: stageIds[stageIds.length - 1], target: quarantineId, animated: false, style: edgeStyle(false, true) });

          // Module 18 §7 UX — materialize_typed_structured_sync's instant preview: real silver
          // nodes (same DatasetNode design, just ember-badged), hanging off "02 typed"/
          // "03 standardized" since that's exactly what each one is a synchronous copy of.
          // Never a MedallionDataset — clicking opens a live sample straight from the
          // warehouse (structuration/preview), not anything read from rawNodes/rawEdges.
          const typedStageId = stageIds[1];
          const standardizedStageId = stageIds[2];
          const silverTypedId = `silver-typed-${e.source}`;
          const silverStructuredId = `silver-structured-${e.source}`;
          flowNodes.push({
            id: silverTypedId,
            type: "dataset",
            position: { x: STAGE_X.typed, y: y + QUARANTINE_Y_OFFSET },
            data: {
              layer: "silver", name: `typed_${source.name}`, lastRowCount: null, testsLabel: t("medallion.tests"),
              previewStageLabel: "typed", isInstantPreview: true,
              selected: false, onClick: () => onOpenSilverPreview?.(e.source, "typed"),
            },
          });
          flowNodes.push({
            id: silverStructuredId,
            type: "dataset",
            position: { x: STAGE_X.standardized, y: y + QUARANTINE_Y_OFFSET },
            data: {
              layer: "silver", name: `structured_${source.name}`, lastRowCount: null, testsLabel: t("medallion.tests"),
              previewStageLabel: "structured", isInstantPreview: true,
              selected: false, onClick: () => onOpenSilverPreview?.(e.source, "structured"),
            },
          });
          flowEdges.push({ id: `${typedStageId}-${silverTypedId}`, source: typedStageId, target: silverTypedId, animated: false, style: edgeStyle(true) });
          flowEdges.push({ id: `${standardizedStageId}-${silverStructuredId}`, source: standardizedStageId, target: silverStructuredId, animated: false, style: edgeStyle(true) });

          structurationChainByBronze.set(e.source, validatedId);
        }
        flowEdges.push({ id: `${validatedId}-${e.target}`, source: validatedId, target: String(e.target), animated: false, style: edgeStyle(false) });
        return;
      }
      flowEdges.push({
        id: `${e.source}-${e.target}`, source: String(e.source), target: String(e.target),
        animated: false, style: edgeStyle(e.source < 0),
      });
    });
    return { nodes: flowNodes, edges: flowEdges };
  }, [rawNodes, rawEdges, selectedId, projectId, onOpenStructuration, onOpenSilverPreview, qualityByDataset, publishedByDataset, dashboardByDataset, publishedLabel, dashboardLabel, i18n.language, t]);

  return (
    <div style={{ height: 480, background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView proOptions={{ hideAttribution: true }}>
        <Background color="var(--border)" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
