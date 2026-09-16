import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as sourcesApi from "../../api/sources.js";
import { useToast } from "../../context/ToastContext.jsx";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import AddSourceDrawer from "./AddSourceDrawer.jsx";
import ExplorerDrawer from "./ExplorerDrawer.jsx";

const TYPE_LABEL = { postgresql: "PostgreSQL", mysql: "MySQL", oracle: "Oracle", minio: "MinIO" };
const STATUS_COLOR = { reachable: "#2f9e6e", unreachable: "#c53d3d", unknown: "#c98a1c" };

export default function SourcesList() {
  const { t, i18n } = useTranslation();
  const showToast = useToast();
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingSource, setEditingSource] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [explorerSource, setExplorerSource] = useState(null);

  const STATUS_LABEL = t("sources.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      setSources(await sourcesApi.listSources());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleTest = async (id) => {
    setTestingId(id);
    try {
      const result = await sourcesApi.testSource(id);
      showToast(result.reachable
        ? t("sources.testSuccess", { ms: result.latency_ms, version: result.version ? " — " + result.version : "" })
        : result.message);
      await load();
    } catch (err) {
      showToast(err.message || t("sources.testFailed"));
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = async (id, name) => {
    try {
      await sourcesApi.deleteSource(id);
      setSources((s) => s.filter((x) => x.id !== id));
      showToast(t("sources.deletedToast", { name }));
    } catch (err) {
      showToast(err.message || t("sources.deleteFailed"));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("sources.badge")}</div>
          <h1 className="page-title">{t("sources.title")}</h1>
          <p className="page-desc">{t("sources.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setDrawerOpen(true)}>{Icon.plus()}{t("sources.addSource")}</button>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("sources.colSource")}</th><th>{t("sources.colType")}</th><th>{t("sources.colOrigin")}</th><th>{t("sources.colHost")}</th><th>{t("common.status")}</th><th>{t("sources.colLastTest")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && sources.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("sources.noSources")}</td></tr>
            )}
            {sources.map((s) => (
              <tr key={s.id}>
                <td>
                  <div className="uc-name">{s.name}</div>
                  <div className="uc-mail">{s.username}@{s.host}:{s.port}</div>
                </td>
                <td>{TYPE_LABEL[s.type]}</td>
                <td>
                  {s.origin === "platform"
                    ? <Badge tone="accent">{t("sources.platformManaged")}</Badge>
                    : <Badge tone="neutral">{t("sources.external")}</Badge>}
                </td>
                <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{s.host}</td>
                <td>
                  <span className="status">
                    <StatusDot color={STATUS_COLOR[s.status]} />
                    {STATUS_LABEL[s.status]}
                  </span>
                </td>
                <td style={{ color: "var(--text-muted)", fontFamily: "var(--font-m)", fontSize: 12 }}>
                  {s.last_tested_at ? new Date(s.last_tested_at).toLocaleString(i18n.language) : "—"}
                </td>
                <td style={{ textAlign: "right", display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} disabled={testingId === s.id} onClick={() => handleTest(s.id)}>
                    {Icon.refresh()} {testingId === s.id ? t("sources.testing") : t("common.test")}
                  </button>
                  <button className="btn-ghost" style={{ padding: "6px 10px" }} onClick={() => setExplorerSource(s)}>{t("sources.explore")}</button>
                  {s.origin === "external" && (
                    <>
                      <button className="btn-icon" onClick={() => setEditingSource(s)} title={t("common.edit")}>{Icon.edit()}</button>
                      <button className="btn-icon" onClick={() => handleDelete(s.id, s.name)} title={t("common.delete")}>{Icon.trash()}</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {drawerOpen && (
        <AddSourceDrawer
          onClose={() => setDrawerOpen(false)}
          onCreated={(s) => { setSources((list) => [...list, s]); setDrawerOpen(false); showToast(t("sources.createdToast", { name: s.name })); }}
        />
      )}

      {editingSource && (
        <AddSourceDrawer
          source={editingSource}
          onClose={() => setEditingSource(null)}
          onUpdated={(s) => { setSources((list) => list.map((x) => (x.id === s.id ? s : x))); setEditingSource(null); showToast(t("sources.updatedToast", { name: s.name })); }}
        />
      )}

      {explorerSource && (
        <ExplorerDrawer source={explorerSource} onClose={() => setExplorerSource(null)} />
      )}
    </>
  );
}
