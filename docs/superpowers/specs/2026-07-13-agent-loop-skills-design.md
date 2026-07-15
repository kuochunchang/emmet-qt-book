# 三角色 agent 閉環 skill 設計

日期：2026-07-13
狀態：已與使用者逐段確認；2026-07-15 核准改為 event-driven CLI
追蹤：Issue #40

> 本文件保存目的與設計決策；執行語義的正本是 `docs/agent-loop.md`。2026-07-15
> 使用者以 Issue #40 核准取代原外部 scheduler 模型：repo-local event manager polling
> GitHub，常駐 agent component 等待本角色事件，再啟動一次一輪即退的 Codex role。
> Role skill 與 Codex child 本身仍不得 sleep 或輪詢。

## 目標

在本 repo 建立三個角色 skill（`dispatcher`、`coder`、`reviewer`），讓各自獨立的
Claude Code 或 Codex worker 透過 GitHub issue／PR／milestone 協調書籍寫作工作，
形成 gate 內自動閉環。人類保留 **gate 升級核准**，並可用全域煞車暫停。

## 已確認的關鍵決策

| 決策 | 結論 |
| --- | --- |
| 自動化邊界 | gate 內全自動（含合併 PR、關 issue、派工）；gate 升級停下等使用者核准 |
| skill 存放 | Claude：`.claude/skills/`；Codex：`.agents/skills/emmet-loop-*/`；皆進版控 |
| 協調架構 | GitHub 單一真相 ＋ `loop:*` label 狀態機；session 完全無狀態 |
| loop 節奏 | event manager 定期 poll；agent 等事件；每個事件的 Codex role 只跑一輪 |
| 通知管道 | GitHub 留言 ＋ 桌面推播（PushNotification）＋ Discord 三管道 |
| 合併方式 | squash merge |
| WIP 上限 | 同時最多一個編碼任務 |

## 機械性前提

1. 三個 session 共用同一個 GitHub 帳號（同一份 `gh` 認證），因此：
   - 不能用 assignee 區分角色。
   - 不能用 GitHub 原生 review approve（不能 approve 自己的 PR）。
   - 角色訊號一律靠 label ＋ 署名留言（`— Dispatcher`、`— Coder`、`— Reviewer`）。
2. 三個 session 不得共用工作樹；role 從 trusted runner 載入控制指令，coder／reviewer
   在另一 task／candidate worktree 處理候選內容。

## 角色職責

### Dispatcher（調度）

狀態機的唯一推進者，唯一有合併權的角色。每次醒來：

1. 讀 `main` 上的 `AGENTS.md` active gate、Meta Issue #1、全部 `loop:*` 標記。
2. 佇列空且 gate 內還有工作 → 依 gate 順序選下一個任務，在 issue 標
   `loop:queued` ＋ 署名留言說明任務範圍。
3. 有 SHA-bound、unblocked `loop:approved` PR → squash 合併 → 更新 Meta Issue #1
   進度；本輪退出，下一輪才考慮派工。
4. 偵測停滯與異常；偵測 gate 退出條件達成 → 彙整證據、三管道通知、停下等核准。
5. 執行 WIP 上限＝1。
6. 發現沒有 loop label 的既有 PR／分支（圈外工作）→ 留言標記請使用者決定，
   不擅自接手。

### Coder（編碼）

唯一寫程式／寫稿的角色。每次醒來找 `loop:queued` 的 issue 或
`loop:changes-requested` 的 PR：

- 新任務：執行 AGENTS.md「開工前必查」→ 從最新 `main` 開聚焦分支 →
  依 authoring-guide 工作（book check 必須實際跑過）→ 開 PR（`Refs #N`）→
  標 `loop:needs-review`。
- 被退件：讀 reviewer 署名留言 → 修正 → push → 標回 `loop:needs-review`。

### Reviewer（審查）

唯一的品質裁決者，永不改碼。每次醒來找 `loop:needs-review` 的 PR：

- 在自己的 worktree checkout PR head，實際重跑 book check 與稿中宣稱的命令。
- 審查重點：gate 合規（無夾帶後續章節）、authoring-guide 合規、宣稱與證據一致。
- 裁決以 label 表達：`loop:approved` 或 `loop:changes-requested` ＋ 署名 review 留言。

## Label 狀態機

七個 label；狀態先掛 issue，PR 出現後轉到 PR 上：

```text
Issue:  loop:queued ──(coder 認領)──► loop:coding
PR:     loop:needs-review ──(reviewer 裁決)──► loop:approved ──(dispatcher 合併)──► 完成
                   ▲                    │
                   └──(coder 修正)──── loop:changes-requested
任何物件: loop:blocked   —— 異常，需 dispatcher 或人類介入
Meta #1:  loop:paused    —— 全域煞車，所有 agent 醒來先查，存在就只睡覺
```

`loop:blocked` 是保留 primary progress label 的 suspension overlay；不是另一個 primary
state。關鍵性質：session 本身不保存 durable state。任何 session 被殺掉、重開機、
換機器後，下一輪先依 GitHub marker、labels 與 SHA reconciliation 後即可恢復。

## 工作區隔離與啟動

```text
~/workspace/emmet-qt-book            ← dispatcher（停在 main，只透過 gh 操作）
~/workspace/emmet-qt-book-coder      ← coder trusted runner（不 checkout task branch）
~/workspace/emmet-qt-book-reviewer   ← reviewer trusted runner（不 checkout PR）
另建 task／candidate worktree       ← 實作或 integration candidate
```

Claude 可在三個 trusted runner 明確呼叫單輪 role。Codex 的連續 CLI 模式使用三個
`scripts/codex-loop agent <role>` listener 與一個 `scripts/codex-loop events`
manager；manager 定期 poll GitHub 並以 Unix socket 通知目前負責角色。每個 wake
無論有活、空佇列或 paused 都只啟動至多一個 Codex iteration；Repo 不安裝或啟用
主機 scheduler。

## 安全機制與錯誤處理

- **全域煞車**：使用者可隨時在 Meta Issue #1 加 `loop:paused`；所有 agent
  醒來第一件事就是查它，存在就無副作用退出。
- **Gate 雙重防線**：dispatcher 只派 gate 內任務；coder 與 reviewer 各自再驗
  「此 issue 屬於 active gate 嗎」，不符即標 `loop:blocked` 拒做。
- **停滯偵測**：任何 `loop:*` 狀態超過 6 小時未動 → dispatcher 標
  `loop:blocked` ＋ 三管道通知。
- **退件循環上限**：同一 PR 被退回 3 次 → dispatcher 標 `loop:blocked`
  請使用者裁決。
- **通知**：`loop:blocked` 與 gate 退出待核准走三管道；PR 合併完成只發
  Discord 簡訊。
- **誠實原則**：book check 沒實際跑過不得標 `loop:needs-review`；reviewer
  重跑失敗即退件。

## 治理配套（交付物）

一個 PR，開新的治理 Issue 追蹤：

1. `.claude/skills/dispatcher/SKILL.md`、`.claude/skills/coder/SKILL.md`、
   `.claude/skills/reviewer/SKILL.md`。
2. `.agents/skills/emmet-loop-{dispatcher,coder,reviewer}/` 與明確呼叫 metadata。
3. `scripts/codex-loop` event manager、常駐 agent component、單輪相容 adapter、
   非重疊鎖及隔離測試。
4. `docs/agent-loop.md`：協定正本（label 定義、狀態轉移、署名規則、安全機制、
   label 建立命令）。三個 skill 引用它，避免規則漂移。
5. `AGENTS.md` 修訂：新增授權一節——dispatcher 得在 active gate 範圍內合併
   已標 `loop:approved` 的 PR；gate 升級仍需使用者核准。
6. 合併後核對七個 label。

## 驗證方式

PR 合併、label 建好後，實際啟動三個 session，用 gate 內真實的下一個任務跑完整
一圈 queued → coding → needs-review → approved → 合併，使用者在旁觀察。
第一圈通過才算完成。

## 不做的事

- 不建立本機共享狀態檔；狀態只存在 GitHub。
- 不使用 GitHub 原生 review approve。
- dispatcher 不寫稿、不改碼；reviewer 不改碼。
- 不自動執行 gate 升級；gate transition 仍依 AGENTS.md 四步驟由使用者核准啟動。
- Role skill 與 Codex child 不 sleep、輪詢或自我喚醒；只有 event manager 輪詢，
  本機 socket／retry 記憶體不成為第二份 durable state。
