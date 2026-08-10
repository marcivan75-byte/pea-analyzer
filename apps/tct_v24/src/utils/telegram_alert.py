"""
Alerting Telegram simple et robuste.
Variables d'environnement :
  TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import requests
from typing import Optional
from src.utils.logger import setup_logger
from src.version import VERSION_LABEL

logger = setup_logger("telegram")

def send_telegram(message: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
    token = token or os.getenv("TELEGRAM_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("Telegram non configuré (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        logger.info("Message Telegram envoyé")
        return True
    except Exception as e:
        logger.error(f"Échec envoi Telegram : {e}")
        return False


def alert_pipeline_summary(n_total: int, n_take: int, n_t2: int, n_ultra: int, top_isins: list = None):
    """Résumé quotidien formaté."""
    lines = [
        f"<b>TCT {VERSION_LABEL} – Résumé quotidien</b>",
        f"Signaux analysés : {n_total}",
        f"TAKE : <b>{n_take}</b>",
        f"T2 : {n_t2} | Ultra Squeeze : {n_ultra}",
    ]
    if top_isins:
        lines.append("Top ISIN : " + ", ".join(top_isins[:5]))
    lines.append("#TCT #PEA #ResearchOnly")
    return send_telegram("\n".join(lines))


def alert_error(context: str, error: str):
    msg = f"⚠️ <b>TCT Erreur</b>\n{context}\n<code>{error[:500]}</code>"
    return send_telegram(msg)
