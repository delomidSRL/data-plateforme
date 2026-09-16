import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import * as supersetApi from "../../api/superset.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";

export default function PublishPanel({ project, dataset, readOnly = false }) {
  const { t, i18n } = useTranslation();
  const [publication, setPublication] = useState(null);
  const [supersetInstances, setSupersetInstances] = useState([]);
  const [selectedInstanceId, setSelectedInstanceId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // last publish action's own response — carries columns_count

  const load = async () => {
    setLoading(true);
    try {
      const [pub, instances] = await Promise.all([
        medallionApi.getDatasetPublication(project.id, dataset.id),
        supersetApi.listInstances(),
      ]);
      setPublication(pub);
      setSupersetInstances(instances.filter((i) => i.is_active));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [project.id, dataset.id]);

  const publish = async (instanceId) => {
    setBusy(true);
    setResult(null);
    try {
      const res = await medallionApi.publishDataset(project.id, dataset.id, instanceId ? { superset_instance_id: Number(instanceId) } : {});
      setResult(res);
      if (res.status === "ok") await load();
    } catch {
      setResult({ status: "superset_error" });
    } finally {
      setBusy(false);
    }
  };

  const unpublish = async () => {
    setBusy(true);
    try {
      await medallionApi.unpublishDataset(project.id, dataset.id);
      setResult(null);
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;

  const isPublished = publication?.published;
  // Same principle as airflow_instance_id, deferred: no instance chosen yet (at project
  // creation or since) — ask for one right here, the first time this project publishes.
  const needsInstanceChoice = !isPublished && (!project.superset_instance_id || result?.status === "no_instance");

  return (
    <div>
      {isPublished && (
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <Badge tone="accent">{t("medallion.publish.published")}</Badge>
            {result?.status === "ok" && result.columns_count != null && (
              <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.publish.columnsCount", { count: result.columns_count })}</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {t("medallion.publish.lastPublished")}{" "}
            <span style={{ fontFamily: "var(--font-m)" }}>{publication.last_published_at ? new Date(publication.last_published_at).toLocaleString(i18n.language) : "—"}</span>
            {publication.published_by && <> · {publication.published_by}</>}
          </div>
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn-ghost" style={{ padding: "6px 10px", textDecoration: "none" }} href={publication.url} target="_blank" rel="noreferrer">
              {Icon.externalLink()} {t("medallion.publish.openInSuperset")}
            </a>
            {!readOnly && (
              <>
                <Button variant="ghost" className="inline" disabled={busy} onClick={() => publish()}>{busy ? t("medallion.publish.publishing") : t("medallion.publish.republish")}</Button>
                <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={busy} onClick={unpublish}>{t("medallion.publish.unpublish")}</button>
              </>
            )}
          </div>
        </div>
      )}

      {!isPublished && !readOnly && needsInstanceChoice && (
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 10 }}>{t("medallion.publish.chooseInstance")}</div>
          <select className="input" style={{ marginBottom: 12 }} value={selectedInstanceId} onChange={(e) => setSelectedInstanceId(e.target.value)}>
            <option value="">{t("medallion.wizard.choose")}</option>
            {supersetInstances.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
          {supersetInstances.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.publish.noInstanceAvailable")}</div>
          )}
          <Button disabled={busy || !selectedInstanceId} onClick={() => publish(selectedInstanceId)}>
            {busy ? t("medallion.publish.publishing") : t("medallion.publish.chooseAndPublish")}
          </Button>
        </div>
      )}

      {!isPublished && !needsInstanceChoice && (
        <div className="card" style={{ padding: 16, textAlign: "center" }}>
          <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>{t("medallion.publish.notPublishedYet")}</div>
          {!readOnly && (
            <Button disabled={busy} onClick={() => publish()}>{busy ? t("medallion.publish.publishing") : t("medallion.publish.publishAction")}</Button>
          )}
        </div>
      )}

      {result && result.status !== "ok" && result.status !== "no_instance" && (
        <div className="error-banner" style={{ marginTop: 12 }}>
          {Icon.warn()}<span>{result.message || t(`medallion.publish.status.${result.status}`)}</span>
        </div>
      )}
    </div>
  );
}
