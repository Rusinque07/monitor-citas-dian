"""
Monitor de citas DIAN -> alerta por Telegram y/o Gmail

Variables de entorno (configura las del canal que quieras usar):
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    GMAIL_USER, GMAIL_APP_PASSWORD   (contraseña de aplicación de Google)
    SERVICIO        ej: "Devoluciones" | "Conferencias o capacitaciones"
    TIPO_PERSONA    (opcional) "Persona Natural" | "Persona Jurídica"
    TIPO_CITA       (opcional) "Videoatención" | "Presencial"
    ONCE            "1" = revisar una vez y salir (GitHub Actions)
    INTERVALO_MIN   minutos entre revisiones en modo bucle (def. 5)
"""

import os
import time
import smtplib
import datetime
from email.message import EmailMessage

import requests
from playwright.sync_api import sync_playwright

URL = "https://agendamiento.dian.gov.co/"
SERVICIO = os.getenv("SERVICIO", "Devoluciones")
TIPO_PERSONA = os.getenv("TIPO_PERSONA", "Persona Natural")
TIPO_CITA = os.getenv("TIPO_CITA", "Videoatención")
INTERVALO_MIN = int(os.getenv("INTERVALO_MIN", "5"))
ONCE = os.getenv("ONCE") == "1"

TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD", "")


def enviar_telegram(texto: str):
    if not (TG_TOKEN and TG_CHAT):
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": texto},
        timeout=30,
    )
    print("Telegram respondió:", r.status_code, r.text[:200])


def enviar_gmail(texto: str):
    if not (GMAIL_USER and GMAIL_PASS):
        return
    msg = EmailMessage()
    msg["Subject"] = f"Citas DIAN habilitadas: {SERVICIO}"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg.set_content(texto)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    print("Correo enviado a", GMAIL_USER)


def alertar(texto: str):
    print("ALERTA:", texto)
    for envio in (enviar_telegram, enviar_gmail):
        try:
            envio(texto)
        except Exception as e:
            print(f"Falló {envio.__name__}: {e}")


def revisar(page) -> bool:
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.get_by_text(TIPO_PERSONA, exact=False).first.click()
    page.get_by_text(TIPO_CITA, exact=False).first.click()
    page.get_by_text(SERVICIO, exact=False).first.click()
    page.wait_for_timeout(2500)

    campo = page.locator("select").first
    if campo.count() == 0 or not campo.is_enabled():
        return False
    opciones = [o.strip() for o in campo.locator("option").all_inner_texts() if o.strip()]
    return len(opciones) > 0


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        while True:
            hora = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                if revisar(page):
                    alertar(f"Citas de {SERVICIO} HABILITADAS en la DIAN. Entra ya: {URL}")
                    break
                print(f"[{hora}] Aún sin habilitar.")
            except Exception as e:
                print(f"[{hora}] Error en la revisión: {e}")
            if ONCE:
                break
            time.sleep(INTERVALO_MIN * 60)
        browser.close()


if __name__ == "__main__":
    main()
