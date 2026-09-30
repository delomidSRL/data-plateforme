import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import Editor from "@monaco-editor/react";
import "../../lib/monaco.js";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";

// Module 19 §3.6 — the explorer groups files the same way the spec lists them: one header
// per dbt sub-folder, root files (dbt_project.yml, packages.yml) ungrouped at the top.
// `profiles.yml` never appears here — the backend's workspace never stores it (§2).
const GROUP_ORDER = ["", "models/bronze", "models/silver", "models/gold", "macros", "tests", "seeds"];

function groupKey(path) {
  const parts = path.split("/");
  if (parts.length === 1) return "";
  if (parts[0] === "models") return `${parts[0]}/${parts[1]}`;
  return parts[0];
}

function languageFor(path) {
  if (path.endsWith(".sql")) return "sql";
  if (path.endsWith(".yml") || path.endsWith(".yaml")) return "yaml";
  if (path.endsWith(".md")) return "markdown";
  return "plaintext";
}

const STATUS_TONE = { modified: "accent", code: "accent" };

/** Module 19 étape 1 — read-only Code tab: arborescence + Monaco viewer (§3.6). No write
 * endpoint exists yet (§3.7 DoD #4) — étape 2 is what turns this editable. `initialDatasetId`
 * lets a canvas node's "Voir le code" action (DatasetPanel) jump straight to its file. */
export default function CodeTab({ project, initialDatasetId, onConsumedInitialDataset }) {
  const { t } = useTranslation();
  const [tree, setTree] = useState(null);
  const [error, setError] = useState(null);
  const [activePath, setActivePath] = useState(null);
  const [openPaths, setOpenPaths] = useState([]);
  const [contents, setContents] = useState({}); // path -> { content, status, version, loading, error }

  useEffect(() => {
    let cancelled = false;
    medallionApi.getWorkspaceTree(project.id)
      .then((res) => { if (!cancelled) setTree(res.files); })
      .catch((err) => { if (!cancelled) setError(err.message || t("medallion.code.loadFailed")); });
    return () => { cancelled = true; };
  }, [project.id, t]);

  const openFile = (path) => {
    setActivePath(path);
    setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setContents((prev) => {
      if (prev[path]) return prev;
      medallionApi.getWorkspaceFile(project.id, path)
        .then((res) => setContents((p) => ({ ...p, [path]: { ...res, loading: false } })))
        .catch((err) => setContents((p) => ({ ...p, [path]: { error: err.message || t("medallion.code.loadFailed"), loading: false } })));
      return { ...prev, [path]: { loading: true } };
    });
  };

  useEffect(() => {
    if (!tree || !initialDatasetId) return;
    const match = tree.find((f) => f.dataset_id === initialDatasetId);
    if (match) openFile(match.path);
    onConsumedInitialDataset?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, initialDatasetId]);

  const groups = useMemo(() => {
    if (!tree) return [];
    const byGroup = {};
    for (const f of tree) (byGroup[groupKey(f.path)] ||= []).push(f);
    const keys = Object.keys(byGroup).sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a), ib = GROUP_ORDER.indexOf(b);
      if (ia !== -1 && ib !== -1) return ia - ib;
      if (ia !== -1) return -1;
      if (ib !== -1) return 1;
      return a.localeCompare(b);
    });
    return keys.map((g) => ({ key: g, label: g || t("medallion.code.rootGroup"), files: [...byGroup[g]].sort((a, b) => a.path.localeCompare(b.path)) }));
  }, [tree, t]);

  const closeFile = (path, e) => {
    e?.stopPropagation();
    setOpenPaths((prev) => {
      const remaining = prev.filter((p) => p !== path);
      if (activePath === path) setActivePath(remaining[remaining.length - 1] || null);
      return remaining;
    });
  };

  if (error) return <div className="error-banner">{Icon.warn()}<span>{error}</span></div>;
  if (!tree) return <div style={{ padding: 16, color: "var(--text-muted)", fontSize: 12.5 }}>{t("medallion.code.loading")}</div>;

  const active = activePath ? contents[activePath] : null;

  return (
    <div className="card" style={{ padding: 0, display: "flex", height: 560, overflow: "hidden" }}>
      <div style={{ width: 260, borderRight: "1px solid var(--border)", overflowY: "auto", padding: 10, flexShrink: 0 }}>
        {tree.length === 0 && <div style={{ fontSize: 12, color: "var(--text-muted)", padding: 8 }}>{t("medallion.code.noFiles")}</div>}
        {groups.map((g) => (
          <div key={g.key} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".04em", textTransform: "uppercase", color: "var(--text-muted)", padding: "4px 6px" }}>
              {g.label}
            </div>
            {g.files.map((f) => {
              const name = f.path.split("/").pop();
              const isActive = activePath === f.path;
              return (
                <button
                  key={f.path} type="button" onClick={() => openFile(f.path)}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, width: "100%",
                    padding: "6px 8px", borderRadius: 7, fontSize: 12.5, fontFamily: "var(--font-m)", textAlign: "left",
                    background: isActive ? "var(--ember-soft)" : "transparent",
                    color: isActive ? "var(--ember-600)" : "var(--text)",
                  }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
                  {STATUS_TONE[f.status] && <Badge tone={STATUS_TONE[f.status]}>{t(`medallion.code.status.${f.status}`)}</Badge>}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {openPaths.length > 0 && (
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)", overflowX: "auto", flexShrink: 0 }}>
            {openPaths.map((p) => {
              const isActive = activePath === p;
              return (
                <div
                  key={p} onClick={() => setActivePath(p)}
                  style={{
                    display: "flex", alignItems: "center", gap: 6, padding: "8px 10px", fontSize: 12, fontFamily: "var(--font-m)",
                    borderRight: "1px solid var(--border)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                    background: isActive ? "var(--bg)" : "transparent", color: isActive ? "var(--text)" : "var(--text-muted)",
                  }}
                >
                  {p.split("/").pop()}
                  <span onClick={(e) => closeFile(p, e)} style={{ opacity: .6, display: "flex" }}>{Icon.x({ width: 11, height: 11 })}</span>
                </div>
              );
            })}
          </div>
        )}

        {!activePath && (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 12.5, textAlign: "center", padding: 20 }}>
            {t("medallion.code.selectFile")}
          </div>
        )}

        {activePath && active?.loading && (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 12.5 }}>
            {t("medallion.code.loading")}
          </div>
        )}

        {activePath && active?.error && (
          <div className="error-banner" style={{ margin: 12 }}>{Icon.warn()}<span>{active.error}</span></div>
        )}

        {activePath && active && !active.loading && !active.error && (
          <div style={{ flex: 1, minHeight: 0 }}>
            <Editor
              height="100%"
              language={languageFor(activePath)}
              value={active.content}
              theme="vs"
              options={{ readOnly: true, minimap: { enabled: false }, fontFamily: "JetBrains Mono, monospace", fontSize: 12.5, scrollBeyondLastLine: false }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
