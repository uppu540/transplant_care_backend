-- Storage buckets for reports and food photos (run in Supabase SQL editor)

insert into storage.buckets (id, name, public)
values
  ('report-uploads', 'report-uploads', false),
  ('food-photos', 'food-photos', false)
on conflict (id) do nothing;

-- Patients can read/write objects under their user id prefix
drop policy if exists "patient upload reports" on storage.objects;
create policy "patient upload reports"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'report-uploads'
  and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "patient read reports" on storage.objects;
create policy "patient read reports"
on storage.objects for select
to authenticated
using (
  bucket_id = 'report-uploads'
  and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "patient upload food photos" on storage.objects;
create policy "patient upload food photos"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'food-photos'
  and (storage.foldername(name))[1] = auth.uid()::text
);

drop policy if exists "patient read food photos" on storage.objects;
create policy "patient read food photos"
on storage.objects for select
to authenticated
using (
  bucket_id = 'food-photos'
  and (storage.foldername(name))[1] = auth.uid()::text
);
