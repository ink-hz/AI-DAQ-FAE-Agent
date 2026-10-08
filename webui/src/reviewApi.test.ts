import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createQaItem,
  fetchReviewFeedback,
  fetchReviewMetrics,
  fetchReviewSession,
  fetchReviewSessions,
  fetchQaItems,
  promoteQaItem,
  saveTurnDecision,
  updateQaItem,
} from './api';

describe('Review Center API helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches session review list with filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{ external_session_id: 'session-1' }],
        total: 12,
        limit: 10,
        offset: 0,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchReviewSessions({ rating: 'bad', limit: 10, offset: 0 });

    expect(result.items[0].external_session_id).toBe('session-1');
    expect(result.total).toBe(12);
    expect(result.limit).toBe(10);
    expect(result.offset).toBe(0);
    expect(fetchMock.mock.calls[0][0]).toBe('/review/sessions?rating=bad&limit=10&offset=0');
  });

  it('fetches a session replay detail', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session: { external_session_id: 'session-1' },
        turns: [{ id: 'turn-1', question: 'Q', answer: 'A' }],
        feedback: [],
        reviews: [],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchReviewSession('session/with space');

    expect(result.turns[0].id).toBe('turn-1');
    expect(fetchMock.mock.calls[0][0]).toBe('/review/sessions/session%2Fwith%20space');
  });

  it('saves a turn decision with flags', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ review_id: 'review-1' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await saveTurnDecision('turn-1', {
      priority: 'P1',
      review_status: 'fix_planned',
      failure_reason: 'wrong answer',
      flags: { add_to_eval: true, create_qa: true },
    });

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(fetchMock.mock.calls[0][0]).toBe('/review/turns/turn-1/decision');
    expect(JSON.parse(String(request.body))).toMatchObject({
      priority: 'P1',
      review_status: 'fix_planned',
      failure_reason: 'wrong answer',
      flags: { add_to_eval: true, create_qa: true },
    });
  });

  it('fetches metrics', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ bad_feedback_pending: 2 }),
    }));

    await expect(fetchReviewMetrics()).resolves.toMatchObject({ bad_feedback_pending: 2 });
  });

  it('fetches flat feedback rows with question answer comment', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{
          feedback_id: 'fb-1',
          rating: 'bad',
          reason_code: 'fact_error',
          comment: '结论错了。',
          question: 'Q',
          answer: 'A',
        }],
        total: 7,
        limit: 20,
        offset: 0,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchReviewFeedback({ rating: 'bad', limit: 20, offset: 0 });

    expect(result.items[0].comment).toBe('结论错了。');
    expect(result.items[0].question).toBe('Q');
    expect(result.total).toBe(7);
    expect(fetchMock.mock.calls[0][0]).toBe('/review/feedback?rating=bad&limit=20&offset=0');
  });

  it('fetches QA review items with filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ items: [{ id: 'qa-1', question: 'Q' }] }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchQaItems({ review_status: 'pending', tag: 'Gemini', limit: 20 });

    expect(result.items[0].id).toBe('qa-1');
    expect(fetchMock.mock.calls[0][0]).toBe('/review/qa-items?review_status=pending&tag=Gemini&limit=20');
  });

  it('creates updates and promotes QA review items', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'qa-1' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'qa-1', updated: true }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'qa-1', updated: true }) });
    vi.stubGlobal('fetch', fetchMock);

    await createQaItem({ question: 'Q', reviewed_answer: 'A', source_type: 'manual' });
    await updateQaItem('qa-1', { review_status: 'approved', quality_score: 5 });
    await promoteQaItem('qa-1');

    expect(fetchMock.mock.calls[0][0]).toBe('/review/qa-items');
    expect(fetchMock.mock.calls[1][0]).toBe('/review/qa-items/qa-1');
    expect(fetchMock.mock.calls[2][0]).toBe('/review/qa-items/qa-1/promote');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('PATCH');
  });
});
