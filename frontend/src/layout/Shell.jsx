import { useCallback, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext.jsx";
import { Icon, Logo } from "../components/icons.jsx";
import LanguageSwitcher from "../components/LanguageSwitcher.jsx";
import IdleTimeoutModal from "../components/IdleTimeoutModal.jsx";
import { useIdleTimeout } from "../hooks/useIdleTimeout.js";

const avatarColor = (role) =>
  role === "admin" ? "linear-gradient(140deg,#E57200,#C96200)" : "linear-gradient(140deg,#5B7A94,#3A4855)";
const initials = (name) => (name || "").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

const IDLE_MINUTES = 30;
const IDLE_WARNING_SECONDS = 20;

export default function Shell() {
  const { t } = useTranslation();
  const { user, logout, isAdmin } = useAuth();
  const [sideOpen, setSideOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  const handleIdleTimeout = useCallback(() => {
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  const { warning: idleWarning, secondsLeft: idleSecondsLeft, stay: staySignedIn } = useIdleTimeout({
    idleMs: IDLE_MINUTES * 60 * 1000,
    warningSeconds: IDLE_WARNING_SECONDS,
    onTimeout: handleIdleTimeout,
    enabled: true,
  });

  const CRUMBS = {
    "/": t("breadcrumbs.dashboard"),
    "/servers": t("breadcrumbs.servers"),
    "/sources": t("breadcrumbs.sources"),
    "/imports": t("breadcrumbs.imports"),
    "/medallion": t("breadcrumbs.medallion"),
    "/medallion/overview": t("breadcrumbs.medallionOverview"),
    "/orchestrators": t("breadcrumbs.orchestrators"),
    "/settings/users": t("breadcrumbs.users"),
    "/settings/ml-templates": t("breadcrumbs.mlLibrary"),
    "/settings/dbt-macros": t("breadcrumbs.dbtMacros"),
  };
  const crumb = CRUMBS[location.pathname]
    || (location.pathname.startsWith("/servers/") ? t("breadcrumbs.serverDetail") : null)
    || (location.pathname.startsWith("/medallion/") ? t("breadcrumbs.medallionProject") : null)
    || (location.pathname.startsWith("/orchestrators/") ? t("breadcrumbs.orchestratorDetail") : null)
    || t("breadcrumbs.default");

  const navItem = (to, icon, label) => (
    <NavLink to={to} className={({ isActive }) => "nav-item" + (isActive ? " active" : "")} onClick={() => setSideOpen(false)}>
      {icon}{label}
    </NavLink>
  );

  // "/medallion/overview" (admin supervision) is a sibling route, not a child of "/medallion"
  // — but it shares the string prefix, so NavLink's default (non-`end`) matching would light
  // up both items at once. Compute this one item's active state manually, excluding overview.
  const medallionActive = location.pathname === "/medallion" || (location.pathname.startsWith("/medallion/") && !location.pathname.startsWith("/medallion/overview"));
  const medallionNavItem = (to, icon, label) => (
    <Link to={to} className={"nav-item" + (medallionActive ? " active" : "")} onClick={() => setSideOpen(false)}>
      {icon}{label}
    </Link>
  );

  return (
    <div className="app-shell">
      {sideOpen && <div className="scrim" onClick={() => setSideOpen(false)} />}
      <aside className={"sidebar" + (sideOpen ? " open" : "")}>
        <div className="side-brand">
          <div className="side-mark"><Logo size={19} /></div>
          <div>
            <div className="side-brand-name">Data Plateforme</div>
            <div className="side-brand-sub">{t("sidebar.brandSub")}</div>
          </div>
        </div>

        <nav className="nav">
          <div className="nav-label">{t("sidebar.sectionData")}</div>
          {navItem("/", Icon.grid(), t("sidebar.dashboard"))}
          {navItem("/sources", Icon.db(), t("sidebar.sources"))}
          {navItem("/imports", Icon.upload(), t("sidebar.imports"))}
          {medallionNavItem("/medallion", Icon.flow(), t("sidebar.medallion"))}
          {isAdmin && navItem("/medallion/overview", Icon.eye(), t("sidebar.medallionOverview"))}

          <div className="nav-label">{t("sidebar.sectionInfra")}</div>
          {navItem("/servers", Icon.server(), t("sidebar.servers"))}
          {navItem("/orchestrators", Icon.globe(), t("sidebar.orchestrators"))}

          <div className="nav-label">{t("sidebar.sectionSettings")}</div>
          {isAdmin ? (
            navItem("/settings/users", Icon.users(), t("sidebar.users"))
          ) : (
            <button className="nav-item disabled" disabled title={t("common.reservedToAdmins")}>
              {Icon.users()}{t("sidebar.users")} {Icon.lockSm()}
            </button>
          )}
          {isAdmin ? (
            navItem("/settings/ml-templates", Icon.wand(), t("sidebar.mlLibrary"))
          ) : (
            <button className="nav-item disabled" disabled title={t("common.reservedToAdmins")}>
              {Icon.wand()}{t("sidebar.mlLibrary")} {Icon.lockSm()}
            </button>
          )}
          {isAdmin ? (
            navItem("/settings/dbt-macros", Icon.code(), t("sidebar.dbtMacros"))
          ) : (
            <button className="nav-item disabled" disabled title={t("common.reservedToAdmins")}>
              {Icon.code()}{t("sidebar.dbtMacros")} {Icon.lockSm()}
            </button>
          )}
        </nav>

        <LanguageSwitcher />

        <div className="side-user">
          <div className="avatar" style={{ background: avatarColor(user?.role) }}>{initials(user?.name)}</div>
          <div className="side-user-meta">
            <div className="su-name">{user?.name}</div>
            <div className="su-role">{isAdmin ? t("sidebar.admin") : t("sidebar.dataEngineer")}</div>
          </div>
          <button className="logout-btn" onClick={logout} title={t("sidebar.logout")}>{Icon.out()}</button>
        </div>
      </aside>

      <div className="main">
        <div className="topbar">
          <button className="burger" onClick={() => setSideOpen(true)} aria-label={t("sidebar.openMenu")}>{Icon.burger()}</button>
          <div className="crumb">Data Plateforme <span style={{ opacity: .4 }}>/</span> <b>{crumb}</b></div>
        </div>
        <div className="content">
          <Outlet />
        </div>
      </div>

      {idleWarning && <IdleTimeoutModal secondsLeft={idleSecondsLeft} onStay={staySignedIn} />}
    </div>
  );
}
