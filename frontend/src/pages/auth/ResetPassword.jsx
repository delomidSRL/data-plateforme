import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { resetPassword } from "../../api/auth.js";
import { Field, PasswordInput } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon, Logo, NetBG } from "../../components/icons.jsx";

export default function ResetPassword() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e?.preventDefault();
    setError("");

    if (password.length < 8) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    if (password !== confirm) {
      setError(t("auth.passwordsDontMatch"));
      return;
    }

    setBusy(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err.message || t("auth.invalidOrExpiredLink"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-stage theme-dark">
      <div className="auth-bg">
        <NetBG />
        <div className="auth-glow g1" />
        <div className="auth-glow g2" />
      </div>

      <div className="auth-card">
        <div className="auth-brand">
          <div className="auth-mark"><Logo /></div>
          <div>
            <div className="auth-brand-name">Data&nbsp;Plateforme</div>
            <div className="auth-brand-sub">by Delomid IT</div>
          </div>
        </div>

        {!done ? (
          <>
            <div className="auth-eyebrow">{t("auth.reset")}</div>
            <h1 className="auth-h1">{t("auth.newPassword")}</h1>
            <p className="auth-sub">{t("auth.newPasswordSub")}</p>

            {!token && <div className="error-banner">{Icon.warn()}<span>{t("auth.invalidLinkMissingToken")}</span></div>}
            {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

            <form onSubmit={submit}>
              <Field label={t("auth.newPassword")}>
                <PasswordInput placeholder="••••••••" value={password} onChange={(e) => setPassword(e.target.value)} required />
              </Field>
              <Field label={t("auth.confirmPassword")}>
                <PasswordInput placeholder="••••••••" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
              </Field>
              <Button type="submit" disabled={!token || busy}>{busy ? t("auth.updating") : t("auth.setPassword")}</Button>
            </form>
          </>
        ) : (
          <div className="auth-ok">
            <div className="auth-ok-ring">{Icon.check({ width: 26, height: 26 })}</div>
            <h1 className="auth-h1">{t("auth.passwordUpdated")}</h1>
            <p className="auth-sub">{t("auth.passwordUpdatedSub")}</p>
            <Button onClick={() => navigate("/login")}>{t("auth.goToLogin")}</Button>
          </div>
        )}
      </div>
    </div>
  );
}
