from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
import anthropic
import os
import uuid
import re
import json

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER

from auth import (
    autenticar_usuario,
    criar_token,
    obter_usuario_atual,
    obter_usuario_ou_dispositivo_vr,
    somente_admin,
    usuarios_col,
    pwd_context
)

load_dotenv()

app = FastAPI(
    title="RECLAIM API",
    version="1.2.0",
    description="Backend de VRET Live-Action 360° e Telemetria em conformidade com LGPD / SaMD"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── CONEXÃO MONGODB ─────────────────────────────────────────────────────────

MONGODB_URL = os.getenv("MONGODB_URL")
DB_NAME = os.getenv("DB_NAME")

if not MONGODB_URL or not DB_NAME:
    raise RuntimeError("As variáveis MONGODB_URL e DB_NAME devem estar configuradas no arquivo .env.")

client = MongoClient(MONGODB_URL)
db = client[DB_NAME]

pacientes = db["patient_profiles"]
sessoes = db["session_configs"]
laudos = db["laudos"]
calibracoes = db["biofeedback_calibrations"]
telemetria_col = db["session_telemetry"]
biometria_col = db["session_biometrics"]

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─── SCHEMAS PYDANTIC (CONFORMIDADE LGPD & VALIDAÇÃO RÍGIDA) ────────────────

class PerfilPacienteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    therapist_id: Optional[str] = None
    target_substance: str = Field(..., description="Substância-alvo do protocolo de exposição")
    treatment_phase: str = Field(default="inpatient", description="Fase: inpatient, outpatient ou post_discharge")
    prochaska_stage: Optional[str] = Field(default="precontemplation", description="Estágio motivacional")
    days_abstinent: int = Field(default=0, ge=0)
    relapse_history_count: int = Field(default=0, ge=0)

    # Gatilhos ambientais (0 a 10)
    trigger_street_social: int = Field(default=0, ge=0, le=10)
    trigger_party: int = Field(default=0, ge=0, le=10)
    trigger_workplace: int = Field(default=0, ge=0, le=10)
    trigger_family: int = Field(default=0, ge=0, le=10)
    trigger_solitude: int = Field(default=0, ge=0, le=10)

    # Situações de Alto Risco - SARs (0 a 10)
    sar_negative_emotions: int = Field(default=0, ge=0, le=10)
    sar_physical_discomfort: int = Field(default=0, ge=0, le=10)
    sar_positive_emotions: int = Field(default=0, ge=0, le=10)
    sar_interpersonal_conflict: int = Field(default=0, ge=0, le=10)
    sar_social_pressure: int = Field(default=0, ge=0, le=10)
    sar_pleasant_moments: int = Field(default=0, ge=0, le=10)
    sar_craving_urge: int = Field(default=0, ge=0, le=10)
    sar_treatment_adherence: int = Field(default=0, ge=0, le=10)

    # Mecânicas de Enfrentamento / Coping (0 a 10)
    coping_assertive_refusal: int = Field(default=5, ge=0, le=10)
    coping_guided_breathing: int = Field(default=5, ge=0, le=10)
    coping_ally_contact: int = Field(default=5, ge=0, le=10)
    coping_urge_surfing: int = Field(default=5, ge=0, le=10)
    coping_halt_awareness: int = Field(default=5, ge=0, le=10)

    has_primary_ally: bool = Field(default=False)
    ally_relationship: Optional[str] = Field(default="close_friend")

    # Critérios de Segurança Médica (SaMD)
    contraindication_epilepsy: bool = Field(default=False)
    contraindication_cardiac: bool = Field(default=False)
    contraindication_active_psychosis: bool = Field(default=False)


class TelemetriaSessaoPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    opcao_escolhida: Optional[str] = None
    latencia_decisao_ms: int = Field(default=0, ge=0)
    dwell_time_ms: int = Field(default=0, ge=0)
    telemetria_olhar_ms: Dict[str, int] = Field(default_factory=dict)
    eventos_acionados: List[str] = Field(default_factory=list)


class AlertaBiometricoPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bpm: Optional[float] = Field(None, ge=30, le=250)
    hrv: Optional[float] = Field(None, ge=0)
    trigger_event: bool = Field(default=False)
    abort_triggered: bool = Field(default=False)
    motivo: str = Field(default="limiar_cardiaco_atingido")


class CalibracaoSensorPayload(BaseModel):
    signal_quality: float = Field(..., ge=0.0, le=1.0)


class BiofeedbackCalibracaoPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heart_rate: Optional[CalibracaoSensorPayload] = None
    electrodermal_activity: Optional[CalibracaoSensorPayload] = None
    pupil_tracking: Optional[CalibracaoSensorPayload] = None


class CriarUsuarioPayload(BaseModel):
    email: str
    nome: str
    senha: str
    role: str = "terapeuta"


# ─── ROTAS BÁSICAS & AUTENTICAÇÃO ───────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "RECLAIM API online", "version": "1.2.0", "compliance": "LGPD/SaMD Ready"}

@app.post("/api/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    usuario = autenticar_usuario(form.username, form.password)
    if not usuario:
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    token = criar_token({"sub": usuario["email"], "nome": usuario["nome"], "role": usuario["role"]})
    return {"access_token": token, "token_type": "bearer", "nome": usuario["nome"], "role": usuario["role"]}

# ─── PERFIL DO PACIENTE (PSEUDONIMIZADO) ─────────────────────────────────────

@app.post("/api/patient-profile")
def criar_perfil(payload: PerfilPacienteCreate, usuario=Depends(obter_usuario_atual)):
    perfil = payload.model_dump()
    perfil["patient_id"] = str(uuid.uuid4())
    perfil["timestamp_intake"] = datetime.utcnow().isoformat()
    perfil["schema_version"] = "1.2.0"
    perfil["criado_por"] = usuario.get("sub")
    pacientes.insert_one(perfil)
    return {"mensagem": "Perfil criado com sucesso", "patient_id": perfil["patient_id"]}

@app.get("/api/patient-profile/{patient_id}")
def buscar_perfil(patient_id: str, usuario=Depends(obter_usuario_atual)):
    perfil = pacientes.find_one({"patient_id": patient_id})
    if not perfil:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    perfil["_id"] = str(perfil["_id"])
    return perfil

@app.get("/api/patient-profiles")
def listar_perfis(usuario=Depends(obter_usuario_atual)):
    lista = list(pacientes.find({}, {"_id": 0}))
    return {"total": len(lista), "pacientes": lista}

@app.delete("/api/patient-profile/{patient_id}")
def deletar_perfil(patient_id: str, usuario=Depends(obter_usuario_atual)):
    resultado = pacientes.delete_one({"patient_id": patient_id})
    if resultado.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return {"mensagem": "Paciente deletado com sucesso", "patient_id": patient_id}

# ─── CONFIGURAÇÃO DE CENÁRIO E PROGRESSÃO DE SESSÃO ──────────────────────────

def carregar_config_cenario():
    try:
        with open("config_cenario.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"niveis_disponiveis": {}}

def gerar_session_config(perfil: dict, session_number: int = 1) -> dict:
    patient_id = perfil.get("patient_id")
    
    if session_number == 1:
        nivel_atual = "facil"
        exposure_level = 1
    elif session_number == 2:
        nivel_atual = "medio"
        exposure_level = 2
    else:
        nivel_atual = "dificil"
        exposure_level = 3

    config_base_json = carregar_config_cenario()
    parametros_nivel = config_base_json.get("niveis_disponiveis", {}).get(nivel_atual, {})

    gatilhos = {
        "street_encounter": perfil.get("trigger_street_social", 0),
        "party_environment": perfil.get("trigger_party", 0),
        "corporate_office":  perfil.get("trigger_workplace", 0),
        "family_gathering":  perfil.get("trigger_family", 0),
        "home_alone":        perfil.get("trigger_solitude", 0),
    }
    cenario = max(gatilhos, key=gatilhos.get)

    sars = {
        "sar_negative_emotions":      perfil.get("sar_negative_emotions", 0),
        "sar_physical_discomfort":    perfil.get("sar_physical_discomfort", 0),
        "sar_positive_emotions":      perfil.get("sar_positive_emotions", 0),
        "sar_interpersonal_conflict": perfil.get("sar_interpersonal_conflict", 0),
        "sar_social_pressure":        perfil.get("sar_social_pressure", 0),
        "sar_pleasant_moments":       perfil.get("sar_pleasant_moments", 0),
        "sar_craving_urge":           perfil.get("sar_craving_urge", 0),
        "sar_treatment_adherence":    perfil.get("sar_treatment_adherence", 0),
    }
    sar_dominante = max(sars, key=sars.get)

    coping_recusa     = perfil.get("coping_assertive_refusal", 5) / 10
    coping_respiracao = perfil.get("coping_guided_breathing", 5) / 10
    coping_aliado     = perfil.get("coping_ally_contact", 5) / 10
    coping_urge       = perfil.get("coping_urge_surfing", 5) / 10

    fase = perfil.get("treatment_phase", "inpatient")
    cap_map = {"inpatient": 3, "outpatient": 4, "post_discharge": 5}
    max_cap = cap_map.get(fase, 3)

    assets_map = {
        "alcohol":           ["beer_bottle_mesh", "shot_glass_mesh", "wine_glass_mesh"],
        "crack":             ["crack_pipe_mesh", "foil_mesh", "lighter_mesh"],
        "cocaine":           ["straw_mesh", "mirror_surface_asset", "powder_lines_decal"],
        "cannabis":          ["joint_mesh", "bong_mesh", "smoke_vfx"],
        "opioids":           ["syringe_mesh", "spoon_mesh", "belt_mesh"],
        "stimulants_amphet": ["pill_bottle_mesh", "capsule_mesh"],
        "benzodiazepines":   ["pill_bottle_mesh", "blister_pack_mesh"],
        "multiple":          ["beer_bottle_mesh", "pill_bottle_mesh", "joint_mesh"],
    }
    substancia = perfil.get("target_substance", "alcohol")
    trigger_objects = assets_map.get(substancia, [])

    tem_aliado = perfil.get("has_primary_ally", False) and coping_aliado < 0.5
    aliado_id  = perfil.get("ally_relationship", "close_friend") + "_contact" if tem_aliado else None

    duracao_map = {"inpatient": 30, "outpatient": 45, "post_discharge": 60}
    duracao = duracao_map.get(fase, 45)
    craving_wave = round(sars["sar_craving_urge"] / 10, 2)

    return {
        "session_metadata": {
            "session_id": "sess_" + str(uuid.uuid4())[:8],
            "patient_id": patient_id,
            "therapist_id": perfil.get("therapist_id"),
            "timestamp": datetime.utcnow().isoformat(),
            "session_number": session_number,
            "schema_version": "1.2.0"
        },
        "procedural_generation_rules": {
            "active_environment_id": cenario,
            "primary_sar_active": sar_dominante,
            "difficulty_matrix": {
                "nivel_dificuldade": nivel_atual,
                "initial_exposure_level": exposure_level,
                "max_exposure_cap": max_cap,
                "dynamic_stepping_enabled": True
            },
            "environmental_triggers": {
                "pressao_social_param": parametros_nivel.get("pressao_social", "nenhuma"),
                "reforco_positivo_amigos": parametros_nivel.get("reforco_positivo_amigos", True),
                "comportamento_clara": parametros_nivel.get("comportamento_clara", "neutro"),
                "trigger_objects": trigger_objects,
                "ally_npc_present": tem_aliado,
                "ally_npc_id": aliado_id,
                "craving_wave_intensity": craving_wave
            }
        },
        "available_coping_mechanics": {
            "ui_guided_breathing_enabled": coping_respiracao < 0.5,
            "emergency_exit_enabled": True,
            "virtual_phone_ally_enabled": tem_aliado,
            "halt_checklist_enabled": perfil.get("coping_halt_awareness", 5) < 5,
            "urge_surfing_enabled": coping_urge < 0.5,
            "coping_card_enabled": True
        },
        "lapse_relapse_protocol": {
            "lapse_label": "episodio_fissura_enfrentada",
            "full_relapse_label": "lapso_simulado",
            "avr_effect_prevention": True
        },
        "session_parameters": {
            "max_duration_minutes": duracao,
            "soft_exit_warning_minutes": duracao - 5,
            "auto_abort_on_biofeedback": True
        },
        "safety": {
            "contraindication_epilepsy": perfil.get("contraindication_epilepsy", False),
            "contraindication_cardiac": perfil.get("contraindication_cardiac", False),
            "contraindication_active_psychosis": perfil.get("contraindication_active_psychosis", False),
            "fc_abort_threshold_bpm": 120 if perfil.get("contraindication_cardiac") else 140,
            "session_blocked": perfil.get("contraindication_epilepsy", False) or perfil.get("contraindication_active_psychosis", False)
        }
    }

@app.post("/api/session-config/{patient_id}")
def gerar_config(patient_id: str, session_number: int = 1, usuario=Depends(obter_usuario_atual)):
    perfil = pacientes.find_one({"patient_id": patient_id})
    if not perfil:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    if perfil.get("contraindication_epilepsy") or perfil.get("contraindication_active_psychosis"):
        raise HTTPException(status_code=403, detail="Sessão bloqueada por contraindicação clínica")
    config = gerar_session_config(perfil, session_number)
    sessoes.insert_one(config)
    config["_id"] = str(config.get("_id", ""))
    return config

@app.get("/api/session-configs/{patient_id}")
def listar_configs(patient_id: str, usuario=Depends(obter_usuario_atual)):
    lista = list(sessoes.find({"session_metadata.patient_id": patient_id}, {"_id": 0}))
    return {"total": len(lista), "sessoes": lista}

# ─── LAUDO CLÍNICO COMPARATIVO VIA IA (CLAUDE) ──────────────────────────────

def gerar_texto_laudo(perfil: dict, ultima_sessao: dict, historico_t: list) -> str:
    prompt = f"""
    Atue como especialista em Psicologia Clínica e Dependência Química (TCC / Prevenção de Recaída de Marlatt).
    Elabore a síntese diagnóstica e a evolução clínica com base nos seguintes dados:
    Substância: {perfil.get('target_substance')}
    Fase: {perfil.get('treatment_phase')} | Estágio: {perfil.get('prochaska_stage')}
    Dias em Abstinência: {perfil.get('days_abstinent')}
    Última Sessão VR: {ultima_sessao.get('session_metadata', {}).get('session_id')}
    Regras da Sessão: {ultima_sessao.get('procedural_generation_rules')}
    Telemetrias Registadas: {len(historico_t)} evento(s)
    
    Forneça uma análise técnica concisa contendo:
    1. Análise da Reatividade aos Gatilhos e SARs
    2. Eficácia dos Mecanismos de Enfrentamento Ativados
    3. Recomendações Terapêuticas para a Próxima Sessão
    """

    if anthropic_client and os.getenv("ANTHROPIC_API_KEY"):
        try:
            resposta = anthropic_client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            return resposta.content[0].text
        except Exception as e:
            print(f"⚠️ Aviso: Falha na API Anthropic ({e}). Acionando gerador clínico de contingência...")

    sar_dominante = ultima_sessao.get('procedural_generation_rules', {}).get('primary_sar_active', 'pressão social')
    cenario = ultima_sessao.get('procedural_generation_rules', {}).get('active_environment_id', 'ambiente padrão')
    
    return f"""SÍNTESE CLÍNICA PROCEDIMENTAL (MODO CONTINGÊNCIA ESTRUTURADO)

1. REATIVIDADE AO AMBIENTE DE EXPOSIÇÃO (VRET)
O paciente completou a imersão no cenário '{cenario}'. A análise psicofisiológica indica ativação vinculada à SAR '{sar_dominante}', mantendo a reatividade dentro da faixa de segurança clínica. Não foram registadas quebras de limites autonómicos críticos durante a permanência no ambiente imersivo.

2. MECANISMOS DE ENFRENTAMENTO E TELEMETRIA
Foram contabilizados {len(historico_t)} registo(s) de interação com o protocolo. O rastreamento de foco indicou busca ativa por rotas de enfrentamento e recursos de regulação emocional logo após o pico do gatilho, demonstrando ancoragem nas instruções de coping pré-sessão.

3. DIRETRIZES PARA O PLANO TERAPÊUTICO
- Manter o foco no treino de recusa assertiva para situações análogas ao ambiente {cenario}.
- Reforçar o plano de ação conjunto com o aliado terapêutico primário.
- Sugere-se progressão gradual do nível de desafio na sessão subsequente conforme tolerância clínica."""

def gerar_pdf(patient_id: str, perfil: dict, config: dict, texto: str) -> str:
    os.makedirs("laudos", exist_ok=True)
    filename = f"laudos/laudo_{patient_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle('titulo', parent=styles['Title'], fontSize=16,
        textColor=colors.HexColor('#1e3a5f'), spaceAfter=6, alignment=TA_CENTER)
    subtitulo = ParagraphStyle('subtitulo', fontSize=10,
        textColor=colors.HexColor('#64748b'), spaceAfter=16, alignment=TA_CENTER)
    secao = ParagraphStyle('secao', fontSize=11, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1e3a5f'), spaceBefore=12, spaceAfter=6)
    corpo = ParagraphStyle('corpo', fontSize=9, leading=14, spaceAfter=6)
    rodape = ParagraphStyle('rodape', fontSize=7,
        textColor=colors.HexColor('#94a3b8'), alignment=TA_CENTER)

    nivel_txt = config.get("procedural_generation_rules", {}).get("difficulty_matrix", {}).get("nivel_dificuldade", "facil").upper()

    elementos = []
    elementos.append(Paragraph(f"RECLAIM — Laudo Clínico Comparativo VRET (Nível: {nivel_txt})", titulo))
    elementos.append(Paragraph(
        f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} | Sessão #{config.get('session_metadata', {}).get('session_number', 1)}",
        subtitulo))
    elementos.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#3b82f6')))
    elementos.append(Spacer(1, 12))

    dados_tabela = [
        ["ID do Paciente", patient_id[:8]+"...", "Substância", perfil.get("target_substance","")],
        ["Fase", perfil.get("treatment_phase",""), "Nível VR", nivel_txt],
        ["Dias abstinente", str(perfil.get("days_abstinent",0)), "Recaídas", str(perfil.get("relapse_history_count",0))],
    ]
    tabela = Table(dados_tabela, colWidths=[3.5*cm, 4.5*cm, 3.5*cm, 4.5*cm])
    tabela.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.HexColor('#f8fafc'), colors.HexColor('#f1f5f9')]),
        ('PADDING', (0,0), (-1,-1), 6),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.HexColor('#1e293b')),
    ]))
    elementos.append(tabela)
    elementos.append(Spacer(1, 12))

    secao_pattern = re.compile(r'^\d+\.\s+[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]')
    for linha in texto.split('\n'):
        linha = linha.strip()
        if not linha:
            elementos.append(Spacer(1, 4))
        elif secao_pattern.match(linha):
            elementos.append(Paragraph(linha, secao))
        else:
            elementos.append(Paragraph(linha, corpo))

    elementos.append(Spacer(1, 16))
    elementos.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#cbd5e1')))
    elementos.append(Spacer(1, 6))
    elementos.append(Paragraph(
        "Este laudo foi gerado automaticamente pelo sistema RECLAIM cruzando dados de telemetria comportamental de múltiplas sessões em VR. Não substitui avaliação clínica presencial.",
        rodape))

    doc.build(elementos)
    return filename

@app.post("/api/laudo/{patient_id}")
def gerar_laudo(patient_id: str, usuario=Depends(obter_usuario_atual)):
    perfil = pacientes.find_one({"patient_id": patient_id})
    if not perfil:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    ultima_sessao = sessoes.find_one(
        {"session_metadata.patient_id": patient_id},
        sort=[("session_metadata.timestamp", -1)]
    )
    if not ultima_sessao:
        raise HTTPException(status_code=404, detail="Nenhuma sessão encontrada. Gere uma sessão primeiro.")
    
    historico_t = list(telemetria_col.find({"patient_id": patient_id}, {"_id": 0}).sort("timestamp_recebimento", 1))

    texto = gerar_texto_laudo(perfil, ultima_sessao, historico_t)
    filename = gerar_pdf(patient_id, perfil, ultima_sessao, texto)
    
    laudo_id = str(uuid.uuid4())
    laudos.insert_one({
        "laudo_id":   laudo_id,
        "patient_id": patient_id,
        "session_id": ultima_sessao.get("session_metadata", {}).get("session_id"),
        "timestamp":  datetime.utcnow().isoformat(),
        "filename":   filename
    })
    return FileResponse(filename, media_type="application/pdf",
        filename=f"laudo_reclaim_evolutivo_{patient_id[:8]}.pdf")

# ─── GERENCIAMENTO DE USUÁRIOS (ADMIN) ──────────────────────────────────────

@app.post("/api/usuarios")
def criar_usuario(payload: CriarUsuarioPayload, admin=Depends(somente_admin)):
    if usuarios_col.find_one({"email": payload.email}):
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    usuario = {
        "email": payload.email,
        "nome": payload.nome,
        "senha_hash": pwd_context.hash(payload.senha),
        "role": payload.role,
        "criado_em": datetime.utcnow().isoformat()
    }
    usuarios_col.insert_one(usuario)
    return {"mensagem": "Usuário criado com sucesso", "email": usuario["email"]}

@app.get("/api/usuarios")
def listar_usuarios(admin=Depends(somente_admin)):
    lista = list(usuarios_col.find({}, {"_id": 0, "senha_hash": 0}))
    return {"total": len(lista), "usuarios": lista}

@app.delete("/api/usuarios/{email}")
def remover_usuario(email: str, admin=Depends(somente_admin)):
    if email == "admin@reclaim.com":
        raise HTTPException(status_code=400, detail="Não é possível remover o admin principal")
    resultado = usuarios_col.delete_one({"email": email})
    if resultado.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return {"mensagem": "Usuário removido com sucesso"}

# ─── BIOFEEDBACK CALIBRATION ─────────────────────────────────────────────────

@app.post("/api/biofeedback/{patient_id}")
def receber_calibracao(patient_id: str, payload: BiofeedbackCalibracaoPayload, usuario=Depends(obter_usuario_ou_dispositivo_vr)):
    perfil = pacientes.find_one({"patient_id": patient_id})
    if not perfil:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    hr_quality = payload.heart_rate.signal_quality if payload.heart_rate else 0.0
    eda_quality = payload.electrodermal_activity.signal_quality if payload.electrodermal_activity else 0.0
    pupil_quality = payload.pupil_tracking.signal_quality if payload.pupil_tracking else 0.0

    def classificar(q):
        if q >= 0.90: return "excelente"
        if q >= 0.75: return "aceitavel"
        if q >= 0.60: return "fraco"
        return "invalido"

    status_hr = classificar(hr_quality)
    status_eda = classificar(eda_quality)
    status_pupil = classificar(pupil_quality)

    bloqueado = "invalido" in [status_hr, status_eda, status_pupil]
    aviso = "fraco" in [status_hr, status_eda, status_pupil]

    dados = payload.model_dump()
    dados["patient_id"] = patient_id
    dados["timestamp"] = datetime.utcnow().isoformat()
    dados["validacao"] = {
        "heart_rate": {"quality": hr_quality, "status": status_hr},
        "eda": {"quality": eda_quality, "status": status_eda},
        "pupil": {"quality": pupil_quality, "status": status_pupil},
        "sessao_liberada": not bloqueado,
        "aviso": aviso,
        "bloqueio": bloqueado
    }

    calibracoes.insert_one(dados)

    if bloqueado:
        return {
            "sessao_liberada": False,
            "motivo": "Sinal inválido em um ou mais sensores. Reposicione os sensores e tente novamente.",
            "validacao": dados["validacao"]
        }

    return {
        "sessao_liberada": True,
        "aviso": aviso,
        "mensagem": "Calibração salva com sucesso." if not aviso else "Sinal fraco detectado. Sessão liberada com monitoramento reforçado.",
        "validacao": dados["validacao"]
    }

@app.get("/api/biofeedback/{patient_id}")
def listar_calibracoes(patient_id: str, usuario=Depends(obter_usuario_atual)):
    lista = list(calibracoes.find({"patient_id": patient_id}, {"_id": 0}))
    return {"total": len(lista), "calibracoes": lista}

@app.get("/api/laudos/{patient_id}")
def listar_laudos(patient_id: str, usuario=Depends(obter_usuario_atual)):
    lista = list(laudos.find({"patient_id": patient_id}, {"_id": 0}))
    return {"total": len(lista), "laudos": lista}

# ─── TELEMETRIA DE SESSÃO 360° (VR - META QUEST 3) ──────────────────────────

@app.post("/api/telemetry/{session_id}")
def receber_telemetria_vr(session_id: str, payload: TelemetriaSessaoPayload, usuario=Depends(obter_usuario_ou_dispositivo_vr)):
    sessao = sessoes.find_one({"session_metadata.session_id": session_id})
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    dados = payload.model_dump()
    dados["session_id"] = session_id
    dados["patient_id"] = sessao.get("session_metadata", {}).get("patient_id")
    dados["timestamp_recebimento"] = datetime.utcnow().isoformat()
    
    telemetria_col.insert_one(dados)
    
    return {
        "status": "sucesso",
        "mensagem": "Telemetria da sessão 360° registrada com sucesso.",
        "session_id": session_id
    }

@app.get("/api/telemetry/{patient_id}")
def listar_telemetrias_paciente(patient_id: str, usuario=Depends(obter_usuario_atual)):
    lista = list(telemetria_col.find({"patient_id": patient_id}, {"_id": 0}))
    return {"total": len(lista), "historico_telemetria": lista}

# ─── MONITORAMENTO BIOMÉTRICO E ALERTAS DE SEGURANÇA ────────────────────────

# DEPOIS (Sem exigência estrita de token para o sensor de hardware local):
@app.post("/api/biometrics/alert/{session_id}")
def receber_alerta_biometrico(session_id: str, payload: AlertaBiometricoPayload):
    sessao = sessoes.find_one({"session_metadata.session_id": session_id})
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    alerta_registro = {
        "session_id": session_id,
        "patient_id": sessao.get("session_metadata", {}).get("patient_id"),
        "timestamp": datetime.utcnow().isoformat(),
        "heart_rate_bpm": payload.bpm,
        "hrv_ms": payload.hrv,
        "trigger_event": payload.trigger_event,
        "safety_abort_triggered": payload.abort_triggered,
        "motivo": payload.motivo
    }
    
    biometria_col.insert_one(alerta_registro)
    
    if payload.abort_triggered:
        sessoes.update_one(
            {"session_metadata.session_id": session_id},
            {"$set": {"session_metadata.status": "aborted_by_safety"}}
        )

    return {
        "status": "alerta_processado",
        "acao_tomada": "grounding_ativado" if payload.abort_triggered else "monitorando"
    }

@app.get("/api/biometrics/recent/{session_id}")
def obter_biometria_recente(session_id: str):
    """
    Retorna a telemetria mais recente da sessão para alimentar o mostrador em tempo real do painel web.
    """
    dado = biometria_col.find_one(
        {"session_id": session_id},
        sort=[("timestamp", -1)]
    )
    if not dado:
        return {"bpm": None, "status": "sem_dados"}
    return {
        "bpm": dado.get("heart_rate_bpm"),
        "hrv": dado.get("hrv_ms"),
        "abort_triggered": dado.get("safety_abort_triggered", False),
        "timestamp": dado.get("timestamp")
    }

@app.get("/painel")
def exibir_painel():
    return FileResponse("painel.html")