# 三角色 agent 閉環協定

追蹤：Issue #40 ｜ 設計規格：`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`

本文件是 loop 工作流的協定正本：label、狀態轉移、角色權限、持久證據、安全機制
與喚醒方式都以此為準。Claude Code 程序位於
`.claude/skills/{dispatcher,coder,reviewer}/`；Codex 程序位於
`.agents/skills/emmet-loop-{dispatcher,coder,reviewer}/`。兩種 client 共用 GitHub
上的同一狀態機；skill 若與本文件不一致，以本文件為準。

## 執行模型

- 一次 role wake 只執行一輪，最多完成一個可稽核的主要狀態轉移，然後退出。
- Role 不 `sleep`、不輪詢、不遞迴啟動自己，也不在 repo 內建立 scheduler。
- GitHub Issue、PR、label、署名留言與 commit SHA 是 durable state；本機 session
  與 worktree 都可能中斷，下一輪必須先 reconciliation 才能開始新工作。
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
- 無 `loop:*` 的既有 PR／分支是圈外工作；dispatcher 只標記並通知使用者，不接手。

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
  `head_sha` 與 `mutations`，讓 scheduler log 可追查。

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

## 喚醒角色

角色是短生命週期 worker；「定時」由角色外部提供。

### Codex App Scheduled Tasks

只在沒有候選分支內容、控制檔與最新 `origin/main` 一致的 dedicated trusted runner
建立 Scheduled Task，prompt 直接
使用 `$emmet-loop-dispatcher`、`$emmet-loop-coder` 或 `$emmet-loop-reviewer`，並明示
「執行恰好一輪後結束」。App 已負責喚醒時，不要在 task 裡再啟動 `codex exec`。
同一角色只建立一個 task；只有 App 明確保證同一 task 不重疊時才直接喚醒 skill。
無法確認該保證，或部署要求 repo identity／control-input 驗證時，使用下節 CLI
adapter 與外部 scheduler。絕不把 App task 指向 checkout 過候選 PR 的 worktree。

### CLI／cron／systemd

一次性準備 trusted runner worktree（存在則不重建；它們不拿來 checkout 任務）：

```bash
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-coder origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-reviewer origin/main
```

先做不啟動角色的預檢，再手動喚醒一輪（預檢仍會 fetch trusted `origin/main`）：

```bash
./scripts/codex-loop dispatcher --dry-run
./scripts/codex-loop dispatcher
./scripts/codex-loop coder
./scripts/codex-loop reviewer
```

`scripts/codex-loop` 只包裝一次 `codex exec --ephemeral`，使用 `workspace-write`、
on-request approval 與 auto reviewer；不使用 dangerous bypass。啟動前會 fetch
`origin/main`，拒絕非本 repo linked worktree、不同 origin，以及與 trusted ref 不同的
`.agents`／`.codex`／AGENTS／治理文件或 adapter。`--workdir` 只能覆寫成同 repo 的
trusted runner，不能指向任意 repo。

每個 role 以 Git common-dir 派生的 `flock` 防止重疊，lock FD 由 child 繼承，即使
adapter parent 被 kill，存活 child 仍持鎖。已在執行時回傳 `EX_TEMPFAIL` 75；預設
7200 秒 timeout，逾時清理 process group 並回傳 124；一般 child exit code 原樣傳回。
Lock metadata 記錄 parent／child PID 供異常恢復；`--timeout-seconds` 可調整上限，
`--print-command` 顯示 shell-safe 命令。

Cron 或 systemd timer 應只重複呼叫上述 one-shot command，並把 75 視為「本輪已由
另一 worker 處理」。三角色時間需錯開；repo 不提供、安裝、enable 或 start 主機 unit。
固定間隔醒來後沒有安全工作就 no-op，不能在角色內等待下一個間隔。

Claude Code 可在各自 worktree 明確呼叫 `/dispatcher`、`/coder`、`/reviewer`；若使用
外部 loop plugin，plugin 只負責再次喚醒，角色本身仍只跑一輪並遵守同一把全域煞車。
