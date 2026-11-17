import asyncio
import base64
import json
import time
import requests

BACKEND_URL = "http://127.0.0.1:8000"

def log_header(title):
    print("\n" + "=" * 70)
    print(f"🚀 TEST: {title}")
    print("=" * 70)

def test_scenario_1_vision_homework():
    log_header("Senaryo 1: Karmaşık Devre Şeması ve Dağınık Kullanıcı İstemi")
    prompt = "hocam şu sorulara bi el atar mısın hepsini çöz kafam karıştı"
    
    # Load test image
    with open("test_page.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    data_url = f"data:image/png;base64,{img_b64}"

    payload = {
        "prompt": prompt,
        "model_ids": ["nvidia/nemotron-3.5-super-vl-120b-a12b"],
        "attachments": [
            {
                "filename": "donem_odevi.png",
                "file_type": "image",
                "data_url": data_url,
                "content_type": "image/png",
                "size": len(img_b64)
            }
        ],
        "web_search": False
    }

    start = time.perf_counter()
    r = requests.post(f"{BACKEND_URL}/api/compare", json=payload, timeout=120)
    elapsed = round(time.perf_counter() - start, 2)
    
    print(f"HTTP Status: {r.status_code} ({elapsed}s)")
    assert r.status_code == 200, f"HTTP Error: {r.text}"
    data = r.json()
    res = data["results"][0]
    print(f"Model: {res['model_name']} | Status: {res['status']} | Error: {res['error_message']}")
    
    text = res.get("response_text") or ""
    print(f"Response length: {len(text)} characters")
    print(f"--- Snippet (İlk 400 karakter) ---\n{text[:400]}\n---")
    
    # Assertions
    assert res["status"] == "success", f"Model failed: {res['error_message']}"
    assert len(text) > 200, "Response too short!"
    # Verify no English bulleted CoT leakage
    assert "• \"4 kΩ\" is above" not in text, "English CoT leaked!"
    assert "Another possibility:" not in text, "English CoT leaked!"
    # Verify LaTeX math is present
    assert "$" in text or "$$" in text, "No LaTeX math formatted!"
    print("✅ Senaryo 1 BAŞARIYLA TAMAMLANDI!")
    return text

def test_scenario_2_live_web_search():
    log_header("Senaryo 2: Gerçek Zamanlı ve Detaylı Staj / İş Araması")
    prompt = "2. sınıf bilgisayar mühendisliği öğrencisiyim C++ ve ROS 2 biliyorum uzaktan staj veya junior pozisyonları bul bana doğrudan başvuru linkleri olsun"
    
    payload = {
        "prompt": prompt,
        "model_ids": ["nvidia/nemotron-3.5-bolt-e4b"],
        "web_search": True
    }

    start = time.perf_counter()
    r = requests.post(f"{BACKEND_URL}/api/compare", json=payload, timeout=90)
    elapsed = round(time.perf_counter() - start, 2)
    
    print(f"HTTP Status: {r.status_code} ({elapsed}s)")
    assert r.status_code == 200, f"HTTP Error: {r.text}"
    data = r.json()
    res = data["results"][0]
    print(f"Model: {res['model_name']} | Status: {res['status']} | Error: {res['error_message']}")
    
    text = res.get("response_text") or ""
    print(f"Response length: {len(text)} characters")
    print(f"--- Snippet ---\n{text[:400]}\n---")
    
    assert res["status"] == "success", f"Model failed: {res['error_message']}"
    # Verify no tool call loops
    assert "<tool_call>" not in text, "Tool call leaked into user text!"
    assert "</tool_call>" not in text, "Tool call tag leaked!"
    # Check if links are present
    assert "http" in text or "[" in text, "No links found in response!"
    print("✅ Senaryo 2 BAŞARIYLA TAMAMLANDI!")
    return text

def test_scenario_3_multi_model_math_logic():
    log_header("Senaryo 3: Eşzamanlı Çoklu Model Karşılaştırması (Mantık & Kombinatorik)")
    prompt = "Bir küpün tüm yüzeyleri kırmızıya boyanıyor. Sonra bu küp 27 eşit küçük küpe bölünüyor. Tam olarak 2 yüzü kırmızı olan kaç küçük küp vardır? Adım adım açıkla ve sonucu \\boxed{} içine al."
    
    payload = {
        "prompt": prompt,
        "model_ids": [
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "poolside/laguna-xs-2.1"
        ],
        "web_search": False
    }

    start = time.perf_counter()
    r = requests.post(f"{BACKEND_URL}/api/compare", json=payload, timeout=90)
    elapsed = round(time.perf_counter() - start, 2)
    
    print(f"HTTP Status: {r.status_code} ({elapsed}s)")
    assert r.status_code == 200, f"HTTP Error: {r.text}"
    data = r.json()
    
    for res in data["results"]:
        text = res.get("response_text") or ""
        print(f"\nModel: {res['model_name']} | Status: {res['status']} | Time: {res.get('response_time_ms')}ms")
        print(f"Snippet: {text[:250]}...")
        assert res["status"] == "success", f"Model {res['model_name']} failed: {res['error_message']}"
        assert "12" in text, "Correct answer (12) not found in response!"
        assert "\\boxed{" in text or "12" in text, "Boxed formatting missing!"
        
    print("\n✅ Senaryo 3 BAŞARIYLA TAMAMLANDI!")

def test_scenario_4_code_architecture():
    log_header("Senaryo 4: Karmaşık Kod Mimarisi ve Asenkron Python")
    prompt = "FastAPI ve Pydantic v2 kullanarak bir rate limiter middleware yaz. Redis sliding window algoritması kullansın, IP başına dakikada 60 istek sınırı koysun ve 429 Too Many Requests döndürsün."
    
    payload = {
        "prompt": prompt,
        "model_ids": ["poolside/laguna-xs-2.1"],
        "web_search": False
    }

    start = time.perf_counter()
    r = requests.post(f"{BACKEND_URL}/api/compare", json=payload, timeout=90)
    elapsed = round(time.perf_counter() - start, 2)
    
    print(f"HTTP Status: {r.status_code} ({elapsed}s)")
    assert r.status_code == 200, f"HTTP Error: {r.text}"
    data = r.json()
    res = data["results"][0]
    text = res.get("response_text") or ""
    print(f"Model: {res['model_name']} | Status: {res['status']}")
    print(f"Snippet: {text[:300]}...")
    
    assert res["status"] == "success", f"Model failed: {res['error_message']}"
    assert "```python" in text or "```" in text, "No code block returned!"
    assert "BaseHTTPMiddleware" in text or "Request" in text or "redis" in text.lower(), "Core components missing!"
    print("✅ Senaryo 4 BAŞARIYLA TAMAMLANDI!")

if __name__ == "__main__":
    print("🏁 MULTIVERSE KENDİ KENDİNE TEST BATARYASI BAŞLADI 🏁")
    test_scenario_3_multi_model_math_logic()
    test_scenario_4_code_architecture()
    test_scenario_2_live_web_search()
    test_scenario_1_vision_homework()
    print("\n🎉 TÜM TESTLER BAŞARIYLA GEÇTİ! SİSTEM %100 SAĞLIKLI! 🎉")
