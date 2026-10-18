"""
Monitor de citas DIAN -> alerta por Telegram y/o Gmail

Flujo que replica el script en https://agendamiento.dian.gov.co/ :
  Agendar cita -> Tipo de persona -> Tipo de cita -> Tipo de servicio
  -> aparece el campo "Trámite" (select) con opciones SOLO si hay disponibilidad.

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

# La página identifica los botones con el atributo "llave":
#   persona: 1 = Natural, 2 = Jurídica
#   cita:    1 = Presencial, 2 = Videoatención
LLAVE_PERSONA = "2" if "jur" in TIPO_PERSONA.lower() else "1"
LLAVE_CITA = "1" if "presencial" in TIPO_CITA.lower() else "2"

TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD", "")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def guardar_diagnostico(page):
    """Guarda captura y texto de lo que ve el navegador, para revisar."""
    try:
        page.screenshot(path="diagnostico.png", full_page=True)
    except Exception as e:
        print("No se pudo guardar la captura:", e)
    try:
        cuerpo = page.inner_text("body")
        with open("diagnostico.txt", "w", encoding="utf-8") as f:
            f.write(f"URL: {page.url}\nTITULO: {page.title()}\n\n{cuerpo}")
        print("URL final:", page.url)
        print("Texto visible (primeros 600 caracteres):")
        print(cuerpo[:600])
    except Exception as e:
        print("No se pudo guardar el texto:", e)


def revisar(page) -> bool:
    resp = page.goto(URL, wait_until="networkidle", timeout=60000)
    print("Estado HTTP de la página:", resp.status if resp else "sin respuesta")

    persona = page.locator(f'.btnTipoPersona[llave="{LLAVE_PERSONA}"]')
    agendar = page.locator('div[nombre="btnSolicitarCita"]')

    # La página abre en "Inicio" (botón "Agendar cita") o ya en el paso 1.
    page.wait_for_selector(
        'div[nombre="btnSolicitarCita"], .btnTipoPersona',
        state="visible",
        timeout=45000,
    )
    if not persona.first.is_visible():
        print("Pantalla de inicio: clic en 'Agendar cita'")
        agendar.first.click(timeout=30000)

    persona.first.click(timeout=30000)
    print(f"Tipo de persona seleccionado: {TIPO_PERSONA}")

    page.locator(f'.btnTipoAtencion[llave="{LLAVE_CITA}"]').first.click(timeout=30000)
    print(f"Tipo de cita seleccionado: {TIPO_CITA}")

    page.locator(".btnCategoria", has_text=SERVICIO).first.click(timeout=30000)
    print(f"Servicio seleccionado: {SERVICIO}")

    # Esperar a que la página cargue (o no) los trámites de ese servicio
    page.wait_for_timeout(4000)

    contenedor = page.locator('div[contenido="servicio"]')
    campo = page.locator('div[nombrecontrol="Servicios"] select')

    visible = contenedor.first.is_visible()
    opciones = []
    if campo.count() > 0:
        opciones = [
            o.strip() for o in campo.first.locator("option").all_inner_texts() if o.strip()
        ]

    print(f"Campo 'Trámite' visible: {visible} | opciones encontradas: {len(opciones)}")
    if opciones:
        print("Trámites:", " | ".join(opciones[:10]))

    return visible and len(opciones) > 0


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="es-CO",
            timezone_id="America/Bogota",
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        while True:
            hora = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                if revisar(page):
                    alertar(f"Citas de {SERVICIO} HABILITADAS en la DIAN. Entra ya: {URL}")
                    break
                print(f"[{hora}] Aún sin habilitar.")
                guardar_diagnostico(page)
            except Exception as e:
                print(f"[{hora}] Error en la revisión: {e}")
                guardar_diagnostico(page)
            if ONCE:
                break
            time.sleep(INTERVALO_MIN * 60)
        browser.close()


if __name__ == "__main__":
    main()
