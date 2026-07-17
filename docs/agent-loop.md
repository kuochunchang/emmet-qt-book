# 四角色 agent 閉環協定

追蹤：Issue #40 ｜ 原始三角色設計（僅歷史背景）：
`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md` ｜ 2026-07-17 第四角色修訂：本文件

本文件是 loop 工作流的協定正本：label、狀態轉移、角色權限、持久證據、安全機制
與喚醒方式都以此為準。Claude Code 程序位於
`.claude/skills/{dispatcher,coder,reviewer,gate-auditor}/`；Codex 程序位於
`.agents/skills/emmet-loop-{dispatcher,coder,reviewer,gate-auditor}/`。兩種 client
共用 GitHub 上的同一狀態機；skill 若與本文件不一致，以本文件為準。既有
`$emmet-gate-auditor` 是人類明確呼叫的唯讀工具；第四角色使用獨立的
`$emmet-loop-gate-auditor`，不得放寬前者的一般安全邊界。

Trusted runner、event-driven CLI 與手動單輪診斷的操作者步驟見
[`agent-loop-operations.md`](agent-loop-operations.md)；該文件是操作導覽，不能放寬
本協定或 active gate。

## 執行模型

- CLI 分成兩種長生命週期 component：四個 `agent <role>` listener 等待本角色事件；
  單一 `events` manager 定期 polling GitHub、依本協定選出目前負責角色並通知。
- 每個事件只啟動一次短生命週期 Codex role iteration；它最多完成一個可稽核的主要
  狀態轉移後退出。Role skill 與 Codex child 不 `sleep`、不輪詢、不遞迴啟動自己。
- Event manager 只讀 GitHub 並送本機事件，不修改 GitHub durable state、候選工作樹
  或 gate；所有 workflow mutation 仍由收到事件後啟動的角色依權限執行。唯一的本機
  worktree mutation 是下述 trusted-control drain-and-rotate：manager 只交棒，不自行
  checkout 或重啟。
- Manager 每次 routing 前 fetch `origin/main` 並比對 control inputs。發現改變時停止
  派送；有 child 先 drain，idle 後交給 launcher-owned detached rotator。Rotator 必須
  驗證 events PID／lock、session ownership、same-repo 與乾淨 worktrees，停止
  components 後才同步 dedicated launcher control worktree 與四個 runners，並由新
  generation 執行五項 preflight 與重建 session。
- GitHub Issue、PR、label、署名留言與 commit SHA 是 durable state；本機 session
  與 worktree 都可能中斷，下一輪必須先 reconciliation 才能開始新工作。Unix socket、
  event fingerprint 與 manager 記憶體中的 retry 狀態都不是 durable workflow state。
- Role 從 control inputs 與最新 `origin/main` 完全一致的 trusted runner 載入
  skill、AGENTS 與 Codex project config。只有非 control paths 前進時，runner 的
  detached HEAD 可在 session 內暫時落後；每個 role iteration 仍先 fetch 最新 main，
  task／candidate worktree 也必須以該 snapshot 建立。Runner 不 checkout 候選分支，
  候選內容不能成為下一輪控制指令。
- 四個角色共用同一個 GitHub 帳號，因此不用 assignee 與 GitHub 原生 review
  approve。留言文末分別署名 `— Dispatcher`、`— Coder`、`— Reviewer`、
  `— Gate Auditor`；署名本身不是授權，仍須核對 marker、角色權限與 live snapshot。

## 角色與權限

| 角色 | 職責 | 可寫入 | 禁止 |
| --- | --- | --- | --- |
| dispatcher | reconciliation、派工、合併安全的 approved PR、停滯偵測、gate 退出彙整 | GitHub label／留言／merge | 改碼、寫稿、審查裁決、gate transition |
| coder | 認領或恢復一件任務、實作、驗證、交審 | 自己的任務分支、GitHub | 合併、push `main`、審查裁決 |
| reviewer | 在最新 `main` 上獨立審查一個 integration candidate 並裁決 | GitHub label／裁決留言 | 改碼、commit、push、合併、派工 |
| gate-auditor | 在 Dispatcher gate-exit checkpoint 上獨立逐條稽核退出條件 | 僅 Meta Issue #1 一則冪等 audit 留言 | label／body／PR／檔案、派工、核准或執行 gate transition |

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

1. 先看 event manager 的 bounded preflight；它只縮小候選，不是授權。Packet 已 paused
   就不寫 GitHub 或工作樹並回報；`snapshot_incomplete` 本身阻斷 mutation，其他 truncation
   只在缺失證據會影響本輪決策時窄補查。
2. `git fetch origin main --prune`，記錄完整 `MAIN_SHA`。Trusted runner preflight 已驗證
   control inputs，client 已注入的 `AGENTS.md` 算本輪讀取，不再用工具輸出整份文件。
   每輪只讀一次 curriculum active-gate 節、authoring guide 與對應 Issue／PR；role skill
   是本協定的角色投影，只有歧義才讀本文件對應段落。
3. 用一次 bounded live query 比對 Meta pause、default main SHA、target labels；PR 再比對
   head／base／draft／mergeability。預設不抓完整 comments/history 或 all-issues；只有
   marker 歧義、recovery 或 packet 指明缺頁時才對缺口分頁。AGENTS、curriculum、Meta
   Issue active gate 不一致時 fail closed。
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
- 同一 immutable source 每輪只讀一次；mutation 結果不明才重查。成功測試只保留
  exit、test count 與必要 hash 的 compact summary，失敗才輸出 bounded diagnostics。
- 每個角色結束時摘要 `role`、穩定 kebab-case `result`、`object`、`main_sha`、
  `head_sha` 與 `mutations`，讓 raw JSONL trace 可追查。

## Dispatcher 一輪

優先序為：補完已 merge 狀態 → 修復失效 approval／異常 → 檢查 gate exit → 安全合併
一個 approved PR → 處理停滯／圈外工作 → 派一個 active-gate slice → no-op。

派工前必須先判斷 active gate 退出條件。證據已齊時只彙整完成 Issue／PR／merge SHA
並通知使用者；使用者核准且 gate-transition PR 合併前，不派下一 gate。

若最新有效 Gate Auditor audit 以 `not-ready` 綁定目前 gate／main 的某一個 Dispatcher
checkpoint comment ID，該舊 checkpoint 對 routing 已失效。Dispatcher 得依 audit 的
明確缺口恢復或派一個目前 active-gate slice；`unknown` 不授權猜測，`exit-ready` 只等待
人類。缺口解決且退出條件重新成立時，即使 main SHA 未改變，也必須建立新的 gate-exit
checkpoint comment，讓它取得新 comment ID。此情形是一般「相同 marker 已存在就不
重複」規則的唯一例外；沒有 superseding `not-ready` audit 時仍維持正常 duplicate
suppression。舊 audit 永遠不綁定新 checkpoint。

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

## Gate Auditor 一輪

Gate Auditor 只接受 event manager 送出的 `reason=gate-audit-requested`；手動呼叫第四
角色只能產生唯讀診斷，不得留言。它先固定最新 `MAIN_SHA`，再完整讀取既有唯讀
`$emmet-gate-auditor` skill，沿用其中三方治理真相、逐條 curriculum 退出條件、
main-bound evidence、checkpoint、freshness 與 verdict 演算法，不建立第二套較寬鬆標準。

只有下列條件全部成立才進入可發佈稽核：

- Meta Issue #1 未 paused，event snapshot 完整，且 bounded live query 證明 zero WIP；
  不得有 primary／blocked label、狀態異常或 successor-gate 誤派工作。
- Packet、GitHub default main 與 trusted `origin/main` 是同一個完整 40 字元 SHA；
  AGENTS、curriculum 與 Meta #1 一致宣告同一 active gate。
- Packet 指向的 Dispatcher comment 仍含該 gate／main 的精確 gate-exit marker；其
  immutable comment ID 是本次 `CHECKPOINT_ID`，不得自行改選其他 checkpoint。
- Meta #1 尚無綁定同一 gate、main 與 `CHECKPOINT_ID` 的有效 Gate Auditor audit。

Audit 只發佈 `not-ready`、`unknown` 或 `exit-ready`。發佈前再重驗 pause、current main、
zero WIP、三方 gate、同一 checkpoint 與 duplicate absence；任何 drift 都不留言。唯一
允許的 mutation 是在 Meta Issue #1 追加一則以精確 marker 開頭、以
`— Gate Auditor` 結尾的完整稽核報告：

```text
<!-- emmet-loop:gate-auditor:audit:v1:gate=<GATE>:main=<SHA>:checkpoint=<ID>:verdict=<not-ready|unknown|exit-ready> -->
```

報告逐條列出 authority、main-bound／live evidence、freshness、blocker 與人類下一步。
相同 marker 已存在就冪等退出；留言結果不明時先重查，不盲目重貼。手動 Auditor 的
`mutations: none` 只在這組合法 event 與發佈前重驗下，縮窄覆寫成這一則 append-only
comment；不得 edit/delete comment、修改 Issue body/state／label、操作 PR、改檔、派工、
喚醒角色或執行 transition。

`exit-ready` 只讓流程進入 `awaiting-user`，不等於 gate 核准。`unknown` fail closed。
`not-ready` 使它所綁定的舊 checkpoint 對 routing 失效；dispatcher 下一輪可依報告缺口
恢復或派目前 active-gate 工作。缺口解決後 dispatcher 必須建立一則新的 gate-exit
checkpoint comment，取得新 comment ID，即使 main SHA 沒有改變也不能沿用舊 comment；
舊 audit 只保留為歷史，不綁定新 checkpoint。

## 全域安全與通知

- 任何 mutation 前重查 `loop:paused`；dispatcher 合併與 reviewer 裁決前一定重查。
- 任一 primary state 超過 6 小時未變，由 dispatcher 加 blocked 並通知；同一 PR
  第 3 次 changes-requested 也交使用者裁決。
- `./scripts/book-check` 未實際通過，coder 不得交審；reviewer 重跑的 PR 歸因失敗
  不得批准。
- Gate transition 永遠不在 loop 授權內。Dispatcher 只能建立退出 checkpoint，Gate
  Auditor 只能提出證據 verdict；`exit-ready` 後仍等待使用者依 AGENTS 的固定流程核准。
- 需要使用者介入時，以 Meta Issue #1 留言為 durable 通知；可另接桌面／Discord，
  但外部通知失敗不得偽裝成已通知，也不得反覆洗版。

## CLI 事件驅動

### 元件與 routing

Event manager 每次 poll 只讀 Meta Issue #1 與所有 open `loop:*` Issue／PR，依下列
優先序送事件：

| GitHub live state | 通知 |
| --- | --- |
| `loop:paused` | 四個 agent 收到 paused control event，不啟動 Codex |
| blocked、互斥／缺少 primary state、WIP 異常或 PR／Issue 不配對 | dispatcher |
| 一個 `loop:approved` PR | dispatcher |
| 一個 `loop:changes-requested` PR | coder |
| 一個 `loop:needs-review` PR | reviewer |
| 一個 queued／coding Issue，尚無 PR | coder |
| 沒有 loop WIP，有綁定目前 `main` 的 gate-exit checkpoint，尚無 matching audit | gate-auditor，`reason=gate-audit-requested` |
| matching audit verdict 是 `not-ready` | dispatcher，依 audit 缺口恢復目前 gate |
| matching audit verdict 是 `unknown` | 不通知角色；fail closed 並輸出 operator blocker |
| matching audit verdict 是 `exit-ready` | 不通知角色；進入 `awaiting-user` |
| 沒有 loop WIP，且沒有目前 `main` 的 gate-exit marker | dispatcher，做 reconciliation、gate exit 判斷或派工 |

Dispatcher 確認目前 gate 已退出時，Meta Issue #1 的署名留言必須含精確 marker：

```text
<!-- emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA> -->
```

Manager 只接受最近 100 則留言中、由目前 `gh` viewer 自己建立、未 minimized、從未
edited、最後一個非空行精確等於 `— Dispatcher`，而且只含一個 gate-exit marker 的
checkpoint。Marker 的 gate 必須等於 Meta Issue body 唯一宣告的 active gate，完整
40 字元 `MAIN_SHA` 必須正好等於 repository default `main` head；comment ID 使用
`fullDatabaseId` 的 BigInt 字串並正規化成正整數。最新有效 comment 是目前 checkpoint；
同文字的新 comment 仍是不同 checkpoint。它只有在沒有 loop WIP 時才進入 audit routing；
任何 WIP 仍依表中較高優先序處理。`main` 前進、marker 過期、留言窗內找不到 marker 或
無法驗證作者時一律 fail closed，重新交 dispatcher 判斷，不能把舊 gate exit 當成新
狀態的完成證據。由 Dispatcher 署名但 gate／marker／pristine invariant 失敗的目前-main
候選不得回退採用較舊 checkpoint；視為 snapshot invariant error。

Gate Auditor 留言也必須由目前 viewer 建立、未 minimized、從未 edited、第一行是唯一
audit marker、最後一個非空行精確等於 `— Gate Auditor`，並精確綁定 active gate、目前
完整 `MAIN_SHA` 與所選 checkpoint ID；verdict 只能是三個 allowlist 值。Audit report
還必須唯一包含完整 fixed fields；其中 `skill`、`verdict`、`audited_gate`、
`observed_active_gate`、`main_sha`、`human_decision_required` 與三個 mutation-aware 欄位
必須和 marker／固定映射一致。三個 verdict 的 exit／governance／transition 狀態固定為
`exit-ready`=`pass/consistent/no`、`not-ready`=`fail/consistent/no`、
`unknown`=`unknown/consistent/no`；`audit_time` 必須是含 timezone 的 ISO 8601。Checkpoint
表可以引用 Dispatcher marker，但角色署名必須防止該引用反向成為新的 checkpoint。同一
checkpoint 有多個互斥 audit、marker／report 欄位缺失或作者／SHA／pristine 狀態無法
驗證時不得挑一個較方便的結果；視為 snapshot invariant error。`not-ready` 只 supersede
它綁定的舊 checkpoint；dispatcher 修復後建立的新 comment ID 不受舊 audit 影響，必須
重新 audit。

同一 fingerprint 被 agent ACK 後，manager 在預設 1800 秒內不重送；唯一例外是
`snapshot-incomplete`：相符的 iteration 成功完成後保持 operator blocker，相同不完整
快照不再定時啟動 Codex，直到 completeness 或 routing-bearing state 改變；尚未完成或
非零退出仍依 retry window 重試。Labels、main、PR head／base／
draft／mergeability 改變會形成新 fingerprint，單純留言造成的 `updatedAt` 不會重送。
若已記錄的 iteration 完成但 workflow state 沒有前進，
manager 改判 `stalled` 並只做一次 dispatcher escalation，不再用 retry window 反覆喚醒
原 owner；唯讀的 `snapshot-incomplete` 補查不算 durable-progress stall。Agent 不在線、
socket 拒絕、沒有 ACK，或尚無法確認一般 iteration 已完成時，才依 poll／retry 規則重試。
Manager 重啟後可重送目前狀態；role iteration 必須靠 GitHub durable marker 保持冪等，
不能依賴本機去重。

任何 Codex child 執行期間，manager 除 paused control event 外不送新的 wake，避免
跨角色同時 mutation。在途 state 持續達預設 1800 秒且本輪沒有 child 時，manager
先以 `oversight-heartbeat` 單獨喚醒 dispatcher，讓它執行 canonical reconciliation、
6 小時停滯與 gate exit 檢查；dispatcher 結束後，後續 poll 才再通知 state owner。

四個 agent 使用 Git common-dir 派生的同一個 mode `0700` runtime directory，各自監聽
mode `0600` Unix stream socket。Event payload 與 ACK 都是單行 JSON；payload 包含
`role`、`action`、`reason`、repository、fingerprint、`event_id`、poll 時間，以及
allowlist 投影的 `preflight`。Preflight 含 pause、Meta active gate、main SHA、目前
gate-exit checkpoint、matching Gate Auditor audit、workflow fingerprint、分頁完整性與最多八個 object 的
labels／head／base／draft／
mergeability；不含 body、comments 或 `updatedAt`。超過上限必須明示 total／truncated，
不能靜默丟證據。錯 role、未知 action、過大或無效 JSON 必須拒絕，不能啟動 Codex。

### 操作員狀態與阻斷判定

每次成功 poll，event manager 另外建立一筆 `result=operator-status` 記錄；
JSONL 模式直接輸出該記錄，pretty 模式將它保留在 component JSONL trace 並顯示易讀投影。欄位
`health`、`blocking`、`owner`、`current`、`next` 與 `attention` 分別回答
目前健康度、是否阻斷、誰負責、現況、下一個安全動作與需介入原因。解說直接取自
同一次 GitHub snapshot、routing decision 與本機 agent completion metadata；它不做
第二次 polling、不使用模型，也不在上述 canonical routing 外任意啟動角色。

| `health` | `blocking` | 意義 |
| --- | --- | --- |
| `healthy` | `false` | durable state 合法且有明確 owner／下一步 |
| `running` | `false` | 正好有一個 Codex iteration 執行中 |
| `draining` | `false` | control inputs 已更新；停止派送並等待目前 child 結束 |
| `rotating` | `false` | detached rotator 正在安全同步 control worktree／runners 與重建 session |
| `awaiting-user` | `false` | 目前 checkpoint 已有 matching `exit-ready` audit 且沒有 WIP；等待使用者核准 transition |
| `paused` | `true` | Meta Issue #1 的 `loop:paused` 正在阻止推進 |
| `blocked` | `true` | state invariant、同時 busy、delivery 或 GitHub polling 有錯 |
| `stalled` | `true` | owner iteration 已完成，但 durable workflow state 沒前進 |

Agent 完成 iteration 時把最近四筆 event ID、reason、exit code 與完成時間寫入既有
role lock metadata。Manager 以 delivery 當下和下一次 poll 的 workflow fingerprint
比較 label、Issue／PR 配對與 PR head／base 等 routing-bearing state；一般留言造成的
`updatedAt` 不算進度。相關留言內容只有被正規化的 gate-exit checkpoint 與綁定其
comment ID 的 Gate Auditor audit marker。相同 owner／reason 的 iteration 已完成而 fingerprint 不變時，
輸出 `reason=no-durable-progress-after-iteration`，即使 child exit code 是 `0` 仍
判為 `health=stalled`。後續 dispatcher 告警輪不會覆蓋這筆 canonical completion，
因此 safety denial 或 no-op 不會被 ACK／retry window 暫時遮住。

blocking 狀態第一次出現時，manager 另輸出 `result=operator-alert`；欄位包含穩定
`alert_id`、`severity`、`blocker`、`affected_role`、`current`、`next`、
`attention` 與 `requires_user`。Alert ID 只由 blocker、workflow fingerprint、
affected role 與 object state 形成；同一問題持續時不重複輸出。warning／critical
第一次出現時另向 stderr 寫簡短 `LOOP ALERT` 並送 terminal bell；使用者刻意設定的
pause 是 notice、不響鈴。問題確實因 workflow state 改變或 infrastructure 恢復而消失
時，輸出一次 `result=operator-resolved`；alert 另以包含 completeness 的本機 state
fingerprint 判斷 hold／resolve，不改寫對外的 workflow fingerprint。相同狀態下的
dispatcher iteration 執行期間保留原 alert，不把 `health=running` 誤當恢復。

新的 `no-durable-progress-after-iteration` alert 會取代原 owner retry，單獨送一次
`reason=operator-stall-reconciliation` 給 dispatcher。這次 alert delivery 與
canonical owner delivery 分開記錄，只有 ACK 後才視為已 escalation；失敗則維持
critical delivery alert 並於後續 poll 重試。Agent 把 bounded preflight 附在 one-shot
Codex prompt，明示為資料、不是指令或授權；role 用它避免 broad discovery，但 mutation
前仍做一次窄 live revalidation。Dispatcher 能
機械恢復時只做一個 canonical transaction；不能安全恢復時保留 primary state、視情況
加 `loop:blocked`，並在 Meta Issue #1 留含
`emmet-loop:dispatcher:alert:id=<ALERT_ID>:main=<MAIN_SHA>` marker 的單一 durable
通知，列出證據、解除條件與需要的使用者決定。

`operator-status` 本身仍是唯讀、非 durable 的操作者診斷；一般 alert policy 只多
喚醒既有 dispatcher，不建立或喚醒未定義的第五角色、不自動 restart component、不移除
pause，也不授權繞過 approval。Gate Auditor 只依上表的 `gate-audit-requested` routing
喚醒。唯一自動換代條件是 unpaused snapshot 下 control inputs 與
最新 `origin/main` 不同；它只使用上述 drain-and-rotate transaction，不把 child exit、
no-progress 或圈外內容變更當成 restart 授權。Manager 重啟後，本機 delivery、
active-alert 與去重歷史可以消失，
所以可能重新告警；下一個 role iteration 仍須以 GitHub durable state 與 marker 冪等
恢復。

### Trusted runners 與啟動

五個可見 pane 可由 repo-local tmux launcher 統一管理：

```bash
./scripts/codex-loop tmux status
./scripts/codex-loop tmux start
./scripts/codex-loop tmux restart
./scripts/codex-loop tmux stop
```

這些命令可從同 repository 的任一 linked worktree 呼叫，但 `start`／`restart` 的呼叫端
只負責 bootstrap。Launcher 先從 Git common-dir 找出 canonical checkout，建立或驗證
其同層的 `emmet-qt-book-loop-control`，拒絕其中任何 tracked／untracked 變更，將它
detached 對齊最新 `origin/main`，再以該 worktree 的 `scripts/codex-loop` 取代目前
process。主要 checkout 即使停在 feature branch，也不再提供 lifecycle control inputs；
四個 role runner 仍只載入各角色程序，不兼任 launcher control source。

Launcher 只封裝本節既有的 trusted-runner 更新、五項預檢、先 agents 後 manager 的
啟動順序，以及先 manager 後 agents 的停止順序；不建立第二套 routing／durable
state，不變更 GitHub label，不安裝 scheduler。它只會取代帶有本 repository
ownership marker 的同名 tmux session，並以 role lock metadata 驗證要停止的 PID；
驗證失敗就停止。完整操作、五 pane 位置、detach 與中途失敗清理見
[`agent-loop-operations.md`](agent-loop-operations.md#tmux-一鍵生命週期建議入口)。

沒有顯式 profile 時，adapter 使用 repo 受控的角色預設：dispatcher 與 coder 為
`gpt-5.6-sol`／high，reviewer 與 gate-auditor 為 `gpt-5.6-sol`／xhigh，四者
verbosity 都是 low。這些值屬 control inputs，合併後由正常 drain-and-rotate 才生效。
操作者可用共同或 role-specific
`--profile` 完整取代對應角色的 repo 預設；adapter 每次 wake 都重驗 profile 檔存在且
可解析，不能缺檔後靜默退回 user config。Event manager 不使用模型。

`start`／`restart` 會自動準備 dedicated launcher control worktree。一次性手動準備
其餘 trusted runner worktree（存在則不重建；它們不拿來 checkout 任務）：

```bash
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-coder origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-reviewer origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-gate-auditor origin/main
```

先做不啟動角色的預檢（預檢仍會 fetch trusted `origin/main`）：

```bash
./scripts/codex-loop agent dispatcher --dry-run
./scripts/codex-loop agent coder --workdir ../emmet-qt-book-coder --dry-run
./scripts/codex-loop agent reviewer --workdir ../emmet-qt-book-reviewer --dry-run
./scripts/codex-loop agent gate-auditor --workdir ../emmet-qt-book-gate-auditor --dry-run
./scripts/codex-loop events --once --dry-run
```

五個 component 應各在可見終端執行；先啟動 agents，最後啟動 manager：

```bash
./scripts/codex-loop agent dispatcher
./scripts/codex-loop agent coder --workdir ../emmet-qt-book-coder
./scripts/codex-loop agent reviewer --workdir ../emmet-qt-book-reviewer
./scripts/codex-loop agent gate-auditor --workdir ../emmet-qt-book-gate-auditor
./scripts/codex-loop events --interval-seconds 60 --retry-seconds 1800 \
  --dispatcher-heartbeat-seconds 1800
```

`agent` 每次啟動 Codex 都使用 `codex exec --ephemeral --json`、`workspace-write`、
on-request approval 與 auto reviewer；不使用 dangerous bypass。`--json` 是底層
machine-readable contract，operator 顯示由 `--output-format auto|pretty|jsonl` 選擇。
Direct agent、events 與 one-shot 預設 `auto`：TTY 即時顯示 thread、turn、message、
command、file change、tool、error 與 usage 的易讀 pretty 投影，pipe／redirect 則保持
JSONL；launcher-owned tmux 五個 pane 明確預設 `pretty`，可用
`tmux restart --output-format jsonl` 切回原始事件顯示。Tmux launcher 的 lifecycle
stdout 本身仍是 JSON，不經這個 role／events renderer。

Pretty 模式會先將 Codex stdout 原始 JSONL bytes、Codex child stderr 與 component
JSONL 分開寫入現有 loop runtime namespace 下的 `logs/` generation files，該目錄在
repository 之外且 mode 為 `0700`，檔案 mode 為 `0600`；component 的 waiting、
delivery、event、exit code 與 timeout 不會插入 Codex stdout stream。未識別或 malformed
Codex 記錄只在終端顯示安全警示，不改寫原始 log。這些檔案只是 local
operator trace，不是 GitHub durable state，不得用於 routing、reconciliation、授權
或推定 mutation 成功。角色成功 command 的 compact summary 與失敗時的 bounded
diagnostics 只限制送回 model context 的內容，不會裁掉 raw trace。

啟動及每次 Codex iteration 前都 fetch `origin/main`，並拒絕非本 repo linked
worktree、不同 origin，以及與 trusted ref 不同的 `.agents`／`.codex`／AGENTS／
治理文件或 adapter。`--workdir` 只能指向同 repo trusted runner。

只有非 control paths 改變時，`runner_head_matches=false` 不阻止下一輪；
`control_inputs_match` 才是 long-lived generation 是否可繼續載入角色程序的判定。
Control drift 時 manager 輸出 `health=draining|rotating`，不 ACK 尚未交付的 role
event；rotator 的 `rotation-state.json` 與 `rotation.log` 只存在 mode 0700 runtime
directory，並非 workflow durable state。Paused snapshot 不做本機換代；只由使用者解除
`loop:paused` 後才重新判定。

Agent component 持有 per-role `flock` 直到停止；child 繼承 lock FD，避免 parent
異常退出時重疊；event manager 另持單一 `events` lock。第二個同角色 agent 或第二個
manager 回傳 75；每次 iteration 預設 7200 秒 timeout，
逾時清理 child process group 並記錄 124。手動診斷仍可用
`./scripts/codex-loop <role>` 執行一次 iteration，但連續運作只使用
`agent <role>` ＋ `events`，不使用 cron、systemd timer 或 App Scheduled Tasks
重複喚醒。Repo 不提供、安裝、enable 或 start 主機 unit。

Claude Code 仍可在各自 trusted runner 明確呼叫 `/dispatcher`、`/coder`、
`/reviewer`、`/gate-auditor` 做單輪診斷；不得另建第二套 polling 或 durable state。
