# agent_in v13.0 — 读网页 skill + temp 可删 + 清理垃圾

> 前置：plan12。
> 产品版本：本增量完成后定为 **2.0**（第 13 次更新，首个功能完整快照）。
> 根因：没有读网页流程，HTML 乱解析打满 20 轮；临时文件写在仓库根，SAFE_MODE 拦删除。

## 明确不做

第 6 个工具、`/use` 抓取脚本、安装 Chromium、把 20 改大、改 Cursor 计划文件。

## 结论

1. `curl` 已能拉 HN；空转在现场写正则、再逐条抓详情。
2. `Remove-Item -Recurse` 在 SAFE_MODE 下阻断；垃圾应进 `{work_dir}/temp/`，仅该目录可删。

## 改什么

1. `skills/读网页.md` + 系统提示索引；产物只写 `temp/`。
2. 删除目标全部在 `temp/` 内时，删除类规则放行。
3. `.gitignore`：`temp/`、`__pycache__/`。清掉根目录 agent 垃圾。

## 开发任务

- [x] `plan13.0.md` 落盘
- [x] 读网页 md + 系统提示
- [x] temp 可删
- [x] gitignore + 清理
- [x] `_test_plan13.py`

## 成功标准

HN 前 3 条数轮内出总结；产物在 `temp/`；根目录递归删 `tmp` 仍拦截。
