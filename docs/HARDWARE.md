# 硬件

我们手头有哪些设备、各自的用途,以及围绕共享 A100 服务器的硬性规则。

## 在用设备

| 设备 | 归属 | 操作系统 | 角色 |
|---|---|---|---|
| MacBook(Shufeng 的) | Shufeng | macOS 15+ | iOS 开发主力机(Xcode + 模拟器),开发期间也跑本地后端。 |
| iPhone 13 | Shufeng | iOS 17+ | 真机测试。摄像头用于 拍照找货 演示。 |
| Mac mini M4(计划中) | 团队 | macOS 15+ | 计划中的团队共享开发/演示机。M4 统一内存使其足以胜任 iOS 构建的 CI 运行器。不阻塞进度。 |
| A100 服务器(SSH `uc`,主机 `mingkai-gigaio`) | 团队 | Ubuntu 24.x (Linux 6.8) | 重活:CLIP 图像嵌入(一次性索引构建)、批量 RAG 评测。**严格的使用范围规则见下文。** |

> **备注 (2026-05-22)**:`uc` 上的 `nvidia-smi` 目前报告驱动/库版本不匹配。在真正跑 CLIP 之前不构成阻塞;在推进拍照搜索功能之前,需要由 Tujie 或 Shufeng 修复驱动(或者锁定一个与之匹配的 torch CUDA 版本)。

## A100 服务器 — 硬性规则

A100 上的目录布局:

```
~/shufeng/
├── cuda-fuzzing/     ← existing, ongoing fuzzing work — DO NOT TOUCH
└── AAALion-/     ← new, this project, sibling of cuda-fuzzing/
```

**规则**(违反其中任意一条,都可能破坏 Shufeng 的其他工作):

1. **所有 AAALion- 的工作都位于 `~/shufeng/AAALion-/` 之下**。自动化步骤中绝不 `cd` 到这个子树之外。
2. **绝不修改、列出或运行 `~/shufeng/cuda-fuzzing/` 下的任何东西**。那是另一个任务;视其为只读且不在范围内。
3. **绝不触碰 `~/shufeng/` 之外的路径**(不碰 `/opt`,不改 `~/.bashrc`,不 `apt install`,不 `pip install --user`)。
4. 使用项目本地的 Python 虚拟环境(venv):`~/shufeng/AAALion-/.venv/`。所有 `pip install` 都落在这里。
5. 只跑 GPU 密集型步骤(CLIP 索引、批量评测)——仅此而已。Web 服务器和请求路径上的代码留在笔记本上;A100 不是服务托管机。

## SSH 辅助脚本

`tools/ssh_a100.sh`(创建后):

```bash
#!/usr/bin/env bash
ssh uc -t 'cd ~/shufeng/AAALion- && exec $SHELL'
```

前提是 `~/.ssh/config` 里有 `uc` 的 Host 条目。如果没有,先配置一个:

```
Host uc
  HostName <your.a100.host>
  User <your.user>
  IdentityFile ~/.ssh/<your.key>
```

## A100 首次初始化(一次性)

```bash
ssh uc
ls ~/shufeng/                                                  # confirm cuda-fuzzing/ present
mkdir -p ~/shufeng/AAALion- && cd ~/shufeng/AAALion-
git clone https://github.com/YushengLiSam/AAALion-.git . || echo "private — rsync from laptop"
python3 -m venv .venv && source .venv/bin/activate
pip install -r rag/requirements.txt
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

预期最后一行输出:`True NVIDIA A100-...`。

## A100 利用率估算

- 对 100-200 张商品图做 CLIP 索引:< 1 分钟。
- 对 30 条 golden 查询做批量 RAG 评测:< 10 秒。
- 每周总算力消耗:不到 30 分钟。A100 对此属于性能过剩,但用起来成本很低。

## 未来硬件

- **Mac mini M4**(如购入):用作局域网内常开的后端主机;团队在房间里用任意一台 iPhone 都能演示,无需随身拖着笔记本。
- **一台额外的 iPhone**(Sam / Tujie 的个人设备):第二台测试机——对验证摄像头和深色模式一致性尤其有用。
