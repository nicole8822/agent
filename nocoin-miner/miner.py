import os
import re
import json
import time
import hashlib
import threading
import queue
import requests
import ast

from dotenv import load_dotenv
from openai import OpenAI

# =========================================================
# CONFIG & CONSTANTS
# =========================================================

load_dotenv()

AGENT_ETH_ADDRESS = os.getenv("AGENT_ETH_ADDRESS")
AGENT_NAME = os.getenv("AGENT_NAME", "nocoin-agent-v3-elite")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

POLL_INTERVAL = 5
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3

# AGENT MEMORY CACHE
MEMORY = {}

# PERSISTENT MEMORY FILE
MEMORY_FILE = "memory_cache.json"

BASE_URL = "https://bqrapnlqqtjedjyhlfci.supabase.co/functions/v1/submit-solution"
API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJxcmFwbmxxcXRqZWRqeWhsZmNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNzUyNjQsImV4cCI6MjA5Mzg1MTI2NH0.mf0fz6kAnK0yeAXrb-XT6yikbdRmeAq5jsikVPPhaFE"

HEADERS = {
    "apikey": API_KEY,
    "Content-Type": "application/json"
}

if not AGENT_ETH_ADDRESS:
    raise Exception("Missing AGENT_ETH_ADDRESS in .env")

# =========================================================
# OPENAI CLIENT
# =========================================================

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# =========================================================
# SOUL & LOGGING
# =========================================================

SOUL_PROMPT = ""
if os.path.exists("soul.md"):
    with open("soul.md", "r", encoding="utf-8") as f:
        SOUL_PROMPT = f.read()

def log(msg):
    print(f"\n{msg}")

def soul_prompt_excerpt():
    return SOUL_PROMPT[:3000] if SOUL_PROMPT else ""

# =========================================================
# MEMORY SYSTEM (NEW)
# =========================================================

def load_memory():
    global MEMORY
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, "r") as f:
                MEMORY = json.load(f)
    except:
        MEMORY = {}

def save_memory():
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(MEMORY, f)
    except:
        pass

# =========================================================
# BRAIN: UNIVERSAL PROMPTING & FALLBACK
# =========================================================

def build_universal_prompt(prompt, category):
    return f"""
{soul_prompt_excerpt()}
You are a fully autonomous reasoning engine capable of solving ANY task:
math, logic, riddles, coding, blockchain, science, reasoning, patterns.

RULES:
- Return ONLY final answer
- No explanation
- No markdown
- No extra text
- Be precise

QUESTION:
{prompt}
"""

def ultra_fallback_solver(prompt):
    res = ask_llm_structured(build_universal_prompt(prompt, "general"), "openai")
    if res and "answer" in res:
        return clean_output(res.get("answer"))
    return None

# =========================================================
# SECURITY
# =========================================================

def safe_eval(expr):
    try:
        if not re.match(r'^[0-9\+\-\*\/\(\)\.\s]+$', expr):
            return None
        return eval(compile(ast.parse(expr, mode='eval'), '<string>', 'eval'))
    except:
        return None

def safe_parse_json(text):
    try:
        return json.loads(text)
    except:
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            return None
    return None

# =========================================================
# HELPERS
# =========================================================

def normalize_answer(answer):
    return " ".join(str(answer).lower().strip().split())

def clean_output(text):
    if not text: return None
    text = re.sub(r"(?i)(answer|final answer)[:\- ]*", "", text.strip())
    return text.split("\n")[0].strip()

def extract_math_expression(text):
    match = re.findall(r'([0-9\+\-\*\/\(\)\. ]+)', text)
    return match[0] if match else None

# =========================================================
# FETCH
# =========================================================

def fetch_puzzle():
    log("[+] Pulling puzzle...")
    try:
        r = requests.get(f"{BASE_URL}?eth={AGENT_ETH_ADDRESS}", headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429:
            time.sleep(10)
            return None
        return r.json().get("puzzle")
    except:
        return None

def parse_puzzle(puzzle):
    return {
        "id": puzzle.get("id"),
        "prompt": puzzle.get("prompt", ""),
        "category": puzzle.get("category", "unknown")
    }

# =========================================================
# LOCAL SOLVERS
# =========================================================

def solve_math(prompt):
    expr = extract_math_expression(prompt)
    return str(safe_eval(expr)) if expr else None

def solve_hashing(prompt):
    lower = prompt.lower()
    if "sha-256 hash of the empty string" in lower:
        return hashlib.sha256(b"").hexdigest()[:6]
    return None

def solve_blockchain(prompt):
    lower = prompt.lower()
    if "bitcoin whitepaper" in lower: return "2008"
    if "creator of bitcoin" in lower: return "satoshi nakamoto"
    return None

# =========================================================
# CLASSIFIER (IMPROVED GENERALIZATION)
# =========================================================

def classify_question(prompt):
    p = prompt.lower()
    if re.search(r"\b\d{4}\b", p): return "year"
    if any(x in p for x in ["calculate", "solve", "+", "-", "*", "/"]): return "math"
    if any(x in p for x in ["hash", "sha"]): return "hashing"
    if any(x in p for x in ["bitcoin", "blockchain"]): return "blockchain"
    if len(p.split()) > 20: return "complex_reasoning"
    return "general"

# =========================================================
# VALIDATION
# =========================================================

def strict_validate(answer, category):
    if not answer or len(answer) > 200:
        return False
    a = str(answer).strip().lower()
    if category == "year": return bool(re.fullmatch(r"\d{4}", a))
    if category == "math": return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", a))
    if category == "hashing": return bool(re.fullmatch(r"[a-f0-9]{6,64}", a))
    return True

# =========================================================
# LLM CORE
# =========================================================

def ask_llm_structured(prompt, provider="openai"):
    system = f"You are a reasoning engine. Return ONLY JSON: {{\"answer\": \"...\", \"confidence\": 0.0}}"
    try:
        if provider == "openai" and client:
            r = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            return safe_parse_json(r.choices[0].message.content)
        else:
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": f"{system}\n{prompt}",
                "stream": False,
                "format": "json"
            }
            r = requests.post(OLLAMA_URL, json=payload, timeout=REQUEST_TIMEOUT)
            return safe_parse_json(r.json().get("response", ""))
    except:
        return None

# =========================================================
# ENHANCED UNIVERSAL ENGINE
# =========================================================

def universal_solver(prompt):
    res = ask_llm_structured(build_universal_prompt(prompt, "auto"), "openai")
    if res:
        return clean_output(res.get("answer"))
    return None

# =========================================================
# MAIN SOLVER
# =========================================================

def solve_puzzle(parsed):
    prompt = parsed["prompt"]

    load_memory()
    if prompt in MEMORY:
        return MEMORY[prompt]

    category = classify_question(prompt)
    log(f"[+] Category: {category}")

    ans = solve_math(prompt) or solve_hashing(prompt) or solve_blockchain(prompt)
    if ans:
        MEMORY[prompt] = ans
        save_memory()
        return ans

    for _ in range(MAX_RETRIES):
        res = universal_solver(prompt) or ultra_fallback_solver(prompt)
        if res:
            res = normalize_answer(res)
            if strict_validate(res, category):
                MEMORY[prompt] = res
                save_memory()
                return res

    manual = normalize_answer(input("\nManual intervention: "))
    MEMORY[prompt] = manual
    save_memory()
    return manual

# =========================================================
# RUN LOOP
# =========================================================

def run():
    log(f"NOCOIN MINER ULTIMATE STARTED - {AGENT_NAME}")

    while True:
        try:
            puzzle = fetch_puzzle()
            if not puzzle:
                time.sleep(POLL_INTERVAL)
                continue

            parsed = parse_puzzle(puzzle)
            answer = solve_puzzle(parsed)

            payload = {
                "eth_address": AGENT_ETH_ADDRESS,
                "agent_name": AGENT_NAME,
                "puzzle_id": parsed["id"],
                "answer": answer
            }

            r = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT)
            log(f"[+] Submitted: {r.status_code}")

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            log("[!] Stopped")
            break
        except Exception as e:
            log(f"[!] Error: {e}")
            time.sleep(5)

# =========================================================
# RATE LIMIT + SOUL COMPLIANCE LAYER (ADDED - SAFE WRAP)
# =========================================================

SUBMIT_TIMES = []

def enforce_rate_limit():
    global SUBMIT_TIMES
    now = time.time()
    SUBMIT_TIMES = [t for t in SUBMIT_TIMES if now - t < 10]
    if len(SUBMIT_TIMES) >= 8:
        time.sleep(10 - (now - SUBMIT_TIMES[0]))

_original_post = requests.post

def guarded_post(*args, **kwargs):
    enforce_rate_limit()
    SUBMIT_TIMES.append(time.time())
    return _original_post(*args, **kwargs)

requests.post = guarded_post

if __name__ == "__main__":
    run()

# =========================================================
# 🚀 ADVANCED DYNAMIC LAYER v2 (NON-DESTRUCTIVE UPGRADE)
# =========================================================

STATS = {
    "puzzles_fetched": 0,
    "solved": 0,
    "failed": 0,
    "last_answer_time": 0
}

BACKOFF = 5

def adaptive_sleep():
    global BACKOFF
    if STATS["failed"] > 5:
        BACKOFF = min(60, BACKOFF * 1.5)
    else:
        BACKOFF = max(5, BACKOFF * 0.9)
    time.sleep(BACKOFF)

def health_logger():
    while True:
        log(f"[HEALTH] solved={STATS['solved']} failed={STATS['failed']} backoff={BACKOFF}")
        time.sleep(30)

def safe_fetch():
    STATS["puzzles_fetched"] += 1
    return fetch_puzzle()

def safe_submit(payload):
    try:
        r = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            STATS["solved"] += 1
        else:
            STATS["failed"] += 1
        return r
    except:
        STATS["failed"] += 1
        return None

# background health thread
threading.Thread(target=health_logger, daemon=True).start()

# optional future hook (does not override main loop)
def dynamic_optimizer():
    while True:
        adaptive_sleep()

# =========================================================
# 🔧 PATCH LAYER (NON-DESTRUCTIVE FIXES + HARDENING)
# =========================================================

# Fix: missing fallback safety for LLM parsing
def safe_llm_answer(res):
    if not res:
        return None
    if isinstance(res, dict):
        return res.get("answer")
    return None

# Fix: strengthen fallback chain (non-invasive override hook)
_original_universal_solver = universal_solver

def universal_solver(prompt):
    try:
        res = ask_llm_structured(build_universal_prompt(prompt, "auto"), "openai")
        ans = safe_llm_answer(res)
        if ans:
            return clean_output(ans)
    except:
        pass
    return None

# =========================================================
# 🚀 CONNECT DYNAMIC STATS TO REAL FLOW
# =========================================================

_original_fetch = fetch_puzzle
def fetch_puzzle():
    STATS["puzzles_fetched"] += 1
    return _original_fetch()

_original_solve = solve_puzzle
def solve_puzzle(parsed):
    start = time.time()
    try:
        result = _original_solve(parsed)
        STATS["solved"] += 1
        STATS["last_answer_time"] = time.time() - start
        return result
    except:
        STATS["failed"] += 1
        raise

# =========================================================
# 🔁 REPLACE SUBMIT WITH SAFE VERSION
# =========================================================

_original_requests_post = requests.post

def requests.post(*args, **kwargs):
    try:
        r = _original_requests_post(*args, **kwargs)
        if r.status_code == 200:
            STATS["solved"] += 1
        else:
            STATS["failed"] += 1
        return r
    except:
        STATS["failed"] += 1
        return None

# =========================================================
# ⚡ AUTO BACKOFF CONTROL LOOP (ACTIVATION FIX)
# =========================================================

def auto_controller():
    while True:
        try:
            adaptive_sleep()
        except:
            time.sleep(5)

threading.Thread(target=auto_controller, daemon=True).start()

# =========================================================
# 🧠 FINAL INTELLIGENCE SAFETY GATE
# =========================================================

def final_answer_gate(answer, category):
    if not answer:
        return None
    answer = normalize_answer(answer)
    if strict_validate(answer, category):
        return answer
    return None

