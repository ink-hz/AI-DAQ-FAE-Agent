import { FormEvent, ReactNode, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  RefreshCw,
} from 'lucide-react';
import {
  createQaItem,
  fetchQaItems,
  fetchReviewFeedback,
  fetchReviewMetrics,
  fetchReviewSession,
  fetchReviewSessions,
  promoteQaItem,
  saveTurnDecision,
  sourceLabel,
  updateQaItem,
} from './api';
import type {
  QaItemPayload,
  ReviewFeedback,
  ReviewFeedbackRow,
  ReviewMetrics,
  ReviewQaItem,
  ReviewSessionDetail,
  ReviewSessionSummary,
  ReviewTurn,
  ReviewTurnReview,
} from './types';

type ReviewTab = 'feedback' | 'sessions' | 'qa';

type ReviewCenterProps = {
  initialTab?: ReviewTab;
  initialFeedback?: ReviewFeedbackRow[];
  initialFeedbackTotal?: number;
  initialSessionDetail?: ReviewSessionDetail | null;
  initialSessions?: ReviewSessionSummary[];
  initialSessionTotal?: number;
  initialSessionLimit?: number;
  initialSessionOffset?: number;
  initialQaItems?: ReviewQaItem[];
  initialQaTotal?: number;
  initialQaLimit?: number;
  initialQaOffset?: number;
};

type SessionFilters = {
  rating: string;
  priority: string;
  review_status: string;
  q: string;
};

type QaFilters = {
  source_type: string;
  review_status: string;
  tag: string;
  q: string;
};

const DEFAULT_SESSION_FILTERS: SessionFilters = {
  rating: 'bad',
  priority: '',
  review_status: '',
  q: '',
};

const DEFAULT_QA_FILTERS: QaFilters = {
  source_type: 'knowledge_qa',
  review_status: 'pending',
  tag: '',
  q: '',
};

export function ReviewCenter({
  initialTab = 'feedback',
  initialFeedback = [],
  initialFeedbackTotal,
  initialSessionDetail = null,
  initialSessions = [],
  initialSessionTotal,
  initialSessionLimit = 50,
  initialSessionOffset = 0,
  initialQaItems = [],
  initialQaTotal,
  initialQaLimit = 50,
  initialQaOffset = 0,
}: ReviewCenterProps = {}) {
  const [tab, setTab] = useState<ReviewTab>(initialTab);
  const [metrics, setMetrics] = useState<ReviewMetrics>({});
  const [feedbackRows, setFeedbackRows] = useState<ReviewFeedbackRow[]>(initialFeedback);
  const [feedbackTotal, setFeedbackTotal] = useState(initialFeedbackTotal ?? initialFeedback.length);
  const [feedbackRating, setFeedbackRating] = useState('bad');
  const [sessions, setSessions] = useState<ReviewSessionSummary[]>(initialSessions);
  const [sessionTotal, setSessionTotal] = useState(initialSessionTotal ?? initialSessions.length);
  const [sessionLimit, setSessionLimit] = useState(initialSessionLimit);
  const [sessionOffset, setSessionOffset] = useState(initialSessionOffset);
  const [sessionDetail, setSessionDetail] = useState<ReviewSessionDetail | null>(initialSessionDetail);
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [qaItems, setQaItems] = useState<ReviewQaItem[]>(initialQaItems);
  const [qaTotal, setQaTotal] = useState(initialQaTotal ?? initialQaItems.length);
  const [qaLimit, setQaLimit] = useState(initialQaLimit);
  const [qaOffset, setQaOffset] = useState(initialQaOffset);
  const [selectedQaId, setSelectedQaId] = useState('');
  const [selectedTurnId, setSelectedTurnId] = useState('');
  const [sessionFilters, setSessionFilters] = useState<SessionFilters>(DEFAULT_SESSION_FILTERS);
  const [qaFilters, setQaFilters] = useState<QaFilters>(DEFAULT_QA_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const selectedQaItem = qaItems.find((item) => item.id === selectedQaId) ?? (
    tab === 'qa' ? qaItems[0] : undefined
  );
  const effectiveSelectedQaId = selectedQaItem?.id || '';
  const sessionCurrentPage = Math.floor(sessionOffset / sessionLimit) + 1;
  const sessionPageCount = Math.max(1, Math.ceil(sessionTotal / sessionLimit));
  const sessionCanGoPrevious = sessionOffset > 0;
  const sessionCanGoNext = sessionOffset + sessionLimit < sessionTotal;
  const qaCurrentPage = Math.floor(qaOffset / qaLimit) + 1;
  const qaPageCount = Math.max(1, Math.ceil(qaTotal / qaLimit));
  const qaCanGoPrevious = qaOffset > 0;
  const qaCanGoNext = qaOffset + qaLimit < qaTotal;

  useEffect(() => {
    void refresh();
  }, []);

  async function refresh(
    nextQaFilters: QaFilters = qaFilters,
    nextQaLimit: number = qaLimit,
    nextQaOffset: number = qaOffset,
    nextSessionFilters: SessionFilters = sessionFilters,
    nextSessionLimit: number = sessionLimit,
    nextSessionOffset: number = sessionOffset,
  ) {
    setError(null);
    try {
      const [nextMetrics, nextSessionsPage, nextQaItems, nextFeedback] = await Promise.all([
        fetchReviewMetrics(),
        fetchReviewSessions({
          ...sessionFilterParams(nextSessionFilters),
          limit: nextSessionLimit,
          offset: nextSessionOffset,
        }),
        fetchQaItems({ ...qaFilterParams(nextQaFilters), limit: nextQaLimit, offset: nextQaOffset }),
        fetchReviewFeedback({ rating: feedbackRating, limit: 100, offset: 0 }),
      ]);
      setMetrics(nextMetrics);
      setFeedbackRows(nextFeedback.items);
      setFeedbackTotal(nextFeedback.total ?? nextFeedback.items.length);
      setSessions(nextSessionsPage.items);
      setSessionTotal(nextSessionsPage.total ?? nextSessionsPage.items.length);
      setSessionLimit(nextSessionsPage.limit ?? nextSessionLimit);
      setSessionOffset(nextSessionsPage.offset ?? nextSessionOffset);
      setQaItems(nextQaItems.items);
      setQaTotal(nextQaItems.total ?? nextQaItems.items.length);
      setQaLimit(nextQaItems.limit ?? nextQaLimit);
      setQaOffset(nextQaItems.offset ?? nextQaOffset);
      setSelectedQaId((current) => (
        nextQaItems.items.some((item) => item.id === current) ? current : ''
      ));
      if (selectedSessionId && nextSessionsPage.items.some((item) => item.external_session_id === selectedSessionId)) {
        const detail = await fetchReviewSession(selectedSessionId);
        setSessionDetail(detail);
      } else if (selectedSessionId) {
        setSelectedSessionId('');
        setSessionDetail(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function refreshFeedback(rating: string) {
    setError(null);
    try {
      const next = await fetchReviewFeedback({ rating, limit: 100, offset: 0 });
      setFeedbackRows(next.items);
      setFeedbackTotal(next.total ?? next.items.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitSessionFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSessionOffset(0);
    await refresh(qaFilters, qaLimit, qaOffset, sessionFilters, sessionLimit, 0);
  }

  async function resetSessionFilters() {
    setSessionFilters(DEFAULT_SESSION_FILTERS);
    setSessionOffset(0);
    await refresh(qaFilters, qaLimit, qaOffset, DEFAULT_SESSION_FILTERS, sessionLimit, 0);
  }

  async function goToSessionPage(nextOffset: number) {
    const safeOffset = Math.max(0, nextOffset);
    setSessionOffset(safeOffset);
    await refresh(qaFilters, qaLimit, qaOffset, sessionFilters, sessionLimit, safeOffset);
  }

  async function changeSessionPageSize(value: string) {
    const nextLimit = Number(value);
    if (!Number.isFinite(nextLimit) || nextLimit <= 0) return;
    setSessionLimit(nextLimit);
    setSessionOffset(0);
    await refresh(qaFilters, qaLimit, qaOffset, sessionFilters, nextLimit, 0);
  }

  async function submitQaFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQaOffset(0);
    await refresh(qaFilters, qaLimit, 0);
  }

  async function resetQaFilters() {
    setQaFilters(DEFAULT_QA_FILTERS);
    setQaOffset(0);
    await refresh(DEFAULT_QA_FILTERS, qaLimit, 0);
  }

  async function goToQaPage(nextOffset: number) {
    const safeOffset = Math.max(0, nextOffset);
    setQaOffset(safeOffset);
    await refresh(qaFilters, qaLimit, safeOffset);
  }

  async function changeQaPageSize(value: string) {
    const nextLimit = Number(value);
    if (!Number.isFinite(nextLimit) || nextLimit <= 0) return;
    setQaLimit(nextLimit);
    setQaOffset(0);
    await refresh(qaFilters, nextLimit, 0);
  }

  async function selectSession(externalSessionId: string) {
    setSelectedSessionId(externalSessionId);
    setError(null);
    try {
      const detail = await fetchReviewSession(externalSessionId);
      setSessionDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const turnId = String(form.get('turn_id') || selectedTurnId).trim();
    if (!turnId) {
      setError('需要 turn_id 才能保存复审');
      return;
    }

    try {
      const result = await saveTurnDecision(turnId, {
        priority: String(form.get('priority') || 'P1') as 'P0' | 'P1' | 'P2' | 'P3',
        review_status: String(form.get('review_status') || 'fix_planned'),
        failure_layer: String(form.get('failure_layer') || ''),
        failure_reason: String(form.get('failure_reason') || ''),
        corrected_answer: String(form.get('corrected_answer') || ''),
        flags: {
          add_to_eval: form.get('add_to_eval') === 'on',
          update_knowledge: form.get('update_knowledge') === 'on',
          create_qa: form.get('create_qa') === 'on',
        },
      });
      setSaved(result.review_id);
      setError(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function submitQaItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const question = String(form.get('question') || '').trim();
    if (!question) {
      setError('需要问题才能保存 QA 候选');
      return;
    }
    const payload: QaItemPayload = {
      source_type: String(form.get('source_type') || 'manual') as QaItemPayload['source_type'],
      source_ref: String(form.get('source_ref') || ''),
      question,
      original_answer: String(form.get('original_answer') || ''),
      reviewed_answer: String(form.get('reviewed_answer') || ''),
      product_tags: splitTags(form.get('product_tags')),
      scenario_tags: splitTags(form.get('scenario_tags')),
      technical_tags: splitTags(form.get('technical_tags')),
      sdk_tags: splitTags(form.get('sdk_tags')),
      quality_score: numberOrNull(form.get('quality_score')),
      review_status: String(form.get('review_status') || 'pending') as QaItemPayload['review_status'],
      review_notes: String(form.get('review_notes') || ''),
    };
    try {
      const result = selectedQaItem
        ? await updateQaItem(selectedQaItem.id, payload)
        : await createQaItem(payload);
      setSelectedQaId(result.id);
      setSaved(`qa:${result.id}`);
      setError(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function promoteSelectedQaItem() {
    if (!selectedQaItem) {
      setError('需要先选择 QA 候选才能标记入库');
      return;
    }
    try {
      await promoteQaItem(selectedQaItem.id);
      setSaved(`qa:${selectedQaItem.id}:candidate`);
      setError(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <main className="review-page-shell">
      <section className="review-workspace">
      <header className="review-header">
        <div>
          <h1>AI DAQ FAE Review Center</h1>
          <p>管理员复审工作台</p>
        </div>
        <button className="secondary-button" type="button" onClick={() => void refresh()}>
          <RefreshCw size={16} />
          刷新
        </button>
      </header>

      <div className="review-metrics" aria-label="复审指标">
        <Metric icon={<AlertTriangle size={16} />} label="待复审差评" value={metrics.bad_feedback_pending ?? 0} />
        <Metric icon={<Database size={16} />} label="QA 待审" value={metrics.qa_pending ?? 0} />
        <Metric icon={<CheckCircle2 size={16} />} label="候选入库" value={metrics.qa_candidates ?? 0} />
      </div>

      <div className="review-tabs" role="tablist" aria-label="复审视图">
        <button
          className={tab === 'feedback' ? 'active' : ''}
          type="button"
          onClick={() => setTab('feedback')}
        >
          负反馈
        </button>
        <button
          className={tab === 'sessions' ? 'active' : ''}
          type="button"
          onClick={() => setTab('sessions')}
        >
          Session 回放
        </button>
        <button
          className={tab === 'qa' ? 'active' : ''}
          type="button"
          onClick={() => setTab('qa')}
        >
          QA 复审
        </button>
      </div>

      {error && <div className="error-line">{error}</div>}
      {saved && <div className="review-save-line">已保存 review: {saved}</div>}

      {tab === 'feedback' ? (
        <FeedbackListView
          rows={feedbackRows}
          total={feedbackTotal}
          rating={feedbackRating}
          onRatingChange={(value) => {
            setFeedbackRating(value);
            void refreshFeedback(value);
          }}
          onRefresh={() => void refresh()}
        />
      ) : tab === 'sessions' ? (
        <>
          <form className="session-filter-bar" onSubmit={submitSessionFilters}>
            <label>
              <span>反馈</span>
              <select
                name="session_rating_filter"
                value={sessionFilters.rating}
                onChange={(event) => setSessionFilters({ ...sessionFilters, rating: event.target.value })}
              >
                <option value="bad">bad</option>
                <option value="good">good</option>
                <option value="">全部反馈</option>
              </select>
            </label>
            <label>
              <span>优先级</span>
              <select
                name="session_priority_filter"
                value={sessionFilters.priority}
                onChange={(event) => setSessionFilters({ ...sessionFilters, priority: event.target.value })}
              >
                <option value="">全部优先级</option>
                <option value="P0">P0</option>
                <option value="P1">P1</option>
                <option value="P2">P2</option>
                <option value="P3">P3</option>
              </select>
            </label>
            <label>
              <span>状态</span>
              <select
                name="session_status_filter"
                value={sessionFilters.review_status}
                onChange={(event) => setSessionFilters({ ...sessionFilters, review_status: event.target.value })}
              >
                <option value="">全部状态</option>
                <option value="pending">pending</option>
                <option value="reviewed">reviewed</option>
                <option value="fix_planned">fix_planned</option>
                <option value="fixed">fixed</option>
                <option value="wont_fix">wont_fix</option>
              </select>
            </label>
            <label>
              <span>关键词</span>
              <input
                name="session_q_filter"
                placeholder="问题或答案关键词"
                value={sessionFilters.q}
                onChange={(event) => setSessionFilters({ ...sessionFilters, q: event.target.value })}
              />
            </label>
            <div className="session-filter-actions">
              <button className="primary-button" type="submit">查询</button>
              <button className="secondary-button" type="button" onClick={resetSessionFilters}>重置</button>
            </div>
          </form>

          <div className="review-grid session-review-grid">
            <div className="review-list-column">
              <div className="review-list">
                {sessions.length === 0 && (
                  <div className="review-empty-row">暂无待复审 session，或当前 PG 不可用。</div>
                )}
                {sessions.map((item) => (
                  <button
                    className={`review-row ${item.external_session_id === selectedSessionId ? 'active' : ''}`}
                    type="button"
                    key={item.external_session_id}
                    onClick={() => void selectSession(item.external_session_id)}
                    title={item.external_session_id}
                  >
                    <strong>{item.first_question || item.external_session_id}</strong>
                    <span>{item.bad_feedback_count ?? 0} bad · {item.turns_count ?? 0} turns</span>
                  </button>
                ))}
              </div>
              <div className="session-pagination" aria-label="Session 分页">
                <div>
                  <strong>第 {sessionCurrentPage} / {sessionPageCount} 页</strong>
                  <span>共 {sessionTotal} 条</span>
                </div>
                <label>
                  <span>每页</span>
                  <select
                    name="session_page_size"
                    value={sessionLimit}
                    onChange={(event) => void changeSessionPageSize(event.target.value)}
                  >
                    <option value={20}>20</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </label>
                <div className="session-pagination-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!sessionCanGoPrevious}
                    onClick={() => void goToSessionPage(sessionOffset - sessionLimit)}
                  >
                    上一页
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!sessionCanGoNext}
                    onClick={() => void goToSessionPage(sessionOffset + sessionLimit)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </div>

          <SessionDetailPanel
            detail={sessionDetail}
            onReviewTurn={(turnId) => setSelectedTurnId(turnId)}
          />

          <form className="review-form" onSubmit={submitDecision}>
            <input
              name="turn_id"
              placeholder="turn_id"
              value={selectedTurnId}
              onChange={(event) => setSelectedTurnId(event.target.value)}
            />
            <select name="priority" defaultValue="P1">
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
              <option>P3</option>
            </select>
            <select name="review_status" defaultValue="fix_planned">
              <option value="reviewed">reviewed</option>
              <option value="fix_planned">fix_planned</option>
              <option value="fixed">fixed</option>
              <option value="wont_fix">wont_fix</option>
            </select>
            <select name="failure_layer" defaultValue="capability evidence">
              <option value="channel">channel</option>
              <option value="context">context</option>
              <option value="planner">planner</option>
              <option value="capability evidence">capability evidence</option>
              <option value="synthesis">synthesis</option>
              <option value="trace/eval">trace/eval</option>
            </select>
            <textarea name="failure_reason" placeholder="failure_reason" />
            <textarea name="corrected_answer" placeholder="corrected_answer" />
            <label><input name="add_to_eval" type="checkbox" /> 加入错误回放测试集</label>
            <label><input name="update_knowledge" type="checkbox" /> 创建知识库改进任务</label>
            <label><input name="create_qa" type="checkbox" /> 转为 QA 经验库候选</label>
            <button className="primary-button" type="submit">保存复审</button>
          </form>
        </div>
        </>
      ) : (
        <>
          <form className="qa-filter-bar" onSubmit={submitQaFilters}>
            <label>
              <span>来源</span>
              <select
                name="source_type_filter"
                value={qaFilters.source_type}
                onChange={(event) => setQaFilters({ ...qaFilters, source_type: event.target.value })}
              >
                <option value="">全部来源</option>
                <option value="knowledge_qa">knowledge_qa</option>
                <option value="manual">manual</option>
                <option value="chat_turn">chat_turn</option>
                <option value="corrected_feedback">corrected_feedback</option>
              </select>
            </label>
            <label>
              <span>状态</span>
              <select
                name="review_status_filter"
                value={qaFilters.review_status}
                onChange={(event) => setQaFilters({ ...qaFilters, review_status: event.target.value })}
              >
                <option value="">全部状态</option>
                <option value="pending">pending</option>
                <option value="approved">approved</option>
                <option value="needs_fix">needs_fix</option>
                <option value="rejected">rejected</option>
                <option value="candidate">candidate</option>
              </select>
            </label>
            <label>
              <span>标签</span>
              <input
                name="tag_filter"
                placeholder="产品 / 场景 / 技术 / SDK"
                value={qaFilters.tag}
                onChange={(event) => setQaFilters({ ...qaFilters, tag: event.target.value })}
              />
            </label>
            <label>
              <span>关键词</span>
              <input
                name="q_filter"
                placeholder="问题或答案关键词"
                value={qaFilters.q}
                onChange={(event) => setQaFilters({ ...qaFilters, q: event.target.value })}
              />
            </label>
            <div className="qa-filter-actions">
              <button className="primary-button" type="submit">查询</button>
              <button className="secondary-button" type="button" onClick={resetQaFilters}>重置</button>
            </div>
          </form>

          <div className="qa-review-grid">
            <div className="review-list-column">
              <div className="review-list">
                {qaItems.length === 0 && (
                  <div className="review-empty-row">暂无 QA 候选，可调整筛选条件或在右侧新建人工草稿。</div>
                )}
                {qaItems.map((item) => (
                  <button
                    className={`review-row qa-review-row ${item.id === effectiveSelectedQaId ? 'active' : ''}`}
                    type="button"
                    key={item.id}
                    onClick={() => setSelectedQaId(item.id)}
                    title={item.question}
                  >
                    <strong>{item.question}</strong>
                    <div className="qa-answer-preview">
                      <span>
                        <b>原答案</b>
                        {previewText(item.original_answer)}
                      </span>
                      <span>
                        <b>复审答案</b>
                        {previewText(item.reviewed_answer)}
                      </span>
                    </div>
                    <span>
                      {item.review_status || 'pending'} · {item.source_type || 'manual'} · {item.quality_score ?? '-'}分
                    </span>
                  </button>
                ))}
              </div>
              <div className="qa-pagination" aria-label="QA 分页">
                <div>
                  <strong>第 {qaCurrentPage} / {qaPageCount} 页</strong>
                  <span>共 {qaTotal} 条</span>
                </div>
                <label>
                  <span>每页</span>
                  <select
                    name="qa_page_size"
                    value={qaLimit}
                    onChange={(event) => void changeQaPageSize(event.target.value)}
                  >
                    <option value={20}>20</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </label>
                <div className="qa-pagination-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!qaCanGoPrevious}
                    onClick={() => void goToQaPage(qaOffset - qaLimit)}
                  >
                    上一页
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={!qaCanGoNext}
                    onClick={() => void goToQaPage(qaOffset + qaLimit)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </div>

            <form className="review-form qa-review-form" key={selectedQaItem?.id || 'new'} onSubmit={submitQaItem}>
              <label className="qa-field">
                <span>来源类型</span>
                <select name="source_type" defaultValue={selectedQaItem?.source_type || 'manual'}>
                  <option value="manual">manual</option>
                  <option value="knowledge_qa">knowledge_qa</option>
                  <option value="chat_turn">chat_turn</option>
                  <option value="corrected_feedback">corrected_feedback</option>
                </select>
              </label>
              <label className="qa-field">
                <span>来源编号</span>
                <input name="source_ref" placeholder="source_ref" defaultValue={selectedQaItem?.source_ref || ''} />
              </label>
              <label className="qa-field">
                <span>问题</span>
                <textarea name="question" placeholder="question" defaultValue={selectedQaItem?.question || ''} />
              </label>
              <label className="qa-field">
                <span>原答案</span>
                <textarea name="original_answer" placeholder="original_answer" defaultValue={selectedQaItem?.original_answer || ''} />
              </label>
              <label className="qa-field">
                <span>复审答案</span>
                <textarea name="reviewed_answer" placeholder="reviewed_answer" defaultValue={selectedQaItem?.reviewed_answer || ''} />
              </label>
              <div className="qa-form-row">
                <label className="qa-field">
                  <span>评分</span>
                  <input name="quality_score" placeholder="quality_score" defaultValue={selectedQaItem?.quality_score ?? ''} />
                </label>
                <label className="qa-field">
                  <span>状态</span>
                  <select name="review_status" defaultValue={selectedQaItem?.review_status || 'pending'}>
                    <option value="pending">pending</option>
                    <option value="approved">approved</option>
                    <option value="needs_fix">needs_fix</option>
                    <option value="rejected">rejected</option>
                    <option value="candidate">candidate</option>
                  </select>
                </label>
              </div>
              <label className="qa-field">
                <span>产品标签</span>
                <input name="product_tags" placeholder="product_tags" defaultValue={joinTags(selectedQaItem?.product_tags)} />
              </label>
              <label className="qa-field">
                <span>场景标签</span>
                <input name="scenario_tags" placeholder="scenario_tags" defaultValue={joinTags(selectedQaItem?.scenario_tags)} />
              </label>
              <label className="qa-field">
                <span>技术标签</span>
                <input name="technical_tags" placeholder="technical_tags" defaultValue={joinTags(selectedQaItem?.technical_tags)} />
              </label>
              <label className="qa-field">
                <span>SDK 标签</span>
                <input name="sdk_tags" placeholder="sdk_tags" defaultValue={joinTags(selectedQaItem?.sdk_tags)} />
              </label>
              <label className="qa-field">
                <span>备注</span>
                <textarea name="review_notes" placeholder="review_notes" defaultValue={selectedQaItem?.review_notes || ''} />
              </label>
              <div className="qa-review-actions">
                <button className="primary-button" type="submit">保存 QA</button>
                <button className="secondary-button" type="button" onClick={promoteSelectedQaItem}>
                  标记入库候选
                </button>
              </div>
            </form>
          </div>
        </>
      )}
      </section>
    </main>
  );
}

function FeedbackListView({
  rows,
  total,
  rating,
  onRatingChange,
  onRefresh,
}: {
  rows: ReviewFeedbackRow[];
  total: number;
  rating: string;
  onRatingChange: (value: string) => void;
  onRefresh: () => void;
}) {
  return (
    <section className="feedback-view">
      <div className="feedback-toolbar">
        <div className="feedback-rating-switch" role="tablist" aria-label="反馈类型">
          <button
            className={rating === 'bad' ? 'active' : ''}
            type="button"
            onClick={() => onRatingChange('bad')}
          >
            只看差评
          </button>
          <button
            className={rating === '' ? 'active' : ''}
            type="button"
            onClick={() => onRatingChange('')}
          >
            全部反馈
          </button>
        </div>
        <span className="feedback-count">共 {total} 条</span>
        <button className="secondary-button" type="button" onClick={onRefresh}>
          刷新
        </button>
      </div>

      <ol className="feedback-list">
        {rows.length === 0 && (
          <li className="review-empty-row">暂无反馈，或当前 PG 不可用。</li>
        )}
        {rows.map((row) => (
          <FeedbackCard key={row.feedback_id} row={row} />
        ))}
      </ol>
    </section>
  );
}

function FeedbackCard({ row }: { row: ReviewFeedbackRow }) {
  const isBad = row.rating === 'bad';
  return (
    <li className={`feedback-card ${isBad ? 'feedback-card-bad' : 'feedback-card-good'}`}>
      <div className="feedback-card-head">
        <span className={`feedback-badge ${isBad ? 'bad' : 'good'}`}>
          {isBad ? '差评' : '好评'}
          {row.reason_code ? ` · ${row.reason_code}` : ''}
        </span>
        {row.synced_from && <span className="feedback-origin">{row.synced_from}</span>}
        {row.created_at && <span className="feedback-time">{row.created_at.slice(0, 19).replace('T', ' ')}</span>}
      </div>

      <div className="feedback-card-block">
        <span className="feedback-card-label">问题</span>
        <p className="feedback-card-question">{row.question || '(无问题文本)'}</p>
      </div>

      <div className="feedback-card-block">
        <span className="feedback-card-label">答案</span>
        <details className="feedback-card-answer-details">
          <summary>{previewText(row.answer)}</summary>
          <p className="feedback-card-answer-full">{row.answer || '(无答案)'}</p>
        </details>
      </div>

      {row.comment ? (
        <div className="feedback-card-comment">
          <span className="feedback-card-label">反馈内容</span>
          <p>{row.comment}</p>
        </div>
      ) : (
        <div className="feedback-card-comment feedback-card-comment-empty">
          <span className="feedback-card-label">反馈内容</span>
          <p>用户未留下文字说明</p>
        </div>
      )}
    </li>
  );
}

function SessionDetailPanel({
  detail,
  onReviewTurn,
}: {
  detail: ReviewSessionDetail | null;
  onReviewTurn: (turnId: string) => void;
}) {
  return (
    <section className="session-detail-panel">
      <header>
        <h3>Session 详情</h3>
        <span>trace_id</span>
        <span>复审此轮</span>
      </header>
      {!detail && <div className="review-empty-row">尚未选择 session。</div>}
      {detail?.turns.map((turn) => (
        <TurnReplayCard
          key={turn.id}
          turn={turn}
          feedback={detail.feedback.filter((item) => item.turn_id === turn.id)}
          reviews={detail.reviews.filter((item) => item.turn_id === turn.id)}
          onReviewTurn={onReviewTurn}
        />
      ))}
    </section>
  );
}

function TurnReplayCard({
  turn,
  feedback,
  reviews,
  onReviewTurn,
}: {
  turn: ReviewTurn;
  feedback: ReviewFeedback[];
  reviews: ReviewTurnReview[];
  onReviewTurn: (turnId: string) => void;
}) {
  const latestReview = reviews[reviews.length - 1];
  return (
    <article className="turn-replay-card">
      <div className="turn-replay-meta">
        <strong>#{turn.turn_index ?? '-'}</strong>
        <code>{turn.trace_id || 'trace_id empty'}</code>
        <button className="secondary-button" type="button" onClick={() => onReviewTurn(turn.id)}>
          复审此轮
        </button>
      </div>
      <div className="turn-replay-block">
        <span>Question</span>
        <p className="turn-replay-text turn-replay-question">{turn.question}</p>
      </div>
      <div className="turn-replay-block">
        <span>Answer</span>
        <p className="turn-replay-text turn-replay-answer">{turn.answer}</p>
      </div>
      <div className="turn-replay-signals">
        <span>{turn.outcome || 'outcome empty'}</span>
        <span>{turn.fallback_used ? 'fallback' : 'no fallback'}</span>
        <span>{turn.duration_ms ?? '-'} ms</span>
        <span>{feedbackSummary(feedback)}</span>
        <span>{latestReview ? `${latestReview.priority || '-'} ${latestReview.review_status || '-'}` : 'unreviewed'}</span>
      </div>
      <TurnReplayEvidence turn={turn} feedback={feedback} reviews={reviews} />
    </article>
  );
}

function TurnReplayEvidence({
  turn,
  feedback,
  reviews,
}: {
  turn: ReviewTurn;
  feedback: ReviewFeedback[];
  reviews: ReviewTurnReview[];
}) {
  const sources = turn.sources || [];
  const stages = turn.stages || [];
  return (
    <div className="turn-replay-evidence">
      <details className="turn-replay-detail">
        <summary>Sources ({sources.length})</summary>
        {sources.length === 0 ? (
          <p className="turn-replay-muted">No sources recorded</p>
        ) : (
          <ol className="turn-replay-list">
            {sources.map((source, index) => (
              <li key={`${sourceLabel(source)}-${index}`}>
                <strong>{sourceLabel(source)}</strong>
                <span>{sourceMeta(source)}</span>
              </li>
            ))}
          </ol>
        )}
      </details>

      <details className="turn-replay-detail">
        <summary>Thinking ({stages.length})</summary>
        {stages.length === 0 ? (
          <p className="turn-replay-muted">No thinking stages recorded</p>
        ) : (
          <ol className="turn-replay-list">
            {stages.map((stage, index) => (
              <li key={`${stage.agent || stage.stage || 'stage'}-${index}`}>
                <strong>{stage.agent || stage.stage || `Stage ${index + 1}`}</strong>
                <span>{stage.message || stage.status || 'no message'}</span>
                {stage.elapsed_ms !== undefined && <em>{stage.elapsed_ms} ms</em>}
              </li>
            ))}
          </ol>
        )}
      </details>

      <details className="turn-replay-detail">
        <summary>Feedback ({feedback.length})</summary>
        {feedback.length === 0 ? (
          <p className="turn-replay-muted">No feedback recorded</p>
        ) : (
          <ol className="turn-replay-list">
            {feedback.map((item) => (
              <li key={item.id}>
                <strong>{item.reason_code ? `${item.rating}:${item.reason_code}` : item.rating}</strong>
                <span>{item.comment || 'no comment'}</span>
              </li>
            ))}
          </ol>
        )}
      </details>

      <details className="turn-replay-detail">
        <summary>Review History ({reviews.length})</summary>
        {reviews.length === 0 ? (
          <p className="turn-replay-muted">No review recorded</p>
        ) : (
          <ol className="turn-replay-list">
            {reviews.map((review) => (
              <li key={review.id}>
                <strong>{`${review.priority || '-'} ${review.review_status || '-'}`}</strong>
                <span>{review.failure_layer || 'layer empty'} · {review.failure_reason || 'reason empty'}</span>
              </li>
            ))}
          </ol>
        )}
      </details>
    </div>
  );
}

function splitTags(value: FormDataEntryValue | null): string[] {
  if (typeof value !== 'string') return [];
  return value
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinTags(value: string[] | undefined): string {
  return value?.join(', ') || '';
}

function numberOrNull(value: FormDataEntryValue | null): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function previewText(value: string | undefined): string {
  const text = value?.trim();
  if (!text) return '未填写';
  return text.length > 96 ? `${text.slice(0, 96)}...` : text;
}

function sessionFilterParams(filters: SessionFilters) {
  return {
    rating: filters.rating,
    priority: filters.priority,
    review_status: filters.review_status,
    q: filters.q.trim(),
  };
}

function qaFilterParams(filters: QaFilters) {
  return {
    source_type: filters.source_type,
    review_status: filters.review_status,
    tag: filters.tag.trim(),
    q: filters.q.trim(),
  };
}

function feedbackSummary(feedback: ReviewFeedback[]): string {
  if (feedback.length === 0) return 'no feedback';
  return feedback.map((item) => (
    item.reason_code ? `${item.rating}:${item.reason_code}` : item.rating
  )).join(', ');
}

function sourceMeta(source: { path?: string; lines?: string; type?: string; kb_id?: string }): string {
  const parts = [source.type, source.kb_id, source.path, source.lines].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'source metadata empty';
}

function Metric({ icon, label, value }: { icon: ReactNode; label: string; value: number }) {
  return (
    <div className="review-metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
