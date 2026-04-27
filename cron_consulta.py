"""
Monitor ANM - Backend completo
Requiere: pip install fastapi uvicorn supabase bcrypt requests beautifulsoup4 python-jose
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import bcrypt
import random
import string
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from jose import jwt, JWTError
import os
from typing import Optional
import urllib3
urllib3.disable_warnings()

# ===================== CONFIG =====================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://xxxx.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "tu-anon-key")
JWT_SECRET   = os.getenv("JWT_SECRET", "cambia-este-secreto-muy-largo-123!")
JWT_EXPIRE_DAYS = 30

# WhatsApp (CallMeBot por defecto)
CALLMEBOT_KEY = os.getenv("CALLMEBOT_KEY", "")

ANM_URL = "https://www.anm.gov.co/notificaciones-por-avisos"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(title="Monitor ANM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_user_id(authorization: str = Header(...)) -> str:
    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

def generar_codigo() -> str:
    return ''.join(random.choices(string.digits, k=6))

def generar_password_temporal() -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=10))

def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    """Envía mensaje WhatsApp via CallMeBot"""
    if not CALLMEBOT_KEY:
        print(f"[WhatsApp simulado] Para {telefono}: {mensaje}")
        return True
    try:
        url = f"https://api.callmebot.com/whatsapp.php"
        params = {"phone": telefono, "text": mensaje, "apikey": CALLMEBOT_KEY}
        resp = requests.get(url, params=params, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Error WhatsApp: {e}")
        return False

# ===================== AUTH =====================
@app.post("/auth/send-code")
async def send_code(body: SendCodeModel):
    """Envía código de verificación por WhatsApp para registro"""
    # Verificar si el teléfono ya está registrado
    existing = supabase.table("usuarios").select("id").eq("telefono", body.phone).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Este número ya tiene una cuenta registrada.")

    # Limpiar códigos anteriores del mismo teléfono
    supabase.table("codigos_verificacion").delete().eq("telefono", body.phone).execute()

    codigo = generar_codigo()
    expira = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

    supabase.table("codigos_verificacion").insert({
        "telefono": body.phone,
        "codigo": codigo,
        "expira_en": expira,
        "usado": False
    }).execute()

    mensaje = f"Monitor ANM - Hola {body.nombre}! Tu código de verificación es: *{codigo}*. Válido por 10 minutos. No lo compartas con nadie."
    enviar_whatsapp(body.phone, mensaje)

    response = {"ok": True}
    # En desarrollo, retornar el código para pruebas
    if os.getenv("ENV", "dev") == "dev":
        response["dev_code"] = codigo
    return response

@app.post("/auth/register")
async def register(body: RegisterModel):
    """Registra un nuevo usuario"""
    # Verificar código
    result = supabase.table("codigos_verificacion")\
        .select("*")\
        .eq("telefono", body.phone)\
        .eq("codigo", body.codigo)\
        .eq("usado", False)\
        .execute()

    if not result.data:
        raise HTTPException(status_code=400, detail="Código incorrecto o ya usado.")

    codigo_rec = result.data[0]
    if datetime.fromisoformat(codigo_rec["expira_en"].replace("Z","")) < datetime.utcnow():
        raise HTTPException(status_code=400, detail="El código expiró. Solicita uno nuevo.")

    # Marcar código como usado
    supabase.table("codigos_verificacion").update({"usado": True}).eq("id", codigo_rec["id"]).execute()

    # Crear usuario
    password_hash = hash_password(body.password)
    nuevo = supabase.table("usuarios").insert({
        "nombre": body.nombre,
        "telefono": body.phone,
        "password_hash": password_hash
    }).execute()

    if not nuevo.data:
        raise HTTPException(status_code=500, detail="Error al crear el usuario.")

    user = nuevo.data[0]
    token = create_token(user["id"])
    enviar_whatsapp(body.phone, f"Monitor ANM - Bienvenido {body.nombre}! Tu cuenta ha sido creada exitosamente.")
    return {"token": token, "user": {"id": user["id"], "nombre": user["nombre"], "telefono": user["telefono"]}}

@app.post("/auth/login")
async def login(body: LoginModel):
    """Inicia sesión"""
    result = supabase.table("usuarios").select("*").eq("telefono", body.phone).execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Número o contraseña incorrectos.")

    user = result.data[0]
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Número o contraseña incorrectos.")

    token = create_token(user["id"])
    return {"token": token, "user": {"id": user["id"], "nombre": user["nombre"], "telefono": user["telefono"]}}

@app.post("/auth/recover")
async def recover(body: RecoverModel):
    """Genera y envía nueva contraseña temporal por WhatsApp"""
    result = supabase.table("usuarios").select("*").eq("telefono", body.phone).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="No existe una cuenta con este número.")

    user = result.data[0]
    nueva_pass = generar_password_temporal()
    nuevo_hash = hash_password(nueva_pass)

    supabase.table("usuarios").update({"password_hash": nuevo_hash}).eq("id", user["id"]).execute()

    mensaje = f"Monitor ANM - Hola {user['nombre']}! Tu nueva contraseña es: *{nueva_pass}*\nPor seguridad, cámbiala después de iniciar sesión."
    enviar_whatsapp(body.phone, mensaje)
    return {"ok": True}

@app.post("/auth/change-password")
async def change_password(body: ChangePassModel, user_id: str = Depends(get_user_id)):
    """Cambia la contraseña del usuario autenticado"""
    result = supabase.table("usuarios").select("*").eq("id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    user = result.data[0]
    if not verify_password(body.password_actual, user["password_hash"]):
        raise HTTPException(status_code=400, detail="La contraseña actual es incorrecta.")

    if len(body.password_nueva) < 6:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener al menos 6 caracteres.")

    nuevo_hash = hash_password(body.password_nueva)
    supabase.table("usuarios").update({"password_hash": nuevo_hash}).eq("id", user_id).execute()
    return {"ok": True}

# ===================== PLACAS =====================
@app.get("/placas")
async def get_placas(user_id: str = Depends(get_user_id)):
    result = supabase.table("placas").select("*").eq("usuario_id", user_id).order("creado_en").execute()
    return result.data

@app.post("/placas")
async def add_placa(body: PlacaModel, user_id: str = Depends(get_user_id)):
    # Verificar duplicado
    existing = supabase.table("placas").select("id").eq("usuario_id", user_id).eq("placa", body.placa).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Esta placa ya está registrada.")

    result = supabase.table("placas").insert({
        "usuario_id": user_id,
        "placa": body.placa,
        "nombre": body.nombre,
        "celular": body.celular
    }).execute()
    return result.data[0]

@app.delete("/placas/{placa_id}")
async def delete_placa(placa_id: str, user_id: str = Depends(get_user_id)):
    supabase.table("placas").delete().eq("id", placa_id).eq("usuario_id", user_id).execute()
    return {"ok": True}

# ===================== ALERTAS =====================
@app.get("/alertas")
async def get_alertas(user_id: str = Depends(get_user_id)):
    result = supabase.table("alertas").select("*").eq("usuario_id", user_id).order("creado_en", desc=True).limit(50).execute()
    return result.data

# ===================== CONSULTA ANM =====================
def consultar_anm(placa: str) -> dict:
    """Consulta la ANM para una placa y detecta fecha de publicación"""
    try:
        session = requests.Session()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        page = session.get(ANM_URL, headers=headers, verify=False, timeout=15)
        soup = BeautifulSoup(page.text, "html.parser")

        # Buscar token CSRF si existe
        csrf = soup.find("input", {"name": "_token"})
        data = {"numero": placa}
        if csrf:
            data["_token"] = csrf.get("value", "")

        resp = session.post(ANM_URL, data=data, headers=headers, verify=False, timeout=15)
        resp_soup = BeautifulSoup(resp.text, "html.parser")

        # Detectar si hay fecha de publicación en la respuesta
        texto = resp_soup.get_text().lower()
        tiene_notificacion = any(kw in texto for kw in ["fecha", "publicacion", "publicación", "notificacion", "notificación", "aviso"])

        # Intentar extraer la fecha
        fecha_encontrada = None
        for tag in resp_soup.find_all(["td", "span", "p", "div"]):
            t = tag.get_text(strip=True)
            if any(kw in t.lower() for kw in ["fecha", "publicacion", "publicación"]) and len(t) < 100:
                fecha_encontrada = t
                break

        return {"tiene_notificacion": tiene_notificacion, "fecha": fecha_encontrada, "error": None}

    except Exception as e:
        return {"tiene_notificacion": False, "fecha": None, "error": str(e)}

@app.post("/consultar")
async def consultar_todas(user_id: str = Depends(get_user_id)):
    """Consulta todas las placas del usuario en la ANM"""
    placas = supabase.table("placas").select("*").eq("usuario_id", user_id).execute().data
    resultados = []
    ahora = datetime.now().isoformat()

    for p in placas:
        resultado = consultar_anm(p["placa"])
        estado = "alerta" if resultado["tiene_notificacion"] else "activa"

        supabase.table("placas").update({
            "ultima_consulta": ahora,
            "estado": estado,
            "fecha_notificacion": resultado["fecha"]
        }).eq("id", p["id"]).execute()

        if resultado["tiene_notificacion"] and resultado["fecha"]:
            supabase.table("alertas").insert({
                "usuario_id": user_id,
                "placa": p["placa"],
                "nombre": p["nombre"],
                "celular": p["celular"],
                "fecha_publicacion": resultado["fecha"],
                "mensaje": f"Notificación ANM detectada para placa {p['placa']} - Fecha: {resultado['fecha']}"
            }).execute()

            msg = f"Monitor ANM - Notificacion detectada!\nPlaca: {p['placa']}\nPropietario: {p['nombre']}\nFecha publicacion: {resultado['fecha']}\nRevisa la ANM: {ANM_URL}"
            enviar_whatsapp(p["celular"], msg)

        resultados.append({"placa": p["placa"], "estado": estado, "fecha": resultado["fecha"], "error": resultado["error"]})

    return {"resultados": resultados, "consultadas": len(placas), "fecha": ahora}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
