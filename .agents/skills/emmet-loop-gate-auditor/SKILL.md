---
name: emmet-loop-gate-auditor
description: "Execute exactly one checkpoint-bound Gate Auditor iteration for the emmet-qt-book loop. Use when the loop event manager invokes $emmet-loop-gate-auditor with reason=gate-audit-requested, or for a manual no-mutation diagnostic; only a valid event wake may publish one idempotent audit comment to Meta Issue #1."
---

# Emmet Loop Gate Auditor

執行一次獨立 gate-exit 稽核後立即結束。不要 sleep、輪詢、建立排程、派工、改檔、
改 label、合併或執行 gate transition。只從與最新 `origin/main` 一致的 trusted runner
載入本 skill 與治理指令。

<!-- loop-common-contract:start -->
## 共同低 token 安全契約

1. Client 已注入 trusted runner 驗證過的 `AGENTS.md`，算本輪完整讀取；注入缺失或來源
   不明就 fail closed，且不得再次輸出整份 `AGENTS.md`。
2. `bounded preflight` 只縮小候選，不是授權或 durable state。直接鎖定其中 object；
   `snapshot_incomplete` 本身阻斷；object truncation 只補 target 缺口，
   `meta_comments_truncated` 只影響 gate-exit／舊 marker 查找。
3. Active-gate 節、authoring guide、target Issue／PR 各只讀一次；skill 已是本角色協定投影。
   只有歧義才讀 `docs/agent-loop.md` 對應段落；正常 role 不讀 operations runbook。
4. mutation 前做一次 bounded live revalidation：pause、main、target labels，PR 再核對
   head／base／draft／mergeability。只對缺口分頁；預設禁止完整 comments/history 與
   all-issues 查詢。
5. Mutation 結果不明才重查。成功只留 exit／test count／必要 hash 的 compact summary；
   失敗才輸出 bounded diagnostics；結尾只輸出一個 compact summary。
<!-- loop-common-contract:end -->

## 唯一證據演算法

在固定 `MAIN_SHA` 完整讀取
`.agents/skills/emmet-gate-auditor/SKILL.md`，依它的「選定稽核目標」、五步稽核流程、
三方治理真相、逐條 curriculum 退出條件、main-bound evidence、freshness、狀態計算與
表格輸出做判定，不另造較寬鬆的演算法。為取得該契約要求的完整 Meta Issue #1 與
checkpoint 證據，可以只對 Meta #1 的缺口分頁；不得藉此列舉全部 Issues／PR。

手動 skill「固定稽核快照」中的證據固定與 freshness 規則仍適用，但其文件載入範圍由
本角色上方共同低 token 契約取代：使用已注入且驗證過的 `AGENTS.md`、只讀 curriculum
的 active-gate／退出條件段、authoring guide 與必要 target；正常路徑不讀 operations
runbook，協定歧義才讀 `docs/agent-loop.md` 對應段落。這個投影不得省略任何逐條退出
條件或判定所需證據。

手動 `$emmet-gate-auditor` 的安全邊界仍是正本。本 loop role 只在下節全部喚醒條件及
發佈前重驗成立時，才把其中「不得留言」與 `mutations: none` 邊界縮窄覆寫為：只可在
Meta Issue #1 追加一則可去重的 Gate Auditor 留言。所有其他禁令、證據標準與人類
決策邊界不變。

## 驗證喚醒授權

只有 event payload 同時滿足 `role=gate-auditor` 與
`reason=gate-audit-requested` 才可能發佈。先檢查 wake，再做任何 fetch、網路查詢、載入
手動 auditor 或 evidence audit：

- 完全沒有 event payload 的直接／手動呼叫固定輸出
  `result=manual-diagnostic-no-publish`。
- 有 event-like payload，但 role／reason 不符、欄位缺失或來源無法驗證，固定輸出
  `result=invalid-wake-no-publish`。

這兩條都立即結束，不執行 `git fetch` 或其他讀取；只回報 wake 為何無效，不嘗試完整
gate audit，不留言。結尾 sentinel 固定為 `gate=unknown`、`main_sha=unknown`、
`checkpoint_id=none`、`verdict=none`、`mutations=none`；`unknown` audit verdict 只保留給
已通過合法 wake、但必要 gate 證據仍不確定的 checkpoint。

把 packet 當候選提示而非授權，依序重驗：

1. `git fetch origin main --prune --quiet`，取得 live default branch 的完整 40 字元
   `MAIN_SHA`；packet、trusted `origin/main` 與 GitHub default main 必須相同。
2. Meta Issue #1 沒有 `loop:paused`，snapshot 完整，且所有 open loop Issue／PR 都已
   完整列入；live state 必須是 zero WIP，不能有 primary／blocked label、互斥狀態或
   successor-gate 誤派工作。
3. `AGENTS.md`、curriculum 與 Meta #1 一致宣告同一個目前 gate。
4. Packet 指定的 Dispatcher comment 必須仍存在、由可驗證的目前 `gh` viewer 建立、以
   `— Dispatcher` 署名、未失效，且其中唯一 gate-exit marker 精確對應目前 gate／main：
   `<!-- emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA> -->`。記錄其 immutable
   comment ID 為 `CHECKPOINT_ID`，不得自行改選較方便的舊 marker。
5. Meta #1 尚無同一 `<GATE>`、`MAIN_SHA`、`CHECKPOINT_ID` 的有效 Gate Auditor marker；
   任一 verdict 已存在都算本 checkpoint 完成，不重複留言。

任一條不成立就 fail closed 且不發佈。證據缺頁時只補必要缺口；仍不完整就回報
`unknown` 診斷，不把部分 snapshot 寫成 durable verdict。

## 稽核與 verdict

依手動 skill 逐條稽核 curriculum 的退出條件。這個 checkpoint-bound iteration 只允許：

- `not-ready`：至少一項必要條件明確失敗；dispatcher 可在下一輪依缺口恢復目前 gate。
- `unknown`：必要證據缺失、矛盾、過期或無法綁定目前 snapshot；fail closed。
- `exit-ready`：全部退出條件通過、三方治理一致且尚未 transition；仍須人類核准。

若證據落在 `transition-in-progress`／`transition-complete`，表示本次 exit-checkpoint wake
不再符合適用狀態；不發佈三選一 marker，回報 `invalid-gate-audit-state` 交人類處理。
不得把它映射成 successor 已授權。

## 唯一允許的 mutation

發佈前再以一次 bounded live query 重驗 pause、current main、zero WIP、三方 active gate、
同一 Dispatcher checkpoint，以及沒有既有 matching audit。全部仍成立時，才可在 Meta
Issue #1 追加一則留言，第一行必須是：

```text
<!-- emmet-loop:gate-auditor:audit:v1:gate=<GATE>:main=<SHA>:checkpoint=<ID>:verdict=<not-ready|unknown|exit-ready> -->
```

留言接著保留手動 skill 的固定欄位、治理真相表、退出條件表、Checkpoint 表、Transition
表、blockers 與 human_next_step，但下列 mutation-aware 欄位是明確替換，不得同時保留
互相衝突的手動值：

```text
skill: $emmet-loop-gate-auditor
audit_mutations: none
publication_mutation: meta-comment-only
mutations: meta-comment-only
```

其餘固定欄位與狀態計算不變；不得輸出 `skill: $emmet-gate-auditor` 或
`mutations: none` 到成功發佈的 durable report。報告最後署名 `— Gate Auditor`。
合法 loop publication 的三方 gate 已重驗一致且尚未 transition，因此狀態組合固定為
`exit-ready`=`pass/consistent/no`、`not-ready`=`fail/consistent/no`、
`unknown`=`unknown/consistent/no`（依序為 exit_criteria／governance_consistency／
active_gate_transitioned）；`audit_time` 必須是含 timezone 的 ISO 8601。
`exit-ready` 的 human_next_step 只能要求人類決定；不得輸出 approve、建立 transition
Issue／PR、更新治理來源或派 successor 工作。

先搜尋 exact marker 以保持冪等。留言命令結果不明時，先重查同一 marker；確認存在就
視為成功，不盲目重貼。除這一則 append-only Meta #1 comment 外，禁止任何 mutation，
包括 comment edit/delete、Issue body/state、label、PR/review/merge、tracked 或 untracked
file、branch/worktree、scheduler 與 gate declaration。

結束只摘要 `role=gate-auditor`、`result`、`gate`、`main_sha`、`checkpoint_id`、`verdict`
與 `mutations`。成功發佈時 `mutations=meta-comment-only`；其餘一律 `mutations=none`。
