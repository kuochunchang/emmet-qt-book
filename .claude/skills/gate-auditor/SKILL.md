---
name: gate-auditor
description: Gate-exit checkpoint 的 one-shot 獨立稽核角色。只在 event manager 以 reason=gate-audit-requested 喚醒時，才可向 Meta Issue #1 發佈一則冪等 verdict；手動呼叫維持唯讀。
---

# Gate Auditor（門檻稽核）

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
   失敗才輸出 bounded diagnostics；單一成功 command 最多回送 8 KiB，失敗 diagnostics
   最多 32 KiB。禁止直接掃描 runtime raw JSONL／stderr logs；診斷既有 iteration 只用
   `scripts/codex-loop inspect-event --runtime-dir <DIR> --event-id <ID>`。
6. 結尾可先輸出 compact 人類摘要，但最後一行必須是可機械解析且單行的
   `LOOP_OUTCOME {"role":"<role>","outcome":"<mutated|terminal-noop|blocked|failed>","result":"<stable-kebab-case>","mutations":[]}`。
   有已確認 workflow mutation 才用 `mutated`；成功且無 mutation 用 `terminal-noop`；
   需人類／外部狀態解除才用 `blocked`；執行或 transport 失敗用 `failed`。不得在
   marker 後再輸出文字。
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
gate audit，不留言。結果卡固定映射為 `gate=unknown`、`main_sha=unknown`、
`checkpoint_id=none`、`verdict=none`、`mutations=none` 與 `cache=none`：Gate 行填 unknown、
有效欄填未檢查、證據填 none、診斷填對應 result／none／none／cache=none。不要另加舊式
machine sentinel；`unknown` audit verdict 只保留給已通過合法 wake、但必要 gate 證據仍
不確定的 checkpoint。

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

Marker 後第一個可見區塊固定為 `## 操作者摘要`，先回答操作者，不要求他從完整表格自行
找結論。摘要依序包含：

- `判定`：`exit-ready` 寫「等待你決定」；`not-ready` 寫「尚未就緒」；`unknown` 寫
  「無法判定（安全停止）」。
- `影響`：明寫 active gate 仍未改變；`exit-ready` 也必須寫 successor 尚未生效。
- `快照`：完整 `MAIN_SHA`、`CHECKPOINT_ID`、`audit_time`，以及「本報告只對這個快照
  有效；main 一變更即失效」。
- `問題`：沒有 blocker 就寫「無」；否則依治理／snapshot／freshness、curriculum 原順序、
  checkpoint、transition／publication 的固定順序列前三項與剩餘數量。完整 blockers 仍
  全部保留在後文。
- `下一步`：指出唯一 owner 與最小安全動作。`exit-ready` 的 owner 只能是使用者，動作
  只能是決定是否啟動 gate transition。

操作者摘要之後才放固定欄位與所有表格；不得因摘要而刪減、折疊成不可見或改寫任何
逐條證據。摘要是 durable report 的易讀投影，不是另一個 verdict 或授權來源。

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

## 結尾的操作者交接

成功發佈時，先用 `apply_patch` 在 `/tmp` 建立本輪唯一、不可預測檔名的 report，
執行 `chmod 600`，再以 `gh issue comment 1 --body-file <REPORT_PATH>` 傳入完整內容。
命令完成後用 `apply_patch` 刪除該暫存檔。這個 file-backed transport 不依賴 command
stdin，可避免 bare `--body-file -` 在無 stdin 時發出空白留言。不得重用舊 report 檔；
建立、chmod 或刪除失敗都 fail closed。

不得使用 inline `--body`、stdin、heredoc、pipe 或 shell substitution，也不要把 report
body 放進會被 pretty renderer 顯示的 command argument，或在成功 command output／agent
message 回顯整份 report。完整 report 只留在 Meta #1；pretty 仍照常無損保留 client
實際產生的底層事件。發佈命令失敗時先依 exact-marker 重查規則收斂，不得更換 transport
盲目重貼。Terminal 最後一則 agent
message 使用下列至多九個 logical lines：

```text
Gate Auditor
判定：<等待你決定|尚未就緒|無法判定（安全停止）|未稽核（安全停止）|無 durable 判定（report 未發佈）|本輪不適用>
Gate：目前 <observed|unknown>；稽核 <audited|unknown>；後繼 <successor|none|unknown>（<未生效|transition 中|已生效|unknown>）
問題：<無|N 項；第一項 [ID] <至多 52 terminal display cells>>
下一步（<使用者|Dispatcher|無>）：<至多 60 terminal display cells 的唯一最小安全動作>
本輪：<已發佈 Meta #1 audit；只新增 report，未改 gate／label／PR／檔案|沿用既有 report，未重貼|未發佈 — 原因|發佈結果未知 — 不得重貼>
有效：<檢查時 main@<12字元 SHA>；at=<ISO 8601>|過期；bound@<12>≠current@<12>；勿沿用|無法確認|未檢查>
診斷：<result> / <verdict|none> / <meta-comment-only|none|unknown> / cache=<none|git-fetch>
證據：<Meta #1 comment #<AUDIT_COMMENT_ID>（checkpoint #<CHECKPOINT_ID>）：immutable permalink|舊 evidence（<packet|checkpoint|report>；已過期）：permalink|none — 原因>
```

除可能自行換行的 evidence permalink 外，每個 logical line 最多 80 terminal display cells；
寬字元按兩格計算，不用 Python 字元數冒充 pane 寬度。卡片只用 12 字元 SHA；完整 40 字元
SHA、全部 blocker、完整時間與固定欄位留在 durable
report。沒有 report 的失敗路徑，完整 SHA 留在前面的 bounded diagnostics／raw trace，
不可為了塞進卡片重複長輸出。`問題` 依固定順序取第一項：治理／snapshot／freshness，
再依 curriculum criterion 原順序，最後才是 checkpoint、transition 與 publication；
其餘只顯示總數，完整清單仍留 report 或 bounded diagnostics。卡片的 `at` 對應
`checked_at`，是實際產生
該有效性結論的最後一次 bounded main check 完成時間，不能拿 `audit_time` 猜填；published
使用發佈前重驗，matching no-op 使用 duplicate check，兩者都不宣稱 publication 後仍 current。

Verdict 只控制「判定」與 owner：

- `exit-ready` →「等待你決定」，owner=`使用者`；明寫退出條件通過，但後繼 gate 未生效，
  只能決定是否啟動 transition。
- `not-ready` →「尚未就緒」，owner=`Dispatcher`；依第一個 canonical blocker 恢復目前
  gate。
- `unknown` →「無法判定（安全停止）」，owner=`使用者`；修復權威／證據缺口並完成
  fresh audit 前禁止 transition。

只有來源 transport／pagination、live query 或 snapshot completeness 無法成立時，才走
`evidence-incomplete-no-publish` 並令 `verdict=none`。若 snapshot 已完整，而權威 evidence
本身明確 missing、conflicting、stale 或 unbound，必須發佈 `verdict=unknown`；不得用
no-publish 逃避 durable unknown blocker。

Iteration outcome 與 verdict 分開，固定使用下表；`computed=<verdict>` 只可在 publication
失敗／未知的「問題」行揭露，不得放入 `verdict` 冒充 durable audit：

| exit path | `result` | 判定／`verdict` | 本輪／`mutations` | 有效與證據 | owner |
| --- | --- | --- | --- | --- | --- |
| exact marker 已確認發佈 | `published` | 依本輪 verdict／該 verdict | 已發佈／`meta-comment-only` | 檢查時有效；新 permalink | 依 verdict |
| matching audit 已存在 | `matching-audit-no-op` | 依既有 verdict／既有 verdict | 沿用、未重貼／`none` | 檢查時有效；既有 permalink | 依既有 verdict |
| packet、checkpoint 或舊 audit SHA 落後 current main | `stale-snapshot-no-publish` | 未稽核／`none` | 未發佈／`none` | stale；舊 link 僅標「已過期」 | `Dispatcher` |
| pause、非 zero WIP、治理／checkpoint invariant 或其他前置條件失敗 | `precondition-failed-no-publish` | 未稽核／`none` | 未發佈／`none` | 依最後 check；沒有新 report | 見下段 |
| 合法 wake 但必要證據仍不完整 | `evidence-incomplete-no-publish` | 未稽核／`none` | 未發佈／`none` | unknown；沒有 report | `使用者` |
| 稽核落在 transition state | `invalid-gate-audit-state` | 本輪不適用／`none` | 未發佈／`none` | 依最後 check；沒有 report | `使用者` |
| 發佈失敗且重查確認 marker 不存在 | `publication-failed-no-publish` | 無 durable 判定／`none` | 未發佈／`none` | 依最後 check；沒有 report | `使用者` |
| 發佈與 marker 重查都無法確認 | `publication-state-unknown` | 無法判定／`none` | 發佈結果未知／`unknown` | unknown；不得宣稱有 report | `使用者` |
| 完全沒有 event payload | `manual-diagnostic-no-publish` | 未稽核／`none` | 未發佈／`none` | not-checked；沒有 report | `無` |
| event-like payload 無效 | `invalid-wake-no-publish` | 未稽核／`none` | 未發佈／`none` | not-checked；沒有 report | `無` |

前置條件失敗時，pause 或權威／GitHub 讀取缺口的 owner 是 `使用者`；WIP、checkpoint 或
可機械 reconciliation 的 protocol state owner 是 `Dispatcher`。Stale 路徑不得直接建立
checkpoint：Dispatcher 必須先對 current main 做 canonical reconciliation，重驗 zero WIP、
三方 gate 與退出證據；全部仍成立時才能建立 fresh checkpoint，再由 Gate Auditor 獨立
重稽核。`publication-state-unknown` 不得盲目重貼；恢復查詢後先搜尋 exact marker。

`no-op`、stale、precondition failure 與 publication state 都是 iteration outcome，不得
加入 marker 的三種 verdict taxonomy。`診斷` 保留 exact result、verdict、workflow
mutation 與 `local_cache_refresh`；手動／非法 wake 的 cache 固定 `none`。只有已確認發佈
可寫 `mutations=meta-comment-only`。沒有嘗試 publication、matching no-op，或發佈失敗後
重查已確認 marker 不存在，才可寫 `mutations=none`；發佈狀態仍不明必須寫
`mutations=unknown`。
