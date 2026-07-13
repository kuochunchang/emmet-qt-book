# 三角色 agent 閉環協定

追蹤：Issue #40 ｜ 設計規格：`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`

本文件是 loop 工作流的協定正本：label 定義、狀態轉移、角色權限、署名規則、
安全機制與啟動程序。三個角色 skill（`.claude/skills/{dispatcher,coder,reviewer}/`）
只描述各自的操作程序；規則若與本文件不一致，以本文件為準。

## 角色總覽

| 角色 | 職責 | 可寫入 | 禁止 |
| --- | --- | --- | --- |
| dispatcher | 派工、合併 `loop:approved` 的 PR、停滯偵測、gate 退出彙整與通知 | GitHub（label、留言、merge） | 改碼、寫稿、審查裁決 |
| coder | 實作 `loop:queued` 任務、回應退件 | 自己的任務分支、GitHub | 合併 PR、裁決審查、push main |
| reviewer | 審查 `loop:needs-review` 的 PR 並裁決 | GitHub（label、review 留言） | 改碼、合併 PR、派工 |

三個 session 共用同一個 GitHub 帳號，因此：

- 不使用 assignee 與 GitHub 原生 review approve（不能 approve 自己的 PR）。
- 每則 GitHub 留言文末署名：`— Dispatcher`、`— Coder`、`— Reviewer`。

## Label 定義

| label | 掛載對象 | 誰設 | 誰移除 | 意義 |
| --- | --- | --- | --- | --- |
| `loop:queued` | Issue | dispatcher | coder（認領時） | 已派工，待認領 |
| `loop:coding` | Issue | coder | dispatcher（PR 合併後） | 實作中 |
| `loop:needs-review` | PR | coder | reviewer（裁決時） | 待審查 |
| `loop:changes-requested` | PR | reviewer | coder（修正後） | 退件，待修正 |
| `loop:approved` | PR | reviewer | dispatcher（合併時） | 審查通過，待合併 |
| `loop:blocked` | Issue／PR | 任何角色 | dispatcher 或使用者 | 異常，待介入 |
| `loop:paused` | Meta Issue #1 | 使用者 | 使用者 | 全域煞車 |

建立命令（協定合併後執行一次）：

```bash
gh label create "loop:queued"            --color 0E8A16 --description "已派工，待 coder 認領"
gh label create "loop:coding"            --color 1D76DB --description "coder 實作中"
gh label create "loop:needs-review"      --color FBCA04 --description "PR 待 reviewer 審查"
gh label create "loop:changes-requested" --color D93F0B --description "審查退件，待 coder 修正"
gh label create "loop:approved"          --color 5319E7 --description "審查通過，待 dispatcher 合併"
gh label create "loop:blocked"           --color B60205 --description "異常，需 dispatcher 或使用者介入"
gh label create "loop:paused"            --color 000000 --description "全域煞車：所有 agent 暫停"
```

## 狀態轉移

```text
Issue:  loop:queued ──(coder 認領)──► loop:coding
PR:     loop:needs-review ──(reviewer 裁決)──► loop:approved ──(dispatcher squash 合併)──► 完成
                   ▲                              │
                   └────(coder 修正後)──── loop:changes-requested
```

- 狀態先掛 Issue；PR 開出後，工作流狀態以 PR 上的 label 為準，Issue 保留
  `loop:coding` 直到對應 PR 合併。
- bundle Issue 可由多個 PR 完成：dispatcher 的派工留言定義本輪 PR 範圍；
  最後完成的 PR 用 `Closes #N`，其餘用 `Refs #N`（沿用 AGENTS.md 規定）。
- 沒有 `loop:*` label 的既有 PR／分支是「圈外工作」：dispatcher 首次發現時
  在該物件留言標記並通知使用者決定，不得擅自接手或合併。

## 派工留言模板（dispatcher）

```markdown
### Loop 派工

- 任務：#<Issue 編號>（<標題>）
- 本輪 PR 範圍：<本輪要完成的具體工作與邊界>
- Gate 依據：<active gate 名稱>；<curriculum 對應段落>
- 驗收要求：book check 與 pytest 實跑通過；<任務特定要求>

— Dispatcher
```

## 安全機制

- **全域煞車**：每個角色每輪醒來第一步查 Meta Issue #1 是否有 `loop:paused`；
  存在則本輪不做任何事，只依調速表睡眠。
- **Gate 雙重防線**：dispatcher 只派 active gate 允許的任務；coder 與 reviewer
  各自以 `git show origin/main:AGENTS.md` 再核對派工屬於 active gate，
  不符即標 `loop:blocked` 並留言拒做——即使是 dispatcher 派的。
- **停滯偵測**：dispatcher 發現任一 `loop:*` 狀態超過 6 小時未變 →
  標 `loop:blocked` ＋ 通知使用者。
- **退件上限**：同一 PR 第 3 次被標 `loop:changes-requested` 時，dispatcher
  標 `loop:blocked` 交使用者裁決。
- **誠實原則**：`scripts/book-check` 與 `python3 -m pytest tests/ -q` 未實際
  執行通過，不得標 `loop:needs-review`；reviewer 重跑失敗一律退件。

## 通知

需要使用者介入（`loop:blocked`、gate 退出待核准）時，dispatcher 走三管道：

1. GitHub：在 Meta Issue #1 留言說明狀況與需要的決定。
2. 桌面推播：PushNotification 工具。
3. Discord：發訊至下方設定的頻道。

PR 合併完成只發 Discord 短訊，不推播。

設定：

- Discord chat_id：`（部署時填入）`

## 啟動程序

一次性準備（在主檢出執行；worktree 已存在則略過）：

```bash
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-coder origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-reviewer origin/main
```

啟動（三個終端機分別執行）：

```bash
cd ~/workspace/emmet-qt-book          # 輸入 /loop /dispatcher
cd ~/workspace/emmet-qt-book-coder    # 輸入 /loop /coder
cd ~/workspace/emmet-qt-book-reviewer # 輸入 /loop /reviewer
```

## 調速指引（各角色共用）

| 情境 | 下次醒來 |
| --- | --- |
| 手上有活（實作／審查未完成） | 立即繼續，不睡眠 |
| 剛交接出去（等對方接手） | 3–5 分鐘 |
| 佇列空、無待辦 | 20–30 分鐘 |
| `loop:paused` 存在 | 30–60 分鐘 |
