import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as airflowApi from "../../api/airflow.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Field, Input } from "../../components/ui/Input.jsx";
import { Icon } from "../../components/icons.jsx";
import { Badge } from "../../components/ui/Badge.jsx";

export default function AirflowTab({ serverId, stack, server }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const webPort = stack.services?.airflow?.web_port;
  const airflowUrl = `http://${server.hostname}:${webPort}`;

  const [connections, setConnections] = useState([]);
  const [dags, setDags] = useState([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [newConn, setNewConn] = useState({ connection_id: "", conn_type: "postgres", host: "", login: "", password: "", port: "" });
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [conns, dagList] = await Promise.all([
        airflowApi.listConnections(serverId, stack.id),
        airflowApi.listDags(serverId, stack.id),
      ]);
      setConnections(conns);
      setDags(dagList);
    } catch (err) {
      setLoadError(err.message || t("servers.airflow.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [stack?.id]);

  const createConnection = async (e) => {
    e?.preventDefault();
    setCreating(true);
    try {
      await airflowApi.createConnection(serverId, stack.id, newConn);
      showToast(t("servers.airflow.connectionCreated"));
      setNewConn({ connection_id: "", conn_type: "postgres", host: "", login: "", password: "", port: "" });
      await load();
    } catch (err) {
      showToast(err.message || t("servers.airflow.createFailed"));
    } finally {
      setCreating(false);
    }
  };

  const togglePause = async (dagId, isPaused) => {
    try {
      await airflowApi.setDagPaused(serverId, stack.id, dagId, !isPaused);
      await load();
    } catch (err) {
      showToast(err.message || t("servers.airflow.actionFailed"));
    }
  };

  const triggerRun = async (dagId) => {
    try {
      await airflowApi.triggerDagRun(serverId, stack.id, dagId, {});
      showToast(t("servers.airflow.runTriggered", { dagId }));
    } catch (err) {
      showToast(err.message || t("servers.airflow.triggerFailed"));
    }
  };

  return (
    <>
      <div className="card" style={{ padding: 16, marginBottom: 20, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div className="uc-name">{t("servers.airflow.interface")}</div>
          <div className="uc-mail">{airflowUrl}</div>
        </div>
        <a className="btn-ghost" href={airflowUrl} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
          {Icon.externalLink()} {t("servers.airflow.open")}
        </a>
      </div>

      {loadError && <div className="error-banner">{Icon.warn()}<span>{loadError}</span></div>}

      <div className="page-head" style={{ marginBottom: 12 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("servers.airflow.connections")}</h2>
      </div>

      <div className="table-wrap" style={{ marginBottom: 20 }}>
        <table className="table">
          <thead><tr><th>{t("servers.airflow.colId")}</th><th>{t("servers.airflow.colType")}</th><th>{t("servers.airflow.colHost")}</th><th>{t("servers.airflow.colPort")}</th></tr></thead>
          <tbody>
            {loading && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && connections.length === 0 && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("servers.airflow.noConnections")}</td></tr>}
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
        <div className="field-label" style={{ marginBottom: 10 }}>{t("servers.airflow.newConnection")}</div>
        <div className="service-tile-body">
          <Field label={t("servers.airflow.connectionId")}><Input value={newConn.connection_id} onChange={(e) => setNewConn((c) => ({ ...c, connection_id: e.target.value }))} required /></Field>
          <Field label={t("servers.airflow.type")}><Input value={newConn.conn_type} onChange={(e) => setNewConn((c) => ({ ...c, conn_type: e.target.value }))} /></Field>
          <Field label={t("servers.airflow.host")}><Input value={newConn.host} onChange={(e) => setNewConn((c) => ({ ...c, host: e.target.value }))} /></Field>
          <Field label={t("servers.airflow.port")}><Input type="number" value={newConn.port} onChange={(e) => setNewConn((c) => ({ ...c, port: Number(e.target.value) }))} /></Field>
          <Field label={t("servers.airflow.login")}><Input value={newConn.login} onChange={(e) => setNewConn((c) => ({ ...c, login: e.target.value }))} /></Field>
          <Field label={t("servers.airflow.password")}><Input type="password" value={newConn.password} onChange={(e) => setNewConn((c) => ({ ...c, password: e.target.value }))} /></Field>
        </div>
        <Button type="submit" className="inline" style={{ marginTop: 14 }} disabled={creating || !newConn.connection_id}>{creating ? t("servers.airflow.creating") : t("servers.airflow.createConnection")}</Button>
      </form>

      <div className="page-head" style={{ marginBottom: 12 }}>
        <h2 style={{ fontFamily: "var(--font-d)", fontSize: 16, fontWeight: 600 }}>{t("servers.airflow.dags")}</h2>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>{t("servers.airflow.colDag")}</th><th>{t("common.status")}</th><th style={{ textAlign: "right" }}>{t("common.actions")}</th></tr></thead>
          <tbody>
            {loading && <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && dags.length === 0 && <tr><td colSpan={3} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("servers.airflow.noDags")}</td></tr>}
            {dags.map((d) => (
              <tr key={d.dag_id}>
                <td className="uc-name">{d.dag_id}</td>
                <td><Badge tone={d.is_paused ? "neutral" : "accent"}>{d.is_paused ? t("servers.airflow.paused") : t("servers.airflow.active")}</Badge></td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => togglePause(d.dag_id, d.is_paused)}>
                    {d.is_paused ? Icon.play() : Icon.pause()} {d.is_paused ? t("servers.airflow.resume") : t("servers.airflow.pause")}
                  </button>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => triggerRun(d.dag_id)}>{Icon.play()} {t("servers.airflow.run")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
