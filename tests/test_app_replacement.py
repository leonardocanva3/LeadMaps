from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import app as leadmaps_app
from src import storage


def make_xlsx_bytes(rows: list[dict]) -> BytesIO:
    output = BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False)
    output.seek(0)
    return output


def fake_dashboard_snapshot(include_history: bool = True):
    return {
        "approach_stats": storage.empty_dashboard_stats(),
        "next_lead": None,
        "scrapes_history": [],
        "elapsed_seconds": 0,
    }, ""


class ReplacementRouteTest(unittest.TestCase):
    def setUp(self):
        leadmaps_app.app.config["TESTING"] = True
        self.client = leadmaps_app.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def post_with_patches(self, data, content_type=None):
        with patch("app.safe_dashboard_snapshot", side_effect=fake_dashboard_snapshot), \
            patch("app.safe_current_leads_count", return_value=0), \
            patch("app.discover_internal_spreadsheets", return_value=[]):
            return self.client.post("/substituir-base", data=data, content_type=content_type)

    def test_authenticated_page_loads(self):
        with patch("app.safe_dashboard_snapshot", side_effect=fake_dashboard_snapshot), \
            patch("app.discover_internal_spreadsheets", return_value=[]):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Substituir Base de Leads", response.data)

    def test_preview_upload(self):
        response = self.post_with_patches(
            {
                "action": "preview",
                "planilha_substituicao": (
                    make_xlsx_bytes([
                        {
                            "nome": "Oficina A",
                            "telefone": "65999998888",
                            "cidade": "Cuiaba",
                            "estado": "MT",
                            "categoria": "Oficina",
                            "avaliações": 1,
                            "website": "",
                            "link_google_maps": "",
                            "whatsapp_link": "",
                        }
                    ]),
                    "leads.xlsx",
                ),
            },
            "multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Leads validos", response.data)
        self.assertIn(b"Unique keys duplicadas", response.data)

    def test_preview_internal_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", dir=leadmaps_app.APP_ROOT, delete=False)
        tmp.close()
        path = Path(tmp.name)
        try:
            pd.DataFrame([{"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"}]).to_excel(path, index=False)
            response = self.post_with_patches(
                {"action": "preview_internal", "internal_spreadsheet": path.name}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Previa da substituicao", response.data)
        finally:
            path.unlink(missing_ok=True)

    def test_confirm_without_phrase_does_not_execute(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        path = Path(tmp.name)
        pd.DataFrame([{"nome": "A", "telefone": "65999998888", "cidade": "Cuiaba"}]).to_excel(path, index=False)
        try:
            with self.client.session_transaction() as session:
                session["replacement_upload_path"] = str(path)
            response = self.post_with_patches({"action": "confirm", "confirmacao_substituicao": ""})
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Confirmacao invalida", response.data)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_extension(self):
        response = self.post_with_patches(
            {
                "action": "preview",
                "planilha_substituicao": (BytesIO(b"nope"), "leads.txt"),
            },
            "multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Arquivo invalido", response.data)

    def test_missing_file(self):
        response = self.post_with_patches({"action": "preview"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Selecione um arquivo Excel", response.data)


if __name__ == "__main__":
    unittest.main()
