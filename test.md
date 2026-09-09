# llm.py 功能总结

> 源文件：`/home/400003460/.qwenpaw/workspaces/default/agent_in/llm.py`（251 行）

一个**零依赖的 OpenAI 兼容 LLM 客户端**，仅使用标准库 `urllib`，提供流式对话和 function calling 能力。

## 核心组成

1. **配置（环境变量可覆盖）**
   - `BASE_URL`：默认指向 `http://118.4.78.6:8088/api/v1`
   - `API_KEY`、`MODEL`（默认 `AngelOrDevil`）、`MAX_TOKENS`（8192）
   - ⚠️ 注意：代码中硬编码了一个 API Key 作为默认值，建议改为只从环境变量读取

2. **`chat()`** — 主入口
   - 接收 OpenAI 格式的 `messages` 和可选 `tools`
   - 返回**生成器**，逐块 yield 统一格式的 Chunk：
     - `reasoning`（思考过程，兼容 `reasoning_content`/`thinking` 字段）
     - `text`（正式回复）
     - `tool_call`（工具调用，含 id/name/arguments）
     - `usage`（token 统计）
     - `error` / `done`

3. **`_chat_stream()`** — 流式处理
   - 逐行解析 SSE（`data:` 前缀）
   - 关键逻辑：按 `index` 累积**分片到达的 `tool_calls` arguments**，流结束后合并成完整 JSON 再发出

4. **`_chat_non_stream()`** — 非流式分支，同样的 Chunk 输出格式

5. **`check_connection()`** — 轻量连通性自检，区分 401（认证失败）、404（模型不存在）等错误

## 设计特点

- 流式/非流式共用同一 Chunk 协议，上层调用方无需关心区别
- 对 JSON 解析失败做了容错（`_raw` 兜底）
- 超时 600 秒，适合长推理模型

它是整个 agent 项目的模型接入层，`agent.py` 等模块应基于它的 `chat()` 生成器构建对话循环。
