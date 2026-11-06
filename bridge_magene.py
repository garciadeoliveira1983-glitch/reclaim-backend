import asyncio
from bleak import BleakScanner, BleakClient
import requests

HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
API_BASE = "http://127.0.0.1:8000"
SESSION_ID_TESTE = "sess_4197ab1f"

def parse_heart_rate(data: bytearray):
    flags = data[0]
    hr_is_uint16 = (flags & 0x01) != 0
    rr_present = (flags & 0x10) != 0
    offset = 1
    if hr_is_uint16:
        bpm = int.from_bytes(data[offset:offset+2], byteorder="little")
        offset += 2
    else:
        bpm = data[offset]
        offset += 1
    return bpm

async def notification_handler(sender, data: bytearray):
    bpm = parse_heart_rate(data)
    print(f"💓 Magene [62814-1] -> FC: {bpm} BPM")
    try:
        requests.post(
            f"{API_BASE}/api/biometrics/alert/{SESSION_ID_TESTE}",
            json={
                "bpm": float(bpm),
                "hrv": 0.0,
                "trigger_event": bpm > 100,
                "abort_triggered": bpm >= 140,
                "motivo": "pico_cardiaco" if bpm > 100 else "monitoramento_continuo"
            },
            timeout=2.0
        )
    except Exception:
        pass

async def main():
    print("-------------------------------------------------------")
    print("Procurando o sensor (62814-1 / Magene)...")
    print("-------------------------------------------------------")
    
    # Busca por 10 segundos para dar tempo do rádio BLE acordar
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and any(x in d.name for x in ["62814", "Magene", "H003", "Heart", "HRM"]),
        timeout=10.0
    )
    
    if not device:
        print("❌ Sensor não encontrado. Certifique-se de que removeu do menu do Windows e umedeceu os eletrodos.")
        return

    print(f"✅ Sensor detectado: {device.name} [{device.address}]. Conectando...")
    async with BleakClient(device) as client:
        print("⚡ CONECTADO COM SUCESSO! Transmitindo batimentos em tempo real:")
        await client.start_notify(HR_MEASUREMENT_UUID, notification_handler)
        while True:
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())