from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
import os
from time import perf_counter
from datetime import datetime
from uuid import uuid4

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

from src import storage as storage_backend

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

from src.main import (
    MAX_COMPANIES,
    buscar_leads,
    get_active_export_path,
    get_history_export_path,
    get_pipeline_leads,
    get_next_pipeline_lead,
    load_scrapes_history,
    import_feedback_spreadsheet,
    burn_pipeline_lead,
    mark_pipeline_pending,
    reset_lead_base,
    save_feedback,
    skip_pipeline_lead,
    undo_last_pipeline_action,
)


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "leadmaps-local-dev")


STATUS_OPTIONS = ["TODOS", "NOVO", "SUCESSO", "BURN"]


@app.before_request
def require_optional_password():
    """Protege o painel apenas se APP_ACCESS_PASSWORD estiver definida."""
    password = os.getenv("APP_ACCESS_PASSWORD", "").strip()

    if not password:
        return None

    allowed_endpoints = {"debug_playwright", "login", "static"}

    if request.endpoint in allowed_endpoints or session.get("authenticated"):
        return None

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""

    if request.method == "POST":
        if request.form.get("password", "") == os.getenv("APP_ACCESS_PASSWORD", ""):
            session["authenticated"] = True
            return redirect(url_for("index"))

        error = "Senha incorreta."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def default_form() -> dict:
    """Valores padrao do formulario de busca."""
    return {
        "nicho": "",
        "cidade": "",
        "limite": MAX_COMPANIES,
        "avaliacoes_maximas": "",
        "apenas_com_telefone": True,
        "apenas_sem_site": True,
    }


def default_stats(total_base: int = 0) -> dict:
    """Valores padrao dos cards de raspagem."""
    leads_com_telefone = 0
    leads_sem_site = 0

    if storage_backend.get_storage() == "supabase":
        total_base = storage_backend.count_leads()
        leads_com_telefone = storage_backend.count_leads_with_phone()
        leads_sem_site = storage_backend.count_leads_without_site()

    return {
        "empresas_analisadas": 0,
        "leads_encontrados": 0,
        "leads_raspagem": 0,
        "novos_adicionados": 0,
        "duplicados_ignorados": 0,
        "total_geral_base": total_base,
        "leads_com_telefone": leads_com_telefone,
        "leads_sem_site": leads_sem_site,
        "descartados_sem_telefone": 0,
        "descartados_possui_site": 0,
        "descartados_avaliacoes": 0,
        "descartados_cidade": 0,
        "total_descartado": 0,
        "arquivo_excel": "",
        "mensagem_exportacao": "",
    }


def lead_payload(lead: dict | None) -> dict | None:
    """Prepara lead da esteira para JSON."""
    if not lead:
        return None

    fields = [
        "Lead ID",
        "Nome",
        "Telefone",
        "Cidade",
        "Endereco",
        "Site",
        "Nota",
        "Quantidade de avaliacoes",
        "Oportunidade",
        "Status abordagem",
        "WhatsApp",
    ]
    return {field: lead.get(field, "") for field in fields}


def format_elapsed(seconds: float) -> str:
    """Formata duracao como 2m 14s ou 14s."""
    total_seconds = int(round(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)

    if minutes:
        return f"{minutes}m {remaining_seconds}s"

    return f"{remaining_seconds}s"


def parse_limit(value: str) -> int:
    """Converte o limite do formulario para um numero simples e seguro."""
    if not value or not value.isdigit():
        return MAX_COMPANIES

    return max(1, int(value))


def parse_optional_int(value: str) -> int | None:
    """Converte um campo numerico opcional."""
    if not value or not value.isdigit():
        return None

    return max(0, int(value))


def parse_page(value: str) -> int:
    if not value or not value.isdigit():
        return 1
    return max(1, int(value))


def pagination_for(status_filter: str, approach_stats: dict, page: int) -> dict:
    status_totals = {
        "TODOS": approach_stats["total_leads"],
        "NOVO": approach_stats["novos"],
        "SUCESSO": approach_stats["sucesso"],
        "BURN": approach_stats["burn"],
    }
    total_items = int(status_totals.get(status_filter, approach_stats["novos"]) or 0)
    page_size = storage_backend.PAGE_SIZE
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    return {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "start": ((page - 1) * page_size) + 1 if total_items else 0,
        "end": min(page * page_size, total_items),
        "has_previous": page > 1,
        "has_next": page < total_pages,
    }


@app.route("/debug-playwright")
def debug_playwright():
    browsers_path = Path("/opt/render/project/.playwright-browsers")

    try:
        playwright_version = version("playwright")
    except PackageNotFoundError:
        playwright_version = None

    folders = []
    if browsers_path.exists():
        folders = [
            path.name
            for path in sorted(browsers_path.iterdir())
            if path.is_dir()
        ][:10]

    return jsonify(
        {
            "playwright_version": playwright_version,
            "playwright_browsers_path": os.getenv("PLAYWRIGHT_BROWSERS_PATH"),
            "render_browsers_path": str(browsers_path),
            "render_browsers_path_exists": browsers_path.exists(),
            "render_browsers_path_folders": folders,
        }
    )


@app.route("/", methods=["GET", "POST"])
def index():
    status_filter = request.args.get("status", "NOVO").upper()
    page = parse_page(request.args.get("page", "1"))
    queue_filter = "NOVO"
    leads, approach_stats = get_pipeline_leads(status_filter, page, storage_backend.PAGE_SIZE)
    pagination = pagination_for(status_filter, approach_stats, page)
    if pagination["page"] != page:
        page = pagination["page"]
        leads, approach_stats = get_pipeline_leads(status_filter, page, storage_backend.PAGE_SIZE)
        pagination = pagination_for(status_filter, approach_stats, page)
    pipeline_lead = get_next_pipeline_lead(status_filter=queue_filter)
    error = ""
    searched = bool(approach_stats["total_leads"])
    elapsed_time = ""
    last_search_at = ""
    stats = default_stats(approach_stats["total_leads"])
    import_summary = None
    form = default_form()

    if request.method == "POST":
        searched = True
        form["nicho"] = request.form.get("nicho", "").strip()
        form["cidade"] = request.form.get("cidade", "").strip()
        form["limite"] = parse_limit(request.form.get("limite", str(MAX_COMPANIES)))
        form["avaliacoes_maximas"] = request.form.get("avaliacoes_maximas", "").strip()
        form["apenas_com_telefone"] = "apenas_com_telefone" in request.form
        form["apenas_sem_site"] = "apenas_sem_site" in request.form

        if not form["nicho"] or not form["cidade"]:
            error = "Informe o nicho e a cidade."
        else:
            try:
                started_at = perf_counter()
                leads, stats = buscar_leads(
                    form["nicho"],
                    form["cidade"],
                    form["limite"],
                    form["apenas_com_telefone"],
                    form["apenas_sem_site"],
                    parse_optional_int(form["avaliacoes_maximas"]),
                    True,
                    True,
                )
                status_filter = "NOVO"
                page = 1
                leads, approach_stats = get_pipeline_leads(status_filter, page, storage_backend.PAGE_SIZE)
                pagination = pagination_for(status_filter, approach_stats, page)
                queue_filter = "NOVO"
                pipeline_lead = get_next_pipeline_lead(status_filter=queue_filter)
                elapsed_time = format_elapsed(perf_counter() - started_at)
                last_search_at = datetime.now().strftime("%d/%m/%Y %H:%M")
            except Exception as exc:
                error = f"Erro ao buscar leads: {exc}"

    return render_template(
        "index.html",
        leads=leads,
        error=error,
        form=form,
        searched=searched,
        stats=stats,
        elapsed_time=elapsed_time,
        last_search_at=last_search_at,
        approach_stats=approach_stats,
        pipeline_lead=pipeline_lead,
        queue_filter=queue_filter,
        status_filter=status_filter,
        status_options=STATUS_OPTIONS,
        scrapes_history=list(reversed(load_scrapes_history()[-5:])),
        import_summary=import_summary,
        reset_summary=None,
        pagination=pagination,
        last_updated_at=datetime.now().strftime("%H:%M:%S"),
    )


@app.route("/importar-planilha", methods=["POST"])
def importar_planilha():
    file = request.files.get("planilha")

    if not file or not file.filename.lower().endswith(".xlsx"):
        leads, approach_stats = get_pipeline_leads("NOVO")
        return render_template(
            "index.html",
            leads=leads,
            error="Envie um arquivo .xlsx valido.",
            form=default_form(),
            searched=bool(approach_stats["total_leads"]),
            stats=default_stats(approach_stats["total_leads"]),
            elapsed_time="",
            last_search_at="",
            approach_stats=approach_stats,
            pipeline_lead=get_next_pipeline_lead(),
            queue_filter="NOVO",
            status_filter="NOVO",
            status_options=STATUS_OPTIONS,
            scrapes_history=list(reversed(load_scrapes_history()[-5:])),
            import_summary=None,
            reset_summary=None,
        )

    upload_dir = Path("exports/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"{uuid4().hex}.xlsx"
    file.save(upload_path)

    try:
        import_summary = import_feedback_spreadsheet(upload_path)
    except Exception as exc:
        leads, approach_stats = get_pipeline_leads("NOVO")
        return render_template(
            "index.html",
            leads=leads,
            error=f"Erro ao importar planilha: {exc}",
            form=default_form(),
            searched=bool(approach_stats["total_leads"]),
            stats=default_stats(approach_stats["total_leads"]),
            elapsed_time="",
            last_search_at="",
            approach_stats=approach_stats,
            pipeline_lead=get_next_pipeline_lead(),
            queue_filter="NOVO",
            status_filter="NOVO",
            status_options=STATUS_OPTIONS,
            scrapes_history=list(reversed(load_scrapes_history()[-5:])),
            import_summary=None,
            reset_summary=None,
        )

    leads, approach_stats = get_pipeline_leads("NOVO")

    return render_template(
        "index.html",
        leads=leads,
        error="",
        form=default_form(),
        searched=bool(approach_stats["total_leads"]),
        stats=default_stats(approach_stats["total_leads"]),
        elapsed_time="",
        last_search_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        approach_stats=approach_stats,
        pipeline_lead=get_next_pipeline_lead(),
        queue_filter="NOVO",
        status_filter="NOVO",
        status_options=STATUS_OPTIONS,
        scrapes_history=list(reversed(load_scrapes_history()[-5:])),
        import_summary=import_summary,
        reset_summary=None,
    )


@app.route("/resetar-base", methods=["POST"])
def resetar_base():
    reset_summary = reset_lead_base()
    leads, approach_stats = get_pipeline_leads("NOVO")

    return render_template(
        "index.html",
        leads=leads,
        error="",
        form=default_form(),
        searched=False,
        stats=default_stats(0),
        elapsed_time="",
        last_search_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        approach_stats=approach_stats,
        pipeline_lead=get_next_pipeline_lead(),
        queue_filter="NOVO",
        status_filter="NOVO",
        status_options=STATUS_OPTIONS,
        scrapes_history=list(reversed(load_scrapes_history()[-5:])),
        import_summary=None,
        reset_summary=reset_summary,
    )


@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json(silent=True) or request.form
    lead_id = data.get("lead_id", "").strip()

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    _, approach_stats = save_feedback(
        lead_id,
        {
            "status": data.get("status", "SUCESSO"),
            "whatsapp_valido": data.get("whatsapp_valido", ""),
            "mensagem_enviada": data.get("mensagem_enviada", ""),
            "observacao": data.get("observacao", ""),
        },
    )

    return jsonify({"ok": True, "approach_stats": approach_stats})


@app.route("/esteira/abrir-whatsapp", methods=["POST"])
def esteira_abrir_whatsapp():
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id", "").strip()

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    _, approach_stats = mark_pipeline_pending(lead_id)
    return jsonify({"ok": True, "approach_stats": approach_stats})


@app.route("/esteira/pular", methods=["POST"])
def esteira_pular():
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id", "").strip()
    queue_filter = data.get("fila", "NOVO").upper()

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    next_lead, approach_stats = skip_pipeline_lead(lead_id, queue_filter)
    return jsonify({
        "ok": True,
        "next_lead": lead_payload(next_lead),
        "approach_stats": approach_stats,
    })


@app.route("/esteira/burn", methods=["POST"])
def esteira_burn():
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id", "").strip()

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    next_lead, approach_stats = burn_pipeline_lead(lead_id)
    return jsonify({
        "ok": True,
        "next_lead": lead_payload(next_lead),
        "approach_stats": approach_stats,
    })


@app.route("/esteira/feedback", methods=["POST"])
def esteira_feedback():
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id", "").strip()

    if not lead_id:
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    whatsapp_valido = data.get("whatsapp_valido", "")
    mensagem_enviada = data.get("mensagem_enviada", "")
    final_status = "SUCESSO" if whatsapp_valido == "SIM" and mensagem_enviada == "SIM" else "BURN"

    save_feedback(
        lead_id,
        {
            "status": final_status,
            "whatsapp_valido": whatsapp_valido,
            "mensagem_enviada": mensagem_enviada,
            "observacao": data.get("observacao", ""),
            "ultima_acao": "Feedback salvo pela esteira",
        },
    )
    next_lead = get_next_pipeline_lead()
    _, approach_stats = get_pipeline_leads("TODOS")
    return jsonify({
        "ok": True,
        "next_lead": lead_payload(next_lead),
        "approach_stats": approach_stats,
    })


@app.route("/esteira/desfazer", methods=["POST"])
def esteira_desfazer():
    next_lead, approach_stats, message = undo_last_pipeline_action()
    return jsonify({
        "ok": True,
        "message": message,
        "next_lead": lead_payload(next_lead),
        "approach_stats": approach_stats,
    })


@app.route("/download")
def download():
    path = Path(get_active_export_path())

    if not path.exists():
        return "Nenhuma planilha foi gerada ainda.", 404

    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/download-historico")
def download_historico():
    path = Path(get_history_export_path())

    if not path.exists():
        return "Nenhuma planilha foi gerada ainda.", 404

    return send_file(path, as_attachment=True, download_name=path.name)


if __name__ == "__main__":
    app.run(debug=True)
