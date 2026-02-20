## Global Drones Reports

Sistema automatizado para geração, envio e arquivamento de relatórios semanais de desenvolvimento em PDF.

## ✅ Funcionalidades

- Formulários web por projeto/equipe (rotas dinâmicas)
- Consolidação e geração de PDF por semana
- Notificações por email (SMTP)
- Agendamento automático de avisos
- Integração com milestones do GitHub (opcional)
- Resumo executivo com LLM (opcional)

## 📋 Pré-requisitos

- Python 3.11+
- SQLite 3
- uv (recomendado) ou pip

## ⚡ Quick start

### 1) Dependências

```bash
uv sync
source .venv/bin/activate
```

### 2) Configuração mínima

```bash
cp .env.example .env
nano .env
cp config.json.example config.json
nano config.json
```

Variáveis mínimas:

- `BASE_URL`
- `SMTP_HOST`
- `SMTP_USER`
- `SMTP_PASSWORD`

### 3) Banco de dados

```bash
python scripts/clean_db.py --yes
```

### 4) Rodar local

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 3456 --reload
```

Acesse: http://localhost:3456/form

## 🧭 Endpoints principais

| Endpoint | Método | Descrição |
|---|---|---|
| `/form` | GET | Landing page com links |
| `/{project}/form` | GET | Formulário do projeto |
| `/{project}/reports` | POST | Criar relatório |
| `/rsd/generate` | POST | Gerar PDF |
| `/notifications/collect` | POST | Notificar coleta por email |
| `/notifications/publish` | POST | Notificar publicação por email |
| `/health` | GET | Health check |

## 🧩 Configuração

### Projetos e equipes

Esses dados ficam em `config.json`.

```bash
# Projeto simples (sem equipes)
{
	"projects": {
		"transpetro": {
			"name": "Transpetro",
			"members": [
				{"name": "Ana", "email": "ana@empresa.com"},
				{"name": "Bruno", "email": "bruno@empresa.com"}
			]
		}
	}
}

# Projeto com equipes e GitHub Project ID
{
	"projects": {
		"agrosmart": {
			"name": "Agrosmart",
			"github_project_id": "xxxxxxxxx",
			"teams": {
				"backend": {
					"name": "Backend",
					"members": [
						{"name": "Lucas", "email": "lucas@empresa.com"},
						{"name": "Gabriel", "email": "gabriel@empresa.com"}
					]
				}
			}
		}
	}
}
```

### Email e agendamento

Esses dados ficam em `config.json` no campo `project_notifications_config`.

**IMPORTANTE:** `days` usa o padrão ISO 8601 onde **0=Segunda-feira** e **6=Domingo**.

```bash
# Notificações por email por time (usa emails dos members do time)
{"project_notifications_config":{"agrosmart":{"channels":[{"name":"backend","enabled":true,"team_slug":"backend","publish":{"title":"Relatório semanal pronto","text":"Segue em anexo o PDF do relatório da semana.","schedules":[{"days":[4],"times":["18:00"]}]}}]}}}

# Notificações por email para canal geral (sem team_slug) usando todos os emails do projeto
{"project_notifications_config":{"agrosmart":{"channels":[{"name":"agile-geral","enabled":true,"publish":{"schedules":[{"days":[4],"times":["17:00"]}]},"collect":{"schedules":[{"days":[0,2,4],"times":["09:00"]}]}}]}}}
```

#### Configuração SMTP mínima

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=seu.email@gmail.com
SMTP_PASSWORD=sua_senha_de_app
SMTP_FROM=seu.email@gmail.com
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

#### Gmail: como gerar senha de app

1. Acesse `https://myaccount.google.com/security`
2. Ative **Verificação em duas etapas**
3. Vá em **Senhas de app**
4. Escolha app `Mail` e dispositivo `Outro (nome personalizado)`
5. Gere a senha e use no `SMTP_PASSWORD`

Se a opção **Senhas de app** não aparecer, a conta pode estar bloqueada por política (Workspace). Nesse caso, use outra conta ou peça liberação ao administrador.

Você pode enviar notificações manualmente usando os endpoints `/notifications/collect` e `/notifications/publish`.

### Integrações opcionais

```bash
# GitHub Token para integração com Projects e Milestones
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx

# Configurar github_project_id dentro de cada projeto
{"projects":{"agrosmart":{"name":"Agrosmart","github_project_id":"xxxxxxxxxx","teams":{"backend":{"name":"Backend","members":[{"name":"Lucas","email":"lucas@empresa.com"}]}}}}}

# Milestones do GitHub (opcional)
PROJECT_MILESTONE_URLS={"agrosmart":["https://github.com/Org/Repo/milestone/1"]}

# LLM para resumo executivo (opcional)
LLM_API_URL=https://llm.globaldrones.com.br
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=sk-xxxxxxxxxxxxx
```

## 🧪 Comandos Manuais Importantes

### 1. Testar API e Health Check
Verificar se o serviço está rodando:
```bash
curl http://localhost:3456/health
```

### 2. Disparar Coleta de Relatórios (Solicitação aos Desenvolvedores)
Envia notificações por email solicitando preenchimento:
```bash
# Formato: /notifications/collect?week={WEEK_ISO}&project_slug={PROJECT}&team={TEAM}
curl -X POST "http://localhost:3456/notifications/collect?week=2026-W05&project_slug=agrosmart&team=backend"
```

### 3. Gerar PDF Manualmente
Gera o arquivo PDF compilando os relatórios da semana. O arquivo é salvo em `data/rsd/`:
```bash
# Exemplo para todo o projeto Agrosmart
curl -X POST "http://localhost:3456/rsd/generate?week=2026-W05&project_slug=agrosmart"

# Exemplo filtrando apenas um time
curl -X POST "http://localhost:3456/rsd/generate?week=2026-W05&project_slug=agrosmart&team=backend"
```

### 4. Publicar Relatório Gerado (Enviar PDF)
Envia o PDF gerado por email para os destinatários configurados:
```bash
curl -X POST "http://localhost:3456/notifications/publish?week=2026-W05&project_slug=agrosmart"
```

### 5. Admin Database
Limpar banco de dados e resetar estado (CUIDADO: apaga todos os dados):
```bash
python scripts/clean_db.py --yes
```

## 📏 Regras de Negócio e Comportamentos

### 1) Regras de Submissão de Relatórios
- **Campos obrigatórios:** `developer_name`, `summary`, `self_assessment`, `next_week_expectation` e pelo menos **uma tarefa**.
- **Validação de equipe:** o `developer_name` precisa estar listado nos membros do time configurado.
- **Semana padrão:** se `week_id` não for informado, o sistema usa a semana ISO atual.
- **Duplicidade:** se já existir relatório para a mesma pessoa/semana/time, a API retorna erro **409** (a menos que `overwrite=true`).

### 2) Agrupamento e Ordenação no PDF
- O PDF é agrupado por **Projeto** e depois por **Time** (ordem alfabética).
- O título do cartão combina `Projeto — Time`. Se o nome do time já inclui o projeto (ex.: “Agrosmart Backend”), o título é simplificado para evitar repetição.
- Cada cartão de desenvolvedor tenta ficar inteiro em uma página, mas o fluxo evita espaços em branco excessivos.

### 3) Gráficos e Resumos Gerenciais
- **Resumo geral da semana (cards no topo):** média de autoavaliação, expectativa para a próxima semana, % de entregas e % de dificuldades.
	- **Gráficos de projeto (GitHub Projects):** só aparecem se `GITHUB_TOKEN` estiver configurado E o projeto tiver `github_project_id` definido.
- **Gráfico BurnUp:** mostra evolução acumulada de escopo, concluído e duplicados (baseado em “pontos de dificuldade”).
- **Progresso Atual vs Previsto:** distribui pontos por status (Backlog, Progress, Review, Done).
- **Milestones (Hours/Difficulty/Count):** barras empilhadas comparando milestones e seus status.

### 4) Milestones do GitHub (Integração Clássica)
Usa `PROJECT_MILESTONE_URLS` para coletar metas específicas por repositório.
- **Sem milestone válido:** a seção de progresso de milestones não aparece.
- **1 milestone:** exibe a evolução e o percentual de conclusão.
- **Vários milestones:** cada milestone aparece com seu próprio status.

### 5) Mensagens e Publicação
- **Coleta (`/notifications/collect`):** envia email com link do formulário conforme `PROJECT_NOTIFICATIONS_CONFIG`.
- **Publicação (`/notifications/publish`):** envia email com link para o PDF gerado, com caminho calculado por `project_slug` e `team`.
- **Destinatários:** se não houver canal configurado, informe `recipients` na chamada.

## 🐳 Docker (opcional)

```bash
docker build -t reports .
docker run -p 3456:3456 -v $(pwd)/data:/app/data --env-file .env reports
```


## 🔎 Local helper: encontrar ProjectV2 ID
Quando você precisar do identificador ProjectV2 do GitHub (usado pelos gráficos de milestones), há um script auxiliar:

```bash
# ProjectV2 da organização por número (formato: org/<ORG>/<NUMBER>)
python scripts/find_project_id.py org/GlobalDrones/3

# ProjectV2 de repositório por número (formato: repo/<OWNER>/<REPO>/<NUMBER>)
python scripts/find_project_id.py repo/GlobalDrones/AgroSmart-API/2

# Múltiplos alvos (separados por vírgula ou por espaço)
python scripts/find_project_id.py org/GlobalDrones/3,repo/GlobalDrones/AgroSmart-API/2
python scripts/find_project_id.py org/GlobalDrones/3 repo/GlobalDrones/AgroSmart-API/2
```

Comportamento:
- Se você passar `org/<ORG>/<NUMBER>` ou `repo/<OWNER>/<REPO>/<NUMBER>`, o script resolve diretamente via GitHub GraphQL usando o número que aparece na URL do projeto.
- A busca por slug foi removida; agora é obrigatório fornecer org/repo + número.
- Para consultas em organizações, o `GITHUB_TOKEN` precisa dos escopos `project` e `read:org`.