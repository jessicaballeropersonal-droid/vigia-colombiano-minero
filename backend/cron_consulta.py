"""
Monitor ANM - Script de consulta automática
Ejecutar cada 24 horas con GitHub Actions, Render, PythonAnywhere, etc.

Uso: python cron_consulta.py
"""

import requests
from supabase import create_client
from bs4 import BeautifulSoup
from datetime import datetime
import os
import urllib3
urllib3.disable_warnings()

# ===================== CONFIG =====================
SUPABASE_URL  = os.getenv("SUPABASE_URL", "https://xxxx.supabase.co")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY", "tu-service-role-key")  # Usar service role para cron
CALLMEBOT_KEY = os.getenv("CALLMEBOT_KEY", "")
ANM_URL       = "https://www.anm.gov.co/notificaciones-por-avisos"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===================== FUNCIONES =====================
def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    if not CALLMEBOT_KEY:
        print(f"  [WA simulado] → {telefono}: {mensaje[:60]}...")
        return True
    try:
        resp = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": telefono, "text": mensaje, "apikey": CALLMEBOT_KEY},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"  Error WhatsApp: {e}")
        return False

def consultar_anm(placa: str) -> dict:
    try:
        session = requests.Session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        page = session.get(ANM_URL, headers=headers, verify=False, timeout=15)
        soup = BeautifulSoup(page.text, "html.parser")

        csrf = soup.find("input", {"name": "_token"})
        data = {"numero": placa}
        if csrf:
            data["_token"] = csrf.get("value", "")

        resp = session.post(ANM_URL, data=data, headers=headers, verify=False, timeout=15)
        resp_soup = BeautifulSoup(resp.text, "html.parser")
        texto = resp_soup.get_text().lower()

        tiene = any(kw in texto for kw in ["fecha", "publicacion", "publicación", "notificacion", "aviso"])

        fecha = None
        for tag in resp_soup.find_all(["td", "span", "p", "div"]):
            t = tag.get_text(strip=True)
            if any(kw in t.lower() for kw in ["fecha", "publicacion"]) and 5 < len(t) < 100:
                fecha = t
                break

        return {"tiene": tiene, "fecha": fecha}
    except Exception as e:
        print(f"  Error consultando ANM: {e}")
        return {"tiene": False, "fecha": None}

# ===================== MAIN =====================
def main():
    ahora = datetime.now()
    print(f"\n{'='*50}")
    print(f"Monitor ANM - Consulta automática")
    print(f"Fecha: {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    # Obtener todas las placas activas de todos los usuarios
    placas = supabase.table("placas").select("*, usuarios(nombre, telefono)").eq("estado", "activa").execute().data
    print(f"Total placas a consultar: {len(placas)}\n")

    notificaciones = 0

    for p in placas:
        print(f"→ Consultando placa {p['placa']} (propietario: {p['nombre']})...")
        resultado = consultar_anm(p["placa"])

        estado = "alerta" if resultado["tiene"] else "activa"
        supabase.table("placas").update({
            "ultima_consulta": ahora.isoformat(),
            "estado": estado,
            "fecha_notificacion": resultado["fecha"]
        }).eq("id", p["id"]).execute()

        if resultado["tiene"] and resultado["fecha"]:
            notificaciones += 1
            print(f"  ✅ NOTIFICACIÓN DETECTADA — Fecha: {resultado['fecha']}")

            # Guardar alerta
            supabase.table("alertas").insert({
                "usuario_id": p["usuario_id"],
                "placa": p["placa"],
                "nombre": p["nombre"],
                "celular": p["celular"],
                "fecha_publicacion": resultado["fecha"],
                "mensaje": f"Notificación ANM — Placa {p['placa']} — Fecha: {resultado['fecha']}"
            }).execute()

            # Notificar al propietario de la placa
            msg_propietario = (
                f"⛏ Monitor ANM - Notificacion!\n"
                f"Su placa *{p['placa']}* tiene una publicacion en la ANM.\n"
                f"Fecha: {resultado['fecha']}\n"
                f"Revisar en: {ANM_URL}"
            )
            enviar_whatsapp(p["celular"], msg_propietario)
            print(f"  📲 WhatsApp enviado a propietario: {p['celular']}")

        else:
            print(f"  — Sin novedad")

    print(f"\n{'='*50}")
    print(f"Consulta finalizada.")
    print(f"Placas revisadas : {len(placas)}")
    print(f"Notificaciones   : {notificaciones}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
