import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import Editor from "@monaco-editor/react";
import "../../lib/monaco.js";
import * as medallionApi from "../../api/medallion.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";

// Module 19 §3.6 — a real, collapsible folder tree (VSCode-style), built from the flat file
// list the backend returns. `profiles.yml` never appears here — the workspace never stores
// it (§2).
const INDENT = 16;

/** Flat [{path, status, ...}] -> a nested {type:"folder", name, path, children: Map} /
 * {type:"file", name, path, file} tree. `path` accumulates as we descend so a folder node's
 * own path can be used as its collapse-state key. */
function buildTree(files) {
  const root = { type: "folder", name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    let acc = "";
    parts.forEach((part, i) => {
      acc = acc ? `${acc}/${part}` : part;
      const isFile = i === parts.length - 1;
      if (isFile) {
        node.children.set(part, { type: "file", name: part, path: acc, file: f });
      } else {
        if (!node.children.has(part)) node.children.set(part, { type: "folder", name: part, path: acc, children: new Map() });
        node = node.children.get(part);
      }
    });
  }
  return root;
}

function sortedChildren(node) {
  return [...node.children.values()].sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function TreeNode({ node, depth, activePath, collapsed, onToggle, onOpenFile }) {
  const { t } = useTranslation();
  const rowStyle = (isActive) => ({
    display: "flex", alignItems: "center", gap: 6, width: "100%", padding: "5px 8px 5px 0",
    paddingLeft: 8 + depth * INDENT, borderRadius: 7, fontSize: 12.5, fontFamily: "var(--font-m)", textAlign: "left",
    background: isActive ? "var(--ember-soft)" : "transparent", color: isActive ? "var(--ember-600)" : "var(--text)",
  });

  if (node.type === "file") {
    const f = node.file;
    const isActive = activePath === f.path;
    return (
      <button type="button" onClick={() => onOpenFile(f.path)} style={rowStyle(isActive)}>
        <span style={{ width: 14, flexShrink: 0 }} />
        <span style={{ flexShrink: 0, opacity: .6, display: "flex" }}>{Icon.file()}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{node.name}</span>
        {STATUS_TONE[f.status] && <Badge tone={STATUS_TONE[f.status]}>{t(`medallion.code.status.${f.status}`)}</Badge>}
      </button>
    );
  }

  const isCollapsed = collapsed.has(node.path);
  return (
    <div>
      <button type="button" onClick={() => onToggle(node.path)} style={rowStyle(false)}>
        <span style={{ width: 14, flexShrink: 0, display: "flex", transform: isCollapsed ? "rotate(-90deg)" : "none", transition: "transform .12s" }}>
          {Icon.chevronDown()}
        </span>
        <span style={{ flexShrink: 0, opacity: .7, display: "flex" }}>{Icon.folder()}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 600 }}>{node.name}</span>
      </button>
      {!isCollapsed && sortedChildren(node).map((child) => (
        <TreeNode key={child.path} node={child} depth={depth + 1} activePath={activePath} collapsed={collapsed} onToggle={onToggle} onOpenFile={onOpenFile} />
      ))}
    </div>
  );
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
  const [collapsed, setCollapsed] = useState(() => new Set()); // folder paths currently collapsed

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

  const treeRoot = useMemo(() => (tree ? buildTree(tree) : null), [tree]);

  const toggleFolder = (path) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

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
        {sortedChildren(treeRoot).map((child) => (
          <TreeNode key={child.path} node={child} depth={0} activePath={activePath} collapsed={collapsed} onToggle={toggleFolder} onOpenFile={openFile} />
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
