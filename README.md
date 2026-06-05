# LeadMaps

Sistema simples em Python para pesquisar empresas no Google Maps, visualizar leads em um painel web e exportar para Excel.

## O que coleta

- Nome
- Telefone
- WhatsApp
- Endereco
- Site
- Nota
- Quantidade de avaliacoes
- Cidade
- Tem Site?
- Oportunidade
- Link do Google Maps

## Filtros

- Mantem apenas empresas da cidade pesquisada.
- Mantem apenas empresas com telefone quando esse filtro estiver marcado.
- Mantem apenas empresas sem site quando esse filtro estiver marcado.
- Permite filtrar por quantidade maxima de avaliacoes.

Se a cidade for digitada com UF, como `Montenegro RS`, o sistema exige que o endereco tenha `Montenegro` e `RS` ou `Rio Grande do Sul`.

## Oportunidade

- ALTA: empresa sem site e com ate 20 avaliacoes.
- MEDIA: empresa sem site e com ate 50 avaliacoes.
- BAIXA: empresa com site ou com mais de 50 avaliacoes.

## Painel web

O painel prioriza a operacao diaria da esteira. Por padrao aparecem os cards principais, a esteira de abordagem, downloads e historico de raspagens. A tabela completa continua disponivel, mas fica recolhida atras do botao `Ver lista completa`.

## Controle de abordagem

Ao clicar em `Conversar`, o WhatsApp abre em nova aba e o LeadMaps mostra um modal para registrar o feedback do lead.

O LeadMaps usa uma base mestra acumulativa. Cada nova raspagem soma contatos novos e ignora duplicados, sem apagar leads pendentes, sucesso ou burn.

Toda raspagem passa por uma porta unica de entrada. Um lead existente nunca volta como `NOVO` se ja estiver na base como `NOVO`, `SUCESSO`, `BURN` ou algum status legado.

Duplicidade e identificada por:

1. Telefone limpo
2. Link do Google Maps
3. Nome + cidade

Arquivo principal da base:

```text
exports/leads_master.json
```

Status possiveis:

- NOVO
- SUCESSO
- BURN

Campos salvos:

- Status abordagem
- WhatsApp valido?
- Mensagem enviada?
- Observacao
- Data/hora do feedback

Os feedbacks ficam em:

```text
exports/feedbacks.json
```

Depois dos feedbacks, o sistema gera:

```text
exports/leads_ativos.xlsx
exports/leads_historico_completo.xlsx
```

`leads_ativos.xlsx` exclui contatos com status `BURN`. O historico completo mantem todos os leads com status e observacoes.

As planilhas incluem campos de operacao:

- Status abordagem
- WhatsApp valido?
- Mensagem enviada?
- Data primeira abordagem
- Data ultimo feedback
- Ultima acao
- Observacao
- Origem raspagem
- Oportunidade

Tambem existem filtros visuais no painel:

- TODOS
- NOVO
- SUCESSO
- BURN

Por padrao, o painel abre em `NOVO`.

O historico das ultimas raspagens fica em:

```text
exports/raspagens.json
```

Cada item guarda data/hora, nicho, cidade, limite, avaliacoes maximas, leads encontrados, novos adicionados, duplicados ignorados e total final da base.

## Esteira de abordagem

O painel tem uma area chamada `Esteira de abordagem`, que entrega um contato por vez para prospeccao manual.

A fila principal mostra apenas leads `NOVO`. `SUCESSO`, `BURN` e qualquer status legado nao aparecem na esteira.

A fila prioriza:

1. Oportunidade `ALTA`
2. Oportunidade `MEDIA`
3. Oportunidade `BAIXA`

Na esteira e possivel:

- Conversar no WhatsApp
- Pular contato
- Marcar como BURN
- Salvar feedback e avancar para o proximo contato
- Desfazer ultimo

Ao abrir o WhatsApp, o LeadMaps apenas registra `data_primeira_abordagem` quando ainda estiver vazia e `ultima_acao = "WhatsApp aberto"`. O status continua `NOVO` ate salvar o feedback.

Ao salvar o feedback, o status e definido automaticamente:

- WhatsApp valido = SIM e mensagem enviada = SIM: `SUCESSO`.
- WhatsApp valido = NAO ou mensagem enviada = NAO: `BURN`.

Ao pular um contato, o status nao muda e a acao fica registrada como `Contato pulado`. O painel evita trazer o mesmo contato imediatamente de volta.

O botao `Desfazer ultimo` restaura o estado anterior da ultima acao da esteira. O historico curto fica em:

```text
exports/acoes_recentes.json
```

Contadores da esteira:

- Total na base
- Restantes para abordar
- Novos
- Sucesso
- Burn
- Mensagens enviadas hoje
- Taxa WhatsApp valido
- Abordados hoje
- Sucesso hoje
- Burn hoje
- Meta diaria
- Faltam para meta
- Estimativa para finalizar
- Enviados na semana
- Enviados no mes

A estimativa para finalizar divide os contatos restantes pela meta diaria e arredonda para cima. A taxa de WhatsApp valido usa `SUCESSO / (SUCESSO + BURN)`.

Quando ainda existem leads `NOVO`, o painel mostra o aviso com a quantidade de contatos restantes. O botao `Nova raspagem` nao apaga nada e apenas permite iniciar uma nova busca.

## Importar planilha marcada

O painel tem a area `Importar planilha de feedback` para enviar um arquivo `.xlsx` marcado manualmente:

- Linha ou celula amarela: atualiza o lead para `SUCESSO`.
- Linha ou celula vermelha: atualiza o lead para `BURN`.
- Sem cor: importa como `NOVO`.

Ao importar, o LeadMaps identifica o lead por telefone limpo, link do Google Maps ou nome+cidade. Se o lead ainda nao existir na base mestra, ele e adicionado.

Antes da importacao, o sistema cria backup automatico:

```text
exports/backups/leads_master_backup_YYYY-MM-DD_HHMM.json
```

A importacao atualiza:

```text
exports/leads_master.json
exports/feedbacks.json
exports/leads_ativos.xlsx
exports/leads_historico_completo.xlsx
```

O resumo da importacao mostra:

- Total de linhas lidas
- Sucessos importados
- Burns importados
- Novos importados
- Duplicados ignorados
- Existentes atualizados
- Total final da base
- Restantes para abordar

## Resetar base

O painel tem o botao `Resetar base de leads`.

Antes de limpar a base, o sistema cria backup automatico:

```text
exports/backups/reset_backup_YYYY-MM-DD_HHMM.json
```

O reset limpa:

- exports/leads_master.json
- exports/feedbacks.json
- exports/leads_ativos.xlsx
- exports/leads_historico_completo.xlsx

O reset nao apaga backups nem planilhas brutas datadas.

## Como instalar

```bash
pip install -r requirements.txt
playwright install chromium
```

Copie o exemplo de variaveis de ambiente:

```bash
copy .env.example .env
```

Por padrao, mantenha:

```text
STORAGE_MODE=local
```

## Como executar

Painel web:

```bash
python app.py
```

Depois acesse:

```text
http://127.0.0.1:5000
```

Terminal:

```bash
python src/main.py
```

O terminal vai pedir:

1. Nicho
2. Cidade
3. Quantidade de resultados para analisar
4. Avaliacoes maximas

Exemplo:

```text
Digite o nicho: pizzaria
Digite a cidade: Porto Alegre
```

O arquivo sera gerado automaticamente em:

```text
exports/leads.xlsx
```

Cada busca tambem gera um arquivo com data e hora, por exemplo:

```text
exports/leads_2026-06-05_0324.xlsx
```

Se `exports/leads.xlsx` estiver aberto no Excel, o sistema nao quebra. Ele mantem o arquivo datado e mostra uma mensagem avisando que a planilha principal esta aberta.

O limite padrao de analise e 100 resultados quando o campo fica vazio.

## Armazenamento

O LeadMaps suporta dois modos:

```text
STORAGE_MODE=local
STORAGE_MODE=supabase
```

No modo `local`, o sistema continua usando os arquivos em `exports/`:

```text
exports/leads_master.json
exports/feedbacks.json
exports/raspagens.json
exports/acoes_recentes.json
```

No modo `supabase`, o sistema usa Supabase/PostgreSQL como base central. A interface e a esteira permanecem iguais.

## Configurar Supabase

1. Crie um projeto no Supabase.
2. Abra o SQL Editor.
3. Execute o arquivo:

```text
database/supabase_schema.sql
```

4. Preencha o `.env`:

```text
STORAGE_MODE=supabase
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sua-service-role-key
APP_SECRET_KEY=uma-chave-secreta
```

Use a `service_role_key` apenas no backend. Nao exponha essa chave em frontend publico.

## Migrar dados locais para Supabase

Depois de configurar o Supabase e o `.env`, execute manualmente:

```bash
python scripts/migrate_local_to_supabase.py
```

O script le:

```text
exports/leads_master.json
exports/feedbacks.json
exports/raspagens.json
```

Ele respeita `unique_key`, evita duplicados e mostra:

- Total local
- Inseridos
- Atualizados
- Ignorados
- Erros

Nao execute a migracao antes de preencher as variaveis do Supabase.

## Senha opcional

Nao ha login por padrao. Se quiser uma protecao simples no painel, defina:

```text
APP_ACCESS_PASSWORD=sua-senha
```

Se ficar vazio, o painel abre normalmente.

## Deploy no Render

Arquivos preparados:

```text
Procfile
runtime.txt
render.yaml
```

No Render, configure as variaveis:

```text
STORAGE_MODE=supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
APP_SECRET_KEY=
APP_ACCESS_PASSWORD=
```

Comando de start:

```bash
gunicorn app:app
```

## Observacao

O Google Maps muda a interface com frequencia. Se algum campo parar de aparecer, pode ser necessario ajustar os seletores no arquivo `src/main.py`.
