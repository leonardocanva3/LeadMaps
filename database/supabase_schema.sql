create extension if not exists "pgcrypto";

create table if not exists leads (
  id uuid primary key default gen_random_uuid(),
  unique_key text unique not null,
  nome text,
  telefone text,
  telefone_limpo text,
  whatsapp text,
  endereco text,
  site text,
  nota text,
  quantidade_avaliacoes integer,
  cidade text,
  tem_site text,
  oportunidade text,
  link_google_maps text,
  status_abordagem text,
  whatsapp_valido text,
  mensagem_enviada text,
  observacao text,
  data_primeira_abordagem timestamptz,
  data_ultimo_feedback timestamptz,
  ultima_acao text,
  origem text,
  origem_raspagem text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists feedbacks (
  id uuid primary key default gen_random_uuid(),
  lead_unique_key text,
  status_abordagem text,
  whatsapp_valido text,
  mensagem_enviada text,
  observacao text,
  created_at timestamptz default now()
);

create table if not exists raspagens (
  id uuid primary key default gen_random_uuid(),
  data_hora timestamptz default now(),
  nicho text,
  cidade text,
  limite integer,
  avaliacoes_maximas integer,
  leads_encontrados integer,
  novos_adicionados integer,
  duplicados_ignorados integer,
  total_base integer
);

create table if not exists acoes_recentes (
  id uuid primary key default gen_random_uuid(),
  lead_unique_key text,
  acao text,
  estado_anterior jsonb,
  estado_novo jsonb,
  created_at timestamptz default now()
);

create index if not exists idx_leads_status on leads(status_abordagem);
create index if not exists idx_feedbacks_lead_unique_key on feedbacks(lead_unique_key);
create index if not exists idx_acoes_recentes_created_at on acoes_recentes(created_at);
