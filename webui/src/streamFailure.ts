import { ChatStreamError } from './api';
import type { ChatMessage } from './types';

export function mergeStreamFailure(
  message: ChatMessage,
  error: ChatStreamError,
): ChatMessage {
  const partial = error.partialTurn;
  const shortRequestId = error.clientRequestId.slice(0, 8);

  return {
    ...message,
    content: partial.content || `请求失败（${error.phase}，请求 ${shortRequestId}）`,
    sources: partial.sources,
    stages: partial.stages,
    done: partial.done,
    streamError: {
      phase: error.phase,
      clientRequestId: error.clientRequestId,
      message: error.message,
    },
  };
}
