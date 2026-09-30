-- Akshar ↔ Supabase schema
-- Run this once in: Supabase Dashboard → SQL Editor → New query → Run

create extension if not exists "pgcrypto";

create table if not exists public.akshar_jobs (
  id text primary key,
  source_name text not null default '',
  filename text not null default '',
  status text not null default 'queued',
  stage text not null default '',
  page_count integer not null default 0,
  force_ocr boolean not null default false,
  export_status text not null default 'idle',
  export_name text,
  error text,
  revision integer not null default 0,
  created_at timestamptz,
  updated_at timestamptz not null default now(),
  payload jsonb not null default '{}'::jsonb
);

create index if not exists akshar_jobs_status_idx on public.akshar_jobs (status);
create index if not exists akshar_jobs_updated_idx on public.akshar_jobs (updated_at desc);

create or replace function public.akshar_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists akshar_jobs_touch on public.akshar_jobs;
create trigger akshar_jobs_touch
before update on public.akshar_jobs
for each row execute function public.akshar_touch_updated_at();

-- Storage bucket for uploaded sources and exports
insert into storage.buckets (id, name, public)
values ('akshar-documents', 'akshar-documents', false)
on conflict (id) do nothing;

-- Server uses the service_role key, which bypasses RLS.
-- Keep RLS on so anon clients cannot read private documents.
alter table public.akshar_jobs enable row level security;

drop policy if exists "akshar_jobs_service_all" on public.akshar_jobs;
-- No anon policies: only service_role (or authenticated policies you add later)
-- can access rows.

drop policy if exists "akshar_storage_service_read" on storage.objects;
drop policy if exists "akshar_storage_service_write" on storage.objects;
