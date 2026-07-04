from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "reclaim-secret-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

client = MongoClient(os.getenv("MONGODB_URL"))
db = client[os.getenv("DB_NAME")]
usuarios_col = db["usuarios"]

def inicializar_admin():
    if not usuarios_col.find_one({"email": "admin@reclaim.com"}):
        usuarios_col.insert_one({
            "email": "admin@reclaim.com",
            "nome": "Administrador",
            "senha_hash": pwd_context.hash("Admin26"),
            "role": "admin",
            "criado_em": datetime.utcnow().isoformat()
        })

inicializar_admin()

def verificar_senha(senha_plana, senha_hash):
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
        email = payload.get("sub")
        if not email:
            raise erro
        return payload
    except JWTError:
        raise erro

def somente_admin(usuario=Depends(obter_usuario_atual)):
    if usuario.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")
    return usuario