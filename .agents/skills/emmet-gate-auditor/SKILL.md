---
name: emmet-gate-auditor
description: Perform a read-only, evidence-bound audit of the emmet-qt-book active gate and advise a human whether its exit conditions are unsatisfied, satisfied, or in transition. Use only when the user explicitly invokes $emmet-gate-auditor to assess gate exit or transition readiness; never mutate GitHub, tracked files, branches, labels, PRs, Issues, scheduler state, or gate declarations.
---

# Emmet Gate Auditor

協助人類做 gate 決策，但不替人類做決策。每次只執行一次唯讀稽核，逐條比對權威退出條件與可追溯證據，輸出固定 verdict 後停止。

這不是 dispatcher／coder／reviewer 之外的第四個 loop 角色。不得呼叫三角色 skill、推進工作、核准 transition，或把「退出條件已滿足」說成「下一 gate 已生效」。

## 安全邊界

- 只使用讀取命令，例如 git status、git show、git log、git merge-base、git rev-parse、git ls-remote、gh issue view、gh pr view 與唯讀的 gh api GET。
- 可以執行 git fetch origin main --quiet 更新本機 remote-tracking cache；這不構成遠端或受版本控制內容的 mutation，但必須記錄 local_cache_refresh。若使用者要求連本機 ref 都不能改，改用 git ls-remote 與 GitHub GET API，不能用舊 cache 猜測。
- 禁止執行任何寫入，包括檔案編輯、commit、push、branch／worktree 變更、Issue／PR 建立或留言、body／label 變更、review、merge、close、reopen、scheduler／timer 操作。
- 禁止以本 skill 的判定喚醒 loop 角色、派工或開始下一 gate 工作。
- 即使發現明顯缺口，也只列出缺口與下一個最小的人類動作；本輪固定輸出 mutations: none。
- mutations: none 明確表示遠端、tracked files、工作流程與 scheduler 都未改變；本機 remote-tracking cache 是否 refresh 由獨立欄位揭露。

## 選定稽核目標

- 使用者指名 gate 時，以該 gate 為 audited_gate；未指名時，以三方治理真相一致宣告的目前 gate 為目標。
- 未指名且三方不一致時，audited_gate 與 observed_active_gate 都輸出 unknown；不得自行挑一份較方便的來源。
- 指名的是尚未生效的未來 gate 時，不得提前稽核其退出或引用未發布能力；輸出 unknown，並指出目前 observed_active_gate。
- 指名的是剛完成 transition 的前一 gate 時，可以核對 transition-complete；不得把 successor 的退出條件混進舊 gate。
- 從 curriculum 取得 successor_gate，不自行推測或跳級。

## 稽核流程

### 1. 固定稽核快照

1. 確認 repository root、remote 與工作樹狀態；不得把目前 feature branch 當成權威狀態。
2. 取得遠端 main 的最新完整 40 字元 SHA，記為 MAIN_SHA。若更新了 origin/main，之後所有 repository 證據都綁定同一個 MAIN_SHA。
3. 從 MAIN_SHA 讀取完整的 AGENTS.md、docs/curriculum.md、docs/authoring-guide.md、docs/agent-loop.md 與 docs/agent-loop-operations.md；不要依賴工作樹中尚未合併的版本。
4. 讀取 GitHub Meta Issue #1 的完整 body、labels 與所有 comments，再讀取目前 gate、退出條件及 transition 所引用的 Issues、PRs、checks、reviewer 裁決與 merge commits。
5. 記錄稽核時間與每個 live source 的最後狀態。不得只看搜尋摘要、單一 comment 或 Issue 的 open／closed 值。

若無法取得最新 main、完整 Meta Issue 或必要的 live GitHub 證據，將受影響項目標成 unknown，不得沿用記憶或舊報告。

### 2. 獨立建立三方治理真相

分別找出下列來源宣告的 active gate，不要讓其中一份覆蓋另一份：

- AGENTS.md @ MAIN_SHA
- docs/curriculum.md @ MAIN_SHA
- GitHub Meta Issue #1 的最新有效狀態

Meta Issue #1 的 active gate 以 body 中的目前狀態欄位為準；comments 是 durable evidence，不能默默覆蓋 body。若較新 comment 與 body 衝突，只能依有效 transition 證據辨識 transition window，否則 fail closed。

同時從 curriculum 取得 audited_gate 的逐字退出條件與固定 successor。GitHub Milestone、label、一般 Issue 活動、feature branch、未合併 PR 或對話都不能改變 active gate。

若 AGENTS.md 與 curriculum 在同一個 MAIN_SHA 已不一致，fail closed。若兩份 main 文件已由有效 transition PR 一起合併、Meta #1 尚待立即同步，可辨識為受追蹤的 transition window；除此之外的三方不一致皆為 unknown 的治理缺陷。

### 3. 逐條驗證退出條件

為 curriculum 中 audited_gate 的每一條退出條件各建一列，不得合併、改寫成較寬鬆的摘要或漏掉否定條件。每列只能使用：

- pass：條件的全部要素都有權威證據，且需要進入 repository 的證據已存在於 MAIN_SHA 的 tree 或 ancestry。
- fail：至少一個必要要素明確未完成或與 live state 衝突。
- unknown：必要來源缺失、無法讀取、相互矛盾、過期或不能綁定 MAIN_SHA。

每列固定記錄 criterion_id、requirement、status、authority、evidence、evidence_sha、freshness 與 reason_or_gap。freshness 只能是 current、stale 或 unbound。

套用以下證據規則：

- Closed Issue、活動紀錄、規劃文字、feature branch commit、open PR、draft PR 或「已經跑過」的宣稱，本身都不是完成證據。
- 需要合併的證據必須以完整 merge／evidence SHA 證明可由 MAIN_SHA 到達，並指出 main 上的檔案、測試或 durable 紀錄。
- 動態條件同時核對 live state。例如「Issue 保持開啟」不能只靠舊截圖；跨 gate Issue 要逐一判定目前 slice，open 不等於未完成，closed 也不等於通過。
- 測試或輸出只有在能重現、或已有符合規範的 durable evidence 時才可採信。重新執行時記錄命令、日期與結果，但不得因此寫回 repository 或 GitHub。
- 對 open transition PR，GitHub CLEAN／MERGEABLE 不是驗證證據；記錄受測 base 與 head。若 main 後來移動，必須重驗目前 integration candidate，否則將該 PR 的驗證標 stale。
- Dispatcher gate-exit marker 必須精確綁定目前完整 MAIN_SHA；舊 SHA 一律標 stale，不能直接沿用。Marker 屬 checkpoint evidence；除非 curriculum 明列，stale marker 不改寫 exit_criteria，但必須列為 blocker。
- checkpoint 還要核對是否有任何未完成或 blocked 的 loop Issue／PR、殘留 primary／blocked labels，或誤派的下一 gate 工作。
- 若 main 在稽核途中移動，不能混用兩個快照。

### 4. 分開計算三種狀態

先計算 exit_criteria；它只彙總 curriculum 的逐條退出條件，不混入 checkpoint 或治理同步狀態：

1. 任一逐項條件為 unknown：unknown。
2. 無 unknown 且至少一項為 fail：fail。
3. 所有逐項條件皆 pass：pass。

再計算 governance_consistency：

- consistent：三方一致宣告 audited_gate，或 transition 完成後一致宣告 successor_gate。
- transition-window：三方暫時不一致，但有使用者核准、tracking Issue、transition PR 與 merge SHA 可完整解釋固定流程進度。
- inconsistent：三方明確衝突，且沒有有效 transition 證據解釋。
- unknown：任一治理來源無法取得或 freshness 不明。

再計算 active_gate_transitioned：

- yes：transition PR 已合併，且最新 main 的 AGENTS.md、curriculum 與 Meta #1 全部一致宣告 successor_gate，並可核對前一 gate 證據與 transition merge SHA。
- no：三方仍一致宣告 audited_gate，或已有有效追蹤的 transition 但固定程序尚未全部完成。
- unknown：三方狀態不一致，且沒有有效的使用者核准、tracking Issue、PR 與 merge 證據可解釋為 transition window。

最後依序產生一個 verdict：

1. exit_criteria 或 active_gate_transitioned 為 unknown，或 governance_consistency 是 inconsistent／unknown：unknown。
2. exit_criteria 為 fail：not-ready。
3. exit_criteria 為 pass、active_gate_transitioned 為 yes：transition-complete。
4. exit_criteria 為 pass，已有使用者明確核准、tracking Issue 與 transition PR，但三方尚未全部完成固定程序：transition-in-progress。
5. exit_criteria 為 pass、active_gate_transitioned 為 no，且尚未開始有效 transition：exit-ready。

Stale／blocked checkpoint 不改寫 curriculum 的 exit_criteria。有效 transition 尚未開始時，它會阻止 exit-ready，整體 verdict 為 unknown；有效 transition 已開始時，保留 transition-in-progress，但必須列為合併或下一步前先處理的 blocker。

若 transition 已開始但退出條件後來失效，以 unknown 或 not-ready 優先，並把 transition 列為必須暫停的風險。transition-in-progress 絕不代表可以啟動 successor；只有 transition-complete 代表被稽核的舊 gate 已由三方共同換成 successor。

### 5. 做最後 freshness check

重新讀取遠端 main SHA 與 Meta Issue #1 的目前 gate。任一來源在稽核期間改變就不要拼接新舊證據；改報 unknown，清楚指出需要以新快照重新執行一次完整稽核。

## 固定輸出契約

先用一段簡短結論回答人類問題，再輸出下列欄位；值不可省略：

~~~text
skill: $emmet-gate-auditor
verdict: <not-ready|unknown|exit-ready|transition-in-progress|transition-complete>
exit_criteria: <pass|fail|unknown>
governance_consistency: <consistent|transition-window|inconsistent|unknown>
active_gate_transitioned: <yes|no|unknown>
audited_gate: <gate|unknown>
observed_active_gate: <gate|unknown>
successor_gate: <gate|none|unknown>
main_sha: <40-character SHA>
audit_time: <ISO 8601 with timezone>
human_decision_required: <yes|no>
local_cache_refresh: <none|git-fetch>
mutations: none
~~~

human_decision_required 的固定映射為：exit-ready、transition-in-progress 與 unknown 是 yes；not-ready 與 transition-complete 是 no。這只描述目前是否需要人類 gate 決策或解決權威歧義，不授予執行權。

接著依序提供：

1. 治理真相表：來源、宣告 gate、freshness、證據連結或 SHA、是否一致。
2. 退出條件表：criterion_id、curriculum 原條件、pass／fail／unknown、main-bound 證據、live 證據、freshness、缺口。
3. Checkpoint 表：Dispatcher marker 與 MAIN_SHA 是否相符、是否有未完成／blocked loop 物件、是否誤派 successor 工作。
4. Transition 表：使用者核准、tracking Issue、PR、current full head、受測 base、integration candidate、merge 狀態、Meta 更新與三方同步；不存在就明寫 none。
5. blockers：所有 fail、unknown、stale 與 unbound 項目；沒有就明寫 none。
6. human_next_step：人類此刻要決定什麼，以及唯一最小且安全的下一步。不要代替人類輸出 approve／reject，也不要建議 loop 角色越過 checkpoint。

Durable GitHub 證據使用 immutable comment／commit permalink；會變動的 body、labels 與 live state 使用對應 URL 加 audit_time，不冒充永久快照。Repository 證據使用完整 SHA 與路徑。把推論標成推論，不得讓它看起來像權威事實。
