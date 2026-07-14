from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from unicodedata import normalize

import pandas as pd

from src import storage


logger = logging.getLogger(__name__)

CONFIRM_PHRASE = "SUBSTITUIR"
COUNTRY_CODE = "55"
ORIGEM_RASPAGEM = "Oficinas Mecanicas - MT"
BATCH_SIZE = 100
TABLES_TO_REPLACE = ["feedbacks", "acoes_recentes", "raspagens", "leads"]
BACKUP_DIR = storage.EXPORT_DIR / "backups"
REJECTED_DIR = storage.EXPORT_DIR / "rejeitados"

LEADS_REQUIRED_COLUMNS = {
    "unique_key",
    "nome",
    "telefone",
    "telefone_limpo",
    "whatsapp",
    "site",
    "quantidade_avaliacoes",
    "cidade",
    "link_google_maps",
    "status_abordagem",
    "whatsapp_valido",
    "mensagem_enviada",
    "observacao",
    "data_primeira_abordagem",
    "data_ultimo_feedback",
    "ultima_acao",
    "origem",
    "origem_raspagem",
}

COLUMN_ALIASES = {
    "nome": {"nome", "name", "empresa", "estabelecimento", "lead"},
    "telefone": {"telefone", "phone", "celular", "numero", "número", "contato", "telefone limpo"},
    "cidade": {"cidade", "city", "municipio", "município"},
    "estado": {"estado", "uf"},
    "categoria": {"categoria", "category", "segmento", "nicho"},
    "avaliacoes": {"avaliacoes", "avaliações", "avaliacao", "avaliação", "reviews", "quantidade avaliacoes", "quantidade de avaliacoes"},
    "website": {"website", "site", "url", "pagina", "página"},
    "link_google_maps": {"link google maps", "link do google maps", "google maps", "maps", "mapa", "url maps", "link_google_maps"},
    "whatsapp_link": {"whatsapp link", "whatsapp_link", "link whatsapp", "whatsapp", "whats app", "wa"},
}


@dataclass
class RejectedRow:
    linha_original: int
    nome: str
    telefone: str
    cidade: str
    motivo_rejeicao: str


@dataclass
class InformativeDuplicate:
    linha_original: int
    nome: str
    cidade: str
    telefone: str
    motivo: str


@dataclass
class ReplacementPlan:
    source_name: str
    sheet_name: str
    total_rows: int
    empty_rows: int
    columns: list[str]
    detected_columns: dict[str, str]
    valid_leads: list[dict[str, Any]] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)
    rejected_rows: list[RejectedRow] = field(default_factory=list)
    informative_duplicates: list[InformativeDuplicate] = field(default_factory=list)
    website_empty: int = 0
    website_invalid: int = 0
    states_not_mt: int = 0
    duplicate_phone_rows: int = 0
    duplicate_name_city_rows: int = 0
    missing_columns: list[str] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.valid_leads)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_rows)

    @property
    def prepared_count(self) -> int:
        return len(self.payloads)

    @property
    def unique_key_count(self) -> int:
        return len({payload.get("unique_key", "") for payload in self.payloads if payload.get("unique_key")})

    def summary(self, current_leads: int | None = None) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "sheet_name": self.sheet_name,
            "total_rows": self.total_rows,
            "empty_rows": self.empty_rows,
            "columns": self.columns,
            "detected_columns": self.detected_columns,
            "missing_columns": self.missing_columns,
            "valid_leads": self.valid_count,
            "invalid_leads": self.rejected_count,
            "duplicate_phone_rows": self.duplicate_phone_rows,
            "duplicate_name_city_rows": self.duplicate_name_city_rows,
            "informative_duplicates": len(self.informative_duplicates),
            "website_empty": self.website_empty,
            "website_invalid": self.website_invalid,
            "states_not_mt": self.states_not_mt,
            "prepared_to_import": self.prepared_count,
            "unique_keys": self.unique_key_count,
            "duplicate_unique_keys": max(0, self.prepared_count - self.unique_key_count),
            "current_leads": current_leads,
        }


def cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def normalize_key(value: Any) -> str:
    text = cell_to_text(value)
    without_accents = normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = [char if char.isalnum() else " " for char in without_accents.lower()]
    return " ".join("".join(normalized).split())


def clean_digits(value: Any) -> str:
    return "".join(char for char in cell_to_text(value) if char.isdigit())


def strip_country_code(digits: str) -> str:
    if digits.startswith(COUNTRY_CODE) and len(digits) > 11:
        return digits[len(COUNTRY_CODE):]
    return digits


def has_repeated_digits(digits: str) -> bool:
    return bool(digits) and len(set(digits)) == 1


def normalize_phone(value: Any) -> tuple[str, str]:
    digits = strip_country_code(clean_digits(value))
    if len(digits) not in {10, 11}:
        raise ValueError("telefone invalido")
    if has_repeated_digits(digits):
        raise ValueError("telefone invalido")

    ddd = int(digits[:2])
    if ddd < 11 or ddd > 99:
        raise ValueError("telefone invalido")

    return digits, f"{COUNTRY_CODE}{digits}"


def normalize_whatsapp_link(phone_international: str) -> str:
    return f"https://wa.me/{phone_international}"


def website_is_valid(value: str) -> bool:
    text = cell_to_text(value)
    if not text:
        return False
    return bool(re.match(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/.*)?$", text.lower()))


def detect_columns(columns: list[str]) -> dict[str, str]:
    normalized_aliases = {
        canonical: {normalize_key(alias) for alias in aliases}
        for canonical, aliases in COLUMN_ALIASES.items()
    }
    detected = {}
    for column in columns:
        normalized = normalize_key(column)
        for canonical, aliases in normalized_aliases.items():
            if canonical not in detected and normalized in aliases:
                detected[canonical] = column
                break
    return detected


def read_first_sheet(source: Any) -> tuple[pd.DataFrame, str]:
    excel = pd.ExcelFile(source)
    for sheet_name in excel.sheet_names:
        dataframe = pd.read_excel(excel, sheet_name=sheet_name, dtype=object)
        if not dataframe.dropna(how="all").empty:
            return dataframe, sheet_name
    return pd.read_excel(excel, sheet_name=excel.sheet_names[0], dtype=object), excel.sheet_names[0]


def pick(row: pd.Series, detected_columns: dict[str, str], canonical: str) -> str:
    source_column = detected_columns.get(canonical)
    if not source_column:
        return ""
    return cell_to_text(row.get(source_column))


def normalize_row(row: pd.Series, detected_columns: dict[str, str], line_number: int) -> tuple[dict[str, Any] | None, RejectedRow | None, dict[str, Any]]:
    name = pick(row, detected_columns, "nome")
    phone_raw = pick(row, detected_columns, "telefone")
    city = pick(row, detected_columns, "cidade")
    state = pick(row, detected_columns, "estado").upper()
    category = pick(row, detected_columns, "categoria")
    website = pick(row, detected_columns, "website")
    maps_link = pick(row, detected_columns, "link_google_maps")
    reviews = pick(row, detected_columns, "avaliacoes")
    metadata = {"state": state, "website_empty": not bool(website), "website_invalid": False}

    if not any([name, phone_raw, city, state, category, website, maps_link, reviews]):
        return None, RejectedRow(line_number, name, phone_raw, city, "linha vazia"), metadata
    if not name:
        return None, RejectedRow(line_number, name, phone_raw, city, "nome ausente"), metadata
    if not city:
        return None, RejectedRow(line_number, name, phone_raw, city, "cidade ausente"), metadata

    try:
        phone_national, phone_international = normalize_phone(phone_raw)
    except ValueError:
        reason = "telefone ausente" if not clean_digits(phone_raw) else "telefone invalido"
        return None, RejectedRow(line_number, name, phone_raw, city, reason), metadata

    if website and not website_is_valid(website):
        metadata["website_invalid"] = True
        website = ""

    lead = {
        "Nome": name,
        "Telefone": phone_national,
        "WhatsApp": normalize_whatsapp_link(phone_international),
        "Endereco": "",
        "Site": website,
        "Nota": "",
        "Quantidade de avaliacoes": storage.to_int(reviews),
        "Cidade": city,
        "Tem Site?": "SIM" if website else "NAO",
        "Oportunidade": "BAIXA" if website else "ALTA",
        "Link do Google Maps": maps_link,
        "status_abordagem": "NOVO",
        "whatsapp_valido": "SIM",
        "mensagem_enviada": "NAO",
        "observacao": "",
        "data_primeira_abordagem": None,
        "data_ultimo_feedback": None,
        "ultima_acao": "",
        "origem": category,
        "origem_raspagem": ORIGEM_RASPAGEM,
        "Adicionado em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    lead["Lead ID"] = storage.unique_key_for_lead(lead)
    return lead, None, metadata


def lead_to_payload(lead: dict[str, Any]) -> dict[str, Any]:
    payload = storage.lead_to_db(lead)
    payload.update(
        {
            "whatsapp_valido": "SIM",
            "mensagem_enviada": "NAO",
            "observacao": "",
            "data_primeira_abordagem": None,
            "data_ultimo_feedback": None,
            "ultima_acao": "",
            "origem": lead.get("origem", ""),
            "origem_raspagem": ORIGEM_RASPAGEM,
        }
    )
    return payload


def build_plan(source: Any, source_name: str | None = None) -> ReplacementPlan:
    dataframe, sheet_name = read_first_sheet(source)
    original_columns = [str(column) for column in dataframe.columns]
    detected_columns = detect_columns(original_columns)
    missing_columns = [
        column
        for column in ["nome", "telefone", "cidade"]
        if column not in detected_columns
    ]

    data = dataframe.dropna(how="all")
    plan = ReplacementPlan(
        source_name=source_name or getattr(source, "filename", "") or str(source),
        sheet_name=sheet_name,
        total_rows=len(data),
        empty_rows=int(len(dataframe) - len(data)),
        columns=original_columns,
        detected_columns=detected_columns,
        missing_columns=missing_columns,
    )

    if missing_columns:
        return plan

    phone_index = {}
    name_city_index = {}
    pending_leads: list[tuple[int, dict[str, Any]]] = []

    for dataframe_index, row in data.iterrows():
        line_number = int(dataframe_index) + 2
        lead, rejected, metadata = normalize_row(row, detected_columns, line_number)
        if metadata.get("website_empty"):
            plan.website_empty += 1
        if metadata.get("website_invalid"):
            plan.website_invalid += 1
        state = metadata.get("state", "")
        if state and normalize_key(state) not in {"mt", "mato grosso"}:
            plan.states_not_mt += 1
        if rejected:
            plan.rejected_rows.append(rejected)
            continue
        if not lead:
            continue

        phone_key = storage.clean_digits(lead["Telefone"])
        if phone_key in phone_index:
            plan.duplicate_phone_rows += 1
            plan.rejected_rows.append(
                RejectedRow(line_number, lead["Nome"], lead["Telefone"], lead["Cidade"], "duplicado por telefone")
            )
            continue

        phone_index[phone_key] = line_number
        name_city_key = storage.normalize_text(f"{lead['Nome']} {lead['Cidade']}")
        if name_city_key in name_city_index:
            plan.duplicate_name_city_rows += 1
            plan.informative_duplicates.append(
                InformativeDuplicate(line_number, lead["Nome"], lead["Cidade"], lead["Telefone"], "mesmo nome e cidade com telefone distinto")
            )
        else:
            name_city_index[name_city_key] = line_number
        pending_leads.append((line_number, lead))

    plan.valid_leads = [lead for _, lead in pending_leads]
    plan.payloads = [lead_to_payload(lead) for _, lead in pending_leads]
    return plan


def plan_from_path(path: Path) -> ReplacementPlan:
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        raise ValueError("arquivo nao e Excel")
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")
    return build_plan(path, path.name)


def save_rejected_report(plan: ReplacementPlan) -> str:
    if not plan.rejected_rows:
        return ""
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REJECTED_DIR / f"rejeitados_substituicao_{timestamp}.xlsx"
    rows = [
        {
            "linha_original": item.linha_original,
            "nome": item.nome,
            "telefone": item.telefone,
            "cidade": item.cidade,
            "motivo_rejeicao": item.motivo_rejeicao,
        }
        for item in plan.rejected_rows
    ]
    pd.DataFrame(rows).to_excel(path, index=False, engine="openpyxl")
    return str(path)


def fetch_table_rows(client: Any, table_name: str) -> list[dict[str, Any]]:
    rows = []
    page_size = 1000
    start = 0
    while True:
        batch = (
            client.table(table_name)
            .select("*")
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def backup_tables(client: Any) -> tuple[str, dict[str, int], dict[str, list[dict[str, Any]]]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"substituicao_base_backup_{timestamp}.json"
    data = {table_name: fetch_table_rows(client, table_name) for table_name in TABLES_TO_REPLACE}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    counts = {table_name: len(rows) for table_name, rows in data.items()}
    validate_backup_file(path, counts)
    return str(path), counts, data


def validate_backup_file(path: Path, expected_counts: dict[str, int]) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("Backup nao foi criado corretamente.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Backup invalido.") from exc
    for table_name in TABLES_TO_REPLACE:
        if table_name not in data or not isinstance(data[table_name], list):
            raise RuntimeError("Backup incompleto.")
        if len(data[table_name]) != expected_counts.get(table_name, -1):
            raise RuntimeError("Backup com contagem inconsistente.")


def assert_supabase_ready(client: Any) -> None:
    sample = client.table("leads").select(",".join(sorted(LEADS_REQUIRED_COLUMNS))).limit(1).execute()
    if sample.data is None:
        raise RuntimeError("Tabela leads indisponivel ou schema incompativel.")
    for table_name in TABLES_TO_REPLACE:
        result = client.table(table_name).select("id").limit(1).execute()
        if result.data is None:
            raise RuntimeError(f"Tabela {table_name} indisponivel.")


def delete_all_rows(client: Any, table_name: str) -> int:
    result = (
        client.table(table_name)
        .delete(count="exact")
        .filter("id", "not.is", "null")
        .execute()
    )
    return int(result.count or 0)


def insert_rows(client: Any, table_name: str, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        client.table(table_name).insert(batch).execute()
        inserted += len(batch)
    return inserted


def restore_from_backup(client: Any, backup_data: dict[str, list[dict[str, Any]]]) -> None:
    for table_name in TABLES_TO_REPLACE:
        delete_all_rows(client, table_name)
    for table_name in reversed(TABLES_TO_REPLACE):
        rows = backup_data.get(table_name, [])
        if rows:
            insert_rows(client, table_name, rows)


def execute_replacement(plan: ReplacementPlan, confirm_phrase: str) -> dict[str, Any]:
    if confirm_phrase != CONFIRM_PHRASE:
        raise RuntimeError("Confirmacao invalida. Digite SUBSTITUIR para confirmar.")
    if plan.prepared_count < 1:
        raise RuntimeError("Nenhum lead valido preparado. Operacao cancelada.")
    if plan.prepared_count != plan.valid_count:
        raise RuntimeError("Plano inconsistente. Operacao cancelada.")
    if storage.get_storage() != "supabase":
        raise RuntimeError("Substituicao real exige STORAGE_MODE=supabase.")

    started_at = perf_counter()
    client = storage.supabase_client()
    assert_supabase_ready(client)
    logger.info(
        "Inicio da substituicao de base: arquivo=%s total=%s validos=%s rejeitados=%s",
        plan.source_name,
        plan.total_rows,
        plan.valid_count,
        plan.rejected_count,
    )
    backup_path, backup_counts, backup_data = backup_tables(client)
    deleted_counts: dict[str, int] = {}
    cleanup_started = False

    try:
        for table_name in ["feedbacks", "acoes_recentes", "raspagens", "leads"]:
            deleted_counts[table_name] = delete_all_rows(client, table_name)
            cleanup_started = True
        inserted = insert_rows(client, "leads", plan.payloads)
        final_count = storage.count_leads()
        if inserted != plan.prepared_count or final_count != plan.prepared_count:
            raise RuntimeError("Contagem final incompativel apos importacao.")
    except Exception:
        if cleanup_started:
            logger.exception("Falha na substituicao apos inicio da limpeza. Iniciando restauracao automatica.")
            try:
                restore_from_backup(client, backup_data)
                logger.error("Restauracao automatica concluida apos falha na substituicao.")
            except Exception:
                logger.critical("Falha tambem na restauracao automatica. Use o backup gerado: %s", backup_path, exc_info=True)
        else:
            logger.exception("Falha na substituicao antes da limpeza. Nenhuma restauracao necessaria.")
        raise

    rejected_report = save_rejected_report(plan)
    elapsed = perf_counter() - started_at
    logger.info(
        "Substituicao concluida: removidos=%s inseridos=%s rejeitados=%s tempo=%.2fs",
        deleted_counts,
        plan.prepared_count,
        plan.rejected_count,
        elapsed,
    )
    return {
        "ok": True,
        "backup_path": backup_path,
        "backup_counts": backup_counts,
        "deleted_counts": deleted_counts,
        "inserted": plan.prepared_count,
        "final_count": final_count,
        "rejected_report": rejected_report,
        "elapsed_seconds": round(elapsed, 2),
    }


def dry_run_summary(path: Path) -> dict[str, Any]:
    plan = plan_from_path(path)
    return plan.summary(current_leads=None)
