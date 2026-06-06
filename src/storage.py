from __future__ import annotations

import json
import os
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from unicodedata import normalize

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

try:
    from supabase import create_client
except ImportError:  # Mantem o modo local funcional sem supabase instalado.
    create_client = None


load_dotenv()

EXPORT_DIR = Path("exports")
MASTER_LEADS_PATH = EXPORT_DIR / "leads_master.json"
FEEDBACKS_PATH = EXPORT_DIR / "feedbacks.json"
SCRAPES_HISTORY_PATH = EXPORT_DIR / "raspagens.json"
RECENT_ACTIONS_PATH = EXPORT_DIR / "acoes_recentes.json"
ACTIVE_EXPORT_PATH = EXPORT_DIR / "leads_ativos.xlsx"
HISTORY_EXPORT_PATH = EXPORT_DIR / "leads_historico_completo.xlsx"

EXPORT_COLUMNS = [
    "Nome",
    "Telefone",
    "WhatsApp",
    "Endereco",
    "Site",
    "Nota",
    "Quantidade de avaliacoes",
    "Cidade",
    "Tem Site?",
    "Oportunidade",
    "Link do Google Maps",
]
FEEDBACK_COLUMNS = [
    "Status abordagem",
    "WhatsApp valido?",
    "Mensagem enviada?",
    "Observacao",
    "Data/hora do feedback",
    "Data ultimo feedback",
    "Data primeira abordagem",
    "Ultima acao",
    "Origem raspagem",
]
FULL_EXPORT_COLUMNS = EXPORT_COLUMNS + FEEDBACK_COLUMNS
STATUS_PRIORITY = {"NOVO": 1, "PENDENTE": 2, "SUCESSO": 3, "BURN": 3}
PAGE_SIZE = 100


def get_storage() -> str:
    """Retorna o modo de armazenamento ativo."""
    mode = os.getenv("STORAGE_MODE", "local").strip().lower()
    return "supabase" if mode == "supabase" else "local"


def normalize_text(value: str) -> str:
    without_accents = normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(without_accents.lower().replace(",", " ").split())


def clean_digits(value: str) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def to_int(value) -> int:
    digits = clean_digits(value)
    return int(digits) if digits else 0


def lead_identity_keys(lead: dict) -> list[str]:
    keys = []
    phone = clean_digits(lead.get("Telefone", ""))
    maps_link = str(lead.get("Link do Google Maps", "") or "").strip()
    name = lead.get("Nome", "")
    city = lead.get("Cidade", "")
    name_city = normalize_text(f"{name} {city}") if name or city else ""

    if phone:
        keys.append(f"phone:{phone}")
    if maps_link:
        keys.append(f"maps:{maps_link}")
    if name_city:
        keys.append(f"namecity:{name_city}")

    return keys


def unique_key_for_lead(lead: dict) -> str:
    keys = lead_identity_keys(lead)
    raw = keys[0] if keys else normalize_text(f"{lead.get('Nome', '')} {lead.get('Cidade', '')}")
    return sha1(raw.encode("utf-8")).hexdigest()


def default_feedback(status: str = "NOVO") -> dict:
    return {
        "Status abordagem": status,
        "WhatsApp valido?": "",
        "Mensagem enviada?": "",
        "Observacao": "",
        "Data/hora do feedback": "",
        "Data ultimo feedback": "",
        "Data primeira abordagem": "",
        "Ultima acao": "",
    }


def normalize_master_lead(lead: dict) -> dict:
    normalized = dict(lead)
    lead_id = normalized.get("Lead ID") or unique_key_for_lead(normalized)
    normalized["Lead ID"] = lead_id
    normalized["status_abordagem"] = normalized.get("status_abordagem", "NOVO")
    normalized["data_primeira_abordagem"] = normalized.get("data_primeira_abordagem", "")
    normalized["data_ultimo_feedback"] = normalized.get(
        "data_ultimo_feedback",
        normalized.get("Data ultimo feedback", normalized.get("Data/hora do feedback", "")),
    )
    normalized["ultima_acao"] = normalized.get("ultima_acao", "")
    normalized["origem_raspagem"] = normalized.get("origem_raspagem", "")
    normalized["Adicionado em"] = normalized.get(
        "Adicionado em",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return normalized


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def supabase_client():
    if create_client is None:
        raise RuntimeError("Instale a dependencia supabase ou use STORAGE_MODE=local.")

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY sao obrigatorios no modo supabase.")

    return create_client(url, key)


def _supabase_count(query) -> int:
    try:
        result = query.select("*", count="exact", head=True).execute()
    except TypeError:
        result = query.select("*", count="exact").limit(0).execute()

    return int(result.count or 0)


def count_leads(
    status: str | None = None,
    date_column: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    if get_storage() == "local":
        leads = [normalize_master_lead(lead) for lead in load_json(MASTER_LEADS_PATH, [])]
        if status and status != "TODOS":
            leads = [lead for lead in leads if lead.get("status_abordagem", "NOVO") == status]
        if date_column and date_from:
            leads = [lead for lead in leads if str(lead.get(date_column, ""))[:10] >= date_from[:10]]
        if date_column and date_to:
            leads = [lead for lead in leads if str(lead.get(date_column, ""))[:10] < date_to[:10]]
        return len(leads)

    query = supabase_client().table("leads")
    if status and status != "TODOS":
        query = query.eq("status_abordagem", status)
    if date_column and date_from:
        query = query.gte(date_column, date_from)
    if date_column and date_to:
        query = query.lt(date_column, date_to)
    return _supabase_count(query)


def count_leads_with_phone() -> int:
    if get_storage() == "local":
        return sum(1 for lead in load_json(MASTER_LEADS_PATH, []) if clean_digits(lead.get("Telefone", "")))

    client = supabase_client()
    total = _supabase_count(client.table("leads"))
    empty_count = _supabase_count(client.table("leads").eq("telefone_limpo", ""))
    null_count = _supabase_count(client.table("leads").is_("telefone_limpo", "null"))
    return max(0, total - empty_count - null_count)


def count_leads_without_site() -> int:
    if get_storage() == "local":
        return sum(1 for lead in load_json(MASTER_LEADS_PATH, []) if not lead.get("Site", ""))

    client = supabase_client()
    empty_count = _supabase_count(client.table("leads").eq("site", ""))
    null_count = _supabase_count(client.table("leads").is_("site", "null"))
    return empty_count + null_count


def count_feedbacks(
    mensagem_enviada: str | None = None,
    whatsapp_valido: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    if get_storage() == "local":
        feedbacks = load_json(FEEDBACKS_PATH, {}).values()
        total = 0
        for feedback in feedbacks:
            if mensagem_enviada and feedback.get("Mensagem enviada?") != mensagem_enviada:
                continue
            if whatsapp_valido and feedback.get("WhatsApp valido?") != whatsapp_valido:
                continue
            created_at = feedback.get("Data/hora do feedback", "")
            if date_from and created_at[:10] < date_from[:10]:
                continue
            if date_to and created_at[:10] >= date_to[:10]:
                continue
            total += 1
        return total

    query = supabase_client().table("feedbacks")
    if mensagem_enviada:
        query = query.eq("mensagem_enviada", mensagem_enviada)
    if whatsapp_valido:
        query = query.eq("whatsapp_valido", whatsapp_valido)
    if date_from:
        query = query.gte("created_at", date_from)
    if date_to:
        query = query.lt("created_at", date_to)
    return _supabase_count(query)


def load_leads_page(status: str = "NOVO", page: int = 1, page_size: int = PAGE_SIZE) -> list[dict]:
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or PAGE_SIZE))

    if get_storage() == "local":
        leads = [normalize_master_lead(lead) for lead in load_json(MASTER_LEADS_PATH, [])]
        if status != "TODOS":
            leads = [lead for lead in leads if lead.get("status_abordagem", "NOVO") == status]
        start = (page - 1) * page_size
        return leads[start:start + page_size]

    start = (page - 1) * page_size
    end = start + page_size - 1
    query = supabase_client().table("leads").select("*").order("created_at", desc=True)
    if status != "TODOS":
        query = query.eq("status_abordagem", status)
    rows = query.range(start, end).execute().data or []
    return [db_to_lead(row) for row in rows]


def get_lead_by_unique_key(lead_unique_key: str) -> dict | None:
    if get_storage() == "local":
        for lead in load_json(MASTER_LEADS_PATH, []):
            normalized = normalize_master_lead(lead)
            if normalized.get("Lead ID") == lead_unique_key:
                return normalized
        return None

    rows = (
        supabase_client()
        .table("leads")
        .select("*")
        .eq("unique_key", lead_unique_key)
        .limit(1)
        .execute()
        .data
        or []
    )
    return db_to_lead(rows[0]) if rows else None


def update_lead_record(lead_unique_key: str, fields: dict) -> None:
    if get_storage() == "local":
        leads = [normalize_master_lead(lead) for lead in load_json(MASTER_LEADS_PATH, [])]
        for lead in leads:
            if lead.get("Lead ID") != lead_unique_key:
                continue
            lead.update(fields)
            break
        save_json(MASTER_LEADS_PATH, leads)
        return

    db_fields = {
        "status_abordagem": fields.get("status_abordagem"),
        "whatsapp_valido": fields.get("whatsapp_valido"),
        "mensagem_enviada": fields.get("mensagem_enviada"),
        "observacao": fields.get("observacao"),
        "data_primeira_abordagem": fields.get("data_primeira_abordagem") or None,
        "data_ultimo_feedback": fields.get("data_ultimo_feedback") or None,
        "ultima_acao": fields.get("ultima_acao"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    db_fields = {key: value for key, value in db_fields.items() if value is not None}
    if db_fields:
        supabase_client().table("leads").update(db_fields).eq("unique_key", lead_unique_key).execute()


def get_next_lead(status_filter: str = "NOVO", skipped_ids: list[str] | None = None) -> dict | None:
    skipped = set(skipped_ids or [])

    if get_storage() == "local":
        leads = [
            normalize_master_lead(lead)
            for lead in load_json(MASTER_LEADS_PATH, [])
            if normalize_master_lead(lead).get("status_abordagem", "NOVO") == status_filter
        ]
        leads.sort(key=lambda lead: ({"ALTA": 0, "MEDIA": 1, "BAIXA": 2}.get(lead.get("Oportunidade", "BAIXA"), 3), lead.get("Adicionado em", "")))
        return next((lead for lead in leads if lead.get("Lead ID") not in skipped), None)

    query = (
        supabase_client()
        .table("leads")
        .select("*")
        .eq("status_abordagem", status_filter)
        .order("created_at", desc=False)
        .limit(20)
    )
    rows = query.execute().data or []
    for row in rows:
        lead = db_to_lead(row)
        if lead.get("Lead ID") not in skipped:
            return lead
    return None


def lead_to_db(lead: dict) -> dict:
    normalized = normalize_master_lead(lead)
    return {
        "unique_key": normalized["Lead ID"],
        "nome": normalized.get("Nome", ""),
        "telefone": normalized.get("Telefone", ""),
        "telefone_limpo": clean_digits(normalized.get("Telefone", "")),
        "whatsapp": normalized.get("WhatsApp", ""),
        "endereco": normalized.get("Endereco", ""),
        "site": normalized.get("Site", ""),
        "nota": normalized.get("Nota", ""),
        "quantidade_avaliacoes": to_int(normalized.get("Quantidade de avaliacoes", "")),
        "cidade": normalized.get("Cidade", ""),
        "tem_site": normalized.get("Tem Site?", ""),
        "oportunidade": normalized.get("Oportunidade", ""),
        "link_google_maps": normalized.get("Link do Google Maps", ""),
        "status_abordagem": normalized.get("status_abordagem", "NOVO"),
        "data_primeira_abordagem": normalized.get("data_primeira_abordagem") or None,
        "data_ultimo_feedback": normalized.get("data_ultimo_feedback") or None,
        "ultima_acao": normalized.get("ultima_acao", ""),
        "origem": normalized.get("origem", ""),
        "origem_raspagem": normalized.get("origem_raspagem", ""),
        "updated_at": datetime.utcnow().isoformat(),
    }


def db_to_lead(row: dict) -> dict:
    return normalize_master_lead(
        {
            "Lead ID": row.get("unique_key") or row.get("id", ""),
            "Nome": row.get("nome", ""),
            "Telefone": row.get("telefone", ""),
            "WhatsApp": row.get("whatsapp", ""),
            "Endereco": row.get("endereco", ""),
            "Site": row.get("site", ""),
            "Nota": row.get("nota", ""),
            "Quantidade de avaliacoes": str(row.get("quantidade_avaliacoes") or ""),
            "Cidade": row.get("cidade", ""),
            "Tem Site?": row.get("tem_site", ""),
            "Oportunidade": row.get("oportunidade", ""),
            "Link do Google Maps": row.get("link_google_maps", ""),
            "Status abordagem": row.get("status_abordagem", "NOVO"),
            "WhatsApp valido?": row.get("whatsapp_valido", ""),
            "Mensagem enviada?": row.get("mensagem_enviada", ""),
            "Observacao": row.get("observacao", ""),
            "Data/hora do feedback": row.get("data_ultimo_feedback") or "",
            "status_abordagem": row.get("status_abordagem", "NOVO"),
            "data_primeira_abordagem": row.get("data_primeira_abordagem") or "",
            "data_ultimo_feedback": row.get("data_ultimo_feedback") or "",
            "ultima_acao": row.get("ultima_acao", ""),
            "origem_raspagem": row.get("origem_raspagem", ""),
            "Adicionado em": row.get("created_at", ""),
        }
    )


def load_master_leads() -> list[dict]:
    if get_storage() == "local":
        return [normalize_master_lead(lead) for lead in load_json(MASTER_LEADS_PATH, [])]

    rows = supabase_client().table("leads").select("*").execute().data or []
    return [db_to_lead(row) for row in rows]


def save_master_leads(leads: list[dict]) -> None:
    normalized = [normalize_master_lead(lead) for lead in leads]

    if get_storage() == "local":
        save_json(MASTER_LEADS_PATH, normalized)
        return

    if normalized:
        supabase_client().table("leads").upsert(
            [lead_to_db(lead) for lead in normalized],
            on_conflict="unique_key",
        ).execute()


def load_feedbacks() -> dict:
    if get_storage() == "local":
        return load_json(FEEDBACKS_PATH, {})

    rows = supabase_client().table("feedbacks").select("*").order("created_at", desc=False).execute().data or []
    feedbacks = {}
    for row in rows:
        key = row.get("lead_unique_key", "")
        if not key:
            continue
        feedbacks[key] = {
            "Status abordagem": row.get("status_abordagem", ""),
            "WhatsApp valido?": row.get("whatsapp_valido", ""),
            "Mensagem enviada?": row.get("mensagem_enviada", ""),
            "Observacao": row.get("observacao", ""),
            "Data/hora do feedback": row.get("created_at", ""),
            "Data ultimo feedback": row.get("created_at", ""),
            "Data primeira abordagem": "",
            "Ultima acao": "",
        }
    return feedbacks


def save_feedbacks(feedbacks: dict) -> None:
    if get_storage() == "local":
        save_json(FEEDBACKS_PATH, feedbacks)


def add_feedback(lead_unique_key: str, feedback: dict) -> None:
    if get_storage() == "local":
        feedbacks = load_feedbacks()
        feedbacks[lead_unique_key] = feedback
        save_feedbacks(feedbacks)
        return

    supabase_client().table("feedbacks").insert(
        {
            "lead_unique_key": lead_unique_key,
            "status_abordagem": feedback.get("Status abordagem", ""),
            "whatsapp_valido": feedback.get("WhatsApp valido?", ""),
            "mensagem_enviada": feedback.get("Mensagem enviada?", ""),
            "observacao": feedback.get("Observacao", ""),
        }
    ).execute()


def build_lead_index(leads: list[dict]) -> dict:
    index = {}
    for position, lead in enumerate(leads):
        for key in lead_identity_keys(lead):
            index.setdefault(key, position)
    return index


def deduplicate_leads(leads: list[dict]) -> list[dict]:
    unique = []
    index = {}
    for lead in leads:
        normalized = normalize_master_lead(lead)
        keys = lead_identity_keys(normalized)
        if not keys or any(key in index for key in keys):
            continue
        for key in keys:
            index[key] = len(unique)
        unique.append(normalized)
    return unique


def merge_status(existing_status: str, incoming_status: str) -> str:
    if existing_status in ["SUCESSO", "BURN"]:
        return existing_status
    if STATUS_PRIORITY.get(incoming_status, 1) > STATUS_PRIORITY.get(existing_status, 1):
        return incoming_status
    return existing_status or incoming_status or "NOVO"


def upsert_leads_from_scrape(new_leads: list[dict], scrape_info: dict | None = None, diagnostico: bool = True):
    master = [normalize_master_lead(lead) for lead in load_master_leads()]
    index = build_lead_index(master)
    added = 0
    duplicates = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scrape_info = scrape_info or {}
    origin = scrape_info.get("origem_raspagem") or f"{scrape_info.get('nicho', '')} | {scrape_info.get('cidade', '')}".strip(" |")

    for lead in new_leads:
        normalized = normalize_master_lead(lead)
        keys = lead_identity_keys(normalized)
        if not keys:
            duplicates += 1
            continue

        existing_position = next((index[key] for key in keys if key in index), None)
        if existing_position is not None:
            duplicates += 1
            master[existing_position]["status_abordagem"] = merge_status(
                master[existing_position].get("status_abordagem", "NOVO"),
                normalized.get("status_abordagem", "NOVO"),
            )
            continue

        normalized["status_abordagem"] = "NOVO"
        normalized["Adicionado em"] = now
        normalized["origem_raspagem"] = origin
        master.append(normalized)
        position = len(master) - 1
        for key in keys:
            index[key] = position
        added += 1

    save_master_leads(master)
    summary = {
        "data_hora": now,
        "nicho": scrape_info.get("nicho", ""),
        "cidade": scrape_info.get("cidade", ""),
        "limite": scrape_info.get("limite", ""),
        "avaliacoes_maximas": scrape_info.get("avaliacoes_maximas", ""),
        "leads_encontrados": len(new_leads),
        "novos_adicionados": added,
        "duplicados_ignorados": duplicates,
        "total_geral_base": len(master),
    }
    save_raspagem(summary)
    return master, summary


def save_raspagem(summary: dict) -> None:
    if get_storage() == "local":
        history = load_raspagens()
        history.append(summary)
        save_json(SCRAPES_HISTORY_PATH, history[-20:])
        return

    supabase_client().table("raspagens").insert(
        {
            "nicho": summary.get("nicho", ""),
            "cidade": summary.get("cidade", ""),
            "limite": int(summary.get("limite") or 0),
            "avaliacoes_maximas": int(summary.get("avaliacoes_maximas") or 0),
            "leads_encontrados": int(summary.get("leads_encontrados") or 0),
            "novos_adicionados": int(summary.get("novos_adicionados") or 0),
            "duplicados_ignorados": int(summary.get("duplicados_ignorados") or 0),
            "total_base": int(summary.get("total_geral_base") or 0),
        }
    ).execute()


def load_raspagens() -> list[dict]:
    if get_storage() == "local":
        return load_json(SCRAPES_HISTORY_PATH, [])

    rows = supabase_client().table("raspagens").select("*").order("data_hora", desc=False).execute().data or []
    return [
        {
            "data_hora": row.get("data_hora", ""),
            "nicho": row.get("nicho", ""),
            "cidade": row.get("cidade", ""),
            "limite": row.get("limite", ""),
            "avaliacoes_maximas": row.get("avaliacoes_maximas", ""),
            "leads_encontrados": row.get("leads_encontrados", 0),
            "novos_adicionados": row.get("novos_adicionados", 0),
            "duplicados_ignorados": row.get("duplicados_ignorados", 0),
            "total_geral_base": row.get("total_base", 0),
        }
        for row in rows
    ]


def save_recent_action(action: dict) -> None:
    if get_storage() == "local":
        actions = load_json(RECENT_ACTIONS_PATH, [])
        actions.append(action)
        save_json(RECENT_ACTIONS_PATH, actions[-30:])
        return

    supabase_client().table("acoes_recentes").insert(
        {
            "lead_unique_key": action.get("lead_id", ""),
            "acao": action.get("acao", ""),
            "estado_anterior": action.get("before", {}),
            "estado_novo": action.get("after", {}),
        }
    ).execute()


def load_recent_actions() -> list[dict]:
    if get_storage() == "local":
        return load_json(RECENT_ACTIONS_PATH, [])

    rows = supabase_client().table("acoes_recentes").select("*").order("created_at", desc=False).execute().data or []
    return [
        {
            "data_hora": row.get("created_at", ""),
            "acao": row.get("acao", ""),
            "lead_id": row.get("lead_unique_key", ""),
            "before": row.get("estado_anterior", {}),
            "after": row.get("estado_novo", {}),
        }
        for row in rows
    ]


def save_recent_actions(actions: list[dict]) -> None:
    if get_storage() == "local":
        save_json(RECENT_ACTIONS_PATH, actions[-30:])


def update_lead_status(lead_unique_key: str, status: str, feedback: dict | None = None) -> list[dict]:
    leads = load_master_leads()
    feedback = feedback or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for lead in leads:
        if lead.get("Lead ID") != lead_unique_key:
            continue
        lead["status_abordagem"] = merge_status(lead.get("status_abordagem", "NOVO"), status)
        lead["data_ultimo_feedback"] = now
        lead["ultima_acao"] = feedback.get("Ultima acao", "Feedback salvo")
        break
    save_master_leads(leads)
    add_feedback(lead_unique_key, feedback)
    return leads


def get_next_lead_for_queue(status_filter: str = "NOVO", skipped_ids: list[str] | None = None) -> dict | None:
    skipped = set(skipped_ids or [])
    leads = [
        lead
        for lead in load_master_leads()
        if lead.get("status_abordagem") == status_filter and lead.get("Lead ID") not in skipped
    ]
    leads.sort(key=lambda lead: ({"ALTA": 0, "MEDIA": 1, "BAIXA": 2}.get(lead.get("Oportunidade", "BAIXA"), 3), lead.get("Adicionado em", "")))
    return leads[0] if leads else None


def undo_last_action():
    actions = load_recent_actions()
    if not actions:
        return None
    return actions[-1]


def enrich_for_export(leads: list[dict]) -> list[dict]:
    feedbacks = load_feedbacks()
    enriched = []
    for lead in leads:
        item = dict(lead)
        lead_id = item.get("Lead ID", "")
        feedback = default_feedback(item.get("status_abordagem", "NOVO"))
        feedback.update(feedbacks.get(lead_id, {}))
        feedback["Status abordagem"] = item.get("status_abordagem", feedback.get("Status abordagem", "NOVO"))
        feedback["Data primeira abordagem"] = item.get("data_primeira_abordagem", feedback.get("Data primeira abordagem", ""))
        feedback["Data ultimo feedback"] = item.get("data_ultimo_feedback", feedback.get("Data ultimo feedback", ""))
        feedback["Ultima acao"] = item.get("ultima_acao", feedback.get("Ultima acao", ""))
        feedback["Origem raspagem"] = item.get("origem_raspagem", "")
        item.update(feedback)
        enriched.append(item)
    return enriched


def export_active_excel() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    active = [lead for lead in enrich_for_export(load_master_leads()) if lead.get("Status abordagem") in ["NOVO", "SUCESSO"]]
    pd.DataFrame(active, columns=FULL_EXPORT_COLUMNS).to_excel(ACTIVE_EXPORT_PATH, index=False, engine="openpyxl")
    return ACTIVE_EXPORT_PATH


def export_full_history_excel() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    history = enrich_for_export(load_master_leads())
    pd.DataFrame(history, columns=FULL_EXPORT_COLUMNS).to_excel(HISTORY_EXPORT_PATH, index=False, engine="openpyxl")
    return HISTORY_EXPORT_PATH
