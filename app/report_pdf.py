from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
import openai
from weasyprint import CSS, HTML

from app.config import get_assets_dir, get_settings, get_views_dir
from app.integrations.github import get_issue_title
from app.milestones import load_milestone_section
from app.github_projects import load_project_charts

logger = logging.getLogger(__name__)


def render_pdf(
    week_id: str,
    reports: list[dict],
    reports_by_team: dict[str, list[dict]],
    output_path: Path,
    period_label: str | None = None,
    project_slug: str | None = None,
    file_title: str | None = None,
    milestone_month: str | None = None,
    reports_by_project: dict[str, dict[str, list[dict]]] | None = None,
) -> Path:
    settings = get_settings()
    env = Environment(
        loader=FileSystemLoader(get_views_dir()),
        autoescape=select_autoescape(["html", "xml"]),
    )

    if settings.github_token:
        url_cache: dict[str, str] = {}
        for team_reports in reports_by_team.values():
            for report in team_reports:
                tasks = report.get("tasks") or []
                for task in tasks:
                    url = task.get("task_url")
                    if not url:
                        continue
                    if url not in url_cache:
                        title = get_issue_title(settings.github_token, url)
                        if title:
                            url_cache[url] = title
                    title = url_cache.get(url)
                    if title:
                        task["title"] = title
        if reports_by_project:
            for project_reports in reports_by_project.values():
                for team_reports in project_reports.values():
                    for report in team_reports:
                        tasks = report.get("tasks") or []
                        for task in tasks:
                            url = task.get("task_url")
                            if not url:
                                continue
                            if url not in url_cache:
                                title = get_issue_title(settings.github_token, url)
                                if title:
                                    url_cache[url] = title
                            title = url_cache.get(url)
                            if title:
                                task["title"] = title

    def _normalize_delivery_links(report: dict) -> None:
        links = report.get("deliveries_links")
        if not links:
            raw = report.get("deliveries_link")
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        links = [str(item) for item in parsed if item]
                    else:
                        links = [raw]
                except json.JSONDecodeError:
                    links = [raw]
        report["deliveries_links"] = links or []

    for report in reports:
        _normalize_delivery_links(report)
    if reports_by_project:
        for project_reports in reports_by_project.values():
            for team_reports in project_reports.values():
                for report in team_reports:
                    _normalize_delivery_links(report)

    svg_counter = 0

    def _bar_svg(value: float, max_value: float) -> str:
        nonlocal svg_counter
        svg_counter += 1
        suffix = f"-{svg_counter}"
        width = 260
        height = 76
        padding = 16
        bar_height = 12
        bar_width = width - padding * 2
        ratio = 0 if max_value <= 0 else max(0, min(1, value / max_value))
        fill_width = int(bar_width * ratio)
        marker_x = padding + fill_width

        return (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
            f"viewBox='0 0 {width} {height}'>"
            f"<defs>"
            f"<linearGradient id='track{suffix}' x1='0' y1='0' x2='1' y2='0'>"
            f"<stop offset='0%' stop-color='#eef5f2'/>"
            f"<stop offset='100%' stop-color='#f5faf8'/>"
            f"</linearGradient>"
            f"<linearGradient id='fill{suffix}' x1='0' y1='0' x2='1' y2='0'>"
            f"<stop offset='0%' stop-color='#3f8f7b'/>"
            f"<stop offset='100%' stop-color='#56b193'/>"
            f"</linearGradient>"
            f"</defs>"
            f"<rect x='0' y='0' width='{width}' height='{height}' rx='16' ry='16' fill='#f7fbf9'/>"
            f"<rect x='{padding}' y='40' width='{bar_width}' height='{bar_height}' rx='6' ry='6' fill='url(#track{suffix})'/>"
            f"<rect x='{padding}' y='40' width='{fill_width}' height='{bar_height}' rx='6' ry='6' fill='url(#fill{suffix})'/>"
            f"<circle cx='{marker_x}' cy='46' r='7' fill='#3f8f7b' stroke='#ffffff' stroke-width='3'/>"
            f"<line x1='{padding}' y1='60' x2='{width - padding}' y2='60' stroke='#e2ece7' stroke-width='1'/>"
            f"</svg>"
        )

    total_reports = len(reports)
    deliveries_count = sum(1 for report in reports if report.get("had_deliveries"))
    difficulties_count = sum(1 for report in reports if report.get("had_difficulties"))
    total_tasks = sum(len(report.get("tasks") or []) for report in reports)
    avg_self_assessment = (
        sum(float(report.get("self_assessment", 0)) for report in reports) / total_reports
        if total_reports
        else 0
    )
    avg_next_week = (
        sum(float(report.get("next_week_expectation", 0)) for report in reports) / total_reports
        if total_reports
        else 0
    )

    def _percent(value: float, total: float) -> int:
        if total <= 0:
            return 0
        return int(round((value / total) * 100))

    deliveries_percent = _percent(deliveries_count, max(total_reports, 1))
    difficulties_percent = _percent(difficulties_count, max(total_reports, 1))
    self_assessment_percent = _percent(avg_self_assessment, 5)
    next_week_percent = _percent(avg_next_week, 5)

    summary_charts = [
        {
            "title": "Entregas registradas",
            "value": f"{deliveries_count}",
            "subtext": f"de {total_reports} relatos",
            "pill": f"{deliveries_percent}%",
            "svg": _bar_svg(deliveries_count, max(total_reports, 1)),
        },
        {
            "title": "Autoavaliação de desempenho",
            "value": f"{avg_self_assessment:.1f}/5",
            "subtext": "média geral",
            "pill": f"{self_assessment_percent}%",
            "svg": _bar_svg(avg_self_assessment, 5),
        },
        {
            "title": "Confiança para próxima semana",
            "value": f"{avg_next_week:.1f}/5",
            "subtext": "expectativa média",
            "pill": f"{next_week_percent}%",
            "svg": _bar_svg(avg_next_week, 5),
        },
        {
            "title": "Dificuldades registradas",
            "value": f"{difficulties_count}",
            "subtext": f"de {total_reports} relatos",
            "pill": f"{difficulties_percent}%",
            "svg": _bar_svg(difficulties_count, max(total_reports, 1)),
        },
    ]

    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None

    week_start = None
    week_end = None
    try:
        year_str, week_str = week_id.split("-W")
        week_start = date.fromisocalendar(int(year_str), int(week_str), 1)
        week_end = date.fromisocalendar(int(year_str), int(week_str), 7)
    except Exception:
        week_start = None
        week_end = None

    summary_tasks_worked: list[dict[str, str]] = []
    summary_tasks_completed: list[dict[str, str]] = []
    summary_tasks_carryover: list[dict[str, str]] = []
    completed_durations: list[int] = []

    def _add_unique(target: list[dict[str, str]], url: str, title: str | None) -> None:
        if any(item["url"] == url for item in target):
            return
        target.append({"url": url, "title": title or url})

    for report in reports:
        for task in report.get("tasks") or []:
            url = task.get("task_url")
            if not url:
                continue
            start_date = _parse_date(task.get("start_date"))
            end_date = _parse_date(task.get("end_date"))
            title = task.get("title")

            if start_date and end_date:
                duration = (end_date - start_date).days + 1
                if duration >= 0:
                    completed_durations.append(duration)
                    task["days_spent"] = duration

            if week_start and week_end and start_date and week_start <= start_date <= week_end:
                _add_unique(summary_tasks_worked, url, title)
            if week_start and week_end and end_date and week_start <= end_date <= week_end:
                _add_unique(summary_tasks_completed, url, title)
            if week_start and start_date and start_date < week_start:
                if end_date is None or (week_start and end_date >= week_start):
                    _add_unique(summary_tasks_carryover, url, title)

    summary_avg_completion = None
    if completed_durations:
        avg_completion = sum(completed_durations) / len(completed_durations)
        max_duration = max(completed_durations)
        summary_avg_completion = {
            "avg_days": avg_completion,
            "max_days": max_duration,
            "count": len(completed_durations),
        }

    milestone_section = None
    milestone_requested = False
    project_charts = None
    if project_slug and project_slug != "__all__":
        milestone_section = load_milestone_section(
            token=settings.github_token,
            week_id=week_id,
            project_urls=settings.project_milestone_urls,
            project_slug=project_slug,
            milestone_month=milestone_month,
        )
        if settings.project_milestone_urls and settings.project_milestone_urls.get(project_slug):
            milestone_requested = True

        milestone_label = None
        if milestone_section and milestone_section.get("month"):
            milestone_label = milestone_section.get("month")

        project_id = None
        try:
            _, project_config = settings.get_project(project_slug)
            project_id = project_config.github_project_id
        except ValueError:
            pass

        if week_end:
            ref_date = week_end + timedelta(days=6)
        else:
            ref_date = date.today()

        resolved_month = milestone_month or (milestone_section.get("month") if milestone_section else None)
        project_charts = load_project_charts(
            token=settings.github_token,
            project_id=project_id,
            milestone_month=resolved_month,
            reference_date=ref_date,
            milestone_label=milestone_label,
        )
        milestone_label = None
        if milestone_section and milestone_section.get("month"):
            milestone_label = milestone_section.get("month")

    

    team_breakdown = []
    if reports_by_team:
        for team_name, team_reports in reports_by_team.items():
            team_tasks = sum(len(report.get("tasks") or []) for report in team_reports)
            team_deliveries = sum(1 for report in team_reports if report.get("had_deliveries"))
            team_breakdown.append(
                f"{team_name}: {len(team_reports)} relatos, {team_tasks} tasks, {team_deliveries} entregas"
            )
    elif reports_by_project:
        for project_name, project_reports in reports_by_project.items():
            project_tasks = sum(
                len(report.get("tasks") or [])
                for team_reports in project_reports.values()
                for report in team_reports
            )
            project_deliveries = sum(
                1
                for team_reports in project_reports.values()
                for report in team_reports
                if report.get("had_deliveries")
            )
            project_count = sum(len(team_reports) for team_reports in project_reports.values())
            team_breakdown.append(
                f"{project_name}: {project_count} relatos, {project_tasks} tasks, {project_deliveries} entregas"
            )

    summary_paragraphs: list[str] = []

    summary_warning: str | None = None
    if milestone_requested and milestone_section is None:
        summary_warning = (
            "Warning: milestones are configured, but GitHub data could not be loaded. "
            "Check GITHUB_TOKEN and repository access permissions."
        )

    def _build_llm_prompt(variant: int = 0) -> str:
        def _safe_text(value: str | None, fallback: str) -> str:
            text = (value or "").strip()
            return text if text else fallback

        report_lines: list[str] = []
        for report in reports:
            developer = report.get("developer_name") or "Desconhecido"
            team = report.get("team_name") or report.get("team_slug")
            header = f"{developer} ({team})" if team else developer
            progress = _safe_text(report.get("progress"), "(sem progresso informado)")
            if report.get("had_difficulties"):
                difficulties = _safe_text(
                    report.get("difficulties_description"),
                    "(dificuldades registradas sem descrição)",
                )
            else:
                difficulties = "(sem dificuldades registradas)"
            next_steps = _safe_text(report.get("next_steps"), "(sem próximos passos informados)")
            report_lines.append(
                "\n".join(
                    [
                        f"- {header}",
                        f"  Progresso: {progress}",
                        f"  Dificuldades: {difficulties}",
                        f"  Próximos passos: {next_steps}",
                    ]
                )
            )

        reports_block = "\n".join(report_lines) or "- (sem relatos)"
        team_lines = "\n".join(f"- {line}" for line in team_breakdown) or "- (sem times)"

        base_prompt = (
            "Gere um resumo executivo em português com 2 parágrafos curtos (2 a 3 frases cada), "
            "sem saudações (não inicie com 'Prezados' ou similares). "
            "O texto deve ser objetivo e consistente com os relatos, destacando avanço, entregas e riscos. "
            "Use um tom profissional e direto.\n\n"
            f"Período: {period_label or week_id}\n"
            f"Relatos: {total_reports}\n"
            f"Tarefas: {total_tasks}\n"
            f"Entregas: {deliveries_count}\n"
            f"Dificuldades: {difficulties_count}\n"
            f"Avanço por time:\n{team_lines}\n\n"
            "Relatos detalhados (texto):\n"
            f"{reports_block}\n"
        )

        if variant == 1:
            return (
                "Resuma executivamente a semana em 2 parágrafos curtos, sem saudações. "
                "Parágrafo 1: progresso e entregas. Parágrafo 2: riscos/dificuldades e próximos passos.\n\n"
                + base_prompt
            )
        if variant == 2:
            return (
                "Crie um resumo executivo conciso (2 parágrafos) com foco em resultados e pontos de atenção, "
                "sem cumprimentos. Evite termos vagos e não invente fatos.\n\n" + base_prompt
            )
        return base_prompt

    def _is_summary_acceptable(text: str) -> bool:
        normalized = text.strip().lower()
        if not normalized:
            return False
        if normalized.startswith("prezados") or normalized.startswith("ola"):
            return False
        if normalized.startswith("resumo executivo"):
            return False
        if len(normalized) < 220:
            return False
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) < 2:
            return False
        if any(len(p) < 60 for p in paragraphs[:2]):
            return False
        return True

    def _format_llm_error_message(error: Exception) -> str:
        error_text = str(error)
        error_text = re.sub(r"sk-[A-Za-z0-9-_]+", "[REDACTED]", error_text)
        error_text = re.sub(r"[a-f0-9]{32,}", "[REDACTED]", error_text, flags=re.IGNORECASE)
        if "<html" in error_text.lower():
            return "Warning: unable to generate the LLM summary (blocked by provider protection)."
        return f"Warning: unable to generate the LLM summary ({error})."

    def _normalize_llm_base_url(raw_url: str) -> str:
        url = raw_url.rstrip("/")
        if url.endswith("/v1"):
            return url
        return f"{url}/v1"

    def _try_llm_summary() -> list[str] | None:
        if not settings.llm_api_url or not settings.llm_model or not settings.llm_api_key:
            return None
        try:
            client = openai.OpenAI(
                api_key=settings.llm_api_key,
                base_url=_normalize_llm_base_url(settings.llm_api_url),
                default_headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "x-litellm-api-key": settings.llm_api_key,
                },
            )

            model_to_try = settings.llm_model
            if not model_to_try:
                return None

            for model in [model_to_try]:
                for variant in range(3):
                    response = client.responses.create(
                        model=model,
                        input=_build_llm_prompt(variant),
                        max_output_tokens=360,
                        temperature=0.3,
                    )

                    content_parts: list[str] = []
                    for item in getattr(response, "output", []) or []:
                        for piece in getattr(item, "content", []) or []:
                            if getattr(piece, "type", "") == "output_text":
                                text = (getattr(piece, "text", "") or "").strip()
                                if text:
                                    content_parts.append(text)

                    content = "\n\n".join(content_parts).strip()
                    if not content:
                        continue

                    if not _is_summary_acceptable(content):
                        continue

                    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                    return paragraphs or None

            return None
        except Exception as exc:
            nonlocal summary_warning
            logger.warning(f"LLM Summary failed: {exc}")
            summary_warning = None
            return None

    llm_summary = _try_llm_summary()
    if llm_summary:
        summary_paragraphs = llm_summary
    else:
        def _auto_summary() -> list[str]:
            done_pct = None
            done_review_pct = None
            selected_count = None
            difficulty_points = None
            try:
                weekly = weekly_table
                done_pct = weekly.get('done_percent')
                done_review_pct = weekly.get('done_review_percent')
                selected_count = weekly.get('selected_count')
                difficulty_points = weekly.get('difficulty_points')
            except Exception:
                weekly = {}

            teams_text = ("; ".join(team_breakdown)) if team_breakdown else "sem detalhamento por time"

            p1_parts = []
            p1_parts.append(f"Período: {period_label or week_id}.")
            p1_parts.append(f"Foram recebidos {total_reports} relatos com {total_tasks} tarefas, sendo {deliveries_count} entregas registradas.")
            if done_pct is not None:
                p1_parts.append(f"Progresso atual: ~{done_pct}% concluído ({done_review_pct}% incluindo revisões).")
            if selected_count is not None:
                p1_parts.append(f"Amostra considerada: {selected_count} itens na iteração selecionada.")

            p1 = " ".join(p1_parts)

            p2_parts = []
            p2_parts.append(f"Foram reportadas {difficulties_count} ocorrências de dificuldade; os principais riscos devem ser tratados pelas equipes listadas: {teams_text}.")
            p2_parts.append("Recomenda-se priorizar os itens em backlog e reduzir bloqueios, além de validar dependências que impactam entregas.")
            p2 = " ".join(p2_parts)

            def _shorten(text: str, max_sentences: int = 3) -> str:
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\\s+", text) if s.strip()]
                return " ".join(sentences[:max_sentences])

            return [_shorten(p1, 3), _shorten(p2, 3)]

        summary_paragraphs = _auto_summary()

    template = env.get_template("report_pdf.html")
    rendered_html = template.render(
        week_id=week_id,
        period_label=period_label or week_id,
        reports_by_team=reports_by_team,
        reports_by_project=reports_by_project,
        reports=reports,
        summary_charts=summary_charts,
        summary_avg_completion=summary_avg_completion,
        milestone_section=milestone_section,
        project_charts=project_charts,
        milestone_label=milestone_label,
        summary_paragraphs=summary_paragraphs,
        summary_tasks_worked=summary_tasks_worked,
        summary_tasks_completed=summary_tasks_completed,
        summary_tasks_carryover=summary_tasks_carryover,
        summary_warning=summary_warning,
        file_title=file_title,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    css_path = get_assets_dir() / "report_pdf.css"
    stylesheets = [CSS(filename=str(css_path))] if css_path.exists() else []

    HTML(string=rendered_html, base_url=str(get_assets_dir().parent)).write_pdf(
        output_path,
        stylesheets=stylesheets,
    )

    logger.info("rsd.pdf.generated", extra={"output_path": str(output_path)})
    return output_path
