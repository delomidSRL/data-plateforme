import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext.jsx";
import { Field, Input, PasswordInput } from "../../components/ui/Input.jsx";
import { Button } from "../../components/ui/Button.jsx";
import { Icon, Logo, NetBG } from "../../components/icons.jsx";

export default function Login() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e?.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message || t("auth.invalidCredentials"));
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

        <div className="auth-eyebrow">{t("auth.secureSpace")}</div>
        <h1 className="auth-h1">{t("auth.welcomeBack")}</h1>
        <p className="auth-sub">{t("auth.welcomeBackSub")}</p>

        {error && <div className="error-banner">{Icon.warn()}<span>{error}</span></div>}

        <form onSubmit={submit}>
          <Field label={t("auth.email")}>
            <Input
              type="email"
              placeholder="vous@delomid.io"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              icon={Icon.mail()}
              required
            />
          </Field>

          <Field>
            <div className="auth-row-between" style={{ marginBottom: 7 }}>
              <label className="field-label" style={{ margin: 0 }}>{t("auth.password")}</label>
              <Link className="link" to="/forgot-password">{t("auth.forgotPassword")}</Link>
            </div>
            <PasswordInput
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>

          <Button type="submit" disabled={busy}>{busy ? t("auth.signingIn") : t("auth.signIn")}</Button>
        </form>
      </div>
    </div>
  );
}
