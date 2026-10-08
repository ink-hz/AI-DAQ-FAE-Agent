import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ReviewCenter } from './ReviewCenter';

describe('ReviewCenter', () => {
  it('uses a full-page admin shell instead of relying on the chat shell', () => {
    const html = renderToStaticMarkup(<ReviewCenter />);

    expect(html).toContain('review-page-shell');
    expect(html).toContain('AI DAQ FAE Review Center');
  });

  it('renders QA and session replay tabs', () => {
    const html = renderToStaticMarkup(<ReviewCenter />);

    expect(html).toContain('QA 复审');
    expect(html).toContain('Session 回放');
    expect(html).toContain('review-workspace');
    expect(html).toContain('review-metrics');
  });

  it('renders the turn decision form fields', () => {
    const html = renderToStaticMarkup(<ReviewCenter initialTab="sessions" />);

    expect(html).toContain('failure_layer');
    expect(html).toContain('failure_reason');
    expect(html).toContain('corrected_answer');
    expect(html).toContain('加入错误回放测试集');
    expect(html).toContain('转为 QA 经验库候选');
  });

  it('renders a session replay detail panel in the session tab', () => {
    const html = renderToStaticMarkup(<ReviewCenter initialTab="sessions" />);

    expect(html).toContain('session-detail-panel');
    expect(html).toContain('Session 详情');
    expect(html).toContain('复审此轮');
    expect(html).toContain('trace_id');
  });

  it('renders session list filters and pagination', () => {
    const html = renderToStaticMarkup(<ReviewCenter
      initialTab="sessions"
      initialSessions={[{
        external_session_id: 'session-1',
        first_question: '老板倒踩的问题',
        bad_feedback_count: 1,
        turns_count: 4,
      }]}
      initialSessionTotal={124}
      initialSessionLimit={20}
      initialSessionOffset={20}
    />);

    expect(html).toContain('session-filter-bar');
    expect(html).toContain('name="session_rating_filter"');
    expect(html).toContain('name="session_priority_filter"');
    expect(html).toContain('name="session_status_filter"');
    expect(html).toContain('name="session_q_filter"');
    expect(html).toContain('session-pagination');
    expect(html).toContain('第 2 / 7 页');
    expect(html).toContain('共 124 条');
    expect(html).toContain('name="session_page_size"');
    expect(html).toContain('老板倒踩的问题');
  });

  it('renders session replay answers as full text, not clamped previews', () => {
    const longAnswer = [
      '结论：这是一条需要完整复盘的回答。',
      '依据一：原始回答必须保留。',
      '依据二：多行内容不能只显示前几行。',
      '注意：Review 页面用于问题回放。',
      '下一步：FAE 需要看到完整答案再判断。',
    ].join('\n');
    const html = renderToStaticMarkup(<ReviewCenter
      initialTab="sessions"
      initialSessionDetail={{
        session: { external_session_id: 'session-1' },
        turns: [{
          id: 'turn-1',
          question: '这条 session 的完整回答是什么？',
          answer: longAnswer,
          trace_id: 'trace-1',
        }],
        feedback: [],
        reviews: [],
      }}
    />);

    expect(html).toContain('turn-replay-answer');
    expect(html).toContain('turn-replay-text');
    expect(html).toContain('下一步：FAE 需要看到完整答案再判断。');
  });

  it('renders session replay evidence details for review', () => {
    const html = renderToStaticMarkup(<ReviewCenter
      initialTab="sessions"
      initialSessionDetail={{
        session: { external_session_id: 'session-1' },
        turns: [{
          id: 'turn-1',
          turn_index: 2,
          question: 'Gemini 335L 的接口是什么？',
          answer: 'Gemini 335L 是 USB 型号。',
          trace_id: 'trace-1',
          sources: [{ title: 'Gemini 335L hardware', path: 'Knowledge/models/gemini_335l.md' }],
          stages: [
            { agent: 'Spec Agent', message: 'Spec Agent 开始取证', elapsed_ms: 11 },
            { agent: 'Spec Agent', message: 'Spec Agent 取证完成: full', elapsed_ms: 42 },
          ],
        }],
        feedback: [{
          id: 'feedback-1',
          turn_id: 'turn-1',
          rating: 'bad',
          reason_code: 'wrong_fact',
          comment: '老板倒踩：接口说错了。',
        }],
        reviews: [{
          id: 'review-1',
          turn_id: 'turn-1',
          priority: 'P0',
          review_status: 'fixed',
          failure_layer: 'capability evidence',
          failure_reason: '证据排序错误',
        }],
      }}
    />);

    expect(html).toContain('turn-replay-evidence');
    expect(html).toContain('Sources (1)');
    expect(html).toContain('Gemini 335L hardware');
    expect(html).toContain('Thinking (2)');
    expect(html).toContain('Spec Agent 取证完成: full');
    expect(html).toContain('Feedback (1)');
    expect(html).toContain('bad:wrong_fact');
    expect(html).toContain('老板倒踩：接口说错了。');
    expect(html).toContain('Review History (1)');
    expect(html).toContain('P0 fixed');
    expect(html).toContain('证据排序错误');
  });

  it('renders QA review workbench fields when QA tab is active', () => {
    const html = renderToStaticMarkup(<ReviewCenter initialTab="qa" />);

    expect(html).toContain('qa-review-grid');
    expect(html).toContain('reviewed_answer');
    expect(html).toContain('quality_score');
    expect(html).toContain('product_tags');
    expect(html).toContain('保存 QA');
    expect(html).toContain('标记入库候选');
  });

  it('shows QA question and answer previews and selects the first item by default', () => {
    const html = renderToStaticMarkup(<ReviewCenter
      initialTab="qa"
      initialQaItems={[{
        id: 'qa-1',
        source_type: 'knowledge_qa',
        source_ref: 'faq-001',
        question: 'Gemini 335Lg 是什么接口？',
        original_answer: 'Gemini 335Lg 是 USB 版本。',
        reviewed_answer: 'Gemini 335Lg 是 GMSL 版本，不应归入 USB 型号。',
        product_tags: ['Gemini 335Lg'],
        scenario_tags: ['规格事实'],
        technical_tags: ['interface'],
        sdk_tags: [],
        quality_score: 4,
        review_status: 'needs_fix',
      }]}
    />);

    expect(html).toContain('qa-answer-preview');
    expect(html).toContain('问题');
    expect(html).toContain('原答案');
    expect(html).toContain('复审答案');
    expect(html).toContain('Gemini 335Lg 是什么接口？');
    expect(html).toContain('Gemini 335Lg 是 USB 版本。');
    expect(html).toContain('Gemini 335Lg 是 GMSL 版本，不应归入 USB 型号。');
    expect(html).toContain('review-row active');
  });

  it('renders QA filters for source status tag and keyword search', () => {
    const html = renderToStaticMarkup(<ReviewCenter initialTab="qa" />);

    expect(html).toContain('qa-filter-bar');
    expect(html).toContain('来源');
    expect(html).toContain('状态');
    expect(html).toContain('标签');
    expect(html).toContain('关键词');
    expect(html).toContain('name="source_type_filter"');
    expect(html).toContain('value="knowledge_qa" selected');
    expect(html).toContain('name="review_status_filter"');
    expect(html).toContain('value="pending" selected');
    expect(html).toContain('查询');
    expect(html).toContain('重置');
  });

  it('renders QA pagination from total limit and offset', () => {
    const html = renderToStaticMarkup(<ReviewCenter
      initialTab="qa"
      initialQaItems={[{
        id: 'qa-1',
        source_type: 'knowledge_qa',
        source_ref: 'faq-001',
        question: 'Q',
        original_answer: 'A',
        reviewed_answer: 'SA',
        review_status: 'pending',
      }]}
      initialQaTotal={1858}
      initialQaLimit={50}
      initialQaOffset={0}
    />);

    expect(html).toContain('qa-pagination');
    expect(html).toContain('第 1 / 38 页');
    expect(html).toContain('共 1858 条');
    expect(html).toContain('上一页');
    expect(html).toContain('下一页');
    expect(html).toContain('name="qa_page_size"');
  });

  it('defaults to a simple negative-feedback list showing question answer and comment', () => {
    const html = renderToStaticMarkup(<ReviewCenter
      initialFeedback={[{
        feedback_id: 'fb-1',
        rating: 'bad',
        reason_code: 'fact_error',
        question: '请问Gemini 335Le有标准的内参吗',
        answer: '系统暂时无法生成可靠的 FAE 答复，请稍后重试。',
        comment: 'Gemini 335Le 没有一组所有相机通用的“标准内参”。它是每台相机出厂标定。',
        synced_from: 'prod',
      }]}
    />);

    expect(html).toContain('feedback-list');
    expect(html).toContain('feedback-card');
    // the three things the user wants, all visible without expanding
    expect(html).toContain('请问Gemini 335Le有标准的内参吗');
    expect(html).toContain('系统暂时无法生成可靠的 FAE 答复，请稍后重试。');
    expect(html).toContain('Gemini 335Le 没有一组所有相机通用的“标准内参”。它是每台相机出厂标定。');
    // comment is the prominent block, not hidden in a details summary
    expect(html).toContain('feedback-card-comment');
    expect(html).toContain('fact_error');
    expect(html).toContain('prod');
  });
});
