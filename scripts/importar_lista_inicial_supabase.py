from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unicodedata import normalize

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import storage


ORIGEM_RASPAGEM = "IMPORTAÇÃO INICIAL 11-06"
COUNTRY_CODE = "55"
BATCH_SIZE = 100

CANONICAL_COLUMNS = {
    "nome": "Nome",
    "telefone": "Telefone",
    "whatsapp": "WhatsApp",
    "endereco": "Endereco",
    "site": "Site",
    "nota": "Nota",
    "quantidade_avaliacoes": "Quantidade de avaliacoes",
    "cidade": "Cidade",
    "tem_site": "Tem Site?",
    "oportunidade": "Oportunidade",
    "link_google_maps": "Link do Google Maps",
}

COLUMN_ALIASES = {
    "Nome": {
        "nome",
        "name",
        "empresa",
        "estabelecimento",
        "lead",
    },
    "Telefone": {
        "telefone",
        "phone",
        "celular",
        "numero",
        "número",
        "contato",
        "telefone_limpo",
    },
    "WhatsApp": {
        "whatsapp",
        "whats app",
        "whats",
        "wa",
        "link whatsapp",
    },
    "Endereco": {
        "endereco",
        "endereço",
        "address",
        "logradouro",
        "localizacao",
        "localização",
    },
    "Site": {
        "site",
        "website",
        "url",
        "pagina",
        "página",
    },
    "Nota": {
        "nota",
        "rating",
        "avaliacao",
        "avaliação",
        "classificacao",
        "classificação",
    },
    "Quantidade de avaliacoes": {
        "quantidade de avaliacoes",
        "quantidade de avaliações",
        "quantidade_avaliacoes",
        "qtd avaliacoes",
        "qtd avaliações",
        "qtd_avaliacoes",
        "reviews",
        "avaliacoes",
        "avaliações",
    },
    "Cidade": {
        "cidade",
        "city",
        "municipio",
        "município",
    },
    "Tem Site?": {
        "tem site",
        "tem site?",
        "tem_site",
        "possui site",
        "possui_site",
    },
    "Oportunidade": {
        "oportunidade",
        "opportunity",
        "prioridade",
    },
    "Link do Google Maps": {
        "link do google maps",
        "link_google_maps",
        "google maps",
        "maps",
        "mapa",
        "url maps",
        "url_google_maps",
    },
}


def normalize_column_name(value: Any) -> str:
    text = str(value or "").strip()
    without_accents = normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = []
    for char in without_accents.lower():
        normalized.append(char if char.isalnum() else " ")
    return " ".join("".join(normalized).split())


NORMALIZED_ALIASES = {
    canonical: {normalize_column_name(alias) for alias in aliases}
    for canonical, aliases in COLUMN_ALIASES.items()
}


def cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def clean_phone(value: Any) -> str:
    return storage.clean_digits(cell_to_text(value))


def build_whatsapp(value: Any) -> str:
    text = cell_to_text(value)
    digits = storage.clean_digits(text)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if not digits:
        return ""
    if digits.startswith(COUNTRY_CODE):
        return f"https://wa.me/{digits}"
    return f"https://wa.me/{COUNTRY_CODE}{digits}"


def detect_columns(columns: list[str]) -> dict[str, str]:
    detected = {}
    used_columns = set()

    for original in columns:
        normalized = normalize_column_name(original)
        if not normalized:
            continue

        for canonical, aliases in NORMALIZED_ALIASES.items():
            if canonical in detected or original in used_columns:
                continue
            if normalized in aliases:
                detected[canonical] = original
                used_columns.add(original)
                break

    return detected


def read_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    dataframe = pd.read_excel(path, dtype=object)
    dataframe = dataframe.dropna(how="all")
    detected_columns = detect_columns([str(column) for column in dataframe.columns])
    rows = []

    for _, row in dataframe.iterrows():
        lead = {}
        for canonical, source_column in detected_columns.items():
            lead[canonical] = row.get(source_column)
        rows.append(lead)

    return rows, detected_columns


def ordered_duplicate_keys_from_db(row: dict[str, Any]) -> list[str]:
    phone = storage.clean_digits(row.get("telefone_limpo") or row.get("telefone") or "")
    whatsapp = cell_to_text(row.get("whatsapp"))
    maps_link = cell_to_text(row.get("link_google_maps"))
    keys = []

    if phone:
        keys.append(f"phone:{phone}")
    if whatsapp:
        keys.append(f"whatsapp:{whatsapp}")
    if maps_link:
        keys.append(f"maps:{maps_link}")

    if keys:
        return keys

    name = cell_to_text(row.get("nome"))
    city = cell_to_text(row.get("cidade"))
    name_city = storage.normalize_text(f"{name} {city}") if name or city else ""
    return [f"namecity:{name_city}"] if name_city else []


def ordered_duplicate_keys_from_lead(lead: dict[str, Any]) -> list[str]:
    phone = storage.clean_digits(lead.get("Telefone", ""))
    whatsapp = cell_to_text(lead.get("WhatsApp"))
    maps_link = cell_to_text(lead.get("Link do Google Maps"))
    keys = []

    if phone:
        keys.append(f"phone:{phone}")
    if whatsapp:
        keys.append(f"whatsapp:{whatsapp}")
    if maps_link:
        keys.append(f"maps:{maps_link}")

    if keys:
        return keys

    name = cell_to_text(lead.get("Nome"))
    city = cell_to_text(lead.get("Cidade"))
    name_city = storage.normalize_text(f"{name} {city}") if name or city else ""
    return [f"namecity:{name_city}"] if name_city else []


def load_existing_index(client) -> dict[str, str]:
    rows = []
    page_size = 1000
    start = 0
    while True:
        batch = (
            client.table("leads")
            .select("unique_key,telefone,telefone_limpo,whatsapp,link_google_maps,nome,cidade")
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

    index = {}
    for row in rows:
        for key in ordered_duplicate_keys_from_db(row):
            index.setdefault(key, row.get("unique_key", ""))
    return index


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    phone = clean_phone(row.get("Telefone") or row.get("telefone"))
    whatsapp = build_whatsapp(row.get("WhatsApp") or row.get("whatsapp") or phone)

    lead = {
        "Nome": cell_to_text(row.get("Nome") or row.get("nome")),
        "Telefone": phone,
        "WhatsApp": whatsapp,
        "Endereco": cell_to_text(row.get("Endereco") or row.get("endereco")),
        "Site": cell_to_text(row.get("Site") or row.get("site")),
        "Nota": cell_to_text(row.get("Nota") or row.get("nota")),
        "Quantidade de avaliacoes": cell_to_text(
            row.get("Quantidade de avaliacoes") or row.get("quantidade_avaliacoes")
        ),
        "Cidade": cell_to_text(row.get("Cidade") or row.get("cidade")),
        "Tem Site?": cell_to_text(row.get("Tem Site?") or row.get("tem_site")),
        "Oportunidade": cell_to_text(row.get("Oportunidade") or row.get("oportunidade")),
        "Link do Google Maps": cell_to_text(
            row.get("Link do Google Maps") or row.get("link_google_maps")
        ),
        "status_abordagem": "NOVO",
        "whatsapp_valido": None,
        "mensagem_enviada": None,
        "ultima_acao": "",
        "origem": "importacao_inicial",
        "origem_raspagem": ORIGEM_RASPAGEM,
        "Adicionado em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    lead["Lead ID"] = storage.unique_key_for_lead(lead)
    return lead


def lead_to_db_with_empty_operational_fields(lead: dict[str, Any]) -> dict[str, Any]:
    payload = storage.lead_to_db(lead)
    payload["whatsapp_valido"] = None
    payload["mensagem_enviada"] = None
    payload["ultima_acao"] = None
    return payload


def insert_batch(client, payloads: list[dict[str, Any]]) -> tuple[int, int]:
    if not payloads:
        return 0, 0

    inserted = 0
    errors = 0
    for start in range(0, len(payloads), BATCH_SIZE):
        batch = payloads[start : start + BATCH_SIZE]
        try:
            client.table("leads").insert(batch).execute()
            inserted += len(batch)
        except Exception as batch_error:
            print(f"Erro ao inserir lote de {len(batch)} leads: {batch_error}")
            for payload in batch:
                try:
                    client.table("leads").insert(payload).execute()
                    inserted += 1
                except Exception as lead_error:
                    errors += 1
                    print(
                        "Erro ao inserir lead "
                        f"{payload.get('nome') or payload.get('unique_key')}: {lead_error}"
                    )

    return inserted, errors


def register_import_summary(total_read: int, inserted: int, duplicates: int, errors: int, total_base: int) -> None:
    summary = {
        "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nicho": ORIGEM_RASPAGEM,
        "cidade": "",
        "limite": total_read,
        "avaliacoes_maximas": 0,
        "leads_encontrados": total_read,
        "novos_adicionados": inserted,
        "duplicados_ignorados": duplicates,
        "total_geral_base": total_base,
    }
    storage.save_raspagem(summary)
    storage.save_recent_action(
        {
            "lead_id": "",
            "acao": (
                "Importação inicial concluída: "
                f"lidos={total_read}; inseridos={inserted}; "
                f"duplicados_ignorados={duplicates}; erros={errors}"
            ),
            "before": {},
            "after": {
                "origem_raspagem": ORIGEM_RASPAGEM,
                "total_lido": total_read,
                "total_inserido": inserted,
                "total_duplicado_ignorado": duplicates,
                "total_com_erro": errors,
                "total_atual_leads": total_base,
            },
        }
    )


def import_initial_list(path: Path) -> dict[str, int]:
    if storage.get_storage() != "supabase":
        raise RuntimeError("Defina STORAGE_MODE=supabase antes de importar.")
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")

    raw_rows, detected_columns = read_rows(path)
    if not detected_columns:
        raise RuntimeError("Nenhuma coluna conhecida foi identificada na planilha.")

    print("Colunas identificadas:")
    for canonical, source in sorted(detected_columns.items()):
        print(f"- {canonical}: {source}")

    client = storage.supabase_client()
    existing_index = load_existing_index(client)
    payloads = []
    duplicates = 0
    transform_errors = 0

    for row in raw_rows:
        try:
            normalized = normalize_row(row)
            keys = ordered_duplicate_keys_from_lead(normalized)
            if not keys:
                duplicates += 1
                continue
            if any(key in existing_index for key in keys):
                duplicates += 1
                continue

            payloads.append(lead_to_db_with_empty_operational_fields(normalized))
            for key in keys:
                existing_index[key] = normalized["Lead ID"]
        except Exception as exc:
            transform_errors += 1
            print(f"Erro ao processar linha: {exc}")

    inserted, insert_errors = insert_batch(client, payloads)
    errors = transform_errors + insert_errors
    total_base = storage.count_leads()
    register_import_summary(len(raw_rows), inserted, duplicates, errors, total_base)

    return {
        "total_lido": len(raw_rows),
        "inseridos": inserted,
        "duplicados": duplicates,
        "erros": errors,
        "total_atual_leads": total_base,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Importa a lista inicial XLSX para public.leads no Supabase."
    )
    parser.add_argument("arquivo", help="Caminho do arquivo XLSX a importar.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = import_initial_list(Path(args.arquivo).expanduser())
    print("")
    print("Resumo da importacao inicial")
    print(f"Total lido: {summary['total_lido']}")
    print(f"Inseridos: {summary['inseridos']}")
    print(f"Duplicados/ignorados: {summary['duplicados']}")
    print(f"Erros: {summary['erros']}")
    print(f"Total atual em public.leads: {summary['total_atual_leads']}")


if __name__ == "__main__":
    main()
