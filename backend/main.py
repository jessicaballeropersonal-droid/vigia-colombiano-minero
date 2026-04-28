"""
Monitor ANM - Backend completo
Compatible con Python 3.14+. Sin SDK supabase ni httpx.
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from twilio.rest import Client as TwilioClient
import random
import string
import re
import requests
from datetime import datetime, timedelta
from jose import jwt, JWTError
import os
import urllib3
urllib3.disable_warnings()

# ===================== CONFIG =====================
SUPABASE_URL    = os.getenv("SUPABASE_URL", "https://xxxx.supabase.co")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "tu-anon-key")
JWT_SECRET      = os.getenv("JWT_SECRET", "cambia-este-secreto-muy-largo-123!")
JWT_EXPIRE_DAYS = 30
TWILIO_SID      = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM     = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
ANM_URL         = "https://www.anm.gov.co/notificaciones-por-avisos"

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

def sb_select(table: str, params: dict) -> list:
    r = requests.get(_sb_url(table), headers=_sb_h(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_insert(table: str, data: dict) -> list:
    r = requests.post(_sb_url(table), headers=_sb_h("return=representation"), json=data, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_update(table: str, filters: dict, data: dict) -> list:
    r = requests.patch(_sb_url(table), headers=_sb_h("return=representation"), json=data, params=filters, timeout=10)
    r.raise_for_status()
    return r.json()

def sb_delete(table: str, filters: dict):
    r = requests.delete(_sb_url(table), headers=_sb_h(), params=filters, timeout=10)
    r.raise_for_status()

# ===================== APP =====================
app = FastAPI(title="Monitor ANM API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===================== MODELOS =====================
class LoginModel(BaseModel):
    phone: str
    password: str

class SendCodeModel(BaseModel):
    phone: str
    nombre: str

class RegisterModel(BaseModel):
    phone: str
    nombre: str
    codigo: str
    password: str

class RecoverModel(BaseModel):
    phone: str

class ChangePassModel(BaseModel):
    password_actual: str
    password_nueva: str

class PlacaModel(BaseModel):
    placa: str
    nombre: str
    celular: str

# ===================== HELPERS =====================
def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)

def create_token(user_id: str) -> str:
    payload = {"sub": user_id, "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_user_id(authorization: str = Header(...)) -> str:
    try:
        payload = jwt.decode(authorization.replace("Bearer ", ""), JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

def generar_codigo() -> str:
    return ''.join(random.choices(string.digits, k=6))

def generar_password_temporal() -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=10))

def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    print(f"[Twilio] SID={TWILIO_SID[:5] if TWILIO_SID else 'VACIO'} TOKEN={TWILIO_TOKEN[:5] if TWILIO_TOKEN else 'VACIO'} FROM={TWILIO_FROM}")
    to = telefono if telefono.startswith("whatsapp:") else \
         f"whatsapp:{telefono if telefono.startswith('+') else '+' + telefono}"
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        message = client.messages.create(from_=TWILIO_FROM, to=to, body=mensaje)
        print(f"[Twilio] To={to} sid={message.sid} status={message.status}")
        return True
    except Exception as e:
        print(f"[Twilio] Exception enviando a {to}: {e}")
        return False

# ===================== AUTH =====================
@app.post("/auth/send-code")
async def send_code(body: SendCodeModel):
    if sb_select("usuarios", {"select": "id", "telefono": f"eq.{body.phone}"}):
        raise HTTPException(status_code=400, detail="Este número ya tiene una cuenta registrada.")

    sb_delete("codigos_verificacion", {"telefono": f"eq.{body.phone}"})

    codigo = generar_codigo()
    expira = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    sb_insert("codigos_verificacion", {
        "telefono": body.phone, "codigo": codigo,
        "expira_en": expira, "usado": False
    })

    ok = enviar_whatsapp(body.phone,
        f"Monitor ANM - Hola {body.nombre}! Tu código de verificación es: *{codigo}*. "
        f"Válido por 10 minutos. No lo compartas con nadie.")

    if not ok:
        raise HTTPException(status_code=502, detail="No se pudo enviar el código por WhatsApp. Verifica el número e intenta de nuevo.")

    return {"ok": True}

@app.post("/auth/register")
async def register(body: RegisterModel):
    rows = sb_select("codigos_verificacion", {
        "select": "*", "telefono": f"eq.{body.phone}",
        "codigo": f"eq.{body.codigo}", "usado": "eq.false"
    })
    if not rows:
        raise HTTPException(status_code=400, detail="Código incorrecto o ya usado.")

    rec = rows[0]
    if datetime.fromisoformat(rec["expira_en"].replace("Z", "")) < datetime.utcnow():
        raise HTTPException(status_code=400, detail="El código expiró. Solicita uno nuevo.")

    sb_update("codigos_verificacion", {"id": f"eq.{rec['id']}"}, {"usado": True})

    nuevo = sb_insert("usuarios", {
        "nombre": body.nombre,
        "telefono": body.phone,
        "password_hash": hash_password(body.password)
    })
    if not nuevo:
        raise HTTPException(status_code=500, detail="Error al crear el usuario.")

    user = nuevo[0]
    enviar_whatsapp(body.phone,
        f"Monitor ANM - Bienvenido {body.nombre}! Tu cuenta ha sido creada exitosamente.")
    return {
        "token": create_token(user["id"]),
        "user": {"id": user["id"], "nombre": user["nombre"], "telefono": user["telefono"]}
    }

@app.post("/auth/login")
async def login(body: LoginModel):
    rows = sb_select("usuarios", {"select": "*", "telefono": f"eq.{body.phone}"})
    if not rows or not verify_password(body.password, rows[0]["password_hash"]):
        raise HTTPException(status_code=401, detail="Número o contraseña incorrectos.")
    user = rows[0]
    return {
        "token": create_token(user["id"]),
        "user": {"id": user["id"], "nombre": user["nombre"], "telefono": user["telefono"]}
    }

@app.post("/auth/recover")
async def recover(body: RecoverModel):
    rows = sb_select("usuarios", {"select": "*", "telefono": f"eq.{body.phone}"})
    if not rows:
        raise HTTPException(status_code=404, detail="No existe una cuenta con este número.")
    user = rows[0]
    nueva_pass = generar_password_temporal()
    sb_update("usuarios", {"id": f"eq.{user['id']}"}, {"password_hash": hash_password(nueva_pass)})
    enviar_whatsapp(body.phone,
        f"Monitor ANM - Hola {user['nombre']}! Tu nueva contraseña es: *{nueva_pass}*\n"
        f"Por seguridad, cámbiala después de iniciar sesión.")
    return {"ok": True}

@app.post("/auth/change-password")
async def change_password(body: ChangePassModel, user_id: str = Depends(get_user_id)):
    rows = sb_select("usuarios", {"select": "*", "id": f"eq.{user_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    user = rows[0]
    if not verify_password(body.password_actual, user["password_hash"]):
        raise HTTPException(status_code=400, detail="La contraseña actual es incorrecta.")
    if len(body.password_nueva) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres.")
    sb_update("usuarios", {"id": f"eq.{user_id}"}, {"password_hash": hash_password(body.password_nueva)})
    return {"ok": True}

# ===================== PLACAS =====================
@app.get("/placas")
async def get_placas(user_id: str = Depends(get_user_id)):
    return sb_select("placas", {"select": "*", "usuario_id": f"eq.{user_id}", "order": "creado_en"})

@app.post("/placas")
async def add_placa(body: PlacaModel, user_id: str = Depends(get_user_id)):
    if sb_select("placas", {"select": "id", "usuario_id": f"eq.{user_id}", "placa": f"eq.{body.placa}"}):
        raise HTTPException(status_code=400, detail="Esta placa ya está registrada.")
    result = sb_insert("placas", {
        "usuario_id": user_id, "placa": body.placa,
        "nombre": body.nombre, "celular": body.celular
    })
    return result[0]

@app.delete("/placas/{placa_id}")
async def delete_placa(placa_id: str, user_id: str = Depends(get_user_id)):
    sb_delete("placas", {"id": f"eq.{placa_id}", "usuario_id": f"eq.{user_id}"})
    return {"ok": True}

# ===================== ALERTAS =====================
@app.get("/alertas")
async def get_alertas(user_id: str = Depends(get_user_id)):
    return sb_select("alertas", {
        "select": "*", "usuario_id": f"eq.{user_id}",
        "order": "creado_en.desc", "limit": 50
    })

# ===================== CONSULTA ANM =====================
def consultar_anm(placa: str) -> dict:
    try:
        session = requests.Session()
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        page = session.get(ANM_URL, headers=hdrs, verify=False, timeout=15)

        m = re.search(r'<input[^>]*name="_token"[^>]*value="([^"]*)"', page.text)
        data = {"numero": placa}
        if m:
            data["_token"] = m.group(1)

        resp = session.post(ANM_URL, data=data, headers=hdrs, verify=False, timeout=15)
        texto = re.sub(r'<[^>]+>', ' ', resp.text).lower()

        tiene = any(kw in texto for kw in
                    ["fecha", "publicacion", "publicación", "notificacion", "notificación", "aviso"])

        fecha = None
        for bloque in re.findall(r'[^<]{5,100}', resp.text):
            b = bloque.strip()
            if any(kw in b.lower() for kw in ["fecha", "publicacion", "publicación"]) and len(b) < 100:
                fecha = b
                break

        return {"tiene_notificacion": tiene, "fecha": fecha, "error": None}
    except Exception as e:
        return {"tiene_notificacion": False, "fecha": None, "error": str(e)}

@app.post("/consultar")
async def consultar_todas(user_id: str = Depends(get_user_id)):
    placas = sb_select("placas", {"select": "*", "usuario_id": f"eq.{user_id}"})
    resultados = []
    ahora = datetime.now().isoformat()

    for p in placas:
        resultado = consultar_anm(p["placa"])
        estado = "alerta" if resultado["tiene_notificacion"] else "activa"

        sb_update("placas", {"id": f"eq.{p['id']}"}, {
            "ultima_consulta": ahora, "estado": estado,
            "fecha_notificacion": resultado["fecha"]
        })

        if resultado["tiene_notificacion"] and resultado["fecha"]:
            sb_insert("alertas", {
                "usuario_id": user_id, "placa": p["placa"],
                "nombre": p["nombre"], "celular": p["celular"],
                "fecha_publicacion": resultado["fecha"],
                "mensaje": f"Notificación ANM detectada para placa {p['placa']} - Fecha: {resultado['fecha']}"
            })
            enviar_whatsapp(p["celular"], (
                f"Monitor ANM - Notificacion detectada!\n"
                f"Placa: {p['placa']}\nPropietario: {p['nombre']}\n"
                f"Fecha publicacion: {resultado['fecha']}\nRevisa la ANM: {ANM_URL}"
            ))

        resultados.append({
            "placa": p["placa"], "estado": estado,
            "fecha": resultado["fecha"], "error": resultado["error"]
        })

    return {"resultados": resultados, "consultadas": len(placas), "fecha": ahora}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
