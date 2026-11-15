"""Collection Radar: deliveries, email rendering and the durable outbox."""

from __future__ import annotations

import hashlib
import json
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from radar.valuation import alert_clearance
from radar.wishlist import ALERTING_INTENTS

try:
    from .clock import utc_now
    from .config import AppConfig
    from .constants import (
        RADAR_ALERT_DROP,
        RADAR_ALERT_RATIO,
        RADAR_FEED_LIMIT,
        RADAR_MATERIAL_DROP,
        RADAR_OUTBOX_KINDS,
        RADAR_SEARCHES_VERSION,
        RADAR_SECONDARY_SECTIONS,
        _RADAR_LOCK,
    )
    from .database import connect_db
    from .radar_criteria import radar_json_field
    from .radar_feed import list_radar_feed
except ImportError:  # Direct execution: `python3 server/app.py`.
    from clock import utc_now
    from config import AppConfig
    from constants import (
        RADAR_ALERT_DROP,
        RADAR_ALERT_RATIO,
        RADAR_FEED_LIMIT,
        RADAR_MATERIAL_DROP,
        RADAR_OUTBOX_KINDS,
        RADAR_SEARCHES_VERSION,
        RADAR_SECONDARY_SECTIONS,
        _RADAR_LOCK,
    )
    from database import connect_db
    from radar_criteria import radar_json_field
    from radar_feed import list_radar_feed


# --------------------------------------------------------------------------- #
# Entregas del radar: digest, alerta inmediata y outbox durable                 #
# --------------------------------------------------------------------------- #
#
# Reglas del PRD §16, y el contrato de confiabilidad de Auction Watch:
#
# - hasta diez oportunidades por entrega;
# - sólo las nuevas o con cambio material — nunca reenviar lo mismo sin cambio;
# - una oportunidad excepcional no espera a la próxima corrida;
# - sin nada accionable no hay correo: el silencio es la respuesta correcta;
# - `Message-ID` determinista, para que un reintento sea el mismo mensaje;
# - un fallo después de empezar a entregar queda `uncertain` y sale del retry
#   automático, porque reenviar a ciegas duplica.


def radar_email_configured(config: AppConfig) -> bool:
    return bool(
        config.radar_email_mode != "disabled"
        and config.radar_smtp_host
        and config.radar_email_from
        and config.radar_email_to
    )


def radar_recipients(config: AppConfig) -> list[str]:
    return [item.strip() for item in str(config.radar_email_to or "").split(",") if item.strip()]


def radar_notification_state(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(row["listing_id"]): {
            "notifiedAt": row["notified_at"],
            "price": row["price_at_notification"],
        }
        for row in conn.execute("SELECT * FROM radar_notifications").fetchall()
    }


def deserves_notification(item: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """Por qué esta publicación merece un aviso, o cadena vacía si no lo merece.

    Avisar de nuevo por lo mismo entrena a ignorar los avisos. Sólo entran las
    que nunca se avisaron y las que cambiaron de forma material desde entonces.
    """

    if previous is None:
        return "nueva"
    before = previous.get("price")
    current = item.get("priceAmount")
    if before is None or current is None or before <= 0:
        return ""

    # Cruzar el objetivo vuelve a avisar aunque la baja sea chica: pasar de 82 a
    # 79 es un 4% y no movería la aguja general, pero si tu objetivo eran 80 es
    # justo el momento que estabas esperando.
    target = (item.get("valuation") or {}).get("target") or {}
    goal = target.get("value")
    if target.get("meets") is True and goal is not None and before > goal:
        return f"cruzó tu objetivo de USD {goal:g}"

    if current < before and (before - current) / before >= RADAR_MATERIAL_DROP:
        return f"bajó {round((before - current) / before * 100)}%"
    return ""


def is_exceptional(item: dict[str, Any]) -> bool:
    """Una excepcional interrumpe; el resto espera al digest o a la app.

    Fail-closed desde el 2026-11-01. Interrumpir de más entrena a ignorar los
    avisos, y la auditoría de ese día encontró 107 publicaciones que cumplían
    el objetivo, todas con bloqueos pendientes, y una N64 sin fuente que llegó
    como alerta. Un mail exige, además del precio:

    - que la búsqueda persiga un deseo de Nacho's Wishlist, una consola que
      falta o un lote elegido (nunca una recomendación vieja);
    - todo lo de `alert_clearance`: identidad inequívoca, envío a Miami
      conocido, publicación leída entera con faltantes y defectos sumados,
      ningún bloqueo ni revisión, disponible, y referencia de mercado
      comparable y confiable.

    Con eso, cualquiera de tres hechos alcanza: el costo funcional cruzó el
    objetivo que fijó el dueño, un descuento profundo contra esa referencia, o
    una baja fuerte de algo que ya venía siguiendo. Ninguno alcanza solo.
    """

    valuation = item.get("valuation") or {}
    if valuation.get("section") in RADAR_SECONDARY_SECTIONS:
        return False
    if not set(item.get("intents") or []) & ALERTING_INTENTS:
        return False
    band = str(item.get("band") or "")
    drop = item.get("priceDrop") or {}
    following = (item.get("decision") or {}).get("decision") == "following"
    if following and drop.get("ratio", 0) >= RADAR_ALERT_DROP:
        return not alert_clearance(valuation, band, price_drop=True)
    if alert_clearance(valuation, band):
        return False
    target = valuation.get("target") or {}
    if target.get("meets") is True and target.get("costKnown") is True:
        return True
    ratio = valuation.get("ratio")
    return isinstance(ratio, (int, float)) and 0 < ratio <= RADAR_ALERT_RATIO


def radar_email_subject(kind: str, items: list[dict[str, Any]]) -> str:
    if kind == "alert":
        first = items[0]
        band = first.get("band") or ""
        return f"Collection Radar · oportunidad {band or 'destacada'}: {first['title'][:70]}"
    return f"Collection Radar · {len(items)} oportunidad{'es' if len(items) != 1 else ''} para revisar"


def radar_email_bodies(config: AppConfig, kind: str, items: list[dict[str, Any]], generated_at: str) -> tuple[str, str]:
    """Texto y HTML de una entrega. Dice cuándo se revisó y qué falta confirmar.

    «Revisado», no «verificado»: la corrida encontró la publicación, pero ninguna
    fuente confirma que siga comprable. Cada una que no verificó el dueño lo dice.
    """
    lines = [
        "Collection Radar" if kind == "digest" else "Collection Radar · oportunidad excepcional",
        f"Revisado {generated_at}",
        "",
    ]
    rows = []
    for item in items:
        drop = item.get("priceDrop") or {}
        cost = (item.get("valuation") or {}).get("cost") or {}
        detail = [item.get("priceLabel") or ""]
        if cost.get("importedTotal") is not None:
            detail.append(f"≈ {cost['importedTotal']} {cost.get('currency', 'USD')} puesto acá")
        if drop.get("ratio"):
            detail.append(f"bajó {round(drop['ratio'] * 100)}% (antes {drop.get('previous')})")
        if item.get("band"):
            detail.append(f"{item['band']}{f' {item['score']}/100' if item.get('score') is not None else ''}")
        if item.get("requiresVerification", True):
            detail.append("sin verificar: abrila y confirmá Buy It Now o Add to Cart")
        reasons = (item.get("matches") or [{}])[0].get("reasons") or []
        why = reasons[0] if reasons else ""
        valuation = item.get("valuation") or {}
        account = radar_email_account(item)

        lines.append(f"· {item['title']}")
        lines.append(f"  {' · '.join(part for part in detail if part)}")
        if account:
            lines.append(f"  {account}")
        if why:
            lines.append(f"  {why}")
        lines.append(f"  {item.get('listingUrl', '')}")
        lines.append("")

        rows.append(
            "<tr><td style='padding:12px 0;border-bottom:1px solid #d8e3f0'>"
            f"<a href='{html_escape(item.get('listingUrl', ''))}' style='color:#0c8fb0;font-weight:600;text-decoration:none'>"
            f"{html_escape(item['title'])}</a><br>"
            f"<span style='color:#3d536c;font-size:13px'>{html_escape(' · '.join(p for p in detail if p))}</span>"
            + (f"<br><span style='color:#0b1a2c;font-size:13px'>{html_escape(account)}</span>" if account else "")
            + (f"<br><span style='color:#3d536c;font-size:13px'>{html_escape(why)}</span>" if why else "")
            + "</td></tr>"
        )

    lines.append("Abrí «Para mí» en Consolas para seguir, descartar o dormir cada una.")
    html = (
        "<div style='font-family:system-ui,sans-serif;max-width:640px'>"
        f"<h2 style='font-family:Georgia,serif;color:#0b1a2c'>{html_escape(lines[0])}</h2>"
        f"<p style='color:#3d536c;font-size:13px'>Revisado {html_escape(generated_at)}</p>"
        f"<table style='width:100%;border-collapse:collapse'>{''.join(rows)}</table>"
        "<p style='color:#3d536c;font-size:13px'>Abrí «Para mí» en Consolas para seguir, descartar o dormir cada una.</p>"
        "</div>"
    )
    return "\n".join(lines), html


RADAR_BAND_LABELS = {
    "ganga": "Ganga real",
    "buena": "Buena compra",
    "razonable": "Precio razonable",
    "premium": "Premium justificable",
    "caro": "Caro",
    "revisar": "Requiere revisión",
    "sin-referencia": "Sin referencia",
}


def radar_band_label(band: str) -> str:
    return RADAR_BAND_LABELS.get(str(band or ""), str(band or ""))


def radar_email_account(item: dict[str, Any]) -> str:
    """La cuenta, los riesgos y la clasificación, para que el mail se lea solo.

    «USD 59,99 + USD 15,85 de envío + fuente faltante estimado en USD 20,00 =
    USD 95,84 antes del courier. Stick del control flojo, Jumper Pak estándar
    y sin devoluciones. Clasificación: Precio razonable, no ganga.»
    """
    valuation = item.get("valuation") or {}
    parts = [str(valuation.get("explanation") or "")]
    risks = [str(risk) for risk in valuation.get("risks") or [] if risk]
    if risks:
        text = ", ".join(risks[:-1]) + (" y " if len(risks) > 1 else "") + risks[-1]
        parts.append(text[:1].upper() + text[1:] + ".")
    band = str(item.get("band") or valuation.get("band") or "")
    if band:
        label = radar_band_label(band)
        parts.append(f"Clasificación: {label}{'' if band == 'ganga' else ', no ganga'}.")
    return " ".join(part for part in parts if part)


def html_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def enqueue_radar_delivery(
    config: AppConfig, kind: str, items: list[dict[str, Any]], run_id: str = ""
) -> dict[str, Any] | None:
    """Deja una entrega lista en la outbox. No la manda: eso es un paso aparte."""
    if kind not in RADAR_OUTBOX_KINDS or not items:
        return None
    recipients = radar_recipients(config)
    if not recipients:
        return None

    now = utc_now()
    listing_ids = [str(item["id"]) for item in items]
    # El id sale del contenido: un reintento reusa el mismo mensaje en vez de
    # crear uno nuevo, y dos corridas con lo mismo no generan dos correos.
    digest_key = hashlib.sha1("\0".join([kind, run_id, *sorted(listing_ids)]).encode("utf-8")).hexdigest()
    delivery_id = f"radar-mail-{digest_key[:20]}"
    subject = radar_email_subject(kind, items)
    text, html = radar_email_bodies(config, kind, items, now)

    with _RADAR_LOCK, connect_db(config) as conn:
        existing = conn.execute("SELECT * FROM radar_outbox WHERE id = ?", (delivery_id,)).fetchone()
        if existing is not None:
            return radar_outbox_row(existing)
        conn.execute(
            """INSERT INTO radar_outbox (
                 id, kind, run_id, status, recipient, subject, body_text, body_html, message_id,
                 listing_ids_json, created_at, updated_at
               ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                delivery_id, kind, run_id, ", ".join(recipients), subject, text, html,
                f"<{delivery_id}@consolas.local>",
                json.dumps(listing_ids, ensure_ascii=False), now, now,
            ),
        )
        row = conn.execute("SELECT * FROM radar_outbox WHERE id = ?", (delivery_id,)).fetchone()
    return radar_outbox_row(row)


def radar_outbox_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "runId": row["run_id"],
        "status": row["status"],
        "recipient": row["recipient"],
        "subject": row["subject"],
        "messageId": row["message_id"],
        "listingIds": radar_json_field(row["listing_ids_json"], []),
        "attempts": row["attempts"],
        "detail": row["detail"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "sentAt": row["sent_at"],
    }


def send_radar_email(config: AppConfig, delivery: dict[str, Any], body_text: str, body_html: str) -> tuple[str, str]:
    """Entrega por SMTP. Devuelve (estado, detalle).

    La distinción que importa: un fallo **antes** de empezar a entregar se puede
    reintentar; uno **después** no, porque el mensaje pudo haber salido. Ese
    segundo caso queda `uncertain` y espera una decisión humana.
    """

    message = EmailMessage()
    message["Subject"] = delivery["subject"]
    message["From"] = config.radar_email_from
    message["To"] = delivery["recipient"]
    message["Message-ID"] = delivery["messageId"]
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    delivery_started = False
    try:
        with smtplib.SMTP(config.radar_smtp_host, config.radar_smtp_port, timeout=30) as server:
            server.ehlo()
            if config.radar_smtp_starttls:
                server.starttls()
                server.ehlo()
            if config.radar_smtp_username and config.radar_smtp_password:
                server.login(config.radar_smtp_username, config.radar_smtp_password)
            delivery_started = True
            server.send_message(message)
    except Exception as error:  # noqa: BLE001
        if not delivery_started:
            return "failed", f"smtp_pre_send_failed: {type(error).__name__}: {error}"
        if isinstance(error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
            return "failed", f"smtp_rejected: {type(error).__name__}: {error}"
        return "uncertain", f"smtp_delivery_uncertain: {type(error).__name__}: {error}"
    return "sent", "sent_via_smtp"


def flush_radar_outbox(config: AppConfig) -> dict[str, Any]:
    """Entrega lo pendiente. Un `uncertain` nunca se reintenta solo."""
    if not radar_email_configured(config):
        return {"ok": True, "sent": 0, "skipped": "not_configured"}

    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT * FROM radar_outbox WHERE status = 'pending' ORDER BY created_at LIMIT 10"
        ).fetchall()
        pending = [(radar_outbox_row(row), str(row["body_text"]), str(row["body_html"])) for row in rows]

    sent = 0
    outcomes: list[dict[str, Any]] = []
    for delivery, text, html in pending:
        # `sending` se persiste ANTES de invocar al transporte: si el proceso
        # muere acá, la entrega no vuelve a la cola por su cuenta.
        with _RADAR_LOCK, connect_db(config) as conn:
            conn.execute(
                "UPDATE radar_outbox SET status = 'sending', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (utc_now(), delivery["id"]),
            )
        status, detail = send_radar_email(config, delivery, text, html)
        now = utc_now()
        with _RADAR_LOCK, connect_db(config) as conn:
            conn.execute(
                "UPDATE radar_outbox SET status = ?, detail = ?, updated_at = ?, sent_at = ? WHERE id = ?",
                (status, detail, now, now if status == "sent" else None, delivery["id"]),
            )
            if status == "sent":
                for listing_id in delivery["listingIds"]:
                    conn.execute(
                        """INSERT INTO radar_notifications (listing_id, notified_at, price_at_notification, kind, message_id)
                           VALUES (?, ?, (SELECT price_amount FROM radar_listings WHERE id = ?), ?, ?)
                           ON CONFLICT(listing_id) DO UPDATE SET
                             notified_at=excluded.notified_at,
                             price_at_notification=excluded.price_at_notification,
                             kind=excluded.kind, message_id=excluded.message_id""",
                        (listing_id, now, listing_id, delivery["kind"], delivery["messageId"]),
                    )
        sent += 1 if status == "sent" else 0
        outcomes.append({"id": delivery["id"], "status": status, "detail": detail})
    return {"ok": True, "sent": sent, "deliveries": outcomes}


def notify_radar_opportunities(config: AppConfig, run_id: str = "") -> dict[str, Any]:
    """Decide qué avisar tras una corrida, lo encola y lo entrega.

    Sin nada accionable no se encola nada: el silencio es la respuesta, no una
    entrega vacía.
    """

    if config.radar_email_mode == "disabled":
        return {"ok": True, "mode": "disabled", "queued": 0}

    feed = list_radar_feed(config, RADAR_FEED_LIMIT)
    with _RADAR_LOCK, connect_db(config) as conn:
        notified = radar_notification_state(conn)

    fresh: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for item in feed["items"]:
        why = deserves_notification(item, notified.get(item["id"]))
        if not why:
            continue
        if config.radar_email_mode in ("alerts", "digest_and_alerts") and is_exceptional(item):
            alerts.append(item)
        elif config.radar_email_mode != "alerts":
            fresh.append(item)

    queued = 0
    for item in alerts:
        if enqueue_radar_delivery(config, "alert", [item], run_id):
            queued += 1
    if fresh and enqueue_radar_delivery(config, "digest", fresh[:RADAR_FEED_LIMIT], run_id):
        queued += 1

    result = {"ok": True, "mode": config.radar_email_mode, "queued": queued,
              "alerts": len(alerts), "digest": len(fresh)}
    if queued:
        result["delivery"] = flush_radar_outbox(config)
    return result


def list_radar_outbox(config: AppConfig, limit: int = 20) -> dict[str, Any]:
    with _RADAR_LOCK, connect_db(config) as conn:
        rows = conn.execute(
            "SELECT * FROM radar_outbox ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
        ).fetchall()
    return {
        "version": RADAR_SEARCHES_VERSION,
        "configured": radar_email_configured(config),
        "mode": config.radar_email_mode,
        "items": [radar_outbox_row(row) for row in rows],
    }
