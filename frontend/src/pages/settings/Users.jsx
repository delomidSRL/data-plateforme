import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext.jsx";
import { useToast } from "../../context/ToastContext.jsx";
import * as usersApi from "../../api/users.js";
import { Badge, StatusDot } from "../../components/ui/Badge.jsx";
import { Icon } from "../../components/icons.jsx";
import AddUserModal from "./AddUserModal.jsx";

const avatarColor = (role) =>
  role === "admin" ? "linear-gradient(140deg,#E57200,#C96200)" : "linear-gradient(140deg,#5B7A94,#3A4855)";
const initials = (name) => (name || "").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

export default function Users() {
  const { t, i18n } = useTranslation();
  const { user: me } = useAuth();
  const showToast = useToast();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modalOpen, setModalOpen] = useState(false);

  const formatDate = (iso) => (iso ? new Date(iso).toLocaleString(i18n.language, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

  const load = async () => {
    setLoading(true);
    try {
      setUsers(await usersApi.listUsers());
      setError("");
    } catch (err) {
      setError(err.message || t("settings.users.loadFailed"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const remove = async (id, name) => {
    try {
      await usersApi.deleteUser(id);
      setUsers((u) => u.filter((x) => x.id !== id));
      showToast(t("settings.users.deletedToast", { name }));
    } catch (err) {
      showToast(err.message || t("settings.users.deleteFailed"));
    }
  };

  const admins = users.filter((u) => u.role === "admin").length;
  const engineers = users.filter((u) => u.role === "engineer").length;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="badge badge-accent" style={{ marginBottom: 8 }}>{t("settings.badge")}</div>
          <h1 className="page-title">{t("settings.users.title")}</h1>
          <p className="page-desc">{t("settings.users.subtitle")}</p>
        </div>
        <button className="add-btn" onClick={() => setModalOpen(true)}>{Icon.plus()}{t("settings.users.addUser")}</button>
      </div>

      <div className="stats">
        <div className="stat"><div className="stat-k">{users.length}</div><div className="stat-l">{t("settings.users.totalAccounts")}</div></div>
        <div className="stat"><div className="stat-k" style={{ color: "var(--ember)" }}>{admins}</div><div className="stat-l">{t("settings.users.administrators")}</div></div>
        <div className="stat"><div className="stat-k">{engineers}</div><div className="stat-l">{t("settings.users.dataEngineers")}</div></div>
      </div>

      {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t("settings.users.colUser")}</th><th>{t("settings.users.colRole")}</th><th>{t("common.status")}</th><th>{t("settings.users.colLastActivity")}</th>
              <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("common.loading")}</td></tr>
            )}
            {!loading && users.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>{t("settings.users.noUsers")}</td></tr>
            )}
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  <div className="user-cell">
                    <div className="avatar" style={{ background: avatarColor(u.role) }}>{initials(u.name)}</div>
                    <div>
                      <div className="uc-name">{u.name}{u.id === me.id && <span style={{ color: "var(--text-muted)", fontWeight: 400, fontSize: 11 }}> · {t("settings.users.you")}</span>}</div>
                      <div className="uc-mail">{u.email}</div>
                    </div>
                  </div>
                </td>
                <td>{u.role === "admin" ? <Badge tone="accent">{t("settings.users.admin")}</Badge> : <Badge tone="neutral">{t("settings.users.engineer")}</Badge>}</td>
                <td>
                  <span className="status">
                    <StatusDot color={u.status === "active" ? "#2f9e6e" : "#c98a1c"} />
                    {u.status === "active" ? t("settings.users.active") : t("settings.users.invited")}
                  </span>
                </td>
                <td style={{ color: "var(--text-muted)", fontFamily: "var(--font-m)", fontSize: 12 }}>{formatDate(u.last_login_at)}</td>
                <td style={{ textAlign: "right" }}>
                  {u.id !== me.id
                    ? <button className="btn-icon" onClick={() => remove(u.id, u.name)} title={t("common.delete")}>{Icon.trash()}</button>
                    : <span style={{ color: "var(--text-muted)", fontSize: 12 }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modalOpen && (
        <AddUserModal
          onClose={() => setModalOpen(false)}
          onCreated={(u) => { setUsers((list) => [...list, u]); setModalOpen(false); showToast(t("settings.users.createdToast", { name: u.name })); }}
        />
      )}
    </>
  );
}
