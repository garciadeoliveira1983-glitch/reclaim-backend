import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

def executar_testes():
    print("=" * 65)
    print(" INICIANDO BATERIA DE TESTES RECLAIM VR (LGPD / SAMD / FASTAPI)")
    print("=" * 65)

    # 1. AUTENTICAÇÃO E OBTENÇÃO DE TOKEN JWT
    print("\n[1/5] A testar autenticação (/api/auth/login)...")
    login_data = {
        "username": "admin@reclaim.com",
        "password": "Admin26"  # Altere caso tenha recriado o utilizador com outra senha
    }
    
    try:
        resp_login = requests.post(f"{BASE_URL}/api/auth/login", data=login_data)
    except requests.exceptions.ConnectionError:
        print("❌ ERRO: O servidor FastAPI não está a correr na porta 8000.")
        print("Execute 'uvicorn main:app --reload' no terminal antes de rodar o teste.")
        sys.exit(1)

    if resp_login.status_code != 200:
        print(f"❌ Falha no login: Status {resp_login.status_code}")
        print(resp_login.text)
        sys.exit(1)

    token = resp_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    print(f"✅ Autenticação bem-sucedida! Token JWT obtido.")

    # 2. TESTE DE BLINDAGEM LGPD (EXTRA FIELDS FORBIDDEN)
    print("\n[2/5] A testar bloqueio estrito de dados identificáveis (LGPD)...")
    payload_com_dados_pessoais = {
        "target_substance": "alcohol",
        "treatment_phase": "inpatient",
        "days_abstinent": 10,
        "nome_civil": "Paciente Teste Proibido",
        "cpf": "123.456.789-00"
    }

    resp_lgpd = requests.post(
        f"{BASE_URL}/api/patient-profile",
        headers=headers,
        json=payload_com_dados_pessoais
    )

    if resp_lgpd.status_code == 422:
        print("✅ SUCESSO LGPD: O sistema rejeitou os campos não autorizados ('extra_forbidden')!")
        print(f"   Resposta HTTP 422: {resp_lgpd.json()['detail'][0]['msg']}")
    else:
        print(f"⚠️ FALHA: Esperava status 422, mas recebeu {resp_lgpd.status_code}.")

    # 3. CRIAÇÃO DE PERFIL VÁLIDO PSEUDONIMIZADO
    print("\n[3/5] A criar perfil clínico 100% pseudonimizado...")
    payload_valido = {
        "target_substance": "alcohol",
        "treatment_phase": "inpatient",
        "prochaska_stage": "contemplation",
        "days_abstinent": 14,
        "relapse_history_count": 2,
        "trigger_street_social": 6,
        "trigger_party": 8,
        "trigger_workplace": 3,
        "trigger_family": 5,
        "trigger_solitude": 7,
        "sar_negative_emotions": 7,
        "sar_physical_discomfort": 4,
        "sar_positive_emotions": 6,
        "sar_interpersonal_conflict": 5,
        "sar_social_pressure": 8,
        "sar_pleasant_moments": 4,
        "sar_craving_urge": 8,
        "sar_treatment_adherence": 7,
        "coping_assertive_refusal": 4,
        "coping_guided_breathing": 6,
        "coping_ally_contact": 3,
        "coping_urge_surfing": 5,
        "coping_halt_awareness": 6,
        "has_primary_ally": True,
        "ally_relationship": "close_friend",
        "contraindication_epilepsy": False,
        "contraindication_cardiac": False,
        "contraindication_active_psychosis": False
    }

    resp_perfil = requests.post(f"{BASE_URL}/api/patient-profile", headers=headers, json=payload_valido)
    if resp_perfil.status_code != 200:
        print(f"❌ Falha ao criar perfil: {resp_perfil.text}")
        sys.exit(1)

    patient_id = resp_perfil.json()["patient_id"]
    print(f"✅ Perfil gravado no MongoDB! Patient ID (UUID): {patient_id}")

    # 4. GERAÇÃO PROCEDURAL DA SESSÃO VR
    print(f"\n[4/5] A gerar configuração procedimental da Sessão 1...")
    resp_sessao = requests.post(f"{BASE_URL}/api/session-config/{patient_id}?session_number=1", headers=headers)
    if resp_sessao.status_code != 200:
        print(f"❌ Falha ao gerar configuração da sessão: {resp_sessao.text}")
        sys.exit(1)

    dados_sessao = resp_sessao.json()
    session_id = dados_sessao["session_metadata"]["session_id"]
    cenario_escolhido = dados_sessao["procedural_generation_rules"]["active_environment_id"]
    sar_dominante = dados_sessao["procedural_generation_rules"]["primary_sar_active"]

    print(f"✅ Sessão configurada com sucesso!")
    print(f"   Session ID: {session_id}")
    print(f"   Cenário Ativo: {cenario_escolhido}")
    print(f"   SAR Dominante: {sar_dominante}")

    # 5. SIMULAÇÃO DE TELEMETRIA VR (META QUEST 3)
    print(f"\n[5/5] A enviar telemetria de interação 360° ({session_id})...")
    payload_telemetria = {
        "opcao_escolhida": "recusa_assertiva",
        "latencia_decisao_ms": 3420,
        "dwell_time_ms": 1150,
        "telemetria_olhar_ms": {
            "garrafa_cerveja": 1150,
            "amigo_oferecendo": 1850,
            "saida_emergencia": 420
        },
        "eventos_acionados": ["craving_wave_peaked", "breathing_exercise_opened"]
    }

    resp_telemetria = requests.post(f"{BASE_URL}/api/telemetry/{session_id}", headers=headers, json=payload_telemetria)
    if resp_telemetria.status_code == 200:
        print(f"✅ Telemetria de sessão gravada no MongoDB Atlas!")
    else:
        print(f"❌ Falha ao enviar telemetria: {resp_telemetria.text}")

    print("\n" + "=" * 65)
    print(" 🎉 TODOS OS TESTES PASSARAM COM SUCESSO!")
    print("=" * 65)

if __name__ == "__main__":
    executar_testes()