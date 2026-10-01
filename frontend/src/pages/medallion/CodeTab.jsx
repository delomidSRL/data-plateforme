import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Editor from "@monaco-editor/react";
import "../../lib/monaco.js";
import * as medallionApi from "../../api/medallion.js";
import { ApiError } from "../../api/client.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon } from "../../components/icons.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import ConflictsPanel from "./ConflictsPanel.jsx";
import DataPreviewPanel from "./DataPreviewPanel.jsx";

// Module 19 étape 4 — compile/run-dev only make sense for an actual dbt model file; the dbt
// model name is exactly its filename stem (dbt's own convention, mirrored by _model_sql()).
function dbtModelNameFor(path) {
  const m = /^models\/(?:silver|gold)\/(.+)\.sql$/.exec(path || "");
  return m ? m[1] : null;
}

// Module 19 §3.6/§4.6 — a real, collapsible folder tree (VSCode-style), built from the flat
// file list the backend returns. `profiles.yml` never appears here — the workspace never
// stores it (§2).
const INDENT = 16;

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

function languageFor(path) {
  if (path.endsWith(".sql")) return "sql";
  if (path.endsWith(".yml") || path.endsWith(".yaml")) return "yaml";
  if (path.endsWith(".md")) return "markdown";
  return "plaintext";
}

const STATUS_TONE = { modified: "accent", code: "accent" };

function TreeNode({ node, depth, activePath, dirtyPaths, collapsed, onToggle, onOpenFile, onContextMenu }) {
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
      <button
        type="button" onClick={() => onOpenFile(f.path)} onContextMenu={(e) => onContextMenu(e, f.path)}
        style={rowStyle(isActive)}
      >
        <span style={{ width: 14, flexShrink: 0 }} />
        <span style={{ flexShrink: 0, opacity: .6, display: "flex" }}>{Icon.file()}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
          {dirtyPaths.has(f.path) && <span style={{ color: "var(--ember)" }}>● </span>}
          {node.name}
        </span>
        {STATUS_TONE[f.status] && <Badge tone={STATUS_TONE[f.status]}>{t(`medallion.code.status.${f.status}`)}</Badge>}
      </button>
    );
  }

  const isCollapsed = collapsed.has(node.path);
  return (
    <div>
      <button type="button" onClick={() => onToggle(node.path)} onContextMenu={(e) => onContextMenu(e, node.path, true)} style={rowStyle(false)}>
        <span style={{ width: 14, flexShrink: 0, display: "flex", transform: isCollapsed ? "rotate(-90deg)" : "none", transition: "transform .12s" }}>
          {Icon.chevronDown()}
        </span>
        <span style={{ flexShrink: 0, opacity: .7, display: "flex" }}>{Icon.folder()}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 600 }}>{node.name}</span>
      </button>
      {!isCollapsed && sortedChildren(node).map((child) => (
        <TreeNode
          key={child.path} node={child} depth={depth + 1} activePath={activePath} dirtyPaths={dirtyPaths}
          collapsed={collapsed} onToggle={onToggle} onOpenFile={onOpenFile} onContextMenu={onContextMenu}
        />
      ))}
    </div>
  );
}

/** Module 19 — the Code tab: arborescence + Monaco. Read-only in étape 1's sense is gone —
 * every file is now editable (§4.1), except the platform-managed ones (packages.yml,
 * profiles.yml — enforced server-side, §4.5) and everything when `readOnly` (admin
 * supervision view, M9) or the project isn't owned by the viewer (same 403/404 the API
 * itself enforces — this prop just keeps the UI from offering an action that would fail). */
export default function CodeTab({ project, readOnly = false, initialDatasetId, onConsumedInitialDataset, onSynced }) {
  const { t } = useTranslation();
  const showToast = useToast();
  const canEdit = !readOnly;

  const [tree, setTree] = useState(null);
  const [error, setError] = useState(null);
  const [activePath, setActivePath] = useState(null);
  const [openPaths, setOpenPaths] = useState([]);
  const [contents, setContents] = useState({}); // path -> { serverContent, serverVersion, draft, status, dataset_id, generator, loading, error }
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [savingPaths, setSavingPaths] = useState(() => new Set());
  const [contextMenu, setContextMenu] = useState(null); // { x, y, path, isFolder }
  const [lastSync, setLastSync] = useState(null); // { ok, errors } — the most recent write's sync result
  const [markers, setMarkers] = useState([]); // markers for the CURRENTLY ACTIVE file only
  const [conflicts, setConflicts] = useState([]); // Module 19 étape 3 — "À arbitrer"
  const [view, setView] = useState("tree"); // "tree" | "conflicts"
  const [compiling, setCompiling] = useState(false);
  const [runningDev, setRunningDev] = useState(false);
  const [compiledByPath, setCompiledByPath] = useState({}); // path -> compiled SQL text
  const [showCompiled, setShowCompiled] = useState(false);
  const [runResult, setRunResult] = useState(null); // { ok, nodes, datasetId } for the active file's last run-dev
  const [showPreview, setShowPreview] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [auditEntries, setAuditEntries] = useState([]);

  const editorRef = useRef(null);
  const monacoRef = useRef(null);

  const activeConflicts = useMemo(() => conflicts.filter((c) => c.status === "proposed" || c.status === "open"), [conflicts]);

  const loadTree = () => medallionApi.getWorkspaceTree(project.id).then((res) => setTree(res.files));
  const loadConflicts = () => medallionApi.getConflicts(project.id).then(setConflicts).catch(() => {});

  useEffect(() => {
    let cancelled = false;
    loadTree().catch((err) => { if (!cancelled) setError(err.message || t("medallion.code.loadFailed")); });
    loadConflicts();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  const handleConflictSettled = () => {
    loadConflicts().then(() => {
      loadTree();
      onSynced?.();
    });
  };

  const openFile = (path) => {
    setActivePath(path);
    setShowAudit(false);
    setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setContents((prev) => {
      if (prev[path]) return prev;
      medallionApi.getWorkspaceFile(project.id, path)
        .then((res) => setContents((p) => ({ ...p, [path]: { serverContent: res.content, serverVersion: res.version, draft: res.content, status: res.status, dataset_id: res.dataset_id, generator: res.generator, loading: false } })))
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
  const dirtyPaths = useMemo(() => new Set(Object.entries(contents).filter(([, c]) => c.draft !== undefined && c.draft !== c.serverContent).map(([p]) => p)), [contents]);

  const toggleFolder = (path) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  const closeFile = (path, e) => {
    e?.stopPropagation();
    if (dirtyPaths.has(path) && !window.confirm(t("medallion.code.discardConfirm"))) return;
    setOpenPaths((prev) => {
      const remaining = prev.filter((p) => p !== path);
      if (activePath === path) setActivePath(remaining[remaining.length - 1] || null);
      return remaining;
    });
  };

  const applySyncResult = (row, syncResult) => {
    setLastSync({ ...syncResult, path: row.path });
    if (activePath === row.path) {
      setMarkers((syncResult.errors || []).filter((e) => !e.path || e.path === row.path).map((e) => ({ line: e.line || 1, message: e.message })));
    }
    onSynced?.();
  };

  const save = async (path) => {
    const c = contents[path];
    if (!c || c.draft === c.serverContent) return;
    setSavingPaths((prev) => new Set(prev).add(path));
    try {
      const out = await medallionApi.putWorkspaceFile(project.id, path, c.draft, c.serverVersion);
      setContents((prev) => ({ ...prev, [path]: { ...prev[path], serverContent: out.content, serverVersion: out.version, status: out.status, dataset_id: out.dataset_id } }));
      setTree((prev) => prev.map((f) => (f.path === path ? { ...f, status: out.status, version: out.version, dataset_id: out.dataset_id } : f)));
      applySyncResult({ path }, out.sync);
      if (out.sync.ok) showToast(t("medallion.code.saved"));
      else showToast(t("medallion.code.savedWithErrors"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const violations = Array.isArray(err.detail) ? err.detail : [];
        setMarkers(violations.map((v) => ({ line: v.line || 1, message: v.message })));
        showToast(t("medallion.code.guardBlocked"));
      } else if (err instanceof ApiError && err.status === 409) {
        showToast(t("medallion.code.conflict"));
        if (window.confirm(t("medallion.code.conflictReload"))) {
          medallionApi.getWorkspaceFile(project.id, path).then((res) =>
            setContents((prev) => ({ ...prev, [path]: { serverContent: res.content, serverVersion: res.version, draft: res.content, status: res.status, dataset_id: res.dataset_id, generator: res.generator, loading: false } })),
          );
        }
      } else {
        showToast(err.message || t("medallion.code.saveFailed"));
      }
    } finally {
      setSavingPaths((prev) => { const next = new Set(prev); next.delete(path); return next; });
    }
  };

  const doCompile = async (path) => {
    const select = dbtModelNameFor(path);
    if (!select) return;
    setCompiling(true);
    setRunResult(null);
    try {
      const out = await medallionApi.compileWorkspace(project.id, select);
      if (out.ok) {
        setCompiledByPath((prev) => ({ ...prev, [path]: out.compiled_sql[path] || Object.values(out.compiled_sql)[0] || "" }));
        setShowCompiled(true);
        setMarkers([]);
        showToast(t("medallion.code.compileOk"));
      } else {
        setMarkers(out.errors.filter((e) => !e.path || e.path === path).map((e) => ({ line: e.line || 1, message: e.message })));
        setLastSync({ ok: false, errors: out.errors, path });
        showToast(t("medallion.code.compileFailed"));
      }
    } catch (err) {
      showToast(err.message || t("medallion.code.compileFailed"));
    } finally {
      setCompiling(false);
    }
  };

  const doRunDev = async (path) => {
    const select = dbtModelNameFor(path);
    if (!select) return;
    setRunningDev(true);
    try {
      const out = await medallionApi.runDev(project.id, select);
      const datasetId = contents[path]?.dataset_id ?? null;
      setRunResult({ ok: out.ok, nodes: out.nodes, datasetId, path });
      if (!out.ok) {
        setMarkers(out.errors.filter((e) => !e.path || e.path === path).map((e) => ({ line: e.line || 1, message: e.message })));
        setLastSync({ ok: false, errors: out.errors, path });
      }
      showToast(out.ok ? t("medallion.code.runDevOk") : t("medallion.code.runDevFailed"));
    } catch (err) {
      showToast(err.message || t("medallion.code.runDevFailed"));
    } finally {
      setRunningDev(false);
    }
  };

  const loadAudit = (path) => {
    if (showAudit) { setShowAudit(false); return; }
    medallionApi.getFileAudit(project.id, path)
      .then((entries) => { setAuditEntries(entries); setShowAudit(true); })
      .catch((err) => showToast(err.message || t("medallion.code.loadFailed")));
  };

  const handleEditorMount = (editor, monacoInstance) => {
    editorRef.current = editor;
    monacoRef.current = monacoInstance;
    editor.addCommand(monacoInstance.KeyMod.CtrlCmd | monacoInstance.KeyCode.KeyS, () => {
      if (activePath) save(activePath);
    });
  };

  useEffect(() => {
    if (!editorRef.current || !monacoRef.current) return;
    const model = editorRef.current.getModel();
    if (!model) return;
    monacoRef.current.editor.setModelMarkers(
      model, "dataplateforme",
      markers.map((m) => ({
        startLineNumber: m.line, endLineNumber: m.line, startColumn: 1, endColumn: 1000,
        message: m.message, severity: monacoRef.current.MarkerSeverity.Error,
      })),
    );
  }, [markers, activePath]);

  const newFile = (parentPath) => {
    const suggestion = parentPath ? `${parentPath}/` : "models/gold/";
    // eslint-disable-next-line no-alert
    const path = window.prompt(t("medallion.code.newFilePrompt"), suggestion);
    if (!path) return;
    medallionApi.createWorkspaceFile(project.id, path.trim(), "")
      .then((out) => {
        loadTree().then(() => openFile(out.path));
        applySyncResult({ path: out.path }, out.sync);
      })
      .catch((err) => showToast(err.message || t("medallion.code.saveFailed")));
  };

  const rename = (path) => {
    // eslint-disable-next-line no-alert
    const toPath = window.prompt(t("medallion.code.renamePrompt"), path);
    if (!toPath || toPath === path) return;
    const row = tree.find((f) => f.path === path);
    medallionApi.moveWorkspaceFile(project.id, path, toPath.trim(), row?.version ?? 0)
      .then((out) => {
        setOpenPaths((prev) => prev.map((p) => (p === path ? out.path : p)));
        setContents((prev) => { const { [path]: old, ...rest } = prev; return old ? { ...rest, [out.path]: { ...old, serverVersion: out.version, status: out.status } } : prev; });
        if (activePath === path) setActivePath(out.path);
        loadTree();
        applySyncResult({ path: out.path }, out.sync);
      })
      .catch((err) => showToast(err.message || t("medallion.code.saveFailed")));
  };

  const remove = (path) => {
    if (!window.confirm(t("medallion.code.deleteConfirm", { path }))) return;
    const row = tree.find((f) => f.path === path);
    const attempt = (confirm) => medallionApi.deleteWorkspaceFile(project.id, path, row?.version ?? 0, confirm)
      .then((sync) => {
        setOpenPaths((prev) => prev.filter((p) => p !== path));
        if (activePath === path) setActivePath(null);
        loadTree();
        applySyncResult({ path }, sync);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409 && err.detail?.dataset_name) {
          if (window.confirm(t("medallion.code.deleteConfirmDataset", { name: err.detail.dataset_name }))) attempt(true);
        } else {
          showToast(err.message || t("medallion.code.saveFailed"));
        }
      });
    attempt(false);
  };

  if (error) return <div className="error-banner">{Icon.warn()}<span>{error}</span></div>;
  if (!tree) return <div style={{ padding: 16, color: "var(--text-muted)", fontSize: 12.5 }}>{t("medallion.code.loading")}</div>;

  const active = activePath ? contents[activePath] : null;
  const activeDirty = activePath && dirtyPaths.has(activePath);
  const activeSaving = activePath && savingPaths.has(activePath);

  return (
    <div onClick={() => setContextMenu(null)}>
      {project.workspace_parse_status === "error" && (
        <div className="error-banner" style={{ marginBottom: 10 }}>
          {Icon.warn()}
          <span>
            {t("medallion.code.projectParseError")}
            {(project.workspace_parse_errors || []).slice(0, 3).map((e, i) => (
              <span key={i} style={{ display: "block", fontFamily: "var(--font-m)", fontSize: 11.5, marginTop: 4 }}>
                {e.path ? `${e.path}${e.line ? `:${e.line}` : ""} — ` : ""}{e.message}
              </span>
            ))}
          </span>
        </div>
      )}

      {activeConflicts.length > 0 && (
        <div className="error-banner" style={{ marginBottom: 10, background: "rgba(229,114,0,.08)", borderColor: "rgba(229,114,0,.3)", color: "var(--ember-600)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>{Icon.warn()}<span>{t("medallion.conflicts.banner", { count: activeConflicts.length })}</span></span>
          <button type="button" className="btn-ghost" style={{ padding: "4px 10px" }} onClick={() => setView(view === "conflicts" ? "tree" : "conflicts")}>
            {view === "conflicts" ? t("medallion.conflicts.backToTree") : t("medallion.conflicts.viewAction")}
          </button>
        </div>
      )}

      {view === "conflicts" ? (
        <ConflictsPanel project={project} conflicts={activeConflicts} onSettled={handleConflictSettled} />
      ) : (
      <div className="card" style={{ padding: 0, display: "flex", height: 560, overflow: "hidden" }}>
        <div style={{ width: 260, borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", flexShrink: 0 }}>
          {canEdit && (
            <div style={{ padding: 8, borderBottom: "1px solid var(--border)" }}>
              <button type="button" className="btn-ghost" style={{ width: "100%", padding: "5px 8px", fontSize: 12 }} onClick={() => newFile(null)}>
                {Icon.plus()} {t("medallion.code.newFile")}
              </button>
            </div>
          )}
          <div style={{ flex: 1, overflowY: "auto", padding: 10 }}>
            {tree.length === 0 && <div style={{ fontSize: 12, color: "var(--text-muted)", padding: 8 }}>{t("medallion.code.noFiles")}</div>}
            {sortedChildren(treeRoot).map((child) => (
              <TreeNode
                key={child.path} node={child} depth={0} activePath={activePath} dirtyPaths={dirtyPaths} collapsed={collapsed}
                onToggle={toggleFolder} onOpenFile={openFile}
                onContextMenu={(e, path, isFolder) => {
                  if (!canEdit) return;
                  e.preventDefault(); e.stopPropagation();
                  setContextMenu({ x: e.clientX, y: e.clientY, path, isFolder: !!isFolder });
                }}
              />
            ))}
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {openPaths.length > 0 && (
            <div style={{ display: "flex", borderBottom: "1px solid var(--border)", overflowX: "auto", flexShrink: 0 }}>
              {openPaths.map((p) => {
                const isActive = activePath === p;
                return (
                  <div
                    key={p} onClick={() => { setActivePath(p); setShowAudit(false); }}
                    style={{
                      display: "flex", alignItems: "center", gap: 6, padding: "8px 10px", fontSize: 12, fontFamily: "var(--font-m)",
                      borderRight: "1px solid var(--border)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: 0,
                      background: isActive ? "var(--bg)" : "transparent", color: isActive ? "var(--text)" : "var(--text-muted)",
                    }}
                  >
                    {dirtyPaths.has(p) && <span style={{ color: "var(--ember)" }}>●</span>}
                    {p.split("/").pop()}
                    <span onClick={(e) => closeFile(p, e)} style={{ opacity: .6, display: "flex" }}>{Icon.x({ width: 11, height: 11 })}</span>
                  </div>
                );
              })}
              {activePath && (
                <div style={{ marginLeft: canEdit ? "auto" : "auto", display: "flex", alignItems: "center", gap: 8, padding: "0 10px" }}>
                  <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => loadAudit(activePath)}>
                    {Icon.refresh()} {t("medallion.code.history")}
                  </button>
                  {canEdit && (
                    <Button className="inline" disabled={!activeDirty || activeSaving} onClick={() => save(activePath)}>
                      {activeSaving ? t("medallion.code.saving") : t("medallion.code.save")}
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}

          {showAudit && activePath && (
            <div style={{ borderBottom: "1px solid var(--border)", padding: 10, maxHeight: 160, overflowY: "auto", flexShrink: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".04em" }}>{t("medallion.code.historyTitle")}</span>
                <button type="button" className="btn-icon" onClick={() => setShowAudit(false)}>{Icon.x({ width: 13, height: 13 })}</button>
              </div>
              {auditEntries.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{t("medallion.code.historyEmpty")}</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {auditEntries.map((e) => (
                    <div key={e.id} style={{ fontSize: 11.5, display: "flex", gap: 8, fontFamily: "var(--font-m)" }}>
                      <span style={{ color: "var(--text-muted)" }}>{new Date(e.created_at).toLocaleString()}</span>
                      <Badge tone="neutral">{e.action}</Badge>
                      <span>{e.actor_name || e.generator || "—"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activePath && dbtModelNameFor(activePath) && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
              <Badge tone="neutral">{t("medallion.code.devBadge")}</Badge>
              <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} disabled={compiling} onClick={() => doCompile(activePath)}>
                {compiling ? t("medallion.code.compiling") : t("medallion.code.compile")}
              </button>
              {compiledByPath[activePath] && (
                <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12, opacity: showCompiled ? 1 : 0.6 }} onClick={() => setShowCompiled((s) => !s)}>
                  {t("medallion.code.toggleCompiled")}
                </button>
              )}
              {canEdit && (
                <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 12 }} disabled={runningDev} onClick={() => doRunDev(activePath)}>
                  {runningDev ? t("medallion.code.runningDev") : t("medallion.code.runDev")}
                </button>
              )}
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
            <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Editor
                  height="100%"
                  path={activePath}
                  language={languageFor(activePath)}
                  value={active.draft}
                  theme="vs"
                  onMount={handleEditorMount}
                  onChange={(value) => setContents((prev) => ({ ...prev, [activePath]: { ...prev[activePath], draft: value ?? "" } }))}
                  options={{ readOnly: !canEdit, minimap: { enabled: false }, fontFamily: "JetBrains Mono, monospace", fontSize: 12.5, scrollBeyondLastLine: false }}
                />
              </div>
              {showCompiled && compiledByPath[activePath] && (
                <div style={{ flex: 1, minWidth: 0, borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
                  <div style={{ padding: "5px 10px", fontSize: 11, color: "var(--text-muted)", borderBottom: "1px solid var(--border)", background: "var(--surface)", fontFamily: "var(--font-m)", flexShrink: 0 }}>
                    {t("medallion.code.compiledSqlLabel")}
                  </div>
                  <div style={{ flex: 1, minHeight: 0 }}>
                    <Editor
                      height="100%" language="sql" value={compiledByPath[activePath]} theme="vs"
                      options={{ readOnly: true, minimap: { enabled: false }, fontFamily: "JetBrains Mono, monospace", fontSize: 12.5, scrollBeyondLastLine: false }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {runResult && runResult.path === activePath && (
            <div style={{ borderTop: "1px solid var(--border)", padding: 10, flexShrink: 0 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                {runResult.nodes.map((n) => (
                  <Badge key={n.unique_id} tone={n.status === "success" || n.status === "pass" ? "accent" : "danger"}>
                    {n.name} · {n.status}
                  </Badge>
                ))}
              </div>
              {runResult.ok && runResult.datasetId != null && (
                <button
                  type="button"
                  className="btn-ghost"
                  style={{ display: "inline-flex", alignItems: "center", gap: 6, marginTop: 8, padding: "5px 12px", fontSize: 12, border: "1px solid var(--border)", borderRadius: 7 }}
                  onClick={() => setShowPreview((s) => !s)}
                >
                  {Icon.eye()} {showPreview ? t("medallion.dataPreview.hidePreview") : t("medallion.dataPreview.previewSource")}
                </button>
              )}
              {showPreview && runResult.ok && runResult.datasetId != null && (
                <div style={{ marginTop: 10 }}>
                  <DataPreviewPanel project={project} datasetId={runResult.datasetId} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      )}

      {lastSync && !lastSync.ok && (
        <div className="error-banner" style={{ marginTop: 10 }}>
          {Icon.warn()}
          <span>
            {t("medallion.code.syncErrors")}
            {lastSync.errors.map((e, i) => (
              <button
                key={i} type="button" className="link" style={{ display: "block", fontFamily: "var(--font-m)", fontSize: 11.5, marginTop: 4, textAlign: "left" }}
                onClick={() => e.path && openFile(e.path)}
              >
                {e.path ? `${e.path}${e.line ? `:${e.line}` : ""} — ` : ""}{e.message}
              </button>
            ))}
          </span>
        </div>
      )}

      {contextMenu && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "fixed", top: contextMenu.y, left: contextMenu.x, zIndex: 200,
            background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8,
            boxShadow: "0 12px 30px -10px rgba(21,27,33,.3)", padding: 4, minWidth: 160,
          }}
        >
          {!contextMenu.isFolder && (
            <button type="button" className="btn-ghost" style={{ display: "block", width: "100%", textAlign: "left", padding: "6px 10px" }}
              onClick={() => { rename(contextMenu.path); setContextMenu(null); }}>
              {Icon.edit()} {t("medallion.code.rename")}
            </button>
          )}
          {contextMenu.isFolder && (
            <button type="button" className="btn-ghost" style={{ display: "block", width: "100%", textAlign: "left", padding: "6px 10px" }}
              onClick={() => { newFile(contextMenu.path); setContextMenu(null); }}>
              {Icon.plus()} {t("medallion.code.newFile")}
            </button>
          )}
          {!contextMenu.isFolder && (
            <button type="button" className="btn-ghost" style={{ display: "block", width: "100%", textAlign: "left", padding: "6px 10px", color: "var(--danger)" }}
              onClick={() => { remove(contextMenu.path); setContextMenu(null); }}>
              {Icon.trash()} {t("medallion.code.delete")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
