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
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-CO,es;q=0.9",
            "Referer": "https://www.anm.gov.co/",
        }
        placa_re = re.compile(
            r'(?<![0-9A-Za-z-])' + re.escape(placa.strip()) + r'(?![0-9A-Za-z-])',
            re.IGNORECASE
        )

        avisos = []
        MAX_PAGES = 3
        for page in range(MAX_PAGES):
            params = {
                "field_pla_tit_min_value": placa,
                "field_punto_de_atencion_regional_value": "All",
                "field_fecha_de_publicacion_o_fij_value": "",
                "field_mes_liberacion_de_area_value": "All",
                "page": page,
            }
            resp = requests.get(ANM_URL, params=params, headers=hdrs, verify=False, timeout=8)

            tbody_m = re.search(r'<tbody>(.*?)</tbody>', resp.text, re.DOTALL | re.IGNORECASE)
            if not tbody_m:
                break

            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL | re.IGNORECASE)
            if not rows:
                break

            for row in rows:
                time_m = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})', row, re.IGNORECASE)
                fecha = time_m.group(1) if time_m else None
                row_text = re.sub(r'<[^>]+>', ' ', row)
                row_text = re.sub(r'\s+', ' ', row_text).strip().lower()
                if placa_re.search(row_text) and fecha:
                    avisos.append(fecha)

        return {"tiene": len(avisos) > 0, "avisos": avisos}
    except Exception as e:
        print(f"  Error consultando ANM [{placa}]: {e}")
        return {"tiene": False, "avisos": []}

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
        avisos    = resultado["avisos"]
        estado    = "alerta" if resultado["tiene"] else "activa"

        fecha_mas_reciente = sorted(avisos)[-1] if avisos else None
        sb_update("placas", {"id": f"eq.{p['id']}"}, {
            "ultima_consulta": ahora.isoformat(),
            "estado": estado,
            "fecha_notificacion": fecha_mas_reciente
        })

        nuevas = 0
        for fecha in avisos:
            ya_existe = sb_select("alertas", {
                "select": "id",
                "usuario_id": f"eq.{p['usuario_id']}",
                "placa": f"eq.{p['placa']}",
                "fecha_publicacion": f"eq.{fecha}"
            })
            if not ya_existe:
                sb_insert("alertas", {
                    "usuario_id": p["usuario_id"],
                    "placa": p["placa"],
                    "nombre": p["nombre"],
                    "celular": p["celular"],
                    "fecha_publicacion": fecha,
                    "mensaje": f"Notificación ANM — Placa {p['placa']} — Fecha: {fecha}"
                })
                nuevas += 1
                notificaciones += 1
                print(f"  NUEVA ALERTA — Fecha: {fecha}")

        if nuevas > 0:
            fechas_str = ", ".join(sorted(avisos))
            enviar_whatsapp(p["celular"], (
                f"⛏ Monitor ANM - {nuevas} aviso(s) detectado(s)!\n"
                f"Placa: *{p['placa']}*\n"
                f"Fecha(s): {fechas_str}\n"
                f"Revisar en: {ANM_URL}"
            ))
            print(f"  WhatsApp enviado a: {p['celular']}")
        elif not avisos:
            print(f"  — Sin novedad")

    print(f"\n{'='*50}")
    print(f"Consulta finalizada.")
    print(f"Placas revisadas : {len(placas)}")
    print(f"Notificaciones   : {notificaciones}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
