from __future__ import annotations

import os
from contextlib import suppress
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from unicodedata import normalize
from urllib.parse import quote_plus

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from supabase import create_client


ROOT = Path(__file__).resolve().parent
MAX_RESULTS = 20
COUNTRY_CODE = "55"


def log(message: str) -> None:
    print(message, flush=True)


def normalize_text(value: str) -> str:
    without_accents = normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(without_accents.lower().replace(",", " ").split())


def clean_digits(value: str) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def to_int(value: str) -> int:
    digits = clean_digits(value)
    return int(digits) if digits else 0


def build_whatsapp_link(phone: str) -> str:
    digits = clean_digits(phone)
    if not digits:
        return ""
    if digits.startswith(COUNTRY_CODE):
        return f"https://wa.me/{digits}"
    return f"https://wa.me/{COUNTRY_CODE}{digits}"


def calculate_opportunity(site: str, reviews: str) -> str:
    reviews_count = to_int(reviews)
    if not site and reviews_count <= 20:
        return "ALTA"
    if not site and reviews_count <= 50:
        return "MEDIA"
    return "BAIXA"


def identity_keys(lead: dict) -> set[str]:
    phone = clean_digits(lead.get("telefone") or lead.get("Telefone", ""))
    name = normalize_text(lead.get("nome") or lead.get("Nome", ""))
    address = normalize_text(lead.get("endereco") or lead.get("Endereco", ""))

    keys = set()
    if phone:
        keys.add(f"phone:{phone}")
    if name and address:
        keys.add(f"nameaddress:{name}|{address}")
    return keys


def unique_key_for_lead(lead: dict) -> str:
    keys = sorted(identity_keys(lead))
    raw = keys[0] if keys else normalize_text(f"{lead.get('nome', '')} {lead.get('cidade', '')}")
    return sha1(raw.encode("utf-8")).hexdigest()


def load_env() -> None:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local", override=True)


def supabase_client():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY sao obrigatorios no .env.local.")
    return create_client(url, key)


def safe_text(page, selector: str) -> str:
    try:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return ""
        return locator.inner_text(timeout=1500).strip()
    except PlaywrightTimeoutError:
        return ""
    except Exception:
        return ""


def safe_attribute(page, selector: str, attribute: str) -> str:
    try:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return ""
        return locator.get_attribute(attribute, timeout=1500) or ""
    except PlaywrightTimeoutError:
        return ""
    except Exception:
        return ""


def extract_rating_and_reviews(page) -> tuple[str, str]:
    rating = safe_text(page, "div.F7nice span[aria-hidden='true']")
    reviews_text = safe_text(page, "div.F7nice span[aria-label*='avalia']")
    return rating, clean_digits(reviews_text)


def extract_lead(page, city: str, segment: str) -> dict:
    name = safe_text(page, "h1.DUwDvf")
    phone = safe_text(page, "button[data-item-id^='phone'] div.Io6YTe")
    address = safe_text(page, "button[data-item-id='address'] div.Io6YTe")
    site = safe_attribute(page, "a[data-item-id='authority']", "href")
    rating, reviews = extract_rating_and_reviews(page)
    now = datetime.utcnow().isoformat()

    lead = {
        "nome": name,
        "telefone": phone,
        "telefone_limpo": clean_digits(phone),
        "whatsapp": build_whatsapp_link(phone),
        "endereco": address,
        "site": site,
        "nota": rating,
        "quantidade_avaliacoes": to_int(reviews),
        "cidade": city,
        "tem_site": "SIM" if site else "NAO",
        "oportunidade": calculate_opportunity(site, reviews),
        "link_google_maps": page.url,
        "status_abordagem": "NOVO",
        "data_primeira_abordagem": None,
        "data_ultimo_feedback": None,
        "ultima_acao": "",
        "origem": "local_collector",
        "origem_raspagem": f"{segment} | {city}",
        "updated_at": now,
    }
    lead["unique_key"] = unique_key_for_lead(lead)
    return lead


def open_google_maps(page, segment: str, city: str) -> None:
    log("[2] Abrindo Google Maps")
    search = quote_plus(f"{segment} em {city}")
    page.goto(f"https://www.google.com/maps/search/{search}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)


def collect_result_links(page, limit: int) -> list[str]:
    log("[3] Coletando resultados")
    links = []
    feed = page.locator("div[role='feed']")
    max_scrolls = max(4, limit // 5 + 2)

    for _ in range(max_scrolls):
        for link in page.locator("a.hfpxzc").all():
            href = link.get_attribute("href")
            if href and href not in links:
                links.append(href)
            if len(links) >= limit:
                return links

        if feed.count() > 0:
            feed.first.evaluate("element => element.scrollBy(0, element.scrollHeight)")
        page.wait_for_timeout(2000)

    return links


def load_existing_keys(client) -> set[str]:
    rows = (
        client.table("leads")
        .select("telefone_limpo,nome,endereco")
        .execute()
        .data
        or []
    )

    keys = set()
    for row in rows:
        keys.update(identity_keys(row))
    return keys


def insert_leads(client, leads: list[dict]) -> None:
    if not leads:
        return
    log("[5] Enviando para Supabase")
    client.table("leads").upsert(leads, on_conflict="unique_key").execute()


def collect_leads(segment: str, city: str, limit: int) -> list[dict]:
    browser = None
    context = None
    page = None
    leads = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            )
            context = browser.new_context(locale="pt-BR")
            page = context.new_page()
            page.set_default_timeout(15000)

            open_google_maps(page, segment, city)
            links = collect_result_links(page, limit)

            for index, link in enumerate(links, start=1):
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                lead = extract_lead(page, city, segment)
                if not lead["nome"] and not lead["telefone_limpo"] and not lead["endereco"]:
                    continue
                log(f"[4] Lead encontrado ({index}/{len(links)}): {lead['nome'] or 'Sem nome'}")
                leads.append(lead)
        finally:
            if page is not None:
                with suppress(Exception):
                    page.close()
            if context is not None:
                with suppress(Exception):
                    context.close()
            if browser is not None:
                with suppress(Exception):
                    browser.close()

    return leads


def prompt_limit() -> int:
    raw_limit = input("Quantidade máxima: ").strip()
    requested_limit = int(raw_limit) if raw_limit.isdigit() else MAX_RESULTS
    return max(1, min(requested_limit, MAX_RESULTS))


def main() -> None:
    load_env()

    segment = input("Digite o segmento: ").strip()
    city = input("Digite a cidade: ").strip()
    limit = prompt_limit()

    if not segment or not city:
        raise RuntimeError("Segmento e cidade sao obrigatorios.")

    log("[1] Iniciando busca")
    client = supabase_client()
    existing_keys = load_existing_keys(client)
    collected = collect_leads(segment, city, limit)

    new_leads = []
    for lead in collected:
        keys = identity_keys(lead)
        if not keys or keys.intersection(existing_keys):
            continue
        existing_keys.update(keys)
        new_leads.append(lead)

    insert_leads(client, new_leads)
    log(f"[6] Finalizado. Coletados: {len(collected)} | novos enviados: {len(new_leads)}")


if __name__ == "__main__":
    main()
