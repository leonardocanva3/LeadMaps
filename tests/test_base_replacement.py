from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import base_replacement


class Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.action = "select"
        self.rows = None

    def select(self, *args, **kwargs):
        self.action = "select"
        return self

    def range(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        self.action = "delete"
        return self

    def neq(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def insert(self, rows):
        self.action = "insert"
        self.rows = rows if isinstance(rows, list) else [rows]
        return self

    def execute(self):
        table = self.client.tables.setdefault(self.table_name, [])
        if self.action == "delete":
            count = len(table)
            self.client.tables[self.table_name] = []
            return Result(data=[], count=count)
        if self.action == "insert":
            if self.client.fail_lead_insert and self.table_name == "leads":
                self.client.fail_lead_insert = False
                raise RuntimeError("insert failure")
            table.extend(self.rows or [])
            return Result(data=self.rows or [], count=len(self.rows or []))
        return Result(data=list(table), count=len(table))


class FakeClient:
    def __init__(self, fail_lead_insert=False):
        self.fail_lead_insert = fail_lead_insert
        self.tables = {
            "leads": [{"id": "1", "unique_key": "old"}],
            "feedbacks": [{"id": "2", "lead_unique_key": "old"}],
            "acoes_recentes": [{"id": "3", "lead_unique_key": "old"}],
            "raspagens": [{"id": "4", "nicho": "old"}],
        }

    def table(self, table_name):
        return FakeQuery(self, table_name)


class BaseReplacementTest(unittest.TestCase):
    def make_xlsx(self, rows):
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        pd.DataFrame(rows).to_excel(tmp.name, index=False)
        return Path(tmp.name)

    def test_phone_with_country_code(self):
        national, international = base_replacement.normalize_phone("+55 (65) 99999-8888")
        self.assertEqual(national, "65999998888")
        self.assertEqual(international, "5565999998888")

    def test_phone_without_country_code(self):
        national, international = base_replacement.normalize_phone("(65) 99999-8888")
        self.assertEqual(national, "65999998888")
        self.assertEqual(international, "5565999998888")

    def test_reject_repeated_phone(self):
        with self.assertRaises(ValueError):
            base_replacement.normalize_phone("99999999999")

    def test_build_plan_valid_and_informative_duplicate(self):
        path = self.make_xlsx(
            [
                {
                    "nome": "Oficina A",
                    "telefone": "(65) 99999-8888",
                    "cidade": "Cuiaba",
                    "estado": "MT",
                    "categoria": "Oficina",
                    "avaliações": 12,
                    "website": "",
                    "link_google_maps": "https://maps.example/a",
                    "whatsapp_link": "errado",
                },
                {
                    "nome": "Oficina A",
                    "telefone": "(65) 98888-7777",
                    "cidade": "Cuiaba",
                    "estado": "MT",
                    "categoria": "Oficina",
                    "avaliações": 8,
                    "website": "site.com.br",
                    "link_google_maps": "https://maps.example/b",
                    "whatsapp_link": "",
                },
            ]
        )
        plan = base_replacement.plan_from_path(path)
        self.assertEqual(plan.valid_count, 2)
        self.assertEqual(plan.rejected_count, 0)
        self.assertEqual(len(plan.informative_duplicates), 1)
        self.assertEqual(plan.payloads[0]["status_abordagem"], "NOVO")
        self.assertEqual(plan.payloads[0]["whatsapp"], "https://wa.me/5565999998888")
        self.assertEqual(plan.payloads[0]["mensagem_enviada"], "NAO")

    def test_duplicate_phone_is_rejected(self):
        path = self.make_xlsx(
            [
                {"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"},
                {"nome": "B", "telefone": "+5565999998888", "cidade": "Cuiaba"},
            ]
        )
        plan = base_replacement.plan_from_path(path)
        self.assertEqual(plan.valid_count, 1)
        self.assertEqual(plan.rejected_count, 1)
        self.assertEqual(plan.duplicate_phone_rows, 1)

    def test_non_excel_is_rejected(self):
        path = Path(tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name)
        with self.assertRaises(ValueError):
            base_replacement.plan_from_path(path)

    def test_empty_spreadsheet_prepares_no_leads(self):
        path = self.make_xlsx([])
        plan = base_replacement.plan_from_path(path)
        self.assertEqual(plan.valid_count, 0)
        self.assertEqual(plan.prepared_count, 0)

    def test_real_mode_requires_confirmation(self):
        path = self.make_xlsx([{"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"}])
        plan = base_replacement.plan_from_path(path)
        with self.assertRaises(RuntimeError):
            base_replacement.execute_replacement(plan, "")

    def test_supabase_unavailable_blocks_before_delete(self):
        path = self.make_xlsx([{"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"}])
        plan = base_replacement.plan_from_path(path)
        with patch("src.base_replacement.storage.get_storage", return_value="supabase"), \
            patch("src.base_replacement.storage.supabase_client", side_effect=RuntimeError("supabase indisponivel")):
            with self.assertRaises(RuntimeError):
                base_replacement.execute_replacement(plan, base_replacement.CONFIRM_PHRASE)

    def test_rollback_restores_backup_when_insert_fails(self):
        path = self.make_xlsx([{"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"}])
        plan = base_replacement.plan_from_path(path)
        fake = FakeClient(fail_lead_insert=True)
        original = {name: list(rows) for name, rows in fake.tables.items()}

        with patch("src.base_replacement.storage.get_storage", return_value="supabase"), \
            patch("src.base_replacement.storage.supabase_client", return_value=fake), \
            patch("src.base_replacement.storage.count_leads", return_value=plan.prepared_count):
            with self.assertRaises(RuntimeError):
                base_replacement.execute_replacement(plan, base_replacement.CONFIRM_PHRASE)

        self.assertEqual(fake.tables, original)


if __name__ == "__main__":
    unittest.main()
