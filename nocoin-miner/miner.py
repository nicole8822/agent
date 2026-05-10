import os
import time
import requests
import traceback
from dotenv import load_dotenv

# =========================================================
# LOAD ENV
# =========================================================

load_dotenv()

AGENT_ETH_ADDRESS = os.getenv("AGENT_ETH_ADDRESS", "<YOUR_ETH_ADDRESS>")
AGENT_NAME = os.getenv("AGENT_NAME", "<YOUR_AGENT_NAME>")

BASE_URL = "https://bqrapnlqqtjedjyhlfci.supabase.co/functions/v1/submit-solution"
API_KEY = os.getenv("API_KEY")

HEADERS = {
    "apikey": API_KEY,
    "Content-Type": "application/json"
}

REQUEST_TIMEOUT = 60
BACKOFF = 5

# =========================================================
# FREE AI STACK ONLY
# =========================================================

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# =========================================================
# LOGGING
# =========================================================

def log(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] {msg}")

DEBUG = True

def debug(tag, data):
    if DEBUG:
        print(f"\n[DEBUG:{tag}] {data}")

# =========================================================
# FETCH PUZZLE
# =========================================================

def fetch_puzzle():
    try:
        r = requests.get(
            f"{BASE_URL}?eth={AGENT_ETH_ADDRESS}",
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        debug("FETCH_STATUS", r.status_code)

        if r.status_code == 200:
            puzzle = r.json().get("puzzle")
            debug("PUZZLE", puzzle)
            return puzzle

    except Exception as e:
        log(f"Fetch error: {e}")
        debug("FETCH_ERR", traceback.format_exc())

    return None

# =========================================================
# OLLAMA (LOCAL - PRIMARY FREE AI)
# =========================================================

def call_ollama(prompt):
    try:
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": prompt,
                "stream": False
            },
            timeout=REQUEST_TIMEOUT
        )

        data = r.json().get("response", "").strip()

        debug("OLLAMA", data)

        return data

    except Exception as e:
        log(f"Ollama error: {e}")
        debug("OLLAMA_ERR", traceback.format_exc())
        return None

# =========================================================
# GEMINI (FREE CLOUD AI)
# =========================================================

def call_gemini(prompt):
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}]
            },
            timeout=REQUEST_TIMEOUT
        )

        text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        debug("GEMINI", text)

        return text

    except Exception as e:
        log(f"Gemini error: {e}")
        debug("GEMINI_ERR", traceback.format_exc())
        return None

# =========================================================
# VALIDATION
# =========================================================

def is_valid(ans):
    if not ans:
        return False
    ans = ans.strip()
    if len(ans) == 0:
        return False
    if len(ans) > 200:
        return False
    if "error" in ans.lower():
        return False
    return True

# =========================================================
# AI CHAIN (FREE ONLY)
# ORDER: OLLAMA → GEMINI
# =========================================================

def solve_with_ai(prompt):
    log("🧠 AI CHAIN START (FREE ONLY)")

    chain = [
        ("Ollama", call_ollama),
        ("Gemini", call_gemini)
    ]

    for name, fn in chain:
        log(f"→ Trying {name}")

        ans = fn(prompt)

        debug(f"{name}_RAW", ans)

        if is_valid(ans):
            log(f"✅ {name} SUCCESS: {ans}")
            return ans
        else:
            log(f"❌ {name} failed")

    log("🚫 ALL FREE AI FAILED")
    return None

# =========================================================
# SUBMIT
# =========================================================

def submit(pid, answer):
    payload = {
        "eth_address": AGENT_ETH_ADDRESS,
        "agent_name": AGENT_NAME,
        "puzzle_id": pid,
        "answer": answer
    }

    log(f"📤 SUBMITTING: {payload}")

    try:
        r = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT)

        debug("SUBMIT_STATUS", r.status_code)
        debug("SUBMIT_RESPONSE", r.text)

        return r.status_code == 200

    except Exception as e:
        log(f"Submit error: {e}")
        return False

# =========================================================
# MAIN LOOP
# =========================================================

def run():
    log(f"SOUL MINER STARTED - {AGENT_NAME}")
    log(f"Wallet: {AGENT_ETH_ADDRESS}")

    while True:
        time.sleep(BACKOFF)

        puzzle = fetch_puzzle()
        if not puzzle:
            continue

        pid = puzzle.get("id")
        prompt = puzzle.get("prompt", "")

        log(f"🧩 PUZZLE: {prompt}")

        answer = solve_with_ai(prompt)

        if not answer:
            log("❌ No AI solved puzzle")
            continue

        log(f"🤖 FINAL ANSWER: {answer}")

        if submit(pid, answer):
            log(f"✅ SUCCESS: {pid}")
        else:
            log(f"❌ FAILED: {pid}")

if __name__ == "__main__":
    run()
