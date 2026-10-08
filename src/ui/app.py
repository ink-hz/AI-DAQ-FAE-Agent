"""Streamlit 单页 chat UI。

启动:streamlit run src/ui/app.py
"""
import json
import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# 5 桶 key → 中文展示标签(§2.6 末段:done.bucket 帮助用户/FAE Reviewer 验证 Router 判对没)
BUCKET_LABEL_ZH = {
    "catalog_overview": "产品矩阵概览",
    "spec_or_compat": "产品规格查询",
    "selection": "选型推荐",
    "fae_experience": "FAE 经验复用",
    "out_of_scope": "范围外(拒答)",
}


st.set_page_config(page_title="AI FAE Agent", layout="wide")
st.title("🔧 AI FAE Agent")
st.caption("Orbbec 内部 FAE+电商售前对话助手 · MVP")

with st.sidebar:
    channel = st.radio(
        "使用身份",
        ["fae", "ecom"],
        format_func=lambda x: "FAE 内部" if x == "fae" else "电商售前",
    )
    debug_sse = st.checkbox("显示原始 SSE 调试信息", value=False)
    if st.button("🆕 新会话"):
        st.session_state.pop("session_id", None)
        st.session_state["messages"] = []
        st.session_state["last_raw_sse"] = ""
        st.rerun()

if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "last_raw_sse" not in st.session_state:
    st.session_state["last_raw_sse"] = ""


def _source_label(s: dict) -> str:
    """sources 数组元素 → 一个简短可读 label。title 优先,fallback 到 kb_id / model。"""
    if not isinstance(s, dict):
        return str(s)
    return s.get("title") or s.get("kb_id") or s.get("model") or "(未命名)"


def _render_assistant_meta(bucket: str | None, sources: list | None, risk_notes: list | None) -> None:
    """assistant 消息尾部:bucket 标签 + 来源 + risk_notes。"""
    if bucket:
        label = BUCKET_LABEL_ZH.get(bucket, bucket)
        st.caption(f"识别为:{label}")
    if sources:
        labels = [_source_label(s) for s in sources]
        # 去重,保留顺序
        seen = set()
        uniq = []
        for lab in labels:
            if lab and lab not in seen:
                seen.add(lab)
                uniq.append(lab)
        if uniq:
            st.caption("来源:" + ", ".join(uniq))
    if risk_notes:
        for note in risk_notes:
            st.warning("⚠️ " + str(note))


# 历史回放(包括 bucket 标签 / 来源 / risk_notes)
for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            _render_assistant_meta(
                msg.get("bucket"),
                msg.get("sources"),
                msg.get("risk_notes"),
            )

if prompt := st.chat_input("问点什么…"):
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        text_buffer = ""
        sources_list: list = []
        risk_notes: list = []
        bucket: str | None = None
        template: str | None = None
        raw_log_lines: list[str] = []

        try:
            payload = {
                "message": prompt,
                "channel": channel,
                "session_id": st.session_state.get("session_id"),
            }
            with requests.post(
                f"{API_BASE}/chat", json=payload, stream=True, timeout=120
            ) as resp:
                resp.raise_for_status()
                current_event: str | None = None
                for raw in resp.iter_lines():
                    if not raw:
                        # 空行 = 一帧结束;清掉 current_event 让下一帧重置
                        current_event = None
                        continue
                    line = raw.decode("utf-8")
                    raw_log_lines.append(line)

                    if line.startswith("event: "):
                        current_event = line[len("event: "):].strip()
                        continue

                    if line.startswith("data: "):
                        raw_data = line[len("data: "):]
                        try:
                            data = json.loads(raw_data)
                        except json.JSONDecodeError:
                            continue

                        # 按当前 event name 分支
                        if current_event == "text_delta":
                            if isinstance(data, dict) and "delta" in data:
                                text_buffer += data["delta"]
                                placeholder.markdown(text_buffer)
                        elif current_event == "sources":
                            if isinstance(data, list):
                                sources_list = data
                        elif current_event == "done":
                            if isinstance(data, dict):
                                bucket = data.get("bucket") or bucket
                                template = data.get("template") or template
                                rn = data.get("risk_notes")
                                if isinstance(rn, list):
                                    risk_notes = rn
                                if data.get("session_id"):
                                    st.session_state["session_id"] = data["session_id"]
                        elif current_event == "session":
                            if isinstance(data, dict) and data.get("session_id"):
                                st.session_state["session_id"] = data["session_id"]
                        else:
                            # 无 event 名时的向后兼容:旧代码逻辑(裸 data 含 delta / session_id)
                            if isinstance(data, dict):
                                if "delta" in data:
                                    text_buffer += data["delta"]
                                    placeholder.markdown(text_buffer)
                                elif "session_id" in data:
                                    st.session_state["session_id"] = data["session_id"]
        except Exception as e:
            placeholder.error(f"请求失败:{e}")
            text_buffer = "(错误)"

        # 文本主体渲染完后,补上 bucket 标签 / 来源 / risk_notes
        _render_assistant_meta(bucket, sources_list, risk_notes)

    # 缓存原始 SSE 日志供 debug 勾选时展示
    st.session_state["last_raw_sse"] = "\n".join(raw_log_lines)

    st.session_state["messages"].append({
        "role": "assistant",
        "content": text_buffer,
        "bucket": bucket,
        "sources": sources_list,
        "risk_notes": risk_notes,
        # template 仅内部 debug 留存,不渲染
        "template": template,
    })

# debug 区块:勾选后展示最近一次的原始 SSE 流(便于 FAE Reviewer 找错分案例)
if debug_sse and st.session_state.get("last_raw_sse"):
    st.divider()
    st.subheader("原始 SSE 调试")
    st.code(st.session_state["last_raw_sse"], language="text")
