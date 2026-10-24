# CodexClean：Codex 残留目录整理器

[English](README.md)

这是一个面向 Windows 的本地命令行工具。它检查 Codex 的本地会话索引，以及 `Documents\Codex` 下由 Codex 创建的按日期分组的工作目录。用户先看扫描结果，再把选中的目录**转移到指定的待检查文件夹**。工具保留清单和恢复命令；不会永久删除文件。

## 识别结果的含义

| 状态 | 含义 | 能否转移 |
| --- | --- | --- |
| `archived` | 本地会话索引或归档记录指向此目录，且没有活跃会话指向它 | 可以 |
| `unlinked` | 目录有 Codex 常见的 `work` 和 `outputs` 子目录，但找不到本地会话记录 | 需要 `--include-unlinked` 明确纳入 |
| `protected-active` | 至少一个未归档的本地会话使用此目录 | 不可以 |
| `ignored-unrecognized` | 目录缺少可识别的 Codex 标记，且无会话关联 | 不可以 |

`unlinked` **不等于已证实“对应聊天已删除”**：会话记录丢失、同步状态不同或仍被其他程序使用，都可能造成同样结果。工具不会自动转移这类目录。它也不会扫描任意 Git 项目、下载文件夹、云端聊天附件或整块硬盘，因为无法可靠断定其中哪些文件是 Codex 产生的。

## 环境

- Windows，Python 3.14 或更新版本。使用 Python 标准库，不需要安装第三方包。
- 运行时要能读取用户自己的 `.codex\state_5.sqlite` 和会话日志。
- 待检查文件夹必须与工作目录在**同一个盘符**，并位于 `Documents\Codex` 与 `.codex` 之外。这样目录转移可直接重命名，不经过跨盘复制和删除。

本机默认路径：

- 工作目录：`%USERPROFILE%\Documents\Codex`
- Codex 数据：`%CODEX_HOME%`（若未设置，使用 `%USERPROFILE%\.codex`）
- 待检查文件夹：`%USERPROFILE%\Documents\Codex_Review`

## 使用

在 PowerShell 中进入本项目文件夹后，先扫描：

```powershell
py -3 .\codex_residue_review.py scan
```

预览转移所有**已归档**目录：

```powershell
py -3 .\codex_residue_review.py move --all
```

如要同时纳入所有 `unlinked` 目录，先预览：

```powershell
py -3 .\codex_residue_review.py move --all --include-unlinked
```

确认列表后，增加 `--yes` 执行转移：

```powershell
py -3 .\codex_residue_review.py move --all --include-unlinked --yes
```

也可以只选目录 ID，例如：

```powershell
py -3 .\codex_residue_review.py move --ids 2026-01-01/example --yes
```

若需修改待检查文件夹，把选项放在子命令之前：

```powershell
py -3 .\codex_residue_review.py --quarantine "C:\Review_Codex" move --all --yes
```

每次转移创建一个时间戳文件夹，其中 `items\` 保留原来的日期和目录名，`manifest.json` 记录每个目录的原路径、目标路径和转移状态。请打开该文件夹逐项检查；**检查完成后，由你在文件管理器中删除不需要的内容**。

转移前或转移后想恢复时，用运行文件夹的完整路径：

```powershell
py -3 .\codex_residue_review.py restore --run "C:\Users\你的用户名\Documents\Codex_Review\运行文件夹名" --all
py -3 .\codex_residue_review.py restore --run "C:\Users\你的用户名\Documents\Codex_Review\运行文件夹名" --all --yes
```

第一条只预览，第二条才恢复。如果原位置已有新文件夹，恢复会拒绝覆盖。

## 安全规则和边界

- 扫描只读取目录名、会话元数据和 SQLite 索引，不读取聊天正文或项目文件内容。
- 默认只有 `archived` 可以转移；`unlinked` 必须明确启用。
- 转移前再次读取会话状态，防止把刚变为活跃的目录移走。
- 目录必须位于指定的工作根目录，且不能是符号链接或 junction。
- 不提供“永久删除”命令。保留原路径清单，支持恢复。
- 移动期间如发生中断，检查该次运行的 `manifest.json` 和 `items\`，再决定恢复或继续；不要直接重新执行同一批转移。

## 测试

```powershell
py -3 -m unittest discover -s .\tests -v
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。本项目是独立的社区工具，与 OpenAI 没有隶属或背书关系。
