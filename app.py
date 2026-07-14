from pathlib import Path
from importlib.metadata import PackageNotFoundError, version
import os
from time import perf_counter
from datetime import datetime
from uuid import uuid4

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from src import base_replacement
from src import storage as storage_backend

try:
    import httpx
except ImportError:
    httpx = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
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
load_dotenv(".env.local", override=True)

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "leadmaps-local-dev")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024


EXTERNAL_SERVICE_ERROR_MESSAGE = (
    "Nao foi possivel carregar os dados do Supabase agora. "
    "A pagina continua disponivel; tente novamente em instantes."
)


STATUS_OPTIONS = ["NOVO"]
APP_ROOT = Path(__file__).resolve().parent
REPLACEMENT_UPLOAD_DIR = Path("exports/substituicao_uploads")


def public_error_message(exc: Exception) -> str:
    if isinstance(exc, (FileNotFoundError, ValueError, RuntimeError)):
        return str(exc)
    return "Nao foi possivel concluir a operacao. Consulte os logs da aplicacao."


def safe_current_leads_count() -> int | None:
    try:
        return storage_backend.count_leads()
    except Exception:
        app.logger.exception("Nao foi possivel contar leads atuais durante previa de substituicao.")
        return None


def resolve_internal_spreadsheet(value: str) -> Path:
    if not value:
        raise RuntimeError("Selecione uma planilha interna.")
    candidate = (APP_ROOT / value).resolve()
    if APP_ROOT not in candidate.parents or candidate == APP_ROOT:
        raise RuntimeError("Planilha interna invalida.")
    if candidate.suffix.lower() not in {".xlsx", ".xls"}:
        raise RuntimeError("Planilha interna invalida.")
    if not candidate.exists():
        raise RuntimeError("Planilha interna nao encontrada.")
    return candidate


def discover_internal_spreadsheets() -> list[dict[str, str]]:
    spreadsheets = []
    for path in sorted([*APP_ROOT.glob("*.xlsx"), *APP_ROOT.glob("*.xls")]):
        try:
            plan = base_replacement.plan_from_path(path)
        except Exception:
            continue
        required = {"nome", "telefone", "cidade", "estado", "categoria", "avaliacoes", "website", "link_google_maps", "whatsapp_link"}
        if required.issubset(set(plan.detected_columns)):
            spreadsheets.append(
                {
                    "name": path.name,
                    "relative_path": path.relative_to(APP_ROOT).as_posix(),
                    "size": path.stat().st_size,
                }
            )
    return spreadsheets


def remove_replacement_upload(path_value: str | None) -> None:
    if not path_value:
        return
    try:
        path = Path(path_value).resolve()
        upload_dir = REPLACEMENT_UPLOAD_DIR.resolve()
        if upload_dir in path.parents and path.exists():
            path.unlink()
    except OSError:
        app.logger.warning("Nao foi possivel remover upload temporario de substituicao.")


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


def empty_dashboard_snapshot() -> dict:
    return {
        "approach_stats": storage_backend.empty_dashboard_stats(),
        "next_lead": None,
        "scrapes_history": [],
        "elapsed_seconds": 0,
    }


def safe_dashboard_snapshot(include_history: bool = True) -> tuple[dict, str]:
    try:
        return storage_backend.get_dashboard_snapshot(include_history=include_history), ""
    except Exception as exc:
        if is_external_service_error(exc):
            app.logger.exception("Falha ao carregar dashboard via Supabase/httpx.")
            return empty_dashboard_snapshot(), EXTERNAL_SERVICE_ERROR_MESSAGE

        app.logger.exception("Falha inesperada ao carregar dashboard.")
        return empty_dashboard_snapshot(), "Nao foi possivel carregar o painel agora."


def is_external_service_error(exc: Exception) -> bool:
    if isinstance(exc, (RuntimeError, OSError)):
        return True

    if httpx is not None and isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.HTTPError,
        ),
    ):
        return True

    return False


def stats_from_dashboard(approach_stats: dict) -> dict:
    return {
        "empresas_analisadas": 0,
        "leads_encontrados": 0,
        "leads_raspagem": 0,
        "novos_adicionados": 0,
        "duplicados_ignorados": 0,
        "total_geral_base": approach_stats.get("total_leads", 0),
        "leads_com_telefone": approach_stats.get("leads_com_telefone", 0),
        "leads_sem_site": approach_stats.get("leads_sem_site", 0),
        "descartados_sem_telefone": 0,
        "descartados_possui_site": 0,
        "descartados_avaliacoes": 0,
        "descartados_cidade": 0,
        "total_descartado": 0,
        "arquivo_excel": "",
        "mensagem_exportacao": "",
    }


def dashboard_context(
    *,
    error: str = "",
    searched: bool | None = None,
    stats: dict | None = None,
    import_summary: dict | None = None,
    reset_summary: dict | None = None,
    replacement_preview: dict | None = None,
    replacement_result: dict | None = None,
    replacement_error: str = "",
    replacement_report: str = "",
) -> dict:
    dashboard_snapshot, dashboard_error = safe_dashboard_snapshot()
    approach_stats = dashboard_snapshot["approach_stats"]
    final_error = error or dashboard_error

    return {
        "leads": [],
        "error": final_error,
        "form": default_form(),
        "searched": bool(approach_stats["total_leads"]) if searched is None else searched,
        "stats": stats or stats_from_dashboard(approach_stats),
        "elapsed_time": "",
        "last_search_at": "",
        "approach_stats": approach_stats,
        "pipeline_lead": dashboard_snapshot["next_lead"],
        "queue_filter": "NOVO",
        "status_filter": "NOVO",
        "status_options": STATUS_OPTIONS,
        "scrapes_history": list(reversed(dashboard_snapshot["scrapes_history"])),
        "import_summary": import_summary,
        "reset_summary": reset_summary,
        "replacement_preview": replacement_preview,
        "replacement_result": replacement_result,
        "replacement_error": replacement_error,
        "replacement_report": replacement_report,
        "replacement_internal_files": discover_internal_spreadsheets(),
        "pagination": {
            "page": 1,
            "page_size": 0,
            "total_items": 0,
            "total_pages": 1,
            "start": 0,
            "end": 0,
            "has_previous": False,
            "has_next": False,
        },
        "last_updated_at": datetime.now().strftime("%H:%M:%S"),
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
        "ERRO": approach_stats["burn"],
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
    status_filter = "NOVO"
    queue_filter = "NOVO"
    dashboard_snapshot, dashboard_error = safe_dashboard_snapshot()
    leads = []
    approach_stats = dashboard_snapshot["approach_stats"]
    pagination = {
        "page": 1,
        "page_size": 0,
        "total_items": 0,
        "total_pages": 1,
        "start": 0,
        "end": 0,
        "has_previous": False,
        "has_next": False,
    }
    pipeline_lead = dashboard_snapshot["next_lead"]
    scrapes_history = list(reversed(dashboard_snapshot["scrapes_history"]))
    error = dashboard_error
    searched = bool(approach_stats["total_leads"])
    elapsed_time = ""
    last_search_at = ""
    stats = stats_from_dashboard(approach_stats)
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
                dashboard_snapshot, dashboard_error = safe_dashboard_snapshot()
                leads = []
                approach_stats = dashboard_snapshot["approach_stats"]
                pagination = {
                    "page": 1,
                    "page_size": 0,
                    "total_items": 0,
                    "total_pages": 1,
                    "start": 0,
                    "end": 0,
                    "has_previous": False,
                    "has_next": False,
                }
                queue_filter = "NOVO"
                pipeline_lead = dashboard_snapshot["next_lead"]
                scrapes_history = list(reversed(dashboard_snapshot["scrapes_history"]))
                if dashboard_error:
                    error = dashboard_error
                elapsed_time = format_elapsed(perf_counter() - started_at)
                last_search_at = datetime.now().strftime("%d/%m/%Y %H:%M")
            except Exception as exc:
                app.logger.exception("Erro ao buscar leads.")
                error = "Erro ao buscar leads. Consulte os logs da aplicacao."

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
        scrapes_history=scrapes_history,
        import_summary=import_summary,
        reset_summary=None,
        replacement_preview=None,
        replacement_result=None,
        replacement_error="",
        replacement_report="",
        pagination=pagination,
        last_updated_at=datetime.now().strftime("%H:%M:%S"),
    )


@app.route("/importar-planilha", methods=["POST"])
def importar_planilha():
    leads, approach_stats = get_pipeline_leads("NOVO")

    return render_template(
        "index.html",
        leads=leads,
        error="Importacao por Excel desativada. Fonte oficial: Supabase public.leads.",
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
        import_summary=None,
        reset_summary=None,
        replacement_preview=None,
        replacement_result=None,
        replacement_error="",
        replacement_report="",
    )


@app.route("/resetar-base", methods=["POST"])
def resetar_base():
    try:
        reset_summary = reset_lead_base(request.form.get("confirmacao", ""))
    except Exception as exc:
        reset_summary = {
            "backup": "",
            "message": f"Reset nao executado: {exc}",
        }
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
        replacement_preview=None,
        replacement_result=None,
        replacement_error="",
        replacement_report="",
    )


@app.route("/substituir-base", methods=["POST"])
def substituir_base():
    action = request.form.get("action", "preview")
    replacement_error = ""
    replacement_preview = None
    replacement_result = None
    replacement_report = ""

    try:
        if action == "preview":
            remove_replacement_upload(session.get("replacement_upload_path"))
            session.pop("replacement_upload_path", None)
            upload = request.files.get("planilha_substituicao")
            if not upload or not upload.filename:
                raise RuntimeError("Selecione um arquivo Excel para gerar a previa.")

            filename = secure_filename(upload.filename)
            app.logger.info(
                "Inicio da previa por upload: arquivo=%s extensao=%s content_length=%s",
                filename,
                Path(filename).suffix.lower(),
                request.content_length,
            )
            if not filename.lower().endswith((".xlsx", ".xls")):
                raise RuntimeError("Arquivo invalido. Envie uma planilha .xlsx ou .xls.")

            REPLACEMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            saved_path = REPLACEMENT_UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex}_{filename}"
            try:
                upload.save(saved_path)
                plan = base_replacement.plan_from_path(saved_path)
            except Exception:
                remove_replacement_upload(str(saved_path))
                raise
            current_leads = safe_current_leads_count()
            replacement_preview = plan.summary(current_leads=current_leads)
            session["replacement_upload_path"] = str(saved_path)
            app.logger.info(
                "Previa de substituicao gerada: arquivo=%s total=%s validos=%s rejeitados=%s",
                filename,
                plan.total_rows,
                plan.valid_count,
                plan.rejected_count,
            )
        elif action == "preview_internal":
            remove_replacement_upload(session.get("replacement_upload_path"))
            session.pop("replacement_upload_path", None)
            internal_path = resolve_internal_spreadsheet(request.form.get("internal_spreadsheet", ""))
            app.logger.info(
                "Inicio da previa por planilha interna: arquivo=%s tamanho=%s",
                internal_path.name,
                internal_path.stat().st_size,
            )
            plan = base_replacement.plan_from_path(internal_path)
            current_leads = safe_current_leads_count()
            replacement_preview = plan.summary(current_leads=current_leads)
            session["replacement_upload_path"] = str(internal_path)
            app.logger.info(
                "Previa interna gerada: arquivo=%s total=%s validos=%s rejeitados=%s",
                internal_path.name,
                plan.total_rows,
                plan.valid_count,
                plan.rejected_count,
            )
        elif action == "confirm":
            confirm_phrase = request.form.get("confirmacao_substituicao", "").strip()
            saved_path = Path(session.get("replacement_upload_path", ""))
            if not saved_path.exists():
                raise RuntimeError("Arquivo da previa nao encontrado. Gere a previa novamente.")

            plan = base_replacement.plan_from_path(saved_path)
            result = base_replacement.execute_replacement(plan, confirm_phrase)
            replacement_result = result
            if result.get("rejected_report"):
                replacement_report = Path(result["rejected_report"]).name
            remove_replacement_upload(str(saved_path))
            session.pop("replacement_upload_path", None)
            app.logger.info(
                "Substituicao executada pela interface: inseridos=%s rejeitados=%s",
                result.get("inserted"),
                plan.rejected_count,
            )
        else:
            raise RuntimeError("Acao invalida.")
    except Exception as exc:
        app.logger.exception(
            "Falha na rotina de substituicao de base: tipo=%s mensagem=%s etapa=%s",
            type(exc).__name__,
            str(exc),
            action,
        )
        replacement_error = public_error_message(exc)

    return render_template(
        "index.html",
        **dashboard_context(
            replacement_preview=replacement_preview,
            replacement_result=replacement_result,
            replacement_error=replacement_error,
            replacement_report=replacement_report,
        ),
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

    current_lead, approach_stats = mark_pipeline_pending(lead_id)
    return jsonify({
        "ok": True,
        "lead": lead_payload(current_lead),
        "approach_stats": approach_stats,
    })


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
    started_at = perf_counter()
    data = request.get_json(silent=True) or {}
    print("Payload recebido na esteira/feedback:", dict(data), flush=True)
    app.logger.info("Feedback da esteira recebido: %s", dict(data))
    lead_id = (data.get("lead_id") or data.get("unique_key") or "").strip()

    if not lead_id:
        app.logger.warning("Feedback da esteira sem lead_id: %s", dict(data))
        return jsonify({"ok": False, "error": "Lead nao encontrado."}), 400

    resultado = data.get("resultado", "")
    if resultado == "mensagem_enviada":
        whatsapp_valido = "SIM"
        mensagem_enviada = "SIM"
    elif resultado == "whatsapp_invalido":
        whatsapp_valido = "NAO"
        mensagem_enviada = "NAO"
    else:
        whatsapp_valido = data.get("whatsapp_valido", "")
        mensagem_enviada = data.get("mensagem_enviada", "")

    final_status = "SUCESSO" if whatsapp_valido == "SIM" and mensagem_enviada == "SIM" else "ERRO"
    ultima_acao = (
        "WhatsApp enviado"
        if final_status == "SUCESSO"
        else "WhatsApp inválido"
    )

    try:
        save_feedback(
            lead_id,
            {
                "status": final_status,
                "whatsapp_valido": whatsapp_valido,
                "mensagem_enviada": mensagem_enviada,
                "observacao": data.get("observacao", ""),
                "ultima_acao": ultima_acao,
                "calculate_stats": False,
            },
        )
        print("Lead atualizado:", lead_id, final_status, flush=True)
        dashboard_snapshot, dashboard_error = safe_dashboard_snapshot(include_history=False)
        if dashboard_error:
            return jsonify({"ok": False, "error": dashboard_error}), 503
        next_lead = dashboard_snapshot["next_lead"]
        print("Próximo lead carregado:", (next_lead or {}).get("Lead ID", ""), flush=True)
        approach_stats = dashboard_snapshot["approach_stats"]
    except Exception as exc:
        app.logger.exception("Erro ao salvar feedback da esteira para %s", lead_id)
        return jsonify({"ok": False, "error": str(exc)}), 500

    app.logger.info(
        "Feedback da esteira salvo: lead_id=%s status=%s whatsapp_valido=%s mensagem_enviada=%s",
        lead_id,
        final_status,
        whatsapp_valido,
        mensagem_enviada,
    )
    print(f"Tempo salvar feedback: {perf_counter() - started_at:.3f}s", flush=True)
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


@app.route("/download-rejeitados/<filename>")
def download_rejeitados(filename):
    safe_name = secure_filename(filename)
    path = (base_replacement.REJECTED_DIR / safe_name).resolve()
    rejected_dir = base_replacement.REJECTED_DIR.resolve()

    if rejected_dir not in path.parents or not path.exists():
        return "Relatorio de rejeitados nao encontrado.", 404

    return send_file(path, as_attachment=True, download_name=path.name)


if __name__ == "__main__":
    app.run(debug=True)
