#!/usr/bin/env python3
"""
SG/MY Credit Card Cashback Calculator — Weekly Auto-Audit & FX Sync
1. Fetches latest SGD -> MYR exchange rate.
2. Checks all card/affiliate URLs in index.html (and cards_links.json if present).
3. Updates index.html (FX rate & benchmark date) and sitemap.xml (<lastmod>).
4. Sends a summary report to Telegram if TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID are set.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

MYT = timezone(timedelta(hours=8))
TODAY = datetime.now(MYT).strftime("%Y-%m-%d")
INDEX_FILE = "index.html"
SITEMAP_FILE = "sitemap.xml"
LINKS_FILE = "cards_links.json"
FX_API_URL = "https://open.er-api.com/v6/latest/SGD"
WAF_TIMEOUT_DOMAINS = ("maybank2u.com.my",)


def fetch_sgd_myr_rate():
    try:
        req = urllib.request.Request(FX_API_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") != "success":
                return None
            rate = data.get("rates", {}).get("MYR")
            if isinstance(rate, (int, float))  and not isinstance(rate, bool) and 2.0 < rate < 5.0:
                return round(float(rate), 2)
    except Exception as exc:
        print(f"[WARN] FX fetch failed: {exc}")
    return None


def check_url(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, "OK"
        except urllib.error.HTTPError as err:
            if err.code in (403, 429):
                return err.code, "BOT_SHIELD_OK"
            if method == "HEAD" and err.code in (400, 404, 405, 501):
                continue
            return err.code, f"HTTP_{err.code}"
        except Exception as exc:
            if method == "HEAD":
                continue
            host = urllib.parse.urlparse(url).netloc.lower()
            is_timeout = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
            if is_timeout and any(host == d or host.endswith("." + d) for d in WAF_TIMEOUT_DOMAINS):
                return 200, "WAF_TIMEOUT_OK"
            return 0, str(exc)[:60]
    return 0, "UNKNOWN"


def extract_urls(html_text):
    urls = re.findall(r"defaultUrl:\s*'([^']+)'", html_text)
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE, "r", encoding="utf-8") as f:
                custom = json.load(f)
                for v in custom.values():
                    if isinstance(v, str) and v.startswith("http"):
                        urls.append(v)
        except Exception as exc:
            print(f"[WARN] Failed reading {LINKS_FILE}: {exc}")
    # Preserve order, deduplicate
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def update_files(new_rate):
    if new_rate is None:
            print("[WARN] FX rate unavailable; skipping index.html and sitemap.xml updates.")
            return
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            html = f.read()

        updated_html = re.sub(
            r"数据基准：\d{4}-\d{2}-\d{2}",
            f"数据基准：{TODAY}",
            html,
        )
        if new_rate is not None:
            updated_html = re.sub(
                r'(id="fx-rate"[^>]*value=")[\d.]+(")',
                rf"\g<1>{new_rate:.2f}\g<2>",
                updated_html,
            )
            updated_html = re.sub(
                r"(fxRate:\s*)[\d.]+",
                rf"\g<1>{new_rate:.2f}",
                updated_html,
            )
            updated_html = re.sub(
                r"(1 SGD = )[\d.]+( MYR)",
                rf"\g<1>{new_rate:.2f}\g<2>",
                updated_html,
            )

        if updated_html != html:
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                f.write(updated_html)
            print(f"[INFO] Updated {INDEX_FILE} (date={TODAY}, fx={new_rate})")

    if os.path.exists(SITEMAP_FILE):
        with open(SITEMAP_FILE, "r", encoding="utf-8") as f:
            sm = f.read()
        updated_sm = re.sub(
            r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>",
            f"<lastmod>{TODAY}</lastmod>",
            sm,
        )
        if updated_sm != sm:
            with open(SITEMAP_FILE, "w", encoding="utf-8") as f:
                f.write(updated_sm)
            print(f"[INFO] Updated {SITEMAP_FILE} (lastmod={TODAY})")


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[INFO] Telegram secrets not set; skipping push.")
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message[:4000],
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(api_url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"[INFO] Telegram sent: HTTP {resp.status}")
    except Exception as exc:
        print(f"[WARN] Telegram send failed: {type(exc).__name__}: {str(exc).replace(token, '[REDACTED]')}")


def main():
    rate = fetch_sgd_myr_rate()
    update_files(rate)

    urls = []
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            urls = extract_urls(f.read())

    broken = []
    ok_count = 0
    for u in urls:
        code, status = check_url(u)
        if status in ("OK", "BOT_SHIELD_OK", "WAF_TIMEOUT_OK"):
            ok_count += 1
        else:
            broken.append(f"- [{code}] {u} ({status})")

    rate_str = f"1 SGD = {rate:.2f} MYR" if rate else "Fetch failed (kept existing)"
    summary_lines = [
        f"SG/MY Cashback Weekly Audit ({TODAY})",
        f"FX Rate: {rate_str}",
        f"Links Checked: {len(urls)} | Healthy: {ok_count} | Issues: {len(broken)}",
    ]
    if broken:
        summary_lines.append("Broken / Flagged URLs:")
        summary_lines.extend(broken[:10])
    else:
        summary_lines.append("All card & affiliate links look healthy.")

    report = "\n".join(summary_lines)
    print(report)
    send_telegram(report)


if __name__ == "__main__":
    main()
