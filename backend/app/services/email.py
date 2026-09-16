import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger("app.email")

settings = get_settings()


def send_email(to: str, subject: str, html_body: str) -> None:
    if not settings.smtp_host:
        logger.info("SMTP not configured — dev fallback, logging email instead of sending.\nTo: %s\nSubject: %s\n%s", to, subject, html_body)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content("Ce message nécessite un client email compatible HTML.")
    message.add_alternative(html_body, subtype="html")

    # Port 465 is implicit TLS (SMTPS) — the connection must be wrapped in SSL from the
    # first byte, unlike 587/25 where the connection starts plaintext and upgrades via
    # STARTTLS. Using SMTP()+starttls() against a 465 server hangs/fails the handshake.
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)


def send_password_reset_email(to: str, reset_link: str) -> None:
    send_email(
        to=to,
        subject="Data Plateforme — Réinitialisation de votre mot de passe",
        html_body=(
            f"<p>Bonjour,</p>"
            f"<p>Cliquez sur le lien ci-dessous pour définir un nouveau mot de passe. "
            f"Ce lien expire dans {settings.reset_token_expire_minutes} minutes.</p>"
            f'<p><a href="{reset_link}">{reset_link}</a></p>'
            f"<p>Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.</p>"
        ),
    )


def send_invitation_email(to: str, name: str, set_password_link: str) -> None:
    send_email(
        to=to,
        subject="Data Plateforme — Invitation",
        html_body=(
            f"<p>Bonjour {name},</p>"
            f"<p>Un compte Data Plateforme a été créé pour vous. "
            f"Cliquez sur le lien ci-dessous pour définir votre mot de passe.</p>"
            f'<p><a href="{set_password_link}">{set_password_link}</a></p>'
        ),
    )
