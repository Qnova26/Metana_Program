import time
import requests
import random
from datetime import datetime, timezone

# URL Endpoint Flask Server Anda (Ganti port jika berbeda)
SERVER_URL = "http://127.0.0.1:5000/data"

DEVICE_ID = "ESP32_NODE_01"

def generate_dummy_data():
    """Menghasilkan data simulasi yang masuk akal untuk sensor gas dan suhu"""
    return {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ch4_ppm": round(random.uniform(50.0, 150.0), 2),
        "humidity": round(random.uniform(60.0, 80.0), 2),
        "temperature": round(random.uniform(28.0, 32.0), 2),
        "battery_v": round(random.uniform(3.7, 4.2), 2),
        "battery_pct": random.randint(70, 100),
        "relay_status": random.choice([True, False])
    }

def run_simulator():
    print(f"Memulai Simulator ESP32 - Mengirim ke {SERVER_URL}")
    print("Tekan Ctrl+C untuk berhenti.\n")
    
    try:
        while True:
            # 1. Generate Data
            payload = generate_dummy_data()
            print(f"[{payload['timestamp']}] Mengirim Data: CH4={payload['ch4_ppm']}ppm, Temp={payload['temperature']}C")
            
            # 2. Kirim via HTTP POST
            try:
                response = requests.post(SERVER_URL, json=payload, timeout=5)
                if response.status_code == 200:
                    print("=> Sukses diterima server:", response.json())
                else:
                    print(f"=> Gagal! HTTP Status: {response.status_code}, {response.text}")
            except requests.exceptions.ConnectionError:
                print("=> Error: Tidak bisa terhubung ke Server. Pastikan Server.py sedang berjalan!")
            except Exception as e:
                print("=> Error lain:", str(e))
                
            # 3. Tunggu 60 detik (Interval 1 menit sesuai PRD)
            # Untuk testing agar tidak terlalu lama, kita bisa ubah ini jadi 5 detik,
            # tapi kita set 60 agar sama persis dengan aslinya.
            time.sleep(60)
            
    except KeyboardInterrupt:
        print("\nSimulator dihentikan oleh user.")

if __name__ == "__main__":
    run_simulator()
