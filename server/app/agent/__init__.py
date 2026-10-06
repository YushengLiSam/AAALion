"""智能体路径(AGENT_PATH=shadow|on)。

只接规则路由判定为"复杂多步"的文本请求(多跳 / 点名对比 / 预算配套 / 跨币种),
其余一律走现有确定性快路。分层:

  router.py  —— 纯规则路由 should_use_agent(),不调 LLM;
  tools.py   —— 与框架无关的普通函数 + pydantic 参数 schema,硬约束写在工具里
                (会话约束只能被 LLM 收紧、不能放宽;只认目录 ID;价格统一人民币);
  graph.py   —— LangGraph StateGraph 编排(agent → tools → agent … → finalize),
                轮数 / recursion_limit / 总时长三重上限,LLM 只能引用商品 ID,
                商品卡由服务端按 ID 回填。

设计说明与"不能怎么说"见 docs/AGENT.md。
"""
