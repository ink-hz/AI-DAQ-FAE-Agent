alter table chat_turns
  add column if not exists question_at timestamptz null,
  add column if not exists answer_at timestamptz null;
