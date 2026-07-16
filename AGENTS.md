# Repository Agent Instructions

本文件適用於整個 `emmet-qt-book` repository。它的目的不是規定書中知識內容，
而是防止作者或 AI agent 跳過寫作 gate、引用未發布能力，或把未驗證結果寫成
可操作教材。

## 文件權責與衝突處理

- `AGENTS.md`：目前允許啟動的工作、禁止範圍與 agent 工作流程。
- `CLAUDE.md`：Claude Code 相容入口；只匯入 `AGENTS.md`，不得另立或放寬
  repository 規則。
- `README.md`：repository 入口與基線摘要；屬衍生導覽，不是 gate 或系統能力的
  權威來源。
- `docs/curriculum.md`：全書教育目標、章序、寫作批次、gate 與系統 Phase 對應。
- `docs/authoring-guide.md`：寫作、版本、驗證與章節模板規範。
- `manuscript/preface.md`：讀者面的七步證據閉環與內容狀態正本。
- `manuscript/SUMMARY.md`：出版與閱讀導航，不是寫作進度來源。
- GitHub Milestones／Issues：即時工作狀態；不能放寬 curriculum 或本文件的 gate。
- `../emmet-qt-bt1` 已發布 tag 與其權威設計文件：系統行為的事實來源。

發生衝突時採較嚴格限制。若 AGENTS、curriculum 與 GitHub live state 不一致，先
停止新章擴寫並修正治理資料；不得自行挑選最方便的一份繼續。
README 若與對應權威來源不一致，以權威來源為準並修正 README；README 本身不能
改變 gate 或系統能力判定。

## 目前 active gate

> 寫作批次：W1
>
> Active gate：`W1-G3`
>
> 最後核准日期：2026-07-16
>
> 核准來源：使用者明確指示；追蹤 Issue #67
>
> 前一 gate 完成證據：`W1-G2` — Issue #3；第 5 章 PR #54 @ `3834e7b`、
> 第 6 章 PR #57 @ `381aec8`、第 7 章 PR #59 @ `312cc35`、第 8 章 PR #62 @
> `6f8aeaa`、第 9 章 PR #64 @ `88f0075`；詳細驗證：Meta Issue #1 的 W1-G2
> Dispatcher 退出證據留言 #4987761815
>
> 詳細進入／退出條件：`docs/curriculum.md`「實施順序與啟動門檻」

只有 default branch `main` 已合併的宣告能啟用 gate；feature branch、舊分支或
未合併 PR 中的 gate 變更一律視為提案。

`W1-G3` 目前只允許：

- Issue #4：撰寫第 10–15 章，完成固定版本資料操作、dataset manifest、通過與
  fail-closed 案例及 data readiness 報告。
- Issue #6：只補寫第 10–15 章直接需要的相關附錄小節，不得一次預寫全部附錄。
- Issue #7：隨第 10–15 章更新資料版本、checksum、命令與 fail-closed 案例的對應
  驗證台帳，並保持開啟到 `W1-final`。
- 經使用者明確核准、已有 GitHub Issue 追蹤且限於最小範圍的兩類修正：
  - 直接阻擋 Issue #4、Issue #6 相關附錄或 Issue #7 對應章節台帳達成
    `W1-G3` 退出條件的缺陷。
  - 修復 agent guard 未被載入，或 `AGENTS.md`、`docs/curriculum.md` 與 Meta
    Issue #1 不一致的治理缺陷。
- 使用者核准且由 Meta Issue #1 追蹤的 gate-transition 工作。
- 使用者於 2026-07-13 核准、並於 2026-07-15 明確核准調整、由 Issue #40 追蹤的
  loop 跨 client 治理工作：同步 `.claude/skills/` 與 `.agents/skills/` 角色程序、
  協定文件，以及 repo-local event manager、常駐 agent component、單輪 Codex
  iteration adapter 與其測試。此例外不授權正文／附錄擴寫、安裝或啟用主機排程、
  在本治理變更的驗證中實際自動合併、關閉工作 Issue，或 gate transition。

建立或標記 Issue 本身不構成上述修正的授權；例外工作不得新增或實質擴寫正文／
附錄，也不得取用後續 gate 的能力、範例、輸出或完成證據。

`W1-G3` 完成前禁止：

- 啟動 Issue #5 的新章撰寫或實質擴寫。
- 脫離 active 正文一次預寫 Issue #6 的全部附錄。
- 撰寫第 4、21–50 章的讀者操作內容。
- 從配套系統開發分支取得範例、輸出或完成宣稱。

Issue #35 是建立本 guard 的一次性治理工作；該次治理工作已完成，不構成後續
gate 的工作授權。

## 後續 gate 概要

W1 固定順序為：

```text
W1-G0  #8 ＋ #7 台帳骨架
  → W1-G1  #2（第 1–3 章）
  → W1-G2  #3（第 5–9 章）
  → W1-G3  #4（第 10–15 章）
  → W1-G4  #5（第 16–20 章）
  → W1-final  #6 ＋ #7 最終驗收
```

Issue #6 只能隨目前 active gate 的正文補寫相關附錄。Issue #7 在 G0 建立骨架、
G1–G4 隨章更新，並保持開啟到 `W1-final`。完整退出條件以 curriculum 為準，
不能只看到前一 Issue 有活動就假設下一 gate 已啟動。

配套系統依賴：

- W2／第 21–25 章：等待 `emmet-qt-bt1` Phase 4 正式 release 且 gate 通過。
- W3／第 4、26 章：等待 Phase 4.5 正式 Foundation 入口 release 且 gate 通過。
- W4／第 27–36 章：等待 Phase 5 Backtest／MCP 正式 release 且 gate 通過。
- W5／第 37–38 章：等待 Phase 6 replay／fault 正式 release 且 gate 通過。
- W6／第 39–42 章：等待 Phase 7 Paper／Testnet 正式 release 且 gate 通過。
- W7／第 43–44 章：等待 Phase 8 正式 release 且 gate 通過，以及 Phase 9 驗收。
- W8／第 45–50 章：等待前序案例與驗證證據形成，再依核准 gate 啟動。

等待中的內容只能累積作者用大綱、問題與證據筆記，不得建立讀者章稿或假想
操作路徑。

## 開工前必查

每次開始工作都必須：

1. 讀取本文件、curriculum 的 active gate、authoring guide 與對應 GitHub Issue。
2. 確認 Issue 屬於目前 gate；若不屬於，停止並說明阻擋。
3. 檢查工作樹並保留使用者既有變更。新的寫入任務若尚未位於對應分支，才從
   最新 `main` 建立聚焦分支；審查或續作既有任務留在其目標分支。
4. 若內容涉及配套系統，使用 `manuscript/front-matter/setup.md` 的隔離 worktree，
   核對 `tag@commit`、乾淨狀態與 lockfile；不得切換現行開發工作樹。
5. 讀取配套系統相符的權威設計、發布證據與既有測試；設計或開發分支不能支持
   「可操作」宣稱。

## 寫作與驗證流程

- 一個 PR 原則上處理一章或高度相關的兩章；GitHub bundle Issue 可由多個 PR 完成。
- 章稿遵循序章的七步證據閉環，留下明確的讀者專業成果。
- 章首使用權威內容狀態；操作章記錄精確 `tag@commit` 與最後驗證日期。
- 所有命令、數值與輸出必須實際重現；未執行不得宣稱通過。
- 會計數字使用字串構造的 `Decimal`；時間與因果語義對照配套系統設計。
- 外部網路案例與固定離線樣本分開；mock 不得冒充真實下載或外部 smoke。
- 不存放 API key、帳戶資料、private 輸出或其他秘密；練習預設不使用真實資金。
- 交易所、API、費率、法律、稅務與安全資訊使用第一手來源，發布前重新查證。
- 發現配套系統缺陷或語義缺口時，保存最小重現與 oracle，轉到
  `emmet-qt-bt1` 追蹤；不得在書稿複製第二套邏輯掩蓋問題。

## PR 與完成定義

- 不直接推送 `main`；使用聚焦分支與 PR。
- PR 以 `Refs #N` 關聯 bundle／meta Issues；最後完成該 Issue 的 PR 才用
  `Closes #N`。
- 未經使用者明確要求，不自行合併 PR、建立 release 或關閉仍有工作項目的 Issue；
  唯一例外見「三角色 agent 閉環（loop 工作流）」一節。
- GitHub comment 於文末註記撰寫 agent，例如 `— Codex`。

章節或基礎設施工作完成前，至少確認：

- 符合目前 active gate，沒有夾帶後續章節。
- 讀者面與作者面文件邊界正確，`SUMMARY.md` 導航同步。
- 狀態、版本、命令、輸出、作者驗證紀錄與專業成果完整。
- book check、連結、格式及與任務相關的驗證通過；在 Issue #8 建立 check 以前，
  #8 本身以它新增的 bootstrap／self-check 證據取代此要求。
- 審查 finding 已處理或以明確 Issue 追蹤，不以「之後再修」冒充完成。

## 三角色 agent 閉環（loop 工作流）

經使用者核准（2026-07-13，追蹤 Issue #40），本 repo 允許三個角色 session
（dispatcher／coder／reviewer）在 active gate 範圍內自動推進工作。協定正本為
`docs/agent-loop.md`；Claude Code 角色程序在 `.claude/skills/`，Codex 角色程序在
`.agents/skills/emmet-loop-{dispatcher,coder,reviewer}/`。兩套入口共用同一組 GitHub
durable state，不得各自建立狀態機。要點：

- dispatcher 得合併已標 `loop:approved`、沒有 `loop:blocked`、有 reviewer 署名且
  綁定目前 PR head 與受審 `main` SHA 的裁決留言、並屬 active gate 派工範圍的 PR；
  這是上節「不自行合併 PR」的唯一例外。
- coder 與 reviewer 的權責與紅線依協定正本；reviewer 的裁決以 label 表達，
  不使用 GitHub 原生 review approve。
- 每次 Codex role iteration 只執行一輪後結束；`scripts/codex-loop agent <role>`
  是等待事件的常駐 component，收到屬於該角色的事件才啟動一次
  `codex exec --json`。只有獨立的 `scripts/codex-loop events` component 得定期
  polling GitHub 並依協定通知角色；role skill 與 Codex child 不 sleep、不 polling、
  不遞迴啟動。Repo 不安裝或啟用主機 scheduler。
- Role skill 與治理指令只能從 control inputs 與最新 `origin/main` 完全一致的
  trusted runner 載入；只有非 control paths 前進時，長駐 runner 的 detached HEAD
  可暫時落後，下一個 role iteration 仍須 fetch 並以最新 main 建立 task／candidate。
  候選 branch／PR 必須在另一 task／candidate worktree 處理，不得成為下一輪控制來源。
- `tmux start/restart` 從一般 checkout 呼叫時只得作為 bootstrap；真正的 lifecycle
  launcher 必須重新載入同 repository、乾淨、detached 且對齊最新 `origin/main` 的
  dedicated `*-loop-control` worktree。主要 checkout、task／candidate worktree 與三個
  role runner 都不得取代這個 launcher control source。
- Trusted runner preflight 已驗證、並由 client 注入的 `AGENTS.md` 算本輪「開工前必查」
  的讀取；role 不得再用工具輸出整份文件。Curriculum 只讀 active-gate 節，authoring
  guide 與對應 Issue／PR 各讀一次；只有協定歧義才開啟 `docs/agent-loop.md` 對應段落。
- Event manager 只把 allowlist 後的 bounded preflight 當候選縮小提示傳給 role；它不是
  授權或 durable state。Role 預設不得列舉全部 Issues／PR 或完整留言歷史，任何 GitHub
  mutation 前仍須窄重驗 pause、main SHA、target labels，PR 再重驗 head／base／draft。
  `snapshot_incomplete` 本身阻斷 mutation；其他 truncation 或證據歧義只在缺失證據會
  影響本輪決策時對缺口分頁。
- 成功驗證只輸出 compact summary；失敗才輸出 bounded diagnostics。這只限制送入模型的
  command output，不影響操作者看到完整 Codex JSONL event stream。
- Event manager 發現 `origin/main` 的 control inputs 改變時，必須停止派送新事件；
  有 child 時先 drain，idle 後只由 launcher-owned detached rotator 驗證 events
  PID／lock、session ownership、乾淨 runners 與 same-repo，再同步 dedicated control
  worktree 與三個 runners、執行四項 preflight 並重建 session。換代失敗時 fail
  closed；不得由 role、候選 branch 或一般 no-progress alert 觸發 restart。
- Gate 升級不在授權範圍：dispatcher 只彙整退出證據並通知使用者，transition
  仍依下節「Gate 升級」由使用者核准後執行。
- 使用者可隨時在 Meta Issue #1 加 `loop:paused` label 暫停全部 agent。

## Gate 升級

只有目前 gate 的退出條件已在 `main` 留下證據，且使用者核准前進時，才能升級。
Gate transition 固定依序執行：

1. 使用者明確核准前進，並由 GitHub Issue 追蹤 transition。
2. 以同一個 PR 更新 `docs/curriculum.md` 的 active gate 與「前一 gate 完成證據」
   欄位（至少記錄完成 Issue／PR 與 merge SHA），以及本文件的 active gate、證據
   摘要與允許／禁止範圍。
3. PR 合併至 `main` 後，立即更新 GitHub Meta Issue #1 的目前 gate、前一 gate
   完成證據、下一步與本次 transition PR 的 merge SHA。
4. 核對 `main` 兩份文件與 Meta Issue 三者一致；此時新 gate 才正式生效。

只更新 feature branch、GitHub label、Milestone 或對話說明，不構成 gate 升級。
