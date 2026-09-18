import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import * as sourcesApi from "../../api/sources.js";
import * as qualityApi from "../../api/quality.js";
import * as serversApi from "../../api/servers.js";
import * as promotionApi from "../../api/promotion.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import LineageCanvas from "./LineageCanvas.jsx";
import ThreeColumnView from "./ThreeColumnView.jsx";
import DatasetPanel from "./DatasetPanel.jsx";
import OriginPanel from "./OriginPanel.jsx";
import StructurationPopup from "./StructurationPopup.jsx";
import SilverPreviewPopup from "./SilverPreviewPopup.jsx";
import PromotionDrawer from "./PromotionDrawer.jsx";
import RunsTab from "./RunsTab.jsx";
import QualityTab from "./QualityTab.jsx";
import VersionsTab from "./VersionsTab.jsx";
import AgentTab from "./AgentTab.jsx";
import ScheduleField, { describeSchedule, isValidSchedule } from "./ScheduleField.jsx";
import ImportWizardDrawer from "../imports/ImportWizardDrawer.jsx";
import SchemaValidationModal from "../imports/SchemaValidationModal.jsx";

const STATUS_TONE = { draft: "neutral", built: "accent", deployed: "accent", paused: "neutral", error: "neutral" };

export default function ProjectDetail({ readOnly = false }) {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const showToast = useToast();

  const STATUS_LABEL = t("medallion.status", { returnObjects: true });
  const BUILD_STEPS = t("medallion.buildSteps", { returnObjects: true });

  const [project, setProject] = useState(null);
  const [owner, setOwner] = useState(null); // Module 9 — only set in readOnly (admin) mode
  const [datasets, setDatasets] = useState([]);
  const [lineage, setLineage] = useState({ nodes: [], edges: [] });
  const [sources, setSources] = useState([]);
  const [quality, setQuality] = useState({ datasets: [] });
  const [openAlerts, setOpenAlerts] = useState([]);
  const [publications, setPublications] = useState([]); // Module 11 — "Publié" badges on gold nodes
  const [dashboards, setDashboards] = useState([]); // Module 12 — "Dashboard" badges on gold nodes
  const [loading, setLoading] = useState(true);

  const [view, setView] = useState("canvas");
  const [tab, setTab] = useState("pipeline");
  const [panel, setPanel] = useState(null); // { dataset } | { defaultLayer } | null
  const [previewData, setPreviewData] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [building, setBuilding] = useState(false);
  const [buildStep, setBuildStep] = useState(0);
  const [deployWatch, setDeployWatch] = useState(null); // { status: "polling"|"detected"|"timeout", elapsed }
  const deployWatchTimer = useRef(null);
  const [restoredBanner, setRestoredBanner] = useState(null); // version_number restored from, or null
  const [pausing, setPausing] = useState(false);
  const [retryingActivation, setRetryingActivation] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState(false);
  const [scheduleDraft, setScheduleDraft] = useState(null);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [importWizardOpen, setImportWizardOpen] = useState(false);
  const [importValidating, setImportValidating] = useState(null);
  const [structurationTargetId, setStructurationTargetId] = useState(null); // Module 6 extension — canvas node click
  const [structurationStage, setStructurationStage] = useState(null); // Module 18 §7 — which 01..05 stage node was clicked
  const [structurationGuided, setStructurationGuided] = useState(false); // Module 18 §7 UX — "+" button: walk 01..05 step by step
  const openStructuration = (datasetId, stage = null, guided = false) => {
    setStructurationTargetId(datasetId);
    setStructurationStage(stage);
    setStructurationGuided(guided);
  };
  const [silverPreviewTarget, setSilverPreviewTarget] = useState(null); // Module 18 §7 UX — {datasetId, stage} | null
  // UX ask — the guided popup's "Terminer" materializes silver.unpacked_<name>/typed_<name>
  // synchronously server-side, so the canvas's `structured` flag (and thus the two new nodes)
  // only exist AFTER that request returns — re-fetching lineage here is what replaces the
  // manual page refresh the engineer had to do until now.
  const onStructurationSaved = () => {
    setStructurationTargetId(null);
    setStructurationStage(null);
    setStructurationGuided(false);
    medallionApi.getLineage(id).then(setLineage);
  };
  const [hasProdEnvironment, setHasProdEnvironment] = useState(false); // Module 17 §5.3.1
  const [hasProdBinding, setHasProdBinding] = useState(false);
  const [promotionOpen, setPromotionOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      if (readOnly) {
        // One bundled admin call plus sources — sources are shared platform-wide connections
        // (not owned per-project), needed here so the read-only DatasetPanel can show the
        // source name instead of a blank picker.
        const [detail, src] = await Promise.all([
          medallionApi.getAdminProjectDetail(id),
          sourcesApi.listSources(),
        ]);
        setProject(detail.project);
        setOwner(detail.owner);
        setDatasets(detail.datasets);
        setLineage(detail.lineage);
        setSources(src);
      } else {
        const [p, ds, lg, src] = await Promise.all([
          medallionApi.getProject(id),
          medallionApi.listDatasets(id),
          medallionApi.getLineage(id),
          sourcesApi.listSources(),
        ]);
        setProject(p);
        setDatasets(ds);
        setLineage(lg);
        setSources(src);
      }
    } finally {
      setLoading(false);
    }
  };

  const loadQuality = async () => {
    try {
      const [q, alerts] = await Promise.all([qualityApi.getProjectQuality(id), qualityApi.listQualityAlerts(id, "open")]);
      setQuality(q);
      setOpenAlerts(alerts);
    } catch {
      // best-effort — quality collection is optional and must never block the pipeline view
    }
  };

  const loadPublications = async () => {
    try {
      setPublications(await medallionApi.listPublications(id));
    } catch {
      // best-effort — the badge is a convenience, never blocks the pipeline view
    }
  };

  const loadDashboards = async () => {
    try {
      setDashboards(await medallionApi.listDashboards(id));
    } catch {
      // best-effort — the badge is a convenience, never blocks the pipeline view
    }
  };

  // Module 17 §5.3.1 — "Déployer sur la prod" only shows when a prod-tagged server exists
  // at all; whether THIS project already has a prod binding just changes the button's label.
  const loadPromotionEligibility = async () => {
    try {
      const [servers, bindings] = await Promise.all([serversApi.listServers(), promotionApi.listBindings(id)]);
      setHasProdEnvironment(servers.some((s) => s.environment === "prod"));
      setHasProdBinding(bindings.some((b) => b.environment === "prod"));
    } catch {
      // best-effort — never blocks the pipeline view
    }
  };

  useEffect(() => { load(); loadQuality(); loadPublications(); loadDashboards(); loadPromotionEligibility(); }, [id]);
  useEffect(() => () => { if (deployWatchTimer.current) clearTimeout(deployWatchTimer.current); }, []);

  // Depositing the DAG file over SFTP isn't the same as Airflow knowing about it — the
  // dag-processor only picks it up on its next scan (up to dag_dir_list_interval, 300s by
  // default). Polls the same get_dag() lookup the preflight reparse check already uses, so
  // "Build & déployer" gives real feedback on this step instead of going silent.
  const watchAirflowDetection = () => {
    setDeployWatch({ status: "polling", elapsed: 0 });
    const poll = async (elapsed) => {
      try {
        const res = await medallionApi.getDeployStatus(id);
        if (res.known_to_airflow) { setDeployWatch({ status: "detected", elapsed, activated: res.activated }); return; }
      } catch {
        // transient Airflow API hiccup — keep retrying rather than giving up on one failure
      }
      if (elapsed >= 330) { setDeployWatch({ status: "timeout", elapsed }); return; }
      setDeployWatch({ status: "polling", elapsed });
      deployWatchTimer.current = setTimeout(() => poll(elapsed + 5), 5000);
    };
    poll(0);
  };

  const degradedDatasetIds = new Set(openAlerts.map((a) => a.dataset_id));
  const qualityByDataset = Object.fromEntries(
    quality.datasets.map((dq) => [dq.dataset_id, { degraded: degradedDatasetIds.has(dq.dataset_id) }])
  );
  const publishedByDataset = Object.fromEntries(publications.map((p) => [p.dataset_id, p]));
  const dashboardByDataset = Object.fromEntries(dashboards.map((d) => [d.dataset_id, d]));

  const handlePreview = async () => {
    setPreviewing(true);
    try {
      const res = await medallionApi.previewProject(id);
      setPreviewData(res);
    } catch (err) {
      showToast(err.message || t("medallion.previewFailed"));
    } finally {
      setPreviewing(false);
    }
  };

  const handleBuild = async () => {
    setBuilding(true);
    setBuildStep(1);
    if (deployWatchTimer.current) clearTimeout(deployWatchTimer.current);
    setDeployWatch(null);
    try {
      await new Promise((r) => setTimeout(r, 400));
      setBuildStep(2);
      const report = await medallionApi.buildProject(id);
      setBuildStep(3);
      await new Promise((r) => setTimeout(r, 400));
      setBuildStep(4);
      showToast(t("medallion.deployedToast", { count: report.connections_created.length }));
      await load();
      if (report.dag_deposited) watchAirflowDetection();
    } catch (err) {
      showToast(err.message || t("medallion.deployFailed"));
    } finally {
      setTimeout(() => { setBuilding(false); setBuildStep(0); }, 800);
    }
  };

  const handlePauseToggle = async () => {
    setPausing(true);
    try {
      if (project.status === "paused") {
        await medallionApi.unpauseProject(id);
        showToast(t("medallion.projectResumed"));
      } else {
        await medallionApi.pauseProject(id);
        showToast(t("medallion.projectPaused"));
      }
      await load();
    } catch (err) {
      showToast(err.message || t("medallion.actionFailed"));
    } finally {
      setPausing(false);
    }
  };

  const retryActivation = async () => {
    setRetryingActivation(true);
    try {
      await medallionApi.unpauseProject(id);
      setDeployWatch((w) => (w ? { ...w, activated: true } : w));
      showToast(t("medallion.activation.retrySuccess"));
    } catch (err) {
      showToast(err.message || t("medallion.actionFailed"));
    } finally {
      setRetryingActivation(false);
    }
  };

  const openScheduleEditor = () => { setScheduleDraft(project.schedule ?? null); setEditingSchedule(true); };

  const saveSchedule = async () => {
    setSavingSchedule(true);
    try {
      const updated = await medallionApi.updateProject(id, { schedule: scheduleDraft });
      setProject(updated);
      setEditingSchedule(false);
      showToast(t("medallion.schedule.saved"));
    } catch (err) {
      showToast(err.message || t("medallion.actionFailed"));
    } finally {
      setSavingSchedule(false);
    }
  };

  const onDatasetSaved = (ds) => {
    setDatasets((list) => {
      const exists = list.some((d) => d.id === ds.id);
      return exists ? list.map((d) => (d.id === ds.id ? ds : d)) : [...list, ds];
    });
    setPanel(null);
    medallionApi.getLineage(id).then(setLineage);
    showToast(t("medallion.datasetSaved"));
    medallionApi.getProject(id).then(setProject);
  };

  const onDatasetDeleted = (did) => {
    setDatasets((list) => list.filter((d) => d.id !== did));
    setPanel(null);
    medallionApi.getLineage(id).then(setLineage);
    showToast(t("medallion.datasetDeleted"));
    medallionApi.getProject(id).then(setProject);
  };

  const handleSelectNode = (nid) => {
    setPanel({ selectedId: nid });
  };

  // Module 13's Agent tab manages its own execution/plan state independently of this
  // component's own datasets/lineage/publications/dashboards (fetched once on mount, §load
  // effect below) — without this, a project built/run/published/dashboarded entirely from the
  // Agent tab left the Pipeline tab showing stale data until a full page reload.
  const handleExecutionUpdate = () => {
    load();
    loadPublications();
    loadDashboards();
  };

  const handleImportAnalyzed = (fi) => {
    setImportWizardOpen(false);
    // Payload mode (§3.5) comes back status=imported directly — no mapping to validate, so
    // the schema modal must be skipped entirely; only "typed" analysis needs it (mirrors
    // ImportsList.jsx's onAnalyzed, which already branches the same way).
    if (fi.status === "awaiting_validation") setImportValidating(fi);
    else if (fi.status === "imported") handleImportValidated(fi);
    else showToast(t("imports.wizard.analyzeFailed"));
  };

  const handleImportValidated = async (fi) => {
    setImportValidating(null);
    try {
      await medallionApi.createDataset(id, {
        layer: "bronze", name: fi.target_table, source_id: fi.target_source_id,
        source_object: `${fi.target_schema}.${fi.target_table}`, load_mode: "full", upstream_dataset_ids: [],
      });
      showToast(t("medallion.origin.bronzeCreated"));
      await load();
    } catch (err) {
      showToast(err.message || t("medallion.origin.bronzeCreateFailed"));
    }
  };

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;
  if (!project) return <div style={{ color: "var(--text-muted)" }}>{t("medallion.notFound")}</div>;

  const selectedDataset = panel?.dataset || (panel?.selectedId != null && panel.selectedId >= 0 ? datasets.find((d) => d.id === panel.selectedId) : null);
  const selectedOrigin = panel?.selectedId != null && panel.selectedId < 0 ? lineage.nodes.find((n) => n.id === panel.selectedId) : null;
  // Module 10 — an origin has no table of its own; preview it via any bronze dataset that
  // declares it as its source (same source_id/source_object, so any match is equivalent).
  const originPreviewDatasetId = selectedOrigin ? lineage.edges.find((e) => e.source === selectedOrigin.id)?.target ?? null : null;

  return (
    <>
      <div className="page-head">
        <div>
          <button className="link" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 10 }} onClick={() => navigate("/medallion")}>
            {Icon.arrowLeft({ width: 14, height: 14 })} {t("medallion.projects")}
          </button>
          <h1 className="page-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {project.name}
            <Badge tone={project.environment === "prod" ? "danger" : "neutral"}>
              {project.environment === "prod" ? t("servers.environmentProd") : t("servers.environmentDev")}
            </Badge>
          </h1>
          <p className="page-desc" style={{ fontFamily: "var(--font-m)", fontSize: 12.5, display: "flex", alignItems: "center", gap: 6 }}>
            <span>
              {project.dbt_project_name} ·{" "}
              {project.schedule
                ? `${t("medallion.schedule.scheduled")} · ${describeSchedule(project.schedule, t) || project.schedule}`
                : t("medallion.manual")}
            </span>
            {!readOnly && !editingSchedule && (
              <button type="button" className="link" style={{ fontSize: 11.5 }} onClick={openScheduleEditor}>{t("common.edit")}</button>
            )}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {project.has_pending_changes && <Badge tone="neutral">{t("medallion.toRedeploy")}</Badge>}
          <Badge tone={STATUS_TONE[project.status]}>{STATUS_LABEL[project.status]}</Badge>
          {!readOnly && project.status !== "draft" && (
            <Button variant="ghost" className="inline" disabled={pausing} onClick={handlePauseToggle}>
              {project.status === "paused" ? Icon.play() : Icon.pause()} {project.status === "paused" ? t("medallion.resume") : t("medallion.pause")}
            </Button>
          )}
          {!readOnly && (
            <Button
              variant="ghost" className="inline"
              disabled={!hasProdEnvironment}
              title={!hasProdEnvironment ? t("medallion.promotion.noProdEnvironment") : undefined}
              onClick={() => setPromotionOpen(true)}
            >
              {Icon.upload()} {hasProdBinding ? t("medallion.promotion.manageAction") : t("medallion.promotion.deployAction")}
            </Button>
          )}
        </div>
      </div>

      {editingSchedule && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <ScheduleField value={scheduleDraft} onChange={setScheduleDraft} disabled={savingSchedule} />
          {project.status !== "draft" && (
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>{t("medallion.schedule.editHint")}</div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <Button className="inline" disabled={savingSchedule || (scheduleDraft != null && !isValidSchedule(scheduleDraft))} onClick={saveSchedule}>
              {savingSchedule ? t("common.saving") : t("common.save")}
            </Button>
            <Button variant="ghost" className="inline" disabled={savingSchedule} onClick={() => setEditingSchedule(false)}>{t("common.cancel")}</Button>
          </div>
        </div>
      )}

      {readOnly && (
        <div className="card" style={{ padding: "10px 14px", marginBottom: 16, display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--text-muted)" }}>
          {Icon.eye({ width: 14, height: 14 })}
          {t("medallion.readOnly.banner", { owner: owner?.name || "…" })}
        </div>
      )}

      <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap" }}>
        {!readOnly && (
          <>
            <Button variant="ghost" className="inline" onClick={() => setPanel({ defaultLayer: "bronze" })}>{Icon.plus()} {t("medallion.addDataset")}</Button>
            <Button variant="ghost" className="inline" onClick={() => setImportWizardOpen(true)}>{Icon.upload()} {t("medallion.origin.importFile")}</Button>
            <Button variant="ghost" className="inline" disabled={previewing} onClick={handlePreview}>{previewing ? t("medallion.previewing") : t("medallion.preview")}</Button>
            <Button className="inline" disabled={building} onClick={handleBuild}>{building ? t("medallion.deploying") : t("medallion.buildAndDeploy")}</Button>
          </>
        )}

        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className={"btn-ghost" + (tab === "pipeline" ? "" : "")} style={{ opacity: tab === "pipeline" ? 1 : 0.6 }} onClick={() => setTab("pipeline")}>{t("medallion.tabPipeline")}</button>
          <button className="btn-ghost" style={{ opacity: tab === "runs" ? 1 : 0.6 }} onClick={() => setTab("runs")}>{t("medallion.tabRuns")}</button>
          <button className="btn-ghost" style={{ opacity: tab === "quality" ? 1 : 0.6 }} onClick={() => setTab("quality")}>{t("medallion.tabQuality")}</button>
          <button className="btn-ghost" style={{ opacity: tab === "versions" ? 1 : 0.6 }} onClick={() => setTab("versions")}>{t("medallion.tabVersions")}</button>
          <button className="btn-ghost" style={{ opacity: tab === "agent" ? 1 : 0.6 }} onClick={() => setTab("agent")}>{t("medallion.tabAgent")}</button>
        </div>
      </div>

      {(building || deployWatch) && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div className="field-label" style={{ marginBottom: 10 }}>{t("medallion.buildTitle")}</div>
          {building && (
            <div style={{ display: "flex", gap: 18, fontSize: 12.5, flexWrap: "wrap" }}>
              {BUILD_STEPS.map((label, i) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 6, color: buildStep > i ? "var(--text)" : "var(--text-muted)" }}>
                  <span style={{
                    width: 16, height: 16, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                    background: buildStep > i + 1 || buildStep === 4 ? "var(--ember)" : "var(--bg)", border: "1px solid var(--border)",
                    color: buildStep > i + 1 || buildStep === 4 ? "#14181d" : "var(--text-muted)", fontSize: 10,
                  }}>{buildStep > i + 1 || (buildStep === 4) ? "✓" : i + 1}</span>
                  {label}
                </div>
              ))}
            </div>
          )}
          {deployWatch && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, marginTop: building ? 12 : 0 }}>
              <span style={{
                width: 16, height: 16, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                background: deployWatch.status === "detected" ? "var(--ember)" : "var(--bg)", border: "1px solid var(--border)",
                color: deployWatch.status === "detected" ? "#14181d" : "var(--text-muted)", fontSize: 10,
              }}>{deployWatch.status === "detected" ? "✓" : deployWatch.status === "timeout" ? "!" : "…"}</span>
              <span style={{ color: deployWatch.status === "polling" ? "var(--text-muted)" : "var(--text)" }}>
                {deployWatch.status === "polling" && t("medallion.airflowDetecting", { elapsed: deployWatch.elapsed })}
                {deployWatch.status === "detected" && t("medallion.airflowDetected", { elapsed: deployWatch.elapsed })}
                {deployWatch.status === "timeout" && t("medallion.airflowDetectTimeout")}
              </span>
            </div>
          )}
          {deployWatch?.status === "detected" && project.schedule && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, marginTop: 8 }}>
              <span style={{
                width: 16, height: 16, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                background: deployWatch.activated ? "var(--ember)" : "var(--bg)", border: "1px solid var(--border)",
                color: deployWatch.activated ? "#14181d" : "var(--text-muted)", fontSize: 10,
              }}>{deployWatch.activated ? "✓" : "!"}</span>
              <span>{deployWatch.activated ? t("medallion.activation.activated", { schedule: describeSchedule(project.schedule, t) || project.schedule }) : t("medallion.activation.failed")}</span>
              {!deployWatch.activated && (
                <button type="button" className="link" onClick={retryActivation} disabled={retryingActivation}>
                  {retryingActivation ? t("common.saving") : t("medallion.activation.retry")}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {restoredBanner != null && (
        <div className="card" style={{ padding: 12, marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 12.5 }}>{t("medallion.versions.restoredBanner", { n: restoredBanner })}</span>
          <div style={{ display: "flex", gap: 8 }}>
            <Button variant="ghost" className="inline" onClick={() => { setTab("runs"); setRestoredBanner(null); }}>{t("medallion.runs.runNow")}</Button>
            <button className="btn-icon" onClick={() => setRestoredBanner(null)}>{Icon.x()}</button>
          </div>
        </div>
      )}

      {tab === "pipeline" && (
        <>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginBottom: 10 }}>
            <button className="btn-ghost" style={{ padding: "6px 10px", opacity: view === "canvas" ? 1 : 0.6 }} onClick={() => setView("canvas")}>{Icon.share()} {t("medallion.canvas")}</button>
            <button className="btn-ghost" style={{ padding: "6px 10px", opacity: view === "columns" ? 1 : 0.6 }} onClick={() => setView("columns")}>{Icon.columns()} {t("medallion.threeColumns")}</button>
          </div>

          {view === "canvas" ? (
            <LineageCanvas
              nodes={lineage.nodes} edges={lineage.edges} selectedId={selectedDataset?.id} onSelect={handleSelectNode}
              projectId={project.id} onOpenStructuration={openStructuration}
              onOpenSilverPreview={(datasetId, stage) => setSilverPreviewTarget({ datasetId, stage })}
              qualityByDataset={qualityByDataset} publishedByDataset={publishedByDataset} dashboardByDataset={dashboardByDataset}
            />
          ) : (
            <ThreeColumnView nodes={lineage.nodes} selectedId={selectedDataset?.id} onSelect={handleSelectNode} qualityByDataset={qualityByDataset} />
          )}
        </>
      )}

      {tab === "runs" && <RunsTab project={project} readOnly={readOnly} />}

      {tab === "quality" && <QualityTab project={project} quality={quality} openAlerts={openAlerts} onReload={loadQuality} readOnly={readOnly} />}
      {tab === "versions" && (
        <VersionsTab project={project} readOnly={readOnly} onRestored={(v) => { setRestoredBanner(v.is_restore_of_version_number); load(); }} />
      )}
      {tab === "agent" && <AgentTab project={project} readOnly={readOnly} onExecutionUpdate={handleExecutionUpdate} />}

      {previewData && (
        <div className="card" style={{ padding: 16, marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div className="field-label" style={{ margin: 0 }}>{t("medallion.generatedPreview")}</div>
            <button className="modal-close" onClick={() => setPreviewData(null)}>{Icon.x()}</button>
          </div>
          {previewData.warnings.map((w, i) => (
            <div key={i} className="error-banner" style={{ background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)" }}>{Icon.warn()}<span>{w}</span></div>
          ))}
          {Object.entries(previewData.dbt_sql).map(([path, content]) => (
            <div key={path} style={{ marginBottom: 14 }}>
              <div style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)", marginBottom: 4 }}>{path}</div>
              <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, whiteSpace: "pre-wrap", background: "var(--bg)", padding: 10, borderRadius: 8, maxHeight: 180, overflowY: "auto" }}>{content}</pre>
            </div>
          ))}
          {Object.entries(previewData.python_tasks || {}).map(([name, content]) => (
            <div key={name} style={{ marginBottom: 14 }}>
              <div style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)", marginBottom: 4 }}>{t("medallion.mlNode", { name })}</div>
              <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, whiteSpace: "pre-wrap", background: "var(--bg)", padding: 10, borderRadius: 8, maxHeight: 180, overflowY: "auto" }}>{content}</pre>
            </div>
          ))}
          <div style={{ fontFamily: "var(--font-m)", fontSize: 11.5, color: "var(--text-muted)", marginBottom: 4 }}>{t("medallion.dagAirflow")}</div>
          <pre style={{ fontFamily: "var(--font-m)", fontSize: 11.5, whiteSpace: "pre-wrap", background: "var(--bg)", padding: 10, borderRadius: 8, maxHeight: 300, overflowY: "auto" }}>{previewData.dag_py}</pre>
        </div>
      )}

      {panel && selectedOrigin && (
        <OriginPanel origin={selectedOrigin} project={project} previewDatasetId={originPreviewDatasetId} onClose={() => setPanel(null)} readOnly={readOnly} />
      )}

      {panel && !selectedOrigin && (
        <DatasetPanel
          project={project}
          datasets={datasets}
          dataset={selectedDataset}
          defaultLayer={panel.defaultLayer}
          initialTab={panel.openTab}
          sources={sources}
          onClose={() => { setPanel(null); loadPublications(); loadDashboards(); }}
          onSaved={onDatasetSaved}
          onDeleted={onDatasetDeleted}
          onStructurationSaved={() => medallionApi.getLineage(id).then(setLineage)}
          readOnly={readOnly}
        />
      )}

      {!readOnly && importWizardOpen && (
        <ImportWizardDrawer onClose={() => setImportWizardOpen(false)} onAnalyzed={handleImportAnalyzed} />
      )}

      {!readOnly && importValidating && (
        <SchemaValidationModal
          fileImport={importValidating}
          onClose={() => setImportValidating(null)}
          onValidated={handleImportValidated}
        />
      )}

      {promotionOpen && (
        <PromotionDrawer
          project={project}
          onClose={() => setPromotionOpen(false)}
          onPromoted={() => loadPromotionEligibility()}
        />
      )}

      {structurationTargetId != null && (() => {
        const structurationDataset = datasets.find((d) => d.id === structurationTargetId);
        return structurationDataset ? (
          <StructurationPopup
            project={project} dataset={structurationDataset} stage={structurationStage} guided={structurationGuided}
            onFinish={onStructurationSaved}
            onClose={() => { setStructurationTargetId(null); setStructurationStage(null); setStructurationGuided(false); }}
          />
        ) : null;
      })()}

      {silverPreviewTarget != null && (() => {
        const bronzeDataset = datasets.find((d) => d.id === silverPreviewTarget.datasetId);
        return bronzeDataset ? (
          <SilverPreviewPopup
            project={project} bronzeDatasetId={bronzeDataset.id} bronzeName={bronzeDataset.name} stage={silverPreviewTarget.stage}
            onClose={() => setSilverPreviewTarget(null)}
          />
        ) : null;
      })()}
    </>
  );
}
