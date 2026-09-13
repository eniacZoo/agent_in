# agent_in v23.0 / 产品 3.0 — 会话树与差分渲染

> 前置：plan22（provider 同源、office2、连接文案）。
> 产品版本升为 **3.0**。不引入 rich / curses，不改 JSONL，不搬 Pi 的 clone/editor。

## 做了什么

1. **会话树**  
   消息补 `id` / `parent`，文件 meta 有 `leaf_id`。发给模型的 window 是 root → 叶。  
   `/tree` 打印 ASCII 树；`/fork [id]` 把叶设到祖先，下一句 user 成兄弟。  
   `/resume` 恢复整棵树和叶，不丢分叉。旧线性 json 按数组串成单链。

2. **差分渲染**  
   仅当前未完成的 thinking / ai 块可重绘：按终端宽度折行，cursor-up 重打变化尾部。  
   已结束回合不回头改。Spinner / `confirm` 仍互斥。非 TTY 退回逐 token 追加。

3. **版本**  
   `ui.APP_VERSION = "3.0"`。README、CHANGELOG、目录结构同步。历史 plan 正文不改。

## 不改

完整 TUI、工具参数流式预览、JSONL 事件日志、时间旅行 UI。

## 验收

- [x] 旧线性 session 能打开
- [x] 分叉后兄弟还在，window 只含叶路径
- [x] `/tree` `/fork` `/history` 行为符合上表
- [x] 折行 / 差分下标单测
- [x] 横幅 / `-V` / `/status` 为 3.0
