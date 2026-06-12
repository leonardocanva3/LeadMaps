from __future__ import annotations

import random
import time
from argparse import ArgumentParser, Namespace
from contextlib import suppress
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from time import perf_counter
from unicodedata import normalize
from urllib.parse import quote_plus

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from supabase import create_client


ROOT = Path(__file__).resolve().parent
MANUAL_MAX_RESULTS = 20
NO_NEW_RESULTS_LIMIT = 8
COUNTRY_CODE = "55"
LOG_FILE: Path | None = None


def log(message: str) -> None:
    print(message, flush=True)
    if LOG_FILE is None:
        return

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


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
    whatsapp = str(lead.get("whatsapp") or lead.get("WhatsApp", "") or "").strip()
    maps_link = str(lead.get("link_google_maps") or lead.get("Link do Google Maps", "") or "").strip()
    name = normalize_text(lead.get("nome") or lead.get("Nome", ""))
    city = normalize_text(lead.get("cidade") or lead.get("Cidade", ""))

    keys = set()
    if phone:
        keys.add(f"phone:{phone}")
    if whatsapp:
        keys.add(f"whatsapp:{whatsapp}")
    if maps_link:
        keys.add(f"maps:{maps_link}")
    if not keys and name and city:
        keys.add(f"namecity:{name}|{city}")
    return keys


def unique_key_for_lead(lead: dict) -> str:
    keys = sorted(identity_keys(lead))
    raw = keys[0] if keys else normalize_text(f"{lead.get('nome', '')} {lead.get('cidade', '')}")
    return sha1(raw.encode("utf-8")).hexdigest()


def load_env() -> None:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local", override=True)


def supabase_client():
    import os

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
    search_text = f"{segment} em {city}"
    log("[2] Abrindo Google Maps")
    log(f"Busca executada: {search_text}")
    search = quote_plus(search_text)
    page.goto(f"https://www.google.com/maps/search/{search}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)


def reached_end_of_results(page) -> bool:
    with suppress(Exception):
        body = normalize_text(page.locator("body").inner_text(timeout=1500))
        return (
            "voce chegou ao final da lista" in body
            or "youve reached the end of the list" in body
            or "you have reached the end of the list" in body
        )
    return False


def collect_result_links(page, limit: int | None = None) -> list[str]:
    log("[3] Coletando resultados")
    links = []
    seen = set()
    feed = page.locator("div[role='feed']")
    no_new_scrolls = 0

    while True:
        before_count = len(seen)
        for link in page.locator("a.hfpxzc").all():
            href = link.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                links.append(href)
            if limit is not None and len(links) >= limit:
                return links

        if reached_end_of_results(page):
            log("Status: final da lista detectado no Google Maps")
            return links

        if len(seen) == before_count:
            no_new_scrolls += 1
        else:
            no_new_scrolls = 0

        if no_new_scrolls >= NO_NEW_RESULTS_LIMIT:
            log(f"Status: cidade finalizada apos {NO_NEW_RESULTS_LIMIT} rolagens sem novos resultados")
            return links

        if feed.count() > 0:
            feed.first.evaluate("element => element.scrollBy(0, element.scrollHeight)")
        else:
            page.mouse.wheel(0, 2500)
        page.wait_for_timeout(2000)


def load_existing_keys(client) -> set[str]:
    rows = []
    page_size = 1000
    start = 0
    while True:
        batch = (
            client.table("leads")
            .select("telefone_limpo,whatsapp,link_google_maps,nome,cidade")
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

    keys = set()
    for row in rows:
        keys.update(identity_keys(row))
    return keys


def insert_leads(client, leads: list[dict]) -> None:
    if not leads:
        return
    log("[5] Enviando para Supabase")
    client.table("leads").upsert(leads, on_conflict="unique_key").execute()


def filter_insertable_leads(collected: list[dict], existing_keys: set[str]) -> tuple[list[dict], int, int]:
    new_leads = []
    without_phone = 0
    duplicates = 0

    for lead in collected:
        if not lead.get("nome") or not lead.get("telefone_limpo"):
            without_phone += 1
            log(f"Lead descartado por falta de telefone: {lead.get('nome') or 'Sem nome'}")
            continue

        keys = identity_keys(lead)
        if keys.intersection(existing_keys):
            duplicates += 1
            continue

        existing_keys.update(keys)
        new_leads.append(lead)

    return new_leads, without_phone, duplicates


def collect_leads(segment: str, city: str, limit: int | None = None) -> list[dict]:
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


def prompt_manual_limit() -> int:
    raw_limit = input("Quantidade maxima: ").strip()
    requested_limit = int(raw_limit) if raw_limit.isdigit() else MANUAL_MAX_RESULTS
    return max(1, min(requested_limit, MANUAL_MAX_RESULTS))


def read_cities() -> list[str]:
    cities_path = ROOT / "cidades.txt"
    if not cities_path.exists():
        raise RuntimeError("Arquivo cidades.txt nao encontrado na raiz do projeto.")

    return [
        line.strip()
        for line in cities_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def collect_and_send_city(client, existing_keys: set[str], segment: str, city: str, limit: int | None = None) -> dict:
    started_at = perf_counter()
    collected = collect_leads(segment, city, limit)
    new_leads, without_phone, duplicates = filter_insertable_leads(collected, existing_keys)
    insert_leads(client, new_leads)
    elapsed = perf_counter() - started_at
    return {
        "cidade": city,
        "encontrados": len(collected),
        "com_telefone": len(new_leads) + duplicates,
        "sem_telefone": without_phone,
        "novos": len(new_leads),
        "duplicados": duplicates,
        "tempo": elapsed,
        "erro": "",
    }


def format_elapsed(seconds: float) -> str:
    total = int(round(seconds))
    minutes, remaining = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remaining}s"
    if minutes:
        return f"{minutes}m {remaining}s"
    return f"{remaining}s"


def setup_auto_log() -> None:
    global LOG_FILE
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    LOG_FILE = ROOT / "logs" / f"coleta_{timestamp}.txt"


def parse_comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def update_summary(summary: dict, result: dict) -> None:
    summary["buscas_executadas"] += 1
    summary["leads_encontrados"] += result["encontrados"]
    summary["com_telefone"] += result["com_telefone"]
    summary["sem_telefone"] += result["sem_telefone"]
    summary["novos_enviados"] += result["novos"]
    summary["duplicados"] += result["duplicados"]


def log_search_result(result: dict) -> None:
    log(f"Encontrados: {result['encontrados']}")
    log(f"Com telefone: {result['com_telefone']}")
    log(f"Sem telefone descartados: {result['sem_telefone']}")
    log(f"Novos enviados: {result['novos']}")
    log(f"Duplicados: {result['duplicados']}")
    log("Status: cidade finalizada")
    log(f"Tempo da busca: {format_elapsed(result['tempo'])}")


def estimated_eta(started_at: float, completed: int, total: int) -> str:
    if completed <= 0:
        return "calculando"
    elapsed = perf_counter() - started_at
    average = elapsed / completed
    remaining = max(0, total - completed)
    return format_elapsed(average * remaining)


def run_manual() -> None:
    segment = input("Digite o segmento: ").strip()
    city = input("Digite a cidade: ").strip()
    limit = prompt_manual_limit()

    if not segment or not city:
        raise RuntimeError("Segmento e cidade sao obrigatorios.")

    log("[1] Iniciando busca")
    client = supabase_client()
    existing_keys = load_existing_keys(client)
    result = collect_and_send_city(client, existing_keys, segment, city, limit)
    log(
        "[6] Finalizado. "
        f"Encontrados: {result['encontrados']} | "
        f"com telefone: {result['com_telefone']} | "
        f"sem telefone descartados: {result['sem_telefone']} | "
        f"novos enviados: {result['novos']} | "
        f"duplicados: {result['duplicados']}"
    )


def run_auto() -> None:
    setup_auto_log()
    started_at = perf_counter()
    segment = input("Digite o nicho: ").strip()

    if not segment:
        raise RuntimeError("Nicho e obrigatorio.")

    cities = read_cities()
    if not cities:
        raise RuntimeError("Nenhuma cidade encontrada em cidades.txt.")

    log("[1] Iniciando busca")
    log(f"Nicho: {segment}")
    log(f"Cidades carregadas: {len(cities)}")

    client = supabase_client()
    existing_keys = load_existing_keys(client)
    summary = {
        "buscas_executadas": 0,
        "leads_encontrados": 0,
        "com_telefone": 0,
        "sem_telefone": 0,
        "novos_enviados": 0,
        "duplicados": 0,
        "erros": 0,
    }

    for index, city in enumerate(cities, start=1):
        log("")
        log(f"[{index}/{len(cities)}] Buscando {segment} em {city}")
        try:
            result = collect_and_send_city(client, existing_keys, segment, city, None)
            update_summary(summary, result)
            log_search_result(result)
        except Exception as exc:
            summary["erros"] += 1
            log(f"Erro na cidade {city}: {exc}")

        if index < len(cities):
            pause = random.randint(10, 20)
            log(f"Pausa de {pause}s antes da proxima cidade.")
            time.sleep(pause)

    total_elapsed = perf_counter() - started_at
    log("")
    log("Prospecção finalizada.")
    log(f"Nicho: {segment}")
    log(f"Cidades processadas: {summary['buscas_executadas']}")
    log(f"Total encontrados: {summary['leads_encontrados']}")
    log(f"Total com telefone: {summary['com_telefone']}")
    log(f"Total sem telefone descartados: {summary['sem_telefone']}")
    log(f"Total novos enviados: {summary['novos_enviados']}")
    log(f"Total duplicados: {summary['duplicados']}")
    log(f"Total erros: {summary['erros']}")
    log(f"Tempo total: {format_elapsed(total_elapsed)}")
    log(f"Arquivo de log: {LOG_FILE}")


def run_mega() -> None:
    setup_auto_log()
    started_at = perf_counter()
    segments = parse_comma_list(input("Digite os nichos (separados por virgula): ").strip())
    cities = parse_comma_list(input("Digite as cidades (separadas por virgula): ").strip())

    if not segments:
        raise RuntimeError("Informe pelo menos um nicho.")
    if not cities:
        raise RuntimeError("Informe pelo menos uma cidade.")

    searches = [(segment, city) for segment in segments for city in cities]
    total_searches = len(searches)

    log("[1] Iniciando busca")
    log("Modo: mega")
    log(f"Nichos: {', '.join(segments)}")
    log(f"Cidades: {', '.join(cities)}")
    log(f"Buscas planejadas: {total_searches}")

    client = supabase_client()
    existing_keys = load_existing_keys(client)
    summary = {
        "buscas_executadas": 0,
        "leads_encontrados": 0,
        "com_telefone": 0,
        "sem_telefone": 0,
        "novos_enviados": 0,
        "duplicados": 0,
        "erros": 0,
    }

    for index, (segment, city) in enumerate(searches, start=1):
        log("")
        log(f"[{index}/{total_searches}]")
        log("Buscando:")
        log(f"{segment} em {city}")
        log(f"ETA estimado: {estimated_eta(started_at, index - 1, total_searches)}")

        try:
            result = collect_and_send_city(client, existing_keys, segment, city, None)
            update_summary(summary, result)
            log_search_result(result)
        except Exception as exc:
            summary["erros"] += 1
            log(f"Erro na busca {segment} em {city}: {exc}")

        completed = summary["buscas_executadas"] + summary["erros"]
        log(f"Progresso: {completed}/{total_searches}")
        log(f"ETA estimado: {estimated_eta(started_at, completed, total_searches)}")

        if index < total_searches:
            pause = random.randint(10, 20)
            log(f"Pausa de {pause}s antes da proxima busca.")
            time.sleep(pause)

    total_elapsed = perf_counter() - started_at
    executed_searches = summary["buscas_executadas"] + summary["erros"]
    log("")
    log("Prospecção massiva finalizada.")
    log(f"Buscas executadas: {executed_searches}")
    log(f"Leads encontrados: {summary['leads_encontrados']}")
    log(f"Com telefone: {summary['com_telefone']}")
    log(f"Duplicados: {summary['duplicados']}")
    log(f"Novos enviados: {summary['novos_enviados']}")
    log(f"Erros: {summary['erros']}")
    log(f"Tempo total: {format_elapsed(total_elapsed)}")
    log(f"Arquivo de log: {LOG_FILE}")


def parse_args() -> Namespace:
    parser = ArgumentParser(description="Coletor local de leads do LeadMaps.")
    parser.add_argument("--auto", action="store_true", help="Executa o nicho em todas as cidades do cidades.txt.")
    parser.add_argument("--mega", action="store_true", help="Executa todas as combinacoes de nichos e cidades.")
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()

    if args.auto:
        run_auto()
        return

    if args.mega:
        run_mega()
        return

    run_manual()


if __name__ == "__main__":
    main()
