from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER
from auth import autenticar_usuario, criar_token, obter_usuario_atual
import anthropic
import os
import uuid
import re

load_dotenv()

app = FastAPI(title="RECLAIM API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = MongoClient(os.getenv("MONGODB_URL"))
db = client[os.getenv("DB_NAME")]
pacientes = db["patient_profiles"]
sessoes = db["session_configs"]
laudos = db["laudos"]

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

@app.get("/")
def root():
    return {"status": "RECLAIM API online", "version": "1.2.0"}

# ─── AUTENTICAÇÃO ────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    usuario = autenticar_usuario(form.username, form.password)
    if not usuario:
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    token = criar_token({"sub": usuario["email"], "nome": usuario["nome"], "role": usuario["role"]})
    return {"access_token": token, "token_type": "bearer", "nome": usuario["nome"], "role": usuario["role"]}

# ─── PATIENT PROFILE ────────────────────────────────────────────────────────

@app.post("/api/patient-profile")
def criar_perfil(perfil: dict, usuario=Depends(obter_usuario_atual)):
    perfil["patient_id"] = str(uuid.uuid4())
    perfil["timestamp_intake"] = datetime.utcnow().isoformat()
    perfil["schema_version"] = "1.1.0"
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

# ─── SESSION CONFIG ──────────────────────────────────────────────────────────

def gerar_session_config(perfil: dict, session_number: int = 1) -> dict:
    gatilhos = {
        "street_encounter": perfil.get("trigger_street_social", 0),
        "party_environment": perfil.get("trigger_party", 0),
        "corporate_office":  perfil.get("trigger_workplace", 0),
        "family_gathering":  perfil.get("trigger_family", 0),
        "home_alone":        perfil.get("trigger_solitude", 0),
    }
    cenario = max(gatilhos, key=gatilhos.get)
    score_cenario = gatilhos[cenario] / 10

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
    nivel_inicial = min(max(1, int(score_cenario * 5)), max_cap - 1)

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
            "patient_id": perfil.get("patient_id"),
            "therapist_id": perfil.get("therapist_id"),
            "timestamp": datetime.utcnow().isoformat(),
            "session_number": session_number,
            "schema_version": "1.1.0"
        },
        "procedural_generation_rules": {
            "active_environment_id": cenario,
            "primary_sar_active": sar_dominante,
            "difficulty_matrix": {
                "initial_exposure_level": nivel_inicial,
                "max_exposure_cap": max_cap,
                "dynamic_stepping_enabled": True,
                "stepping_trigger": "biofeedback_normalized"
            },
            "environmental_triggers": {
                "meta_human_persuasion_aggressiveness": round(score_cenario * 0.6, 2),
                "audio_clutter_db": round(-20 + score_cenario * 10, 1),
                "visual_stimuli_density": round(score_cenario * (1 - coping_recusa) * 0.5, 2),
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

# ─── LAUDO PDF ───────────────────────────────────────────────────────────────

def gerar_texto_laudo(perfil: dict, config: dict) -> str:
    sar_labels = {
        "sar_negative_emotions":      "Emoções negativas",
        "sar_physical_discomfort":    "Desconforto físico",
        "sar_positive_emotions":      "Emoções positivas",
        "sar_interpersonal_conflict": "Conflito interpessoal",
        "sar_social_pressure":        "Pressão social",
        "sar_pleasant_moments":       "Momentos agradáveis",
        "sar_craving_urge":           "Fissura/Craving",
        "sar_treatment_adherence":    "Adesão ao tratamento",
    }
    cenario_labels = {
        "street_encounter":  "Encontro na Rua",
        "party_environment": "Festa Surpresa",
        "corporate_office":  "Dia Difícil no Trabalho",
        "family_gathering":  "Celebração Familiar",
        "home_alone":        "Solidão do Final de Semana",
    }
    sars_texto = "\n".join([f"- {sar_labels.get(k,k)}: {perfil.get(k,0)}/10" for k in sar_labels])
    cenario = config.get("procedural_generation_rules", {}).get("active_environment_id", "")
    sar_dom = config.get("procedural_generation_rules", {}).get("primary_sar_active", "")

    prompt = f"""Você é um psicólogo clínico especialista em dependência química.
Gere um laudo clínico profissional em português brasileiro baseado nos dados abaixo.

DADOS DO PACIENTE:
- Substância principal: {perfil.get('target_substance')}
- Fase do tratamento: {perfil.get('treatment_phase')}
- Estágio Prochaska: {perfil.get('prochaska_stage')}
- Framework clínico: {perfil.get('coping_framework')}
- Dias abstinente: {perfil.get('days_abstinent')}
- Histórico de recaídas: {perfil.get('relapse_history_count')}

PERFIL DE SITUAÇÕES DE ALTO RISCO (Marlatt/Knapp):
{sars_texto}

SESSÃO VR CONFIGURADA:
- Cenário principal: {cenario_labels.get(cenario, cenario)}
- SAR dominante: {sar_labels.get(sar_dom, sar_dom)}
- Nível de exposição inicial: {config.get('procedural_generation_rules',{}).get('difficulty_matrix',{}).get('initial_exposure_level')}
- Duração máxima: {config.get('session_parameters',{}).get('max_duration_minutes')} minutos

CARTÃO DE ENFRENTAMENTO:
1. {perfil.get('coping_card_strategy_1','')}
2. {perfil.get('coping_card_strategy_2','')}
3. {perfil.get('coping_card_strategy_3','')}

Estruture o laudo com estas seções numeradas:
1. ANÁLISE DO PERFIL DE RISCO
2. SITUAÇÕES DE ALTO RISCO IDENTIFICADAS
3. CONFIGURAÇÃO DA SESSÃO VR
4. ESTRATÉGIAS DE ENFRENTAMENTO
5. RECOMENDAÇÕES CLÍNICAS

IMPORTANTE: Escreva em texto corrido e limpo. Não use Markdown. Não use asteriscos, hashtags, traços como separadores ou qualquer símbolo de formatação. Use apenas texto puro com parágrafos separados por linha em branco. Os títulos das seções devem aparecer apenas com o número e o nome, sem símbolos. Seja técnico, objetivo e baseado em evidências (Marlatt & Gordon, 1985; Knapp & Bertolote, 2004). Máximo 400 palavras."""

    resposta = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resposta.content[0].text

def gerar_pdf(patient_id: str, perfil: dict, config: dict, texto: str) -> str:
    os.makedirs("laudos", exist_ok=True)
    filename = f"laudos/laudo_{patient_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    titulo  = ParagraphStyle('titulo', parent=styles['Title'], fontSize=16,
        textColor=colors.HexColor('#1e3a5f'), spaceAfter=6, alignment=TA_CENTER)
    subtitulo = ParagraphStyle('subtitulo', fontSize=10,
        textColor=colors.HexColor('#64748b'), spaceAfter=16, alignment=TA_CENTER)
    secao  = ParagraphStyle('secao', fontSize=11, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1e3a5f'), spaceBefore=12, spaceAfter=6)
    corpo  = ParagraphStyle('corpo', fontSize=9, leading=14, spaceAfter=6)
    rodape = ParagraphStyle('rodape', fontSize=7,
        textColor=colors.HexColor('#94a3b8'), alignment=TA_CENTER)

    elementos = []
    elementos.append(Paragraph("RECLAIM — Laudo Clínico de Sessão VRET", titulo))
    elementos.append(Paragraph(
        f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} | Framework: {perfil.get('coping_framework','').upper()}",
        subtitulo))
    elementos.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#3b82f6')))
    elementos.append(Spacer(1, 12))

    dados_tabela = [
        ["ID do Paciente", patient_id[:8]+"...", "Substância", perfil.get("target_substance","")],
        ["Fase",           perfil.get("treatment_phase",""), "Prochaska", perfil.get("prochaska_stage","")],
        ["Dias abstinente",str(perfil.get("days_abstinent",0)), "Recaídas", str(perfil.get("relapse_history_count",0))],
    ]
    tabela = Table(dados_tabela, colWidths=[3.5*cm, 4.5*cm, 3.5*cm, 4.5*cm])
    tabela.setStyle(TableStyle([
        ('FONTSIZE',      (0,0), (-1,-1), 8),
        ('FONTNAME',      (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME',      (2,0), (2,-1), 'Helvetica-Bold'),
        ('GRID',          (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS',(0,0), (-1,-1), [colors.HexColor('#f8fafc'), colors.HexColor('#f1f5f9')]),
        ('PADDING',       (0,0), (-1,-1), 6),
        ('TEXTCOLOR',     (0,0), (-1,-1), colors.HexColor('#1e293b')),
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
        "Este laudo foi gerado automaticamente pelo sistema RECLAIM com base nos dados clínicos inseridos pelo terapeuta responsável. Não substitui avaliação clínica presencial.",
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
    texto    = gerar_texto_laudo(perfil, ultima_sessao)
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
        filename=f"laudo_reclaim_{patient_id[:8]}.pdf")

# ─── GERENCIAMENTO DE USUÁRIOS (ADMIN) ──────────────────────────────────────

from auth import usuarios_col, somente_admin

@app.post("/api/usuarios")
def criar_usuario(dados: dict, admin=Depends(somente_admin)):
    from auth import pwd_context
    if usuarios_col.find_one({"email": dados.get("email")}):
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    usuario = {
        "email": dados.get("email"),
        "nome": dados.get("nome"),
        "senha_hash": pwd_context.hash(dados.get("senha")),
        "role": dados.get("role", "terapeuta"),
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

calibracoes = db["biofeedback_calibrations"]

@app.post("/api/biofeedback/{patient_id}")
def receber_calibracao(patient_id: str, dados: dict, usuario=Depends(obter_usuario_atual)):
    perfil = pacientes.find_one({"patient_id": patient_id})
    if not perfil:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    # Validação de qualidade do sinal
    hr_quality  = dados.get("heart_rate", {}).get("signal_quality", 0)
    eda_quality = dados.get("electrodermal_activity", {}).get("signal_quality", 0)
    pupil_quality = dados.get("pupil_tracking", {}).get("signal_quality", 0)

    def classificar(q):
        if q >= 0.90: return "excelente"
        if q >= 0.75: return "aceitavel"
        if q >= 0.60: return "fraco"
        return "invalido"

    status_hr    = classificar(hr_quality)
    status_eda   = classificar(eda_quality)
    status_pupil = classificar(pupil_quality)

    bloqueado = "invalido" in [status_hr, status_eda, status_pupil]
    aviso     = "fraco" in [status_hr, status_eda, status_pupil]

    dados["patient_id"]  = patient_id
    dados["timestamp"]   = datetime.utcnow().isoformat()
    dados["validacao"]   = {
        "heart_rate":   {"quality": hr_quality,    "status": status_hr},
        "eda":          {"quality": eda_quality,   "status": status_eda},
        "pupil":        {"quality": pupil_quality, "status": status_pupil},
        "sessao_liberada": not bloqueado,
        "aviso":           aviso,
        "bloqueio":        bloqueado
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

# ─── LISTAGEM DE LAUDOS POR PACIENTE ─────────────────────────────────────────

@app.get("/api/laudos/{patient_id}")
def listar_laudos(patient_id: str, usuario=Depends(obter_usuario_atual)):
    lista = list(laudos.find({"patient_id": patient_id}, {"_id": 0}))
    return {"total": len(lista), "laudos": lista}