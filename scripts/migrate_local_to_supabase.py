from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import storage


def migrate():
    if storage.get_storage() != "supabase":
        print("Defina STORAGE_MODE=supabase no .env antes de migrar.")
        return

    local_leads = storage.load_json(storage.MASTER_LEADS_PATH, [])
    local_feedbacks = storage.load_json(storage.FEEDBACKS_PATH, {})
    local_raspagens = storage.load_json(storage.SCRAPES_HISTORY_PATH, [])
    client = storage.supabase_client()
    inserted = 0
    updated = 0
    ignored = 0
    errors = 0

    for lead in local_leads:
        try:
            normalized = storage.normalize_master_lead(lead)
            existing = (
                client.table("leads")
                .select("unique_key,status_abordagem")
                .eq("unique_key", normalized["Lead ID"])
                .execute()
                .data
                or []
            )

            if existing:
                db_status = existing[0].get("status_abordagem", "NOVO")
                normalized["status_abordagem"] = storage.merge_status(
                    db_status,
                    normalized.get("status_abordagem", "NOVO"),
                )
                updated += 1
            else:
                inserted += 1

            client.table("leads").upsert(
                storage.lead_to_db(normalized),
                on_conflict="unique_key",
            ).execute()
        except Exception as exc:
            errors += 1
            print(f"Erro ao migrar lead: {lead.get('Nome', 'Sem nome')} | {exc}")

    for lead_unique_key, feedback in local_feedbacks.items():
        try:
            storage.add_feedback(lead_unique_key, feedback)
        except Exception as exc:
            errors += 1
            print(f"Erro ao migrar feedback {lead_unique_key}: {exc}")

    for raspagem in local_raspagens:
        try:
            storage.save_raspagem(raspagem)
        except Exception as exc:
            errors += 1
            print(f"Erro ao migrar raspagem: {exc}")

    print("Resumo da migracao")
    print(f"Total local: {len(local_leads)}")
    print(f"Inseridos: {inserted}")
    print(f"Atualizados: {updated}")
    print(f"Ignorados: {ignored}")
    print(f"Erros: {errors}")


if __name__ == "__main__":
    migrate()
