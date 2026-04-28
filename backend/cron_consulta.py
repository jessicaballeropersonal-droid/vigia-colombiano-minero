"""
Monitor ANM - Script de consulta automática
Ejecutar cada 24 horas con GitHub Actions.
Compatible con Python 3.14+. Sin SDK supabase ni httpx.
"""

import requests
import re
from datetime import datetime
import os
import urllib3
urllib3.disable_warnings()

# ===================== CONFIG =====================
SUPABASE_URL  = os.getenv("SUPABASE_URL", "https://xxxx.supabase.co")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY", "tu-service-role-key")
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM  = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
ANM_URL      = "https://www.anm.gov.co/notificaciones-por-avisos"

# ===================== SUPABASE REST API =====================
def _sb_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"

def _sb_h(prefer: str = None) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h

def sb_select(table, params):
    r = requests.get(_sb_url(table), headers=_sb_h(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_insert(table, data):
    r = requests.post(_sb_url(table), headers=_sb_h("return=representation"), json=data, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_update(table, filters, data):
    r = requests.patch(_sb_url(table), headers=_sb_h("return=representation"), json=data, params=filters, timeout=10)
    r.raise_for_status()
    return r.json()

# ===================== FUNCIONES =====================
def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    if not TWILIO_SID or not TWILIO_TOKEN:
        print(f"  [WA simulado] → {telefono}: {mensaje[:60]}...")
        return True
    try:
        to = telefono if telefono.startswith("whatsapp:") else \
             f"whatsapp:{telefono if telefono.startswith('+') else '+' + telefono}"
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"From": TWILIO_FROM, "To": to, "Body": mensaje},
            timeout=10
        )
        return r.status_code == 201
    except Exception as e:
        print(f"  Error WhatsApp: {e}")
        return False

def consultar_anm(placa: str) -> dict:
    try:
        session = requests.Session()
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        page = session.get(ANM_URL, headers=hdrs, verify=False, timeout=15)

        m = re.search(r'<input[^>]*name="_token"[^>]*value="([^"]*)"', page.text)
        data = {"numero": placa}
        if m:
            data["_token"] = m.group(1)

        resp = session.post(ANM_URL, data=data, headers=hdrs, verify=False, timeout=15)
        texto = re.sub(r'<[^>]+>', ' ', resp.text).lower()

        tiene = any(kw in texto for kw in
                    ["fecha", "publicacion", "publicación", "notificacion", "aviso"])

        fecha = None
        for bloque in re.findall(r'[^<]{5,100}', resp.text):
            b = bloque.strip()
            if any(kw in b.lower() for kw in ["fecha", "publicacion", "publicación"]) and len(b) < 100:
                fecha = b
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

    placas = sb_select("placas", {
        "select": "*, usuarios(nombre, telefono)",
        "estado": "eq.activa"
    })
    print(f"Total placas a consultar: {len(placas)}\n")

    notificaciones = 0
    for p in placas:
        print(f"→ Consultando placa {p['placa']} (propietario: {p['nombre']})...")
        resultado = consultar_anm(p["placa"])

        estado = "alerta" if resultado["tiene"] else "activa"
        sb_update("placas", {"id": f"eq.{p['id']}"}, {
            "ultima_consulta": ahora.isoformat(),
            "estado": estado,
            "fecha_notificacion": resultado["fecha"]
        })

        if resultado["tiene"] and resultado["fecha"]:
            notificaciones += 1
            print(f"  NOTIFICACIÓN DETECTADA — Fecha: {resultado['fecha']}")

            sb_insert("alertas", {
                "usuario_id": p["usuario_id"],
                "placa": p["placa"],
                "nombre": p["nombre"],
                "celular": p["celular"],
                "fecha_publicacion": resultado["fecha"],
                "mensaje": f"Notificación ANM — Placa {p['placa']} — Fecha: {resultado['fecha']}"
            })

            enviar_whatsapp(p["celular"], (
                f"⛏ Monitor ANM - Notificacion!\n"
                f"Su placa *{p['placa']}* tiene una publicacion en la ANM.\n"
                f"Fecha: {resultado['fecha']}\n"
                f"Revisar en: {ANM_URL}"
            ))
            print(f"  WhatsApp enviado a: {p['celular']}")
        else:
            print(f"  — Sin novedad")

    print(f"\n{'='*50}")
    print(f"Consulta finalizada.")
    print(f"Placas revisadas : {len(placas)}")
    print(f"Notificaciones   : {notificaciones}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
