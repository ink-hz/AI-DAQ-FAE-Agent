create extension if not exists pgcrypto;

create table if not exists chat_sessions (
    id uuid primary key default gen_random_uuid(),
    external_session_id text unique not null,
    channel text not null,
    user_id text null,
    external_user_id text null,
    conversation_title text null,
    created_at timestamptz not null default now(),
    last_active_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists chat_turns (
    id uuid primary key default gen_random_uuid(),
    session_id uuid references chat_sessions(id),
    external_session_id text not null,
    turn_index integer not null,
    trace_id text not null,
    channel text not null,
    question text not null,
    answer text not null,
    sources jsonb not null default '[]'::jsonb,
    stages jsonb not null default '[]'::jsonb,
    done jsonb not null default '{}'::jsonb,
    planned_capabilities jsonb not null default '[]'::jsonb,
    capability_coverage jsonb not null default '{}'::jsonb,
    fallback_used boolean not null default false,
    fallback_reason text null,
    outcome text null,
    duration_ms integer null,
    created_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb,
    unique (external_session_id, turn_index)
);

create table if not exists turn_feedback (
    id uuid primary key default gen_random_uuid(),
    turn_id uuid references chat_turns(id),
    external_session_id text not null,
    trace_id text not null,
    rating text not null check (rating in ('good', 'bad')),
    reason_code text null,
    comment text not null default '',
    channel text not null,
    user_id text null,
    external_user_id text null,
    created_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists turn_reviews (
    id uuid primary key default gen_random_uuid(),
    turn_id uuid references chat_turns(id),
    priority text not null check (priority in ('P0', 'P1', 'P2', 'P3')),
    review_status text not null check (
        review_status in ('pending', 'reviewed', 'fix_planned', 'fixed', 'eval_exported', 'wont_fix')
    ),
    failure_layer text null,
    failure_reason text not null default '',
    expected_answer_notes text not null default '',
    reviewer text not null,
    should_add_to_eval boolean not null default false,
    should_update_knowledge boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists eval_candidates (
    id uuid primary key default gen_random_uuid(),
    turn_id uuid references chat_turns(id),
    candidate_status text not null check (candidate_status in ('candidate', 'curated', 'exported', 'rejected')),
    testset_name text null,
    case_json jsonb not null default '{}'::jsonb,
    exported_path text null,
    created_at timestamptz not null default now(),
    exported_at timestamptz null
);

create table if not exists knowledge_improvement_tasks (
    id uuid primary key default gen_random_uuid(),
    turn_id uuid references chat_turns(id),
    task_status text not null check (task_status in ('open', 'in_progress', 'merged', 'rejected')),
    knowledge_area text not null,
    gap_summary text not null,
    proposed_source text not null default '',
    owner text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_chat_turns_trace_id on chat_turns(trace_id);
create index if not exists idx_chat_turns_session_turn on chat_turns(external_session_id, turn_index);
create index if not exists idx_chat_turns_channel_created on chat_turns(channel, created_at desc);
create index if not exists idx_chat_turns_fallback_created on chat_turns(fallback_used, created_at desc);
create index if not exists idx_turn_feedback_turn_id on turn_feedback(turn_id);
create index if not exists idx_turn_feedback_rating_created on turn_feedback(rating, created_at desc);
create index if not exists idx_turn_reviews_priority_status on turn_reviews(priority, review_status);
