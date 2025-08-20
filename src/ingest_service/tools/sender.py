import argparse, json, time, sys, random
from pathlib import Path
from typing import Optional, List, Dict
import requests

RETRY_CODES = {408, 425, 429, 500, 502, 503, 504}

def backoff_sleep(attempt: int, base: float = 0.5, cap: float = 8.0):
    sleep = min(cap, base * (2 ** attempt)) * (0.5 + random.random() / 2.0)
    time.sleep(sleep)

def post_json(session: requests.Session, url: str, payload: dict, api_key: Optional[str], timeout: float, retries: int) -> bool:
    headers = {"content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    data = json.dumps(payload)
    for attempt in range(retries + 1):
        try:
            resp = session.post(url, data=data, headers=headers, timeout=timeout)
            if resp.status_code < 300:
                return True
            if resp.status_code in RETRY_CODES:
                backoff_sleep(attempt)
                continue
            sys.stderr.write(f"HTTP {resp.status_code}: {resp.text}\n")
            return False
        except requests.RequestException as e:
            sys.stderr.write(f"request error: {e}\n")
            backoff_sleep(attempt)
    return False

def post_batch(session: requests.Session, url: str, batch: List[dict], api_key: Optional[str], timeout: float, retries: int) -> bool:
    headers = {"content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    data = json.dumps(batch)
    for attempt in range(retries + 1):
        try:
            resp = session.post(url, data=data, headers=headers, timeout=timeout)
            if resp.status_code < 300:
                return True
            if resp.status_code in RETRY_CODES:
                backoff_sleep(attempt)
                continue
            sys.stderr.write(f"HTTP {resp.status_code}: {resp.text}\n")
            return False
        except requests.RequestException as e:
            sys.stderr.write(f"request error: {e}\n")
            backoff_sleep(attempt)
    return False

def main():
    p = argparse.ArgumentParser(description="Enviar NDJSON para o webhook (/ingest ou /ingest/batch)")
    p.add_argument("--file", required=True, help="Arquivo NDJSON (1 JSON por linha)")
    p.add_argument("--url", required=True, help="URL do webhook: /ingest ou /ingest/batch")
    p.add_argument("--api-key", default=None, help="x-api-key header (se exigido)")
    p.add_argument("--batch-size", type=int, default=1, help=">1 usa /ingest/batch")
    p.add_argument("--qps", type=float, default=50.0, help="Máximo de requisições por segundo")
    p.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout (s)")
    p.add_argument("--retries", type=int, default=4, help="Retries para 429/5xx")
    args = p.parse_args()

    session = requests.Session()
    path = Path(args.file)
    if not path.exists():
        sys.stderr.write(f"Arquivo não encontrado: {path}\n")
        sys.exit(2)

    sent_ok = 0
    sent_fail = 0
    t0 = time.time()
    delay = 1.0 / max(args.qps, 0.1)

    if args.batch_size > 1:
        batch = []
        with path.open("r", encoding="utf-8") as f:
            for ln, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                if " # " in raw:
                    raw = raw.split(" # ", 1)[0].strip()
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    sys.stderr.write(f"[linha {ln}] erro parse json: {e}\n")
                    sent_fail += 1
                    continue
                batch.append(obj)
                if len(batch) >= args.batch_size:
                    ok = post_batch(session, args.url, batch, args.api_key, args.timeout, args.retries)
                    sent_ok += len(batch) if ok else 0
                    sent_fail += 0 if ok else len(batch)
                    batch.clear()
                    time.sleep(delay)
        if batch:
            ok = post_batch(session, args.url, batch, args.api_key, args.timeout, args.retries)
            sent_ok += len(batch) if ok else 0
            sent_fail += 0 if ok else len(batch)
    else:
        with path.open("r", encoding="utf-8") as f:
            for ln, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                if " # " in raw:
                    raw = raw.split(" # ", 1)[0].strip()
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    sys.stderr.write(f"[linha {ln}] erro parse json: {e}\n")
                    sent_fail += 1
                    continue
                ok = post_json(session, args.url, obj, args.api_key, args.timeout, args.retries)
                sent_ok += 1 if ok else 0
                sent_fail += 0 if ok else 1
                time.sleep(delay)

    dt = time.time() - t0
    rate = (sent_ok + sent_fail) / dt if dt > 0 else 0.0
    print(f"Concluído. ok={sent_ok} fail={sent_fail} tempo={dt:.2f}s taxa={rate:.1f} msg/s")
