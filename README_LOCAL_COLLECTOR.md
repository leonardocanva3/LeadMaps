# LeadMaps Local Collector

Este coletor roda no Windows do usuario com Playwright local e envia os leads direto para a tabela `leads` do Supabase usada pelo LeadMaps online.

O Render fica apenas com login, dashboard e visualizacao dos leads.

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

## 4. Executar o coletor

```powershell
python local_collector.py
```

O terminal vai pedir:

```text
Digite o segmento:
Digite a cidade:
Quantidade máxima:
```

Exemplo:

```text
Digite o segmento: psicologa
Digite a cidade: Sao Paulo SP
Quantidade máxima: 20
```

O limite maximo inicial e 20 resultados, mesmo se for digitado um numero maior.

## Logs

Durante a execucao, o coletor mostra:

```text
[1] Iniciando busca
[2] Abrindo Google Maps
[3] Coletando resultados
[4] Lead encontrado
[5] Enviando para Supabase
[6] Finalizado
```

## Duplicados

Antes de inserir, o coletor consulta a tabela `leads` e evita duplicados por:

- telefone limpo
- nome + endereco

Os inserts usam `upsert` em `unique_key`, a mesma coluna unica usada pelo dashboard.
