import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import * as medallionApi from "../../api/medallion.js";
import * as foldersApi from "../../api/medallionFolders.js";
import * as sourcesApi from "../../api/sources.js";
import * as airflowInstancesApi from "../../api/airflowInstances.js";
import * as supersetApi from "../../api/superset.js";
import { Badge } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import NewProjectWizard from "./NewProjectWizard.jsx";
import FolderNameModal from "./FolderNameModal.jsx";
import DeleteFolderModal from "./DeleteFolderModal.jsx";
import MoveProjectModal from "./MoveProjectModal.jsx";

const STATUS_TONE = { draft: "neutral", built: "accent", deployed: "accent", paused: "neutral", error: "neutral" };

function ProjectRow({ p, sourceName, STATUS_LABEL, t, navigate, onMove }) {
  return (
    <tr key={p.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/medallion/${p.id}`)}>
      <td>
        <div className="uc-name">{p.name}</div>
        <div className="uc-mail">{p.dbt_project_name}</div>
      </td>
      <td style={{ fontSize: 13 }}>{sourceName(p.warehouse_source_id)}</td>
      <td style={{ fontSize: 13 }}>{sourceName(p.object_store_source_id)}</td>
      <td style={{ fontFamily: "var(--font-m)", fontSize: 12.5, color: "var(--text-muted)" }}>{p.schedule || t("medallion.manual")}</td>
      <td>
        <Badge tone={STATUS_TONE[p.status]}>{STATUS_LABEL[p.status]}</Badge>
        {p.has_pending_changes && <span style={{ marginLeft: 8 }}><Badge tone="neutral">{t("medallion.toRedeploy")}</Badge></span>}
      </td>
      <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
        <button type="button" className="link" style={{ fontSize: 12, marginRight: 10 }} onClick={() => onMove(p)}>
          {t("medallion.folders.moveAction")}
        </button>
        <span style={{ color: "var(--text-muted)" }}>{Icon.arrowLeft({ style: { transform: "rotate(180deg)" }, width: 16, height: 16 })}</span>
      </td>
    </tr>
  );
}

function FolderSection({ folder, projects, expanded, onToggle, onRename, onDelete, sourceName, STATUS_LABEL, t, navigate, onMove }) {
  const isRoot = folder === null;
  return (
    <div className="card" style={{ padding: 0, marginBottom: 14, overflow: "hidden" }}>
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", cursor: "pointer", borderBottom: expanded ? "1px solid var(--border)" : "none" }}
        onClick={onToggle}
      >
        <span style={{ display: "inline-flex", color: "var(--text-muted)", transform: expanded ? "none" : "rotate(-90deg)", transition: "transform .15s" }}>
          {Icon.chevronDown({ width: 14, height: 14 })}
        </span>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{isRoot ? t("medallion.folders.root") : folder.name}</div>
        <Badge tone="neutral">{projects.length}</Badge>
        {!isRoot && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 12 }} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="link" style={{ fontSize: 12 }} onClick={() => onRename(folder)}>{t("medallion.folders.rename")}</button>
            <button type="button" className="link" style={{ fontSize: 12 }} onClick={() => onDelete(folder)}>{t("medallion.folders.delete")}</button>
          </div>
        )}
      </div>
      {expanded && (
        projects.length === 0 ? (
          <div style={{ padding: 16, fontSize: 12.5, color: "var(--text-muted)" }}>{t("medallion.folders.empty")}</div>
        ) : (
          <table className="table">
            <thead>
              <tr><th>{t("medallion.colProject")}</th><th>{t("medallion.colWarehouse")}</th><th>{t("medallion.colObjectStore")}</th><th>{t("medallion.colSchedule")}</th><th>{t("common.status")}</th><th></th></tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <ProjectRow key={p.id} p={p} sourceName={sourceName} STATUS_LABEL={STATUS_LABEL} t={t} navigate={navigate} onMove={onMove} />
              ))}
            </tbody>
          </table>
        )
      )}
    </div>
  );
}

export default function ProjectsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [projects, setProjects] = useState([]);
  const [folders, setFolders] = useState([]);
  const [sources, setSources] = useState([]);
  const [instances, setInstances] = useState([]);
  const [supersetInstances, setSupersetInstances] = useState([]);
  const [loading, setLoading] = useState(true);
  const [wizardOpen, setWizardOpen] = useState(false);

  // Module 15 §4.1 — expand/collapse state is client-only (no backend persistence in v1);
  // keyed by folder id, "root" for "Sans dossier". Defaults to expanded (undefined -> true)
  // so a fresh load shows everything, exactly like the flat list did before this module.
  const [collapsed, setCollapsed] = useState({});
  const [folderModal, setFolderModal] = useState(null); // { folder } for rename, {} for create, null closed
  const [deleteTarget, setDeleteTarget] = useState(null); // folder being deleted
  const [moveTarget, setMoveTarget] = useState(null); // project being moved

  const STATUS_LABEL = t("medallion.status", { returnObjects: true });

  const load = async () => {
    setLoading(true);
    try {
      const [p, f, s, ai, si] = await Promise.all([
        medallionApi.listProjects(), foldersApi.listFolders(), sourcesApi.listSources(),
        airflowInstancesApi.listInstances(), supersetApi.listInstances(),
      ]);
      setProjects(p);
      setFolders(f);
      setSources(s);
      setInstances(ai);
      setSupersetInstances(si);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const sourceName = (id) => sources.find((s) => s.id === id)?.name || "—";

  const grouped = useMemo(() => {
    const byFolder = new Map();
    const root = [];
    for (const p of projects) {
      if (p.folder_id == null) root.push(p);
      else {
        if (!byFolder.has(p.folder_id)) byFolder.set(p.folder_id, []);
        byFolder.get(p.folder_id).push(p);
      }
    }
    return { byFolder, root };
  }, [projects]);

  const toggle = (key) => setCollapsed((c) => ({ ...c, [key]: !c[key] }));

  const handleFolderSaved = (saved) => {
    setFolders((fs) => {
      const exists = fs.some((f) => f.id === saved.id);
      const next = exists ? fs.map((f) => (f.id === saved.id ? saved : f)) : [...fs, saved];
      return [...next].sort((a, b) => a.name.localeCompare(b.name));
    });
    setFolderModal(null);
  };

  const handleFolderDeleted = (fid) => {
    setFolders((fs) => fs.filter((f) => f.id !== fid));
    setProjects((ps) => ps.map((p) => (p.folder_id === fid ? { ...p, folder_id: null } : p)));
    setDeleteTarget(null);
  };

  const handleProjectMoved = (updated) => {
    setProjects((ps) => ps.map((p) => (p.id === updated.id ? updated : p)));
    setMoveTarget(null);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("medallion.badge")}</div>
          <h1 className="page-title">{t("medallion.title")}</h1>
          <p className="page-desc">{t("medallion.subtitle")}</p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn-ghost" onClick={() => setFolderModal({})}>{Icon.plus()}{t("medallion.folders.new")}</button>
          <button className="add-btn" onClick={() => setWizardOpen(true)}>{Icon.plus()}{t("medallion.newProject")}</button>
        </div>
      </div>

      {loading && <div style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>{t("common.loading")}</div>}

      {!loading && projects.length === 0 && folders.length === 0 && (
        <div style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>{t("medallion.noProjects")}</div>
      )}

      {!loading && (projects.length > 0 || folders.length > 0) && (
        <>
          {folders.map((f) => (
            <FolderSection
              key={f.id}
              folder={f}
              projects={grouped.byFolder.get(f.id) || []}
              expanded={!collapsed[`f${f.id}`]}
              onToggle={() => toggle(`f${f.id}`)}
              onRename={(folder) => setFolderModal({ folder })}
              onDelete={(folder) => setDeleteTarget(folder)}
              sourceName={sourceName}
              STATUS_LABEL={STATUS_LABEL}
              t={t}
              navigate={navigate}
              onMove={(p) => setMoveTarget(p)}
            />
          ))}
          <FolderSection
            folder={null}
            projects={grouped.root}
            expanded={!collapsed.root}
            onToggle={() => toggle("root")}
            sourceName={sourceName}
            STATUS_LABEL={STATUS_LABEL}
            t={t}
            navigate={navigate}
            onMove={(p) => setMoveTarget(p)}
          />
        </>
      )}

      {wizardOpen && (
        <NewProjectWizard
          sources={sources}
          instances={instances}
          supersetInstances={supersetInstances}
          onClose={() => setWizardOpen(false)}
          onCreated={(p) => { navigate(`/medallion/${p.id}`); }}
        />
      )}

      {folderModal && (
        <FolderNameModal folder={folderModal.folder} onClose={() => setFolderModal(null)} onSaved={handleFolderSaved} />
      )}
      {deleteTarget && (
        <DeleteFolderModal folder={deleteTarget} onClose={() => setDeleteTarget(null)} onDeleted={handleFolderDeleted} />
      )}
      {moveTarget && (
        <MoveProjectModal project={moveTarget} folders={folders} onClose={() => setMoveTarget(null)} onMoved={handleProjectMoved} />
      )}
    </>
  );
}
