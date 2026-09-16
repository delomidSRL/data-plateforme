import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import RestoreVersionModal from "./RestoreVersionModal.jsx";

const monoBlock = {
  flex: 1, fontFamily: "var(--font-m)", fontSize: 11.5, padding: 8, borderRadius: 6,
  border: "1px solid var(--border)", whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0,
};

export default function VersionsTab({ project, onRestored, readOnly = false }) {
  const { t, i18n } = useTranslation();
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [diff, setDiff] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      setVersions(await medallionApi.listVersions(project.id));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [project.id]);

  const toggleDiff = async (version) => {
    if (expanded === version.id) { setExpanded(null); return; }
    setExpanded(version.id);
    setDiff(null);
    setDiffLoading(true);
    try {
      setDiff(await medallionApi.getVersionDiff(project.id, version.id, "active"));
    } catch {
      setDiff(null);
    } finally {
      setDiffLoading(false);
    }
  };

  const hasDiffContent = diff && (diff.datasets_added.length || diff.datasets_removed.length || diff.sql_changed.length || diff.tests_changed.length);

  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("medallion.versions.colNumber")}</th>
              <th>{t("medallion.versions.colDate")}</th>
              <th>{t("medallion.versions.colAuthor")}</th>
              <th>{t("medallion.versions.colStatus")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>}
            {!loading && versions.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("medallion.versions.noVersions")}</td></tr>
            )}
            {versions.map((v) => {
              const isActive = v.id === project.active_version_id;
              return (
                <Fragment key={v.id}>
                  <tr>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5 }}>v{v.version_number}</td>
                    <td style={{ fontFamily: "var(--font-m)", fontSize: 12 }}>{new Date(v.created_at).toLocaleString(i18n.language)}</td>
                    <td style={{ fontSize: 12.5 }}>{v.created_by_name || "—"}</td>
                    <td style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {isActive && <Badge tone="accent">{t("medallion.versions.active")}</Badge>}
                      {v.is_restore_of_version_number != null && (
                        <Badge tone="neutral">{t("medallion.versions.restoreOf", { n: v.is_restore_of_version_number })}</Badge>
                      )}
                    </td>
                    <td style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                      {!isActive && (
                        <>
                          <Button variant="ghost" className="inline" onClick={() => toggleDiff(v)}>
                            {expanded === v.id ? t("medallion.versions.hideDiff") : t("medallion.versions.compare")}
                          </Button>
                          {!readOnly && <Button className="inline" onClick={() => setRestoreTarget(v)}>{t("medallion.versions.restore")}</Button>}
                        </>
                      )}
                    </td>
                  </tr>
                  {expanded === v.id && (
                    <tr>
                      <td colSpan={5} style={{ padding: 0 }}>
                        <div className="card" style={{ padding: 14, margin: "0 0 10px" }}>
                          {diffLoading && <div style={{ color: "var(--text-muted)", fontSize: 12.5 }}>{t("common.loading")}</div>}
                          {!diffLoading && !diff && <div style={{ color: "var(--text-muted)", fontSize: 12.5 }}>{t("medallion.versions.diffFailed")}</div>}
                          {!diffLoading && diff && !hasDiffContent && (
                            <div style={{ color: "var(--text-muted)", fontSize: 12.5 }}>{t("medallion.versions.noDiff")}</div>
                          )}
                          {!diffLoading && diff && hasDiffContent && (
                            <>
                              {diff.datasets_added.length > 0 && (
                                <div style={{ marginBottom: 10 }}>
                                  <div className="field-label" style={{ marginBottom: 6 }}>{t("medallion.versions.diffAdded")}</div>
                                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                                    {diff.datasets_added.map((name) => <Badge key={name} tone="accent">{name}</Badge>)}
                                  </div>
                                </div>
                              )}
                              {diff.datasets_removed.length > 0 && (
                                <div style={{ marginBottom: 10 }}>
                                  <div className="field-label" style={{ marginBottom: 6 }}>{t("medallion.versions.diffRemoved")}</div>
                                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                                    {diff.datasets_removed.map((name) => (
                                      <span key={name} className="badge badge-neutral" style={{ color: "#b3261e", borderColor: "#b3261e" }}>{name}</span>
                                    ))}
                                  </div>
                                </div>
                              )}
                              {diff.sql_changed.map((c) => (
                                <div key={c.name} style={{ marginBottom: 10 }}>
                                  <div className="field-label" style={{ marginBottom: 6 }}>{t("medallion.versions.diffSqlChanged", { name: c.name })}</div>
                                  <div style={{ display: "flex", gap: 8 }}>
                                    <pre style={{ ...monoBlock, background: "#fdecea" }}>{c.before || "—"}</pre>
                                    <pre style={{ ...monoBlock, background: "#e9f7ee" }}>{c.after || "—"}</pre>
                                  </div>
                                </div>
                              ))}
                              {diff.tests_changed.map((c) => (
                                <div key={c.name} style={{ marginBottom: 10 }}>
                                  <div className="field-label" style={{ marginBottom: 6 }}>{t("medallion.versions.diffTestsChanged", { name: c.name })}</div>
                                  <div style={{ display: "flex", gap: 8 }}>
                                    <pre style={{ ...monoBlock, background: "#fdecea" }}>{JSON.stringify(c.before)}</pre>
                                    <pre style={{ ...monoBlock, background: "#e9f7ee" }}>{JSON.stringify(c.after)}</pre>
                                  </div>
                                </div>
                              ))}
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {restoreTarget && (
        <RestoreVersionModal
          project={project}
          version={restoreTarget}
          onClose={() => setRestoreTarget(null)}
          onRestored={(newVersion) => {
            setRestoreTarget(null);
            setExpanded(null);
            load();
            onRestored?.(newVersion);
          }}
        />
      )}
    </>
  );
}
