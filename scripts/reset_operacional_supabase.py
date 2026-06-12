from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import storage


CONFIRM_PHRASE = "LIMPAR BASE SUPABASE LEADMAPS"


def main() -> None:
    confirm_phrase = " ".join(sys.argv[1:]).strip()
    result = storage.reset_supabase_operational(confirm_phrase)

    print(result["message"])
    for table_name, count in result["deleted"].items():
        print(f"{table_name}: {count}")


if __name__ == "__main__":
    if " ".join(sys.argv[1:]).strip() != CONFIRM_PHRASE:
        print(f"Confirmacao obrigatoria: {CONFIRM_PHRASE}")
        raise SystemExit(2)

    main()
