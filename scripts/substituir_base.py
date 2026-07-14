from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.base_replacement import CONFIRM_PHRASE, execute_replacement, plan_from_path


def print_summary(summary: dict) -> None:
    print("Resumo da substituicao de base")
    print(f"Arquivo: {summary['source_name']}")
    print(f"Aba: {summary['sheet_name']}")
    print(f"Total de linhas: {summary['total_rows']}")
    print(f"Leads validos: {summary['valid_leads']}")
    print(f"Leads invalidos: {summary['invalid_leads']}")
    print(f"Duplicados por telefone: {summary['duplicate_phone_rows']}")
    print(f"Duplicidades nome+cidade informativas: {summary['informative_duplicates']}")
    print(f"Websites vazios: {summary['website_empty']}")
    print(f"Websites invalidos: {summary['website_invalid']}")
    print(f"Estados diferentes de MT: {summary['states_not_mt']}")
    print(f"Preparados para importar: {summary['prepared_to_import']}")
    print(f"Unique keys geradas: {summary['unique_keys']}")
    print(f"Unique keys duplicadas: {summary['duplicate_unique_keys']}")
    if summary["missing_columns"]:
        print(f"Colunas obrigatorias ausentes: {', '.join(summary['missing_columns'])}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Substitui a base de leads no Supabase com validacao, backup e rollback."
    )
    parser.add_argument("arquivo", help="Caminho do arquivo .xlsx ou .xls.")
    parser.add_argument("--dry-run", action="store_true", help="Valida e resume sem alterar dados.")
    parser.add_argument("--confirmar", default="", help="Use SUBSTITUIR para executar a substituicao real.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.arquivo).expanduser()
    plan = plan_from_path(path)
    summary = plan.summary()
    print_summary(summary)

    if args.dry_run:
        print("")
        print("Dry-run concluido. Nenhum dado foi alterado.")
        return

    if args.confirmar != CONFIRM_PHRASE:
        print("")
        print("Modo real nao executado. Confirmacao obrigatoria: --confirmar SUBSTITUIR")
        raise SystemExit(2)

    result = execute_replacement(plan, args.confirmar)
    print("")
    print("Substituicao concluida")
    print(f"Backup: {result['backup_path']}")
    print(f"Inseridos: {result['inserted']}")
    print(f"Total final: {result['final_count']}")
    print(f"Relatorio de rejeitados: {result['rejected_report'] or '-'}")
    print(f"Tempo: {result['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
