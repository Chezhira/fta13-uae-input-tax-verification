-- FTA13 user-scoped persistence. Run in the Supabase SQL editor.
create extension if not exists pgcrypto;

create table if not exists public.documents (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    filename text not null,
    mime_type text not null check (mime_type in ('application/pdf', 'image/png', 'image/jpeg')),
    size_bytes bigint not null check (size_bytes > 0 and size_bytes <= 20971520),
    sha256 text not null check (char_length(sha256) = 64),
    storage_path text not null unique,
    detected_languages text[] not null default '{}',
    extraction jsonb not null default '{}',
    created_at timestamptz not null default now()
);

create table if not exists public.assessments (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    supplier_reference text,
    supply_reference text,
    status text not null default 'open' check (status in ('open', 'complete', 'exception_available')),
    payload jsonb not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.documents enable row level security;
alter table public.assessments enable row level security;

drop policy if exists "documents_select_own" on public.documents;
drop policy if exists "documents_insert_own" on public.documents;
drop policy if exists "documents_delete_own" on public.documents;
drop policy if exists "assessments_select_own" on public.assessments;
drop policy if exists "assessments_insert_own" on public.assessments;
drop policy if exists "assessments_update_own" on public.assessments;
drop policy if exists "assessments_delete_own" on public.assessments;

create policy "documents_select_own" on public.documents for select
using (auth.uid() = user_id);
create policy "documents_insert_own" on public.documents for insert
with check (auth.uid() = user_id);
create policy "documents_delete_own" on public.documents for delete
using (auth.uid() = user_id);

create policy "assessments_select_own" on public.assessments for select
using (auth.uid() = user_id);
create policy "assessments_insert_own" on public.assessments for insert
with check (auth.uid() = user_id);
create policy "assessments_update_own" on public.assessments for update
using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "assessments_delete_own" on public.assessments for delete
using (auth.uid() = user_id);

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'fta13-documents',
    'fta13-documents',
    false,
    20971520,
    array['application/pdf', 'image/png', 'image/jpeg']
)
on conflict (id) do update set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "storage_insert_own_folder" on storage.objects;
drop policy if exists "storage_select_own_folder" on storage.objects;
drop policy if exists "storage_delete_own_folder" on storage.objects;

create policy "storage_insert_own_folder" on storage.objects for insert to authenticated
with check (bucket_id = 'fta13-documents' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "storage_select_own_folder" on storage.objects for select to authenticated
using (bucket_id = 'fta13-documents' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "storage_delete_own_folder" on storage.objects for delete to authenticated
using (bucket_id = 'fta13-documents' and (storage.foldername(name))[1] = auth.uid()::text);
