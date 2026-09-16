import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "../../components/ui/Drawer.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import * as promotionApi from "../../api/promotion.js";
import * as airflowInstancesApi from "../../api/airflowInstances.js";
import * as sourcesApi from "../../api/sources.js";

// Module 17 §5.6 — "Assistant prod" (liaisons + mapping des sources) and the promotion
// modal (diff + mapping recap + qualité + confirmation), combined in one drawer: the prod
// binding config step only shows once (or when re-editing), the mapping/promotion sections
// take over once a prod binding exists.
export default function PromotionDrawer({ project, onClose, onPromoted }) {
  const { t } = useTranslation();
  const showToast = useToast();

  const [loading, setLoading] = useState(true);
  const [instances, setInstances] = useState([]);
  const [sources, setSources] = useState([]);
  const [prodBinding, setProdBinding] = useState(null);
  const [devBinding, setDevBinding] = useState(null);
  const [mappings, setMappings] = useState([]);
  const [preview, setPreview] = useState(null);

  const [airflowInstanceId, setAirflowInstanceId] = useState("");
  const [warehouseSourceId, setWarehouseSourceId] = useState("");
  const [objectStoreSourceId, setObjectStoreSourceId] = useState("");
  const [scheduleTouched, setScheduleTouched] = useState(false);
  const [scheduleValue, setScheduleValue] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [confirmingId, setConfirmingId] = useState(null);
  const [promoting, setPromoting] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [bindings, inst, src] = await Promise.all([
        promotionApi.listBindings(project.id),
        airflowInstancesApi.listInstances(),
        sourcesApi.listSources(),
      ]);
      setInstances(inst);
      setSources(src);
      const home = bindings.find((b) => b.is_home) || null;
      const prod = bindings.find((b) => b.environment === "prod") || null;
      setDevBinding(home);
      setProdBinding(prod);
      if (prod) {
        setAirflowInstanceId(prod.airflow_instance_id);
        setWarehouseSourceId(prod.warehouse_source_id);
        setObjectStoreSourceId(prod.object_store_source_id);
        setScheduleValue(prod.schedule || "");
        const [m, p] = await Promise.all([promotionApi.listSourceMappings(project.id), promotionApi.getPromotionPreview(project.id)]);
        setMappings(m);
        setPreview(p);
      } else if (home) {
        setAirflowInstanceId(home.airflow_instance_id);
        setWarehouseSourceId(home.warehouse_source_id);
        setObjectStoreSourceId(home.object_store_source_id);
        setScheduleValue(home.schedule || ""); // §10.3 — prefilled from dev, still requires an explicit save
      }
    } catch (err) {
      setError(err.message || t("medallion.promotion.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [project.id]);

  const pgSources = sources.filter((s) => s.type === "postgresql");
  const minioSources = sources.filter((s) => s.type === "minio");

  const saveConfig = async () => {
    setSavingConfig(true);
    setError("");
    try {
      await promotionApi.upsertProdBinding(project.id, {
        airflow_instance_id: Number(airflowInstanceId),
        warehouse_source_id: Number(warehouseSourceId),
        object_store_source_id: Number(objectStoreSourceId),
        schedule: scheduleValue.trim() || null,
        target: "prod",
      });
      showToast(t("medallion.promotion.configSaved"));
      await load();
    } catch (err) {
      setError(err.message || t("medallion.promotion.configFailed"));
    } finally {
      setSavingConfig(false);
    }
  };

  const confirmMapping = async (originId, targetId) => {
    if (!targetId) return;
    setConfirmingId(originId);
    try {
      await promotionApi.confirmSourceMapping(project.id, originId, Number(targetId));
      const [m, p] = await Promise.all([promotionApi.listSourceMappings(project.id), promotionApi.getPromotionPreview(project.id)]);
      setMappings(m);
      setPreview(p);
    } catch (err) {
      showToast(err.message || t("medallion.promotion.mappingFailed"));
    } finally {
      setConfirmingId(null);
    }
  };

  const runPromote = async () => {
    setPromoting(true);
    setError("");
    try {
      const result = await promotionApi.promote(project.id);
      showToast(t("medallion.promotion.promoted", { version: preview?.dev_version_number }));
      onPromoted?.(result);
      await load();
    } catch (err) {
      setError(err.message || t("medallion.promotion.promoteFailed"));
    } finally {
      setPromoting(false);
    }
  };

  const configValid = airflowInstanceId && warehouseSourceId && objectStoreSourceId;

  return (
    <Drawer title={t("medallion.promotion.title")} description={t("medallion.promotion.description", { name: project.name })} onClose={onClose}>
      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}
      {loading ? (
        <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>
      ) : (
        <>
          <div className="card" style={{ padding: 14, marginBottom: 16 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>{t("medallion.promotion.sectionConfig")}</div>
            <Field label={t("medallion.promotion.airflowInstance")}>
              <select className="input" value={airflowInstanceId} onChange={(e) => setAirflowInstanceId(e.target.value)}>
                <option value="">{t("medallion.wizard.choose")}</option>
                {instances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
              </select>
            </Field>
            <div style={{ display: "flex", gap: 12 }}>
              <div style={{ flex: 1 }}>
                <Field label={t("medallion.promotion.warehouse")}>
                  <select className="input" value={warehouseSourceId} onChange={(e) => setWarehouseSourceId(e.target.value)}>
                    <option value="">{t("medallion.wizard.choose")}</option>
                    {pgSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </Field>
              </div>
              <div style={{ flex: 1 }}>
                <Field label={t("medallion.promotion.objectStore")}>
                  <select className="input" value={objectStoreSourceId} onChange={(e) => setObjectStoreSourceId(e.target.value)}>
                    <option value="">{t("medallion.wizard.choose")}</option>
                    {minioSources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </Field>
              </div>
            </div>
            <Field label={t("medallion.promotion.schedule")}>
              <Input
                value={scheduleValue} placeholder={t("medallion.promotion.schedulePlaceholder")}
                onChange={(e) => { setScheduleValue(e.target.value); setScheduleTouched(true); }}
              />
              {!prodBinding && devBinding?.schedule && !scheduleTouched && (
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>{t("medallion.promotion.schedulePrefilledHint")}</div>
              )}
            </Field>
            <Button disabled={!configValid || savingConfig} onClick={saveConfig}>
              {savingConfig ? t("medallion.promotion.saving") : prodBinding ? t("medallion.promotion.updateConfig") : t("medallion.promotion.createBinding")}
            </Button>
          </div>

          {prodBinding && (
            <div className="card" style={{ padding: 14, marginBottom: 16 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>{t("medallion.promotion.sectionMapping")}</div>
              {mappings.length === 0 ? (
                <div style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.promotion.noSourcesToMap")}</div>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("medallion.promotion.colDevSource")}</th>
                        <th>{t("medallion.promotion.colProdSource")}</th>
                        <th>{t("common.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mappings.map((m) => (
                        <tr key={m.origin_source_id}>
                          <td style={{ fontSize: 12.5 }}>{m.origin_name} <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-m)", fontSize: 11 }}>({m.origin_type})</span></td>
                          <td style={{ minWidth: 180 }}>
                            <select
                              className="input" style={{ fontSize: 12 }}
                              value={m.target_source_id || ""}
                              onChange={(e) => confirmMapping(m.origin_source_id, e.target.value)}
                            >
                              <option value="">{t("medallion.wizard.choose")}</option>
                              {sources.filter((s) => s.type === m.origin_type).map((s) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                              ))}
                            </select>
                          </td>
                          <td>
                            {confirmingId === m.origin_source_id ? (
                              <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{t("medallion.promotion.saving")}</span>
                            ) : m.confirmed ? (
                              <Badge tone="accent">{t("medallion.promotion.confirmed")}</Badge>
                            ) : (
                              <Badge tone="neutral">{t("medallion.promotion.pendingConfirm")}</Badge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {prodBinding && preview && (
            <div className="card" style={{ padding: 14 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>{t("medallion.promotion.sectionPromote")}</div>

              {!preview.dev_deployed && (
                <div className="error-banner">{Icon.warn()}<span>{t("medallion.promotion.devNeverDeployed")}</span></div>
              )}

              {preview.dev_deployed && (
                <>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10 }}>
                    {preview.prod_version_number
                      ? t("medallion.promotion.diffAgainst", { version: preview.prod_version_number })
                      : t("medallion.promotion.initialDeploy")}
                  </div>

                  {preview.diff && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 10, fontFamily: "var(--font-m)", fontSize: 12 }}>
                      {preview.diff.datasets_added.map((n) => <div key={`a-${n}`} style={{ color: "#2f9e6e" }}>+ {n}</div>)}
                      {preview.diff.datasets_removed.map((n) => <div key={`r-${n}`} style={{ color: "var(--danger)" }}>− {n}</div>)}
                      {preview.diff.sql_changed.map((c) => <div key={`s-${c.name}`} style={{ color: "var(--ember)" }}>~ {c.name} ({t("medallion.promotion.sqlChanged")})</div>)}
                      {preview.diff.tests_changed.map((c) => <div key={`t-${c.name}`} style={{ color: "var(--ember)" }}>~ {c.name} ({t("medallion.promotion.testsChanged")})</div>)}
                      {preview.diff.datasets_added.length === 0 && preview.diff.datasets_removed.length === 0 && preview.diff.sql_changed.length === 0 && preview.diff.tests_changed.length === 0 && (
                        <div style={{ color: "var(--text-muted)" }}>{t("medallion.promotion.noDiff")}</div>
                      )}
                    </div>
                  )}

                  {preview.blocking_alerts.length > 0 && (
                    <div className="error-banner" style={{ marginBottom: 8 }}>
                      {Icon.warn()}
                      <span>{t("medallion.promotion.blockingAlerts", { count: preview.blocking_alerts.length })}</span>
                    </div>
                  )}
                  {preview.advisory_alerts.length > 0 && (
                    <div style={{ background: "rgba(229,114,0,.08)", border: "1px solid rgba(229,114,0,.3)", borderRadius: 8, padding: 10, marginBottom: 10, fontSize: 12 }}>
                      {t("medallion.promotion.advisoryAlerts", { count: preview.advisory_alerts.length })}
                    </div>
                  )}
                  {!preview.mapping_complete && (
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 10 }}>{t("medallion.promotion.mappingIncomplete")}</div>
                  )}

                  <Button disabled={!preview.can_promote || promoting} onClick={runPromote}>
                    {promoting ? t("medallion.promotion.promoting") : t("medallion.promotion.confirmPromote", { version: preview.dev_version_number })}
                  </Button>
                </>
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
