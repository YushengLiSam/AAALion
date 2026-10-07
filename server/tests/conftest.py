"""server/tests 公共夹具。

P0.0 起有进程内限流器(app.services.ratelimit.limiter,进程级单例)。所有测试共用
一个 pytest 进程,TestClient 的来源 IP 都是 "testclient",不清零的话前面测试的请求
会把后面测试的额度用光。这里每个测试前后都清零限流状态和越权统计。
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_security_state():
    def _reset():
        # 只清理已经导入的模块,绝不在这里抢先导入 app.*:有的测试在导入前设置
        # LIONPICK_JWT_SECRET 等环境变量,提前导入会让它们读到错误的值。
        rl = sys.modules.get("app.services.ratelimit")
        if rl is not None:
            rl.limiter.reset()
        sec = sys.modules.get("app.security")
        if sec is not None:
            sec.stats.reset()

    _reset()
    yield
    _reset()
