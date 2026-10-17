from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

# ─── CONFIGURAÇÃO DE SEGURANÇA ───────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "A variável de ambiente SECRET_KEY não está configurada no arquivo .env. "
        "Defina uma chave criptográfica forte antes de iniciar o servidor para conformidade e segurança."
    )

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ─── CONEXÃO COM O BANCO DE DADOS ────────────────────────────────────────────

MONGODB_URL = os.getenv("MONGODB_URL")
DB_NAME = os.getenv("DB_NAME")

if not MONGODB_URL or not DB_NAME:
    raise RuntimeError(
        "As variáveis MONGODB_URL e DB_NAME devem estar configuradas no arquivo .env."
    )

client = MongoClient(MONGODB_URL)
db = client[DB_NAME]
usuarios_col = db["usuarios"]

def inicializar_admin():
    """Garante a existência do usuário administrador padrão caso ainda não exista."""
    if not usuarios_col.find_one({"email": "admin@reclaim.com"}):
        senha_admin = os.getenv("ADMIN_DEFAULT_PASSWORD", "Admin26")
        usuarios_col.insert_one({
            "email": "admin@reclaim.com",
            "nome": "Administrador",
            "senha_hash": pwd_context.hash(senha_admin),
            "role": "admin",
            "criado_em": datetime.utcnow().isoformat()
        })

inicializar_admin()

# ─── FUNÇÕES DE AUTENTICAÇÃO E HASH ──────────────────────────────────────────

def verificar_senha(senha_plana: str, senha_hash: str) -> bool:
    return pwd_context.verify(senha_plana, senha_hash)

def autenticar_usuario(email: str, senha: str):
    usuario = usuarios_col.find_one({"email": email})
    if not usuario:
        return None
    if not verificar_senha(senha, usuario["senha_hash"]):
        return None
    return {"email": usuario["email"], "nome": usuario["nome"], "role": usuario["role"]}

def criar_token(dados: dict):
    dados_copy = dados.copy()
    expira = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    dados_copy.update({"exp": expira})
    return jwt.encode(dados_copy, SECRET_KEY, algorithm=ALGORITHM)

def obter_usuario_atual(token: str = Depends(oauth2_scheme)):
    erro = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        identificador = payload.get("sub")
        if not identificador:
            raise erro
        return payload
    except JWTError:
        raise erro

def somente_admin(usuario=Depends(obter_usuario_atual)):
    if usuario.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return usuario

# ─── SUPORTE A TOKEN DE SESSÃO VR (META QUEST 3) ─────────────────────────────

def criar_token_sessao_vr(session_id: str, patient_id: str):
    """
    Gera um token JWT de curta duração (ex: 4 horas) específico para o dispositivo VR
    durante a execução da sessão clínica, evitando login manual no óculos.
    """
    expira = datetime.utcnow() + timedelta(hours=4)
    dados = {
        "sub": f"vr_device_{patient_id}",
        "session_id": session_id,
        "patient_id": patient_id,
        "role": "vr_client",
        "exp": expira
    }
    return jwt.encode(dados, SECRET_KEY, algorithm=ALGORITHM)

def obter_cliente_vr_atual(token: str = Depends(oauth2_scheme)):
    """
    Valida se o token enviado pelo aplicativo Unity do Meta Quest 3 é legítimo.
    """
    erro = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de dispositivo VR inválido ou expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "vr_client":
            raise erro
        return payload
    except JWTError:
        raise erro

def obter_usuario_ou_dispositivo_vr(token: str = Depends(oauth2_scheme)):
    """
    Permite autenticação tanto por usuários cadastrados (terapeutas/admins)
    quanto pelo dispositivo VR (Meta Quest 3). Útil para endpoints de telemetria.
    """
    erro = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas ou token expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        role = payload.get("role")
        if role not in ["admin", "terapeuta", "vr_client"]:
            raise erro
        return payload
    except JWTError:
        raise erro