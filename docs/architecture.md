# P0 Architecture Baseline

## 系统边界

`Training Core` 是唯一的训练流程控制者。前端只负责交互和状态展示；LLM、ASR、朗读分析和素材来源均通过 adapter 接入。

```text
React/Vite UI
    |
FastAPI API
    |
Training Core ---- adapters (speech / llm / material)
    |
SQLite + local files
```

## 模块所有权

- `backend/app/core/`: 领域模型、状态机和确定性训练规则
- `backend/app/db/`: SQLite 连接、schema 和持久化实现
- `backend/app/api/`: HTTP API 与请求响应模型
- `backend/app/adapters/`: 可替换的外部能力接口骨架（material / speech / llm）；具体供应商实现尚未接入
- `backend/app/preprocess/`: 预置素材校验、对齐、切句和分 Part
- `frontend/src/`: 页面、训练交互、音频录制与 API 调用
- `backend/tests/`: 后端和领域测试

## 阶段 1 决策

当前 P0 提供确定性的训练状态机、预置素材预处理、听写提交校验和事件 API。`adapters/` 只定义外部能力的替换边界；ASR 音素对比、LLM 生成和真实素材供应商仍需后续实现，不能把接口骨架描述为已接入能力。

## P2 最小服务

- `LearningTimeService` 将显式结束的活动区间写入 `training_time_logs`，并更新 `learning_stats`。
- `WeeklyAssessmentService` 保存周测听写分数、朗读维度和 gate 结果；必测项未完成或失败时进入 `REINFORCEMENT_REQUIRED`。
- 全篇朗读接口只保存评分结果，不自行冒充 ASR 分析；实际语速、停顿、重音判断由后续 speech adapter 提供。
- 素材搜索当前查询 SQLite 中已导入的本地素材；外部搜索结果必须通过 `MaterialProvider` 接入并经过预处理后才能进入训练状态机。
