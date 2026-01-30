import sys
import os
import random
import datetime
from pathlib import Path

sys.path.append(os.getcwd())

try:
    from app.config import get_settings
    from app.db import create_report
except ImportError:
    sys.path.append(str(Path(__file__).parent.parent))
    from app.config import get_settings
    from app.db import create_report

def seed_data():
    settings = get_settings()
    projects = settings.list_projects()
    
    iso = datetime.date.today().isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    
    print(f"Seeding data for week: {week_id}")

    summaries = [
        "Foquei na refatoração do módulo de autenticação e comecei a integração com a API de pagamentos.",
        "Finalizei os testes unitários da nova feature de relatórios e corrigi bugs encontrados em QA.",
        "Trabalhei na otimização de queries do banco de dados para melhorar a performance do dashboard.",
        "Implementei o novo design da landing page e ajustei a responsividade para mobile."
    ]

    progress_texts = [
        "Concluí a migração de 50% dos endpoints para a nova arquitetura.",
        "Dashboard v2 está 90% pronto, faltando apenas validação final.",
        "Integração com Gateway de Pagamento finalizada.",
        "Componentes de UI atualizados para o novo Design System."
    ]

    next_steps_texts = [
        "Iniciar desenvolvimento do módulo de notificações.",
        "Realizar code review das PRs pendentes e preparar release.",
        "Investigar causa raiz dos timeouts na API de busca.",
        "Planejar a sprint da próxima semana com o time de produto."
    ]

    sample_tasks = [
        {"title": "Implementar Login OAuth", "url": "https://github.com/org/repo/issues/101"},
        {"title": "Corrigir bug de renderização no Safari", "url": "https://github.com/org/repo/issues/102"},
        {"title": "Atualizar documentação da API", "url": "https://github.com/org/repo/issues/103"},
        {"title": "Otimizar imagem do Docker", "url": "https://github.com/org/repo/issues/104"},
        {"title": "Reunião de Alinhamento Técnico", "url": ""}
    ]

    count = 0

    for project_slug, project_config in projects.items():
        teams = project_config.resolved_teams()
        
        for team_slug, team_config in teams.items():
            print(f"  -> Project: {project_config.name} | Team: {team_config.name}")
            
            members = team_config.members if team_config.members else [f"Dev {team_config.name} 1", f"Dev {team_config.name} 2"]
            
            target_members = members[:2] if len(members) >= 2 else (members * 2)[:2]
            
            for i, developer_name in enumerate(target_members):
                summary = random.choice(summaries)
                progress = random.choice(progress_texts)
                next_steps = random.choice(next_steps_texts)
                
                num_tasks = random.randint(1, 3)
                chosen_tasks = random.sample(sample_tasks, num_tasks)
                tasks_payload = []
                today_str = datetime.date.today().isoformat()
                
                for t in chosen_tasks:
                   tasks_payload.append({
                       "task_url": t["url"],
                       "start_date": today_str,
                       "end_date": today_str if random.choice([True, False]) else None,
                       "days_spent": random.randint(1, 5) if random.choice([True, False]) else 0
                   })

                had_difficulties = random.choice([True, False])
                had_deliveries = random.choice([True, False])

                payload = {
                    "week_id": week_id,
                    "project_slug": project_slug,
                    "project_name": project_config.name,
                    "team_slug": team_slug,
                    "team_name": team_config.name,
                    "developer_name": developer_name,
                    "summary": summary,
                    "progress": progress,
                    "had_difficulties": 1 if had_difficulties else 0,
                    "difficulties_description": "Bloqueio na API externa." if had_difficulties else "",
                    "next_steps": next_steps,
                    "tasks": tasks_payload,
                    "had_deliveries": 1 if had_deliveries else 0,
                    "deliveries_notes": "Entregue versão alpha." if had_deliveries else "",
                    "deliveries_link": json.dumps(["http://release.link"]) if had_deliveries else "",
                    "deliveries_links": ["http://release.link"] if had_deliveries else [],
                    "self_assessment": random.randint(3, 5),
                    "next_week_expectation": random.randint(3, 5),
                }

                import json
                payload["deliveries_link"] = json.dumps(payload["deliveries_links"]) if payload["deliveries_links"] else ""

                try:
                    create_report(settings, payload)
                    print(f"    - Created report for {developer_name}")
                    count += 1
                except Exception as e:
                    print(f"    ! Error creating report for {developer_name}: {e}")

    print(f"Done! Created {count} reports.")

if __name__ == "__main__":
    seed_data()
