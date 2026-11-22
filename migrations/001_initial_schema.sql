-- TransplantCare initial schema + RLS
-- Run against Supabase Postgres

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.app_role as enum ('patient', 'caregiver', 'doctor');
create type public.dose_status as enum ('taken', 'skipped', 'missed');
create type public.link_status as enum ('pending', 'active', 'revoked');
create type public.meal_type as enum ('breakfast', 'lunch', 'dinner', 'snack', 'other');

-- ---------------------------------------------------------------------------
-- Profiles
-- ---------------------------------------------------------------------------
create table public.profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references auth.users (id) on delete cascade,
  role public.app_role not null default 'patient',
  full_name text not null,
  transplant_date date,
  doctor_name text,
  doctor_whatsapp text, -- E.164 preferred
  locale text not null default 'en', -- en | te | hi
  fcm_token text,
  timezone text not null default 'Asia/Kolkata',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.consents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  policy_version text not null,
  accepted boolean not null default true,
  accepted_at timestamptz not null default now()
);

create table public.emergency_contacts (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  full_name text not null,
  phone text not null,
  relation text,
  sort_order int not null default 0,
  created_at timestamptz not null default now(),
  constraint emergency_contacts_max_five check (sort_order between 0 and 4)
);

create unique index emergency_contacts_patient_order_idx
  on public.emergency_contacts (patient_id, sort_order);

-- ---------------------------------------------------------------------------
-- Medicines
-- ---------------------------------------------------------------------------
create table public.medicines (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  name text not null,
  dose text not null,
  schedule_times time[] not null default '{}',
  notes text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.dose_logs (
  id uuid primary key default gen_random_uuid(),
  medicine_id uuid not null references public.medicines (id) on delete cascade,
  patient_id uuid not null references public.profiles (id) on delete cascade,
  scheduled_at timestamptz not null,
  status public.dose_status not null,
  logged_at timestamptz not null default now(),
  unique (medicine_id, scheduled_at)
);

create index if not exists dose_logs_patient_scheduled_idx
  on public.dose_logs (patient_id, scheduled_at);

-- ---------------------------------------------------------------------------
-- Dietary limits & food
-- ---------------------------------------------------------------------------
create table public.dietary_limits (
  patient_id uuid primary key references public.profiles (id) on delete cascade,
  potassium_mg numeric,
  sodium_mg numeric,
  phosphorus_mg numeric,
  protein_g numeric,
  fluid_ml numeric,
  calories_kcal numeric,
  set_by text default 'doctor',
  updated_at timestamptz not null default now()
);

create table public.food_composition (
  id uuid primary key default gen_random_uuid(),
  name_en text not null,
  name_hi text,
  name_te text,
  category text,
  serving_g numeric not null default 100,
  potassium_mg numeric,
  sodium_mg numeric,
  phosphorus_mg numeric,
  protein_g numeric,
  fluid_ml numeric,
  calories_kcal numeric,
  source text default 'IFCT'
);

create table public.food_logs (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  logged_at timestamptz not null default now(),
  meal_type public.meal_type not null default 'other',
  description text,
  photo_path text,
  potassium_mg numeric,
  sodium_mg numeric,
  phosphorus_mg numeric,
  protein_g numeric,
  fluid_ml numeric,
  calories_kcal numeric,
  estimate_source text, -- llm | manual | composition
  raw_estimate jsonb
);

-- ---------------------------------------------------------------------------
-- Appointments & labs (phase 2 ready)
-- ---------------------------------------------------------------------------
create table public.appointments (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  title text not null default 'Checkup',
  starts_at timestamptz not null,
  location text,
  notes text,
  reminder_minutes int[] default '{1440,60}'
);

create table public.lab_reports (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  collected_at date,
  storage_path text not null,
  extracted_at timestamptz,
  extraction_raw jsonb,
  created_at timestamptz not null default now()
);

create table public.lab_values (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references public.lab_reports (id) on delete cascade,
  patient_id uuid not null references public.profiles (id) on delete cascade,
  analyte text not null, -- creatinine, egfr, potassium, tacrolimus, hemoglobin, ...
  value numeric not null,
  unit text,
  ref_low numeric,
  ref_high numeric,
  out_of_range boolean generated always as (
    (ref_low is not null and value < ref_low)
    or (ref_high is not null and value > ref_high)
  ) stored,
  measured_at date
);

-- ---------------------------------------------------------------------------
-- Guidance (doctor-editable)
-- ---------------------------------------------------------------------------
create table public.guidance_content (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title_en text not null,
  body_en text not null,
  title_hi text,
  body_hi text,
  title_te text,
  body_te text,
  category text not null, -- precautions | exercise
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Caregiver links, chat, SOS
-- ---------------------------------------------------------------------------
create table public.caregiver_links (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  caregiver_user_id uuid references auth.users (id) on delete cascade,
  invite_code text not null unique default encode(extensions.gen_random_bytes(8), 'hex'),
  status public.link_status not null default 'pending',
  created_at timestamptz not null default now(),
  unique (patient_id, caregiver_user_id)
);

create table public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  red_flag boolean not null default false,
  tools_used jsonb,
  created_at timestamptz not null default now()
);

create table public.sos_events (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references public.profiles (id) on delete cascade,
  lat double precision,
  lng double precision,
  symptoms text,
  status text not null default 'triggered', -- triggered | cancelled | sent
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------
create or replace function public.current_profile_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select id from public.profiles where user_id = auth.uid() limit 1;
$$;

create or replace function public.is_caregiver_of(p_patient_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.caregiver_links
    where patient_id = p_patient_id
      and caregiver_user_id = auth.uid()
      and status = 'active'
  );
$$;

create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_updated_at before update on public.profiles
  for each row execute function public.set_updated_at();
create trigger medicines_updated_at before update on public.medicines
  for each row execute function public.set_updated_at();

-- Enforce max 5 emergency contacts
create or replace function public.enforce_max_emergency_contacts()
returns trigger language plpgsql as $$
declare cnt int;
begin
  select count(*) into cnt from public.emergency_contacts where patient_id = new.patient_id;
  if tg_op = 'INSERT' and cnt >= 5 then
    raise exception 'Maximum 5 emergency contacts';
  end if;
  return new;
end;
$$;

create trigger trg_max_emergency_contacts
  before insert on public.emergency_contacts
  for each row execute function public.enforce_max_emergency_contacts();

-- Auto-create profile stub on signup (optional; app may upsert)
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, full_name, role)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', 'Patient'), 'patient')
  on conflict (user_id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.consents enable row level security;
alter table public.emergency_contacts enable row level security;
alter table public.medicines enable row level security;
alter table public.dose_logs enable row level security;
alter table public.dietary_limits enable row level security;
alter table public.food_composition enable row level security;
alter table public.food_logs enable row level security;
alter table public.appointments enable row level security;
alter table public.lab_reports enable row level security;
alter table public.lab_values enable row level security;
alter table public.guidance_content enable row level security;
alter table public.caregiver_links enable row level security;
alter table public.chat_messages enable row level security;
alter table public.sos_events enable row level security;

-- Profiles
create policy profiles_select_own on public.profiles for select
  using (user_id = auth.uid() or public.is_caregiver_of(id));
create policy profiles_update_own on public.profiles for update
  using (user_id = auth.uid());
create policy profiles_insert_own on public.profiles for insert
  with check (user_id = auth.uid());
create policy profiles_delete_own on public.profiles for delete
  using (user_id = auth.uid());

-- Consents
create policy consents_own on public.consents for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Emergency contacts: caregiver read-only, patient full access
create policy emergency_contacts_select on public.emergency_contacts for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy emergency_contacts_insert on public.emergency_contacts for insert
  with check (patient_id = public.current_profile_id());
create policy emergency_contacts_update on public.emergency_contacts for update
  using (patient_id = public.current_profile_id());
create policy emergency_contacts_delete on public.emergency_contacts for delete
  using (patient_id = public.current_profile_id());

-- Caregivers read-only: block writes via with check only for patient
create policy medicines_access on public.medicines for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy medicines_write on public.medicines for insert
  with check (patient_id = public.current_profile_id());
create policy medicines_update on public.medicines for update
  using (patient_id = public.current_profile_id());
create policy medicines_delete on public.medicines for delete
  using (patient_id = public.current_profile_id());

create policy dose_logs_select on public.dose_logs for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy dose_logs_write on public.dose_logs for insert
  with check (patient_id = public.current_profile_id());
create policy dose_logs_update on public.dose_logs for update
  using (patient_id = public.current_profile_id());

create policy dietary_limits_select on public.dietary_limits for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy dietary_limits_insert on public.dietary_limits for insert
  with check (patient_id = public.current_profile_id());
create policy dietary_limits_update on public.dietary_limits for update
  using (patient_id = public.current_profile_id());
create policy dietary_limits_delete on public.dietary_limits for delete
  using (patient_id = public.current_profile_id());

create policy food_composition_read on public.food_composition for select
  using (true);

create policy food_logs_select on public.food_logs for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy food_logs_write on public.food_logs for insert
  with check (patient_id = public.current_profile_id());
create policy food_logs_delete on public.food_logs for delete
  using (patient_id = public.current_profile_id());

create policy appointments_select on public.appointments for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy appointments_insert on public.appointments for insert
  with check (patient_id = public.current_profile_id());
create policy appointments_update on public.appointments for update
  using (patient_id = public.current_profile_id());
create policy appointments_delete on public.appointments for delete
  using (patient_id = public.current_profile_id());

create policy lab_reports_select on public.lab_reports for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy lab_reports_insert on public.lab_reports for insert
  with check (patient_id = public.current_profile_id());
create policy lab_reports_update on public.lab_reports for update
  using (patient_id = public.current_profile_id());
create policy lab_reports_delete on public.lab_reports for delete
  using (patient_id = public.current_profile_id());

create policy lab_values_select on public.lab_values for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy lab_values_insert on public.lab_values for insert
  with check (patient_id = public.current_profile_id());
create policy lab_values_update on public.lab_values for update
  using (patient_id = public.current_profile_id());
create policy lab_values_delete on public.lab_values for delete
  using (patient_id = public.current_profile_id());

create policy guidance_read on public.guidance_content for select using (true);

create policy caregiver_links_patient on public.caregiver_links for all
  using (
    patient_id = public.current_profile_id()
    or caregiver_user_id = auth.uid()
  )
  with check (patient_id = public.current_profile_id());

create policy chat_select on public.chat_messages for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy chat_insert on public.chat_messages for insert
  with check (patient_id = public.current_profile_id());

create policy sos_select on public.sos_events for select
  using (patient_id = public.current_profile_id() or public.is_caregiver_of(patient_id));
create policy sos_insert on public.sos_events for insert
  with check (patient_id = public.current_profile_id());

-- Seed default guidance
insert into public.guidance_content (slug, title_en, body_en, category) values
('infection-prevention', 'Infection prevention',
 'Wash hands often, avoid crowded indoor spaces when possible, and follow your transplant team''s mask guidance. Report fever promptly.',
 'precautions'),
('food-safety', 'Food safety',
 'Avoid raw/undercooked meat, unpasteurized dairy, and leftover food kept too long. Peel fruits when advised by your team.',
 'precautions'),
('sun-protection', 'Sun protection',
 'Immunosuppressants raise skin-cancer risk. Use SPF, hats, and shade; check skin regularly.',
 'precautions'),
('avoid-nsaids', 'Medicines & supplements to avoid',
 'Do not take NSAIDs (like ibuprofen), grapefruit, or herbal supplements unless your transplant doctor approves.',
 'precautions'),
('exercise-gentle', 'Gentle exercise plan',
 'Start with short daily walks as cleared by your doctor. Increase gradually; stop if chest pain, dizziness, or graft-site pain occurs and contact your team.',
 'exercise');

-- Sample Indian foods (subset — expand later)
insert into public.food_composition
  (name_en, name_hi, name_te, category, potassium_mg, sodium_mg, phosphorus_mg, protein_g, fluid_ml, calories_kcal)
values
  ('Cooked white rice (100g)', 'सफेद चावल', 'బియ్యం అన్నం', 'grain', 35, 1, 37, 2.7, 70, 130),
  ('Dal (cooked, 100g)', 'दाल', 'పప్పు', 'legume', 200, 5, 100, 9.0, 75, 116),
  ('Chapati (1 medium)', 'रोटी', 'రోటీ', 'grain', 70, 120, 60, 3.0, 20, 100),
  ('Cucumber (100g)', 'खीरा', 'దోసకాయ', 'vegetable', 150, 2, 20, 0.7, 95, 15),
  ('Apple (1 medium)', 'सेब', 'ఆపిల్', 'fruit', 195, 2, 20, 0.5, 150, 95),
  ('Curd (100g)', 'दही', 'పెరుగు', 'dairy', 140, 50, 90, 3.5, 85, 60),
  ('Chicken curry (100g)', 'चिकन करी', 'చికెన్ కూర', 'nonveg', 220, 400, 150, 18.0, 60, 180),
  ('Idli (2 pcs)', 'इडली', 'ఇడ్లీ', 'grain', 60, 200, 40, 4.0, 80, 120);
