import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as airflowInstancesApi from "../../api/airflowInstances.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Icon } from "../../components/icons.jsx";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import DeployAccessPanel from "./DeployAccessPanel.jsx";

const TEST_COLOR = { reachable: "#2f9e6e", unreachable: "#c53d3d", unknown: "#c98a1c", unsupported_version: "#c53d3d" };

export default function OrchestratorDetail() {
  const { t } = useTranslation();
  const { id } = useParams();
  const navigate = useNavigate();
  const showToast = useToast();

  const [instance, setInstance] = useState(null);
  const [dags, setDags] = useState([]);
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [testing, setTesting] = useState(false);
  const [newConn, setNewConn] = useState({ connection_id: "", conn_type: "postgres", host: "", login: "", password: "", port: "" });
  const [creating, setCreating] = useState(false);

  const STATUS_LABEL = t("orchestrators.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const inst = await airflowInstancesApi.getInstance(id);
      setInstance(inst);
      const [dagList, conns] = await Promise.all([
        airflowInstancesApi.listDags(id).catch(() => []),
        airflowInstancesApi.listConnections(id).catch(() => []),
      ]);
      setDags(dagList);
      setConnections(conns);
    } catch (err) {
      setLoadError(err.message || t("orchestrators.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await airflowInstancesApi.testInstance(id);
      showToast(result.message);
      await load();
    } catch (err) {
      showToast(err.message || t("orchestrators.testFailed"));
    } finally {
      setTesting(false);
    }
  };

  const togglePause = async (dagId, isPaused) => {
    try {
      await airflowInstancesApi.setDagPaused(id, dagId, !isPaused);
      await load();
    } catch (err) {
      showToast(err.message || t("orchestrators.actionFailed"));
    }
  };

  const triggerRun = async (dagId) => {
    try {
      await airflowInstancesApi.triggerDagRun(id, dagId, {});
      showToast(t("orchestrators.runTriggered", { dagId }));
    } catch (err) {
      showToast(err.message || t("orchestrators.triggerFailed"));
    }
  };

  const createConnection = async (e) => {
    e?.preventDefault();
    setCreating(true);
    try {
      await airflowInstancesApi.createConnection(id, newConn);
      showToast(t("orchestrators.connectionCreated"));
      setNewConn({ connection_id: "", conn_type: "postgres", host: "", login: "", password: "", port: "" });
      const conns = await airflowInstancesApi.listConnections(id);
      setConnections(conns);
    } catch (err) {
      showToast(err.message || t("orchestrators.createConnFailed"));
    } finally {
      setCreating(false);
    }
  };

  if (loading && !instance) return <div style={{ color: "var(--text-muted)" }}>{t("common.loading")}</div>;
  if (!instance) return <div style={{ color: "var(--text-muted)" }}>{loadError || t("orchestrators.notFound")}</div>;

  const managedDags = dags.filter((d) => d.managed);
  const observedDags = dags.filter((d) => !d.managed);

  return (
    <>
      <div className="page-head">
        <div>
          <button className="link" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 10 }} onClick={() => navigate("/orchestrators")}>
            {Icon.arrowLeft({ width: 14, height: 14 })} {t("orchestrators.title")}
          </button>
          <h1 className="page-title">{instance.name}</h1>
          <p className="page-desc" style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{instance.base_url}</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {instance.origin === "platform"
            ? <Badge tone="accent">{t("orchestrators.platformManaged")}</Badge>
            : <Badge tone="neutral">{t("orchestrators.external")}</Badge>}
          <a className="btn-ghost" href={instance.base_url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
            {Icon.externalLink()} {t("orchestrators.open")}
          </a>
        </div>
      </div>

      {loadError && <div className="error-banner">{Icon.warn()}<span>{loadError}</span></div>}

      <div className="card" style={{ padding: 16, marginBottom: 20, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <span className="status">
            <StatusDot color={TEST_COLOR[instance.status]} />
            {STATUS_LABEL[instance.status]}
          </span>
          {instance.airflow_version && <span style={{ marginLeft: 12, fontFamily: "var(--font-m)", fontSize: 12, color: "var(--text-muted)" }}>Airflow {instance.airflow_version}</span>}
        </div>
        <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={testing} onClick={handleTest}>
          {Icon.refresh()} {testing ? t("orchestrators.testing") : t("common.test")}
        </button>
      </div>

      {instance.origin === "external" && (
        <DeployAccessPanel instance={instance} onInstanceChanged={setInstance} />
      )}

      <div className="page-head" style={{ marginBottom: 12, marginTop: 24 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("orchestrators.connections")}</h2>
      </div>
      <div className="table-wrap" style={{ marginBottom: 20 }}>
        <table className="table">
          <thead><tr><th>{t("orchestrators.colConnId")}</th><th>{t("orchestrators.colType")}</th><th>{t("orchestrators.colHost")}</th><th>{t("orchestrators.colPort")}</th></tr></thead>
          <tbody>
            {connections.length === 0 && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("orchestrators.noConnections")}</td></tr>}
            {connections.map((c) => (
              <tr key={c.connection_id}>
                <td className="uc-name">{c.connection_id}</td>
                <td>{c.conn_type}</td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{c.host || "—"}</td>
                <td>{c.port || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form onSubmit={createConnection} className="card" style={{ padding: 16, marginBottom: 28 }}>
        <div className="field-label" style={{ marginBottom: 10 }}>{t("orchestrators.newConnection")}</div>
        <div className="service-tile-body">
          <Field label={t("orchestrators.colConnId")}><Input value={newConn.connection_id} onChange={(e) => setNewConn((c) => ({ ...c, connection_id: e.target.value }))} required /></Field>
          <Field label={t("orchestrators.colType")}><Input value={newConn.conn_type} onChange={(e) => setNewConn((c) => ({ ...c, conn_type: e.target.value }))} /></Field>
          <Field label={t("orchestrators.colHost")}><Input value={newConn.host} onChange={(e) => setNewConn((c) => ({ ...c, host: e.target.value }))} /></Field>
          <Field label={t("orchestrators.colPort")}><Input type="number" value={newConn.port} onChange={(e) => setNewConn((c) => ({ ...c, port: Number(e.target.value) }))} /></Field>
          <Field label={t("orchestrators.login")}><Input value={newConn.login} onChange={(e) => setNewConn((c) => ({ ...c, login: e.target.value }))} /></Field>
          <Field label={t("orchestrators.password")}><Input type="password" value={newConn.password} onChange={(e) => setNewConn((c) => ({ ...c, password: e.target.value }))} /></Field>
        </div>
        <Button type="submit" className="inline" style={{ marginTop: 14 }} disabled={creating || !newConn.connection_id}>{creating ? t("orchestrators.creating") : t("orchestrators.createConnection")}</Button>
      </form>

      <div className="page-head" style={{ marginBottom: 12 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("orchestrators.dagsManaged")}</h2>
        <p className="page-desc" style={{ margin: 0, fontSize: 12.5 }}>{t("orchestrators.dagsManagedDesc")}</p>
      </div>
      <DagTable dags={managedDags} loading={loading} onTogglePause={togglePause} onTrigger={triggerRun} pilotable />

      <div className="page-head" style={{ marginTop: 24, marginBottom: 12 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("orchestrators.dagsObserved")}</h2>
        <p className="page-desc" style={{ margin: 0, fontSize: 12.5 }}>{t("orchestrators.dagsObservedDesc")}</p>
      </div>
      <DagTable dags={observedDags} loading={loading} pilotable={false} />
    </>
  );
}

function DagTable({ dags, loading, onTogglePause, onTrigger, pilotable }) {
  const { t } = useTranslation();
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>{t("orchestrators.colDag")}</th><th>{t("common.status")}</th>
            {pilotable && <th style={{ textAlign: "right" }}>{t("common.actions")}</th>}
          </tr>
        </thead>
        <tbody>
          {loading && <tr><td colSpan={pilotable ? 3 : 2} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
          {!loading && dags.length === 0 && <tr><td colSpan={pilotable ? 3 : 2} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("orchestrators.noDags")}</td></tr>}
          {dags.map((d) => (
            <tr key={d.dag_id}>
              <td className="uc-name" style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>{d.dag_id}</td>
              <td><Badge tone={d.is_paused ? "neutral" : "accent"}>{d.is_paused ? t("orchestrators.paused") : t("orchestrators.active")}</Badge></td>
              {pilotable && (
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => onTogglePause(d.dag_id, d.is_paused)}>
                    {d.is_paused ? Icon.play() : Icon.pause()} {d.is_paused ? t("orchestrators.resume") : t("orchestrators.pause")}
                  </button>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => onTrigger(d.dag_id)}>{Icon.play()} {t("orchestrators.run")}</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
