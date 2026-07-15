# 三角色 agent 閉環協定

追蹤：Issue #40 ｜ 設計規格：`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`

本文件是 loop 工作流的協定正本：label、狀態轉移、角色權限、持久證據、安全機制
與喚醒方式都以此為準。Claude Code 程序位於
`.claude/skills/{dispatcher,coder,reviewer}/`；Codex 程序位於
`.agents/skills/emmet-loop-{dispatcher,coder,reviewer}/`。兩種 client 共用 GitHub
上的同一狀態機；skill 若與本文件不一致，以本文件為準。

Trusted runner、event-driven CLI 與手動單輪診斷的操作者步驟見
[`agent-loop-operations.md`](agent-loop-operations.md)；該文件是操作導覽，不能放寬
本協定或 active gate。

## 執行模型

- CLI 分成兩種長生命週期 component：三個 `agent <role>` listener 等待本角色事件；
  單一 `events` manager 定期 polling GitHub、依本協定選出目前負責角色並通知。
- 每個事件只啟動一次短生命週期 Codex role iteration；它最多完成一個可稽核的主要
  狀態轉移後退出。Role skill 與 Codex child 不 `sleep`、不輪詢、不遞迴啟動自己。
- Event manager 只讀 GitHub 並送本機事件，不修改 GitHub、工作樹或 gate；所有 mutation
  仍由收到事件後啟動的 dispatcher／coder／reviewer iteration 依其權限執行。
- GitHub Issue、PR、label、署名留言與 commit SHA 是 durable state；本機 session
  與 worktree 都可能中斷，下一輪必須先 reconciliation 才能開始新工作。Unix socket、
  event fingerprint 與 manager 記憶體中的 retry 狀態都不是 durable workflow state。
- Role 從與最新 `origin/main` 一致的 trusted runner 載入 skill、AGENTS 與 Codex
  project config；runner 不 checkout 候選分支。Coder／Reviewer 在另外的 task／candidate
  worktree 作業，候選內容不能成為下一輪的控制指令。
- 三個角色共用同一個 GitHub 帳號，因此不用 assignee 與 GitHub 原生 review
  approve。留言文末分別署名 `— Dispatcher`、`— Coder`、`— Reviewer`。

## 角色與權限

| 角色 | 職責 | 可寫入 | 禁止 |
| --- | --- | --- | --- |
| dispatcher | reconciliation、派工、合併安全的 approved PR、停滯偵測、gate 退出彙整 | GitHub label／留言／merge | 改碼、寫稿、審查裁決、gate transition |
| coder | 認領或恢復一件任務、實作、驗證、交審 | 自己的任務分支、GitHub | 合併、push `main`、審查裁決 |
| reviewer | 在最新 `main` 上獨立審查一個 integration candidate 並裁決 | GitHub label／裁決留言 | 改碼、commit、push、合併、派工 |

## Label 與狀態不變量

Primary progress labels：

| label | 對象 | 狀態擁有者 | 意義 |
| --- | --- | --- | --- |
| `loop:queued` | Issue | dispatcher → coder | 已派工，待認領 |
| `loop:coding` | Issue | coder → dispatcher | 已認領；直到對應 PR 合併都保留 |
| `loop:needs-review` | PR | coder → reviewer | 已交付精確 tested head，待審查 |
| `loop:changes-requested` | PR | reviewer → coder | 退件，待修正 |
| `loop:approved` | PR | reviewer → dispatcher | 指定 head／base 已審查通過，待合併 |

Control overlays：

| label | 對象 | 誰設／移除 | 意義 |
| --- | --- | --- | --- |
| `loop:blocked` | Issue／PR | 任何角色設；dispatcher 或使用者在留下 resolution 後移除 | 保留 primary state 的暫停 overlay |
| `loop:paused` | Meta Issue #1 | 僅使用者 | 全域煞車 |

首次部署只建立缺少的 label；既有同名 label 先核對語義，不盲目覆寫：

```bash
gh label create "loop:queued"            --color 0E8A16 --description "已派工，待 coder 認領"
gh label create "loop:coding"            --color 1D76DB --description "coder 實作中"
gh label create "loop:needs-review"      --color FBCA04 --description "PR 待 reviewer 審查"
gh label create "loop:changes-requested" --color D93F0B --description "審查退件，待 coder 修正"
gh label create "loop:approved"          --color 5319E7 --description "指定 SHA 審查通過，待 dispatcher 合併"
gh label create "loop:blocked"           --color B60205 --description "暫停 overlay：需介入或恢復"
gh label create "loop:paused"            --color 000000 --description "全域煞車：所有 agent 本輪退出"
```

每個 loop Issue／PR 恰有一個 primary progress label；`loop:blocked` 不取代 primary
label。Blocked 工作仍占 WIP，所有 claim、review、merge selector 都排除它，dispatcher
不得因 blocked 而另派新工作。多個 primary labels、缺少可判定前態或 SHA 證據衝突時
fail closed：加 blocked、留下恢復條件，不猜測狀態。

狀態轉移：

```text
Issue: queued ──(coder claim)──► coding ──(dispatcher 確認 merge)──► 完成本輪
PR:    needs-review ──(reviewer)──► approved ──(dispatcher)──► merged
                     └───────────► changes-requested ──(coder)──► needs-review
```

- Bundle Issue 可由多個 PR 完成；dispatcher 派工留言界定本輪 slice。最後完成 Issue
  的 PR 才用 `Closes #N`，其餘用 `Refs #N`。
- 跨 gate 的 Issue（例如 W1 的 #7）不能用 open／closed 單獨判定本 gate 是否完成。
- 無 `loop:*` 的既有 PR／分支是圈外工作；dispatcher 只留 durable comment 或 Meta
  通知，不加任何 loop label，也不接手。

## 每輪共同前置與 reconciliation

每個角色依序：

1. 查 Meta Issue #1 是否有 `loop:paused`；有就不寫 GitHub 或工作樹，回報 paused。
2. `git fetch origin main --prune`，記錄完整 `MAIN_SHA`；以同一個 `origin/main` snapshot
   讀 `AGENTS.md`、curriculum active gate、authoring guide 與本文件。
3. 讀 Meta Issue #1 及本輪候選的 live Issue／PR。AGENTS、curriculum、Meta Issue
   active gate 不一致時 fail closed。
4. 先補完前一輪留下的半完成 transaction，再選新工作。留言需有穩定 marker／SHA；
   已有相同 durable evidence 時不得重複留言或通知。

共同恢復規則：

- Push／PR／label／merge 命令結果不明時先重查 live state，禁止盲目重試。
- Primary label 轉移是一個邏輯 transaction：先留下包含 object、from、to 與相關 SHA
  的 durable marker，再提交期望的完整 label set 並重查。Client 若以多個 API call
  實作，中斷造成的零個／多個 primary labels 由下一輪依 marker 冪等補完；沒有有效
  marker 才 blocked，不能猜測。
- `approved` 的 reviewer head 或 reviewed base 不再等於目前 head／最新 main 時，approval
  失效；dispatcher 留下含 reviewed／current SHA 的穩定 reconciliation marker，機械性
  轉回 `needs-review` 後退出，不得自行重作品質裁決或在同輪合併／派工。
- 停滯時間取目前 primary label 被加上的 timeline 時間，不用物件 `updatedAt`；一般
  留言不應重置停滯計時。
- 每個角色結束時摘要 `role`、穩定 kebab-case `result`、`object`、`main_sha`、
  `head_sha` 與 `mutations`，讓 agent JSONL log 可追查。

## Dispatcher 一輪

優先序為：補完已 merge 狀態 → 修復失效 approval／異常 → 檢查 gate exit → 安全合併
一個 approved PR → 處理停滯／圈外工作 → 派一個 active-gate slice → no-op。

派工前必須先判斷 active gate 退出條件。證據已齊時只彙整完成 Issue／PR／merge SHA
並通知使用者；使用者核准且 gate-transition PR 合併前，不派下一 gate。

合併前重新查 pause 與 PR，且必須同時成立：

- PR open、非 draft、base=`main`、沒有 `loop:blocked`，且唯一 primary label 是
  `loop:approved`。
- 有有效 Dispatcher 派工留言，live Issue 與 slice 都在 active gate。
- 最新 Reviewer 留言含 `Verdict: approved`、完整 `Reviewed-Head` 與 `Reviewed-Base`；
  它們分別等於目前 `headRefOid` 與最新 `origin/main`。
- PR 可立即安全合併；以
  `gh pr merge <N> --squash --delete-branch --match-head-commit <HEAD_SHA>` 執行，禁止
  `--admin` 與 `--auto`。

合併結果不明時先重查 `mergedAt`／merge commit。確認成功後才冪等清理 Issue primary
state、記錄 merge SHA 與 Meta 進度。這是唯一獲授權的自動合併路徑。

派工留言至少包含 Issue、精確 slice、gate 依據、驗收 oracle，並署名：

```markdown
### Loop 派工

- 任務：#<Issue 編號>（<標題>）
- 本輪 PR 範圍：<具體工作與禁止邊界>
- Gate 依據：<active gate>；<curriculum 對應段落>
- 驗收要求：`./scripts/book-check`；<task-specific oracle>

— Dispatcher
```

## Coder 一輪

只選 unblocked 工作，優先序：`changes-requested` PR → 可唯一恢復的 `coding` Issue →
最舊 `queued` Issue → no-op。Claim 前先決定唯一 branch 名稱；claim marker 必須包含
`Issue`、完整 `Branch`、`Claimed-Main` 與 slice。Claim 後中斷時，從 marker、remote
task branch、PR 與 head SHA 恢復；無法唯一判定就 blocked，不另開同一任務。

Coder 從 trusted runner 啟動，在另一個每任務 task worktree 建立或恢復 branch；runner
不 checkout task branch。只改派工 slice；先跑 task-specific oracle，再從 task repo root
實跑 `./scripts/book-check`。Stage 前檢查完整 diff、untracked files、秘密與
`Refs`／`Closes`，不得 force-push。Push 後確認 GitHub `headRefOid` 等於本機實測
`TESTED_HEAD_SHA`，先留下 durable handoff，再讓新建或既有 PR 的唯一 primary state
成為 `needs-review`：

```text
Tested-Head: <完整 40 字元 SHA>
Based-On-Main: <完整 40 字元 SHA>
Verification: ./scripts/book-check；<其他實跑命令與結果>
— Coder
```

`Based-On-Main` 是建立 tested head、或在驗證前最後一次把 tested head rebase／merge
到其上的完整 `origin/main` SHA，不是單純「最後 fetch 到的 SHA」。PR 建立後尚未加
primary label 就中斷，視為可依 handoff marker 補完的半完成 transaction。

任一驗證未通過、remote SHA 不符或 pause／gate 改變，都不得交審。Blocked 保留原
primary state 與可恢復條件。

## Reviewer 一輪

只選最舊、unblocked 且唯一 primary state 為 `needs-review` 的 PR。Reviewer 從
trusted runner 啟動，在另一個 disposable candidate worktree 記錄並 fetch live
`headRefOid` 與最新 `MAIN_SHA`；runner 不 checkout PR。GitHub merge ref 只有在
`git rev-list --parents -n 1 <MERGE_SHA>` 的兩個 parent 依序精確等於 `MAIN_SHA`、
`headRefOid` 時才可作 integration candidate。否則由這兩個 exact SHA 在 candidate
worktree 建立未提交的 merge 狀態：先以 `MAIN_SHA` 建 detached worktree 與空 hooks
目錄，再執行
`git -C <candidate> -c core.hooksPath=<empty-hooks> merge --no-ff --no-commit --no-edit <headRefOid>`，
並核對 `HEAD=MAIN_SHA`、`MERGE_HEAD=headRefOid`。若 head 已被 main 吸收而無法形成
`MERGE_HEAD`，重查 live PR、不得裁決，交 dispatcher reconciliation；不能測裸 head、
stale merge ref 或相信 PR 自述。

實跑 `./scripts/book-check`、派工 task-specific oracle 與代表性宣稱。可歸因於 PR 的
失敗才是 finding；環境或權限故障加 blocked 並寫明恢復條件。裁決前再次 fetch／查詢
pause、head 與 main；任一 SHA 改變就保留 `needs-review`、不做 GitHub mutation 並結束
本輪，下一輪針對新 SHA 完整重驗，不能在同輪替未驗證的 base 發布裁決。

先留言、後轉 primary label，格式至少為：

```text
Verdict: approved | changes-requested
Reviewed-Head: <完整 40 字元 SHA>
Reviewed-Base: main@<完整 40 字元 SHA>
Verification: <實跑命令與結果>
— Reviewer
```

退件另外逐條列 `檔案:行號`、問題、證據與期望結果。Reviewer 不使用 GitHub 原生
approve，不修改候選內容。

## 全域安全與通知

- 任何 mutation 前重查 `loop:paused`；dispatcher 合併與 reviewer 裁決前一定重查。
- 任一 primary state 超過 6 小時未變，由 dispatcher 加 blocked 並通知；同一 PR
  第 3 次 changes-requested 也交使用者裁決。
- `./scripts/book-check` 未實際通過，coder 不得交審；reviewer 重跑的 PR 歸因失敗
  不得批准。
- Gate transition 永遠不在 loop 授權內。Dispatcher 只能彙整退出證據，等待使用者
  核准後依 AGENTS 的固定 transition 流程執行。
- 需要使用者介入時，以 Meta Issue #1 留言為 durable 通知；可另接桌面／Discord，
  但外部通知失敗不得偽裝成已通知，也不得反覆洗版。

## CLI 事件驅動

### 元件與 routing

Event manager 每次 poll 只讀 Meta Issue #1 與所有 open `loop:*` Issue／PR，依下列
優先序送事件：

| GitHub live state | 通知 |
| --- | --- |
| `loop:paused` | 三個 agent 收到 paused control event，不啟動 Codex |
| blocked、互斥／缺少 primary state、WIP 異常或 PR／Issue 不配對 | dispatcher |
| 一個 `loop:approved` PR | dispatcher |
| 一個 `loop:changes-requested` PR | coder |
| 一個 `loop:needs-review` PR | reviewer |
| 一個 queued／coding Issue，尚無 PR | coder |
| 沒有 loop WIP | dispatcher，做 reconciliation、gate exit 判斷或派工 |

同一 fingerprint 被 agent ACK 後，manager 在預設 1800 秒內不重送；GitHub state 改變
就立即形成新 fingerprint。Agent 不在線、socket 拒絕或沒有 ACK 時，不記為已送達，
下次 poll 重試。Manager 重啟後可重送目前狀態；role iteration 必須靠 GitHub durable
marker 保持冪等，不能依賴本機去重。

任何 Codex child 執行期間，manager 除 paused control event 外不送新的 wake，避免
跨角色同時 mutation。在途 state 持續達預設 1800 秒且本輪沒有 child 時，manager
先以 `oversight-heartbeat` 單獨喚醒 dispatcher，讓它執行 canonical reconciliation、
6 小時停滯與 gate exit 檢查；dispatcher 結束後，後續 poll 才再通知 state owner。

三個 agent 使用 Git common-dir 派生的同一個 mode `0700` runtime directory，各自監聽
mode `0600` Unix stream socket。Event payload 與 ACK 都是單行 JSON；payload 包含
`role`、`action`、`reason`、相關 object snapshot、repository、fingerprint、
`event_id` 與 poll 時間。錯 role、未知 action、過大或無效 JSON 必須拒絕，不能啟動
Codex。

### Trusted runners 與啟動

四個可見終端可由 repo-local tmux launcher 統一管理：

```bash
./scripts/codex-loop tmux status
./scripts/codex-loop tmux start
./scripts/codex-loop tmux restart
./scripts/codex-loop tmux stop
```

Launcher 只封裝本節既有的 trusted-runner 更新、四項預檢、先 agents 後 manager 的
啟動順序，以及先 manager 後 agents 的停止順序；不建立第二套 routing／durable
state，不變更 GitHub label，不安裝 scheduler。它只會取代帶有本 repository
ownership marker 的同名 tmux session，並以 role lock metadata 驗證要停止的 PID；
驗證失敗就停止。完整操作、2×2 pane 位置、detach 與中途失敗清理見
[`agent-loop-operations.md`](agent-loop-operations.md#tmux-一鍵生命週期建議入口)。

Model 與 reasoning effort 不由 launcher 硬編碼；每次 Codex iteration 依正常 Codex
設定優先序解析，三個 agent 可選擇共同的 `--profile`，event manager 不使用模型。

一次性準備 trusted runner worktree（存在則不重建；它們不拿來 checkout 任務）：

```bash
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-coder origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-reviewer origin/main
```

先做不啟動角色的預檢（預檢仍會 fetch trusted `origin/main`）：

```bash
./scripts/codex-loop agent dispatcher --dry-run
./scripts/codex-loop agent coder --workdir ../emmet-qt-book-coder --dry-run
./scripts/codex-loop agent reviewer --workdir ../emmet-qt-book-reviewer --dry-run
./scripts/codex-loop events --once --dry-run
```

四個 component 應各在可見終端執行；先啟動 agents，最後啟動 manager：

```bash
./scripts/codex-loop agent dispatcher
./scripts/codex-loop agent coder --workdir ../emmet-qt-book-coder
./scripts/codex-loop agent reviewer --workdir ../emmet-qt-book-reviewer
./scripts/codex-loop events --interval-seconds 60 --retry-seconds 1800 \
  --dispatcher-heartbeat-seconds 1800
```

`agent` 每次啟動 Codex 都使用 `codex exec --ephemeral --json`、`workspace-write`、
on-request approval 與 auto reviewer；不使用 dangerous bypass。Codex 的 JSONL stdout
與 progress／error stderr 都直接繼承到 agent 終端，不擷取、摘要或延後，因此操作者
可看到 thread、turn、reasoning、message、command、file change 與 tool 等全部 CLI
事件。Component 自身的 waiting、delivery、event、exit code 與 timeout 也輸出單行
JSON，便於同一份 log 稽核。

啟動及每次 Codex iteration 前都 fetch `origin/main`，並拒絕非本 repo linked
worktree、不同 origin，以及與 trusted ref 不同的 `.agents`／`.codex`／AGENTS／
治理文件或 adapter。`--workdir` 只能指向同 repo trusted runner。

Agent component 持有 per-role `flock` 直到停止；child 繼承 lock FD，避免 parent
異常退出時重疊；event manager 另持單一 `events` lock。第二個同角色 agent 或第二個
manager 回傳 75；每次 iteration 預設 7200 秒 timeout，
逾時清理 child process group 並記錄 124。手動診斷仍可用
`./scripts/codex-loop <role>` 執行一次 iteration，但連續運作只使用
`agent <role>` ＋ `events`，不使用 cron、systemd timer 或 App Scheduled Tasks
重複喚醒。Repo 不提供、安裝、enable 或 start 主機 unit。

Claude Code 仍可在各自 trusted runner 明確呼叫 `/dispatcher`、`/coder`、
`/reviewer` 做單輪診斷；不得另建第二套 polling 或 durable state。
