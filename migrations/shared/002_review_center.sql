create extension if not exists pgcrypto;

alter table turn_reviews
add column if not exists corrected_answer text not null default '';

alter table turn_reviews
alter column reviewer set default 'web-reviewer';

create table if not exists qa_review_items (
    id uuid primary key default gen_random_uuid(),
    source_type text not null check (source_type in ('knowledge_qa', 'chat_turn', 'manual', 'corrected_feedback')),
    source_ref text not null default '',
    turn_id uuid null references chat_turns(id),
    question text not null,
    original_answer text not null default '',
    reviewed_answer text not null default '',
    product_tags jsonb not null default '[]'::jsonb,
    scenario_tags jsonb not null default '[]'::jsonb,
    technical_tags jsonb not null default '[]'::jsonb,
    sdk_tags jsonb not null default '[]'::jsonb,
    quality_score integer null check (quality_score between 1 and 5),
    review_status text not null check (review_status in ('pending', 'approved', 'needs_fix', 'rejected', 'candidate')),
    reviewer text not null default 'web-reviewer',
    review_notes text not null default '',
    should_export_to_knowledge boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_qa_review_items_status_updated
on qa_review_items(review_status, updated_at desc);

create index if not exists idx_qa_review_items_source
on qa_review_items(source_type, source_ref);

create unique index if not exists idx_qa_review_items_unique_source
on qa_review_items(source_type, source_ref)
where source_ref <> '';

create index if not exists idx_qa_review_items_turn_id
on qa_review_items(turn_id);

create index if not exists idx_qa_review_items_product_tags
on qa_review_items using gin(product_tags);

create index if not exists idx_qa_review_items_technical_tags
on qa_review_items using gin(technical_tags);

create index if not exists idx_turn_reviews_turn_created
on turn_reviews(turn_id, created_at desc);
