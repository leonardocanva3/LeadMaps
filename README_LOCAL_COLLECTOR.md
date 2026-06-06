# LeadMaps Local Collector

Este coletor roda no Windows do usuario com Playwright local e envia leads direto para a tabela `leads` do Supabase usada pelo LeadMaps online.

O Render fica apenas com login, dashboard e visualizacao dos leads. A raspagem pesada deve rodar no PC local.

## 1. Criar `.env.local`

Crie um arquivo `.env.local` na raiz do projeto com:

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
LEADMAPS_USER_ID=
```

Preencha `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` com os dados do Supabase. O schema atual do projeto nao usa `user_id` na tabela `leads`, entao `LEADMAPS_USER_ID` fica reservado caso a coluna seja adicionada depois.

## 2. Instalar dependencias locais

```powershell
pip install -r requirements-local.txt
```

## 3. Instalar Chromium local

```powershell
python -m playwright install chromium
```

## 4. Preencher `cidades.txt`

Edite o arquivo `cidades.txt`, uma cidade por linha:

```text
Piracicaba SP
Campinas SP
Limeira SP
Americana SP
Rio Claro SP
```

## 5. Rodar modo manual

```powershell
python local_collector.py
```

O terminal vai pedir:

```text
Digite o segmento:
Digite a cidade:
Quantidade maxima:
```

O modo manual mantém o limite inicial de ate 20 resultados.

## 6. Rodar modo automatico

```powershell
python local_collector.py --auto
```

O terminal vai pedir apenas:

```text
Digite o nicho:
```

O modo automatico le todas as cidades do `cidades.txt` e executa buscas como:

```text
Psicologa em Piracicaba SP
Psicologa em Campinas SP
Psicologa em Limeira SP
```

Ele roda cidade por cidade ate finalizar os resultados do Google Maps. Nao existe limite fixo de contatos por cidade no modo automatico.

## Criterio de parada

O coletor continua rolando a lista enquanto surgem novos resultados. A cidade e considerada finalizada quando:

- o Google Maps mostra mensagem de final da lista; ou
- acontecem 8 rolagens seguidas sem nenhum resultado novo.

Essa regra e apenas uma protecao contra loop infinito.

## Filtro de telefone

Somente leads com pelo menos nome + telefone sao enviados ao Supabase.

Leads sem telefone sao descartados e registrados no log. O site do estabelecimento, quando existe no Google Maps, e salvo na coluna `site`, que ja e usada pelo dashboard.

## Duplicados

Antes de inserir, o coletor consulta a tabela `leads` e evita duplicados por:

- telefone limpo
- nome + endereco

Os inserts usam `upsert` em `unique_key`, a mesma coluna unica usada pelo dashboard.

## Logs

No modo automatico, o coletor cria:

```text
logs/coleta_YYYY-MM-DD_HH-MM.txt
```

O log registra inicio, nicho, cidades carregadas, cidade atual, busca executada, encontrados, com telefone, sem telefone descartados, duplicados, novos enviados ao Supabase, erros por cidade, tempo por cidade e resumo final.
