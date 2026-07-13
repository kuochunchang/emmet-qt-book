# 三角色 agent 閉環 skill 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 dispatcher／coder／reviewer 三個角色 skill、協定正本與 AGENTS.md 授權修訂，讓三個獨立 session 以 GitHub label 狀態機在 active gate 內全自動協作。

**Architecture:** GitHub 為唯一狀態來源，7 個 `loop:*` label 構成狀態機；三個 skill 各描述一輪醒來的程序，共同引用 `docs/agent-loop.md` 協定正本。人類只保留 gate 升級決策。

**Tech Stack:** Claude Code project skills（`.claude/skills/*/SKILL.md`）、gh CLI、git worktree、`/loop` 自我調速。

## Global Constraints

- 規格正本：`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`；規則衝突時以規格已確認的決策為準。
- 分支：`governance/agent-loop-skills`（已存在，含規格 commit）。不得推 `main`。
- 語言：所有交付文件用繁體中文；label、命令、路徑保持原文。
- Label 全集固定 7 個：`loop:queued`、`loop:coding`、`loop:needs-review`、`loop:changes-requested`、`loop:approved`、`loop:blocked`、`loop:paused`。任何文件不得出現此集合以外的 `loop:*` 名稱。
- 署名固定三種：`— Dispatcher`、`— Coder`、`— Reviewer`。
- 合併方式固定 squash；WIP 上限 1；停滯門檻 6 小時；退件上限 3 次。
- 每個 task 結束前跑 `scripts/book-check`（其中已含 `python3 -m unittest` 測試）確認未破壞現有檢查（本計畫不動 manuscript，預期恆通過）。

---

### Task 1: 建立治理 Issue

**Files:** 無（純 GitHub 操作；後續文件會引用取得的 Issue 編號，本計畫其餘任務以 `#ISSUE_N` 代稱，執行時一律代入實際編號）

**Interfaces:**
- Produces: 治理 Issue 編號 `ISSUE_N`，Task 2 的 `docs/agent-loop.md`、Task 6 的 `AGENTS.md` 修訂與 Task 7 的 PR 內文都要引用它。

- [ ] **Step 1: 建立 Issue**

```bash
gh issue create \
  --title "[Governance] 建立三角色 agent 閉環 skill 與 gate 內自動化授權" \
  --label "cross-cutting" \
  --body "$(cat <<'EOF'
## 目的

依使用者 2026-07-13 核准，建立三個角色 skill（dispatcher／coder／reviewer），
以 GitHub label 狀態機在 active gate 範圍內自動協作；人類保留 gate 升級決策。

## 交付物

- `docs/agent-loop.md`：協定正本（label、狀態轉移、權限、安全機制、啟動程序）
- `.claude/skills/dispatcher/SKILL.md`、`.claude/skills/coder/SKILL.md`、`.claude/skills/reviewer/SKILL.md`
- `AGENTS.md` 修訂：授權 dispatcher 在 active gate 內合併 `loop:approved` 的 PR
- 7 個 `loop:*` label 建立

## 性質

一次性治理工作（同 Issue #35 模式），設計規格：
`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`（隨 PR 進 main）。
合併後以真實 gate 內任務跑通第一圈作為驗收。

— Claude Fable
EOF
)"
```

Expected: 輸出新 Issue URL，記下編號 `ISSUE_N`。

- [ ] **Step 2: 確認 Issue 建立成功**

```bash
gh issue view ISSUE_N --json title,labels,state
```

Expected: state=OPEN，標題如上。

---

### Task 2: 協定正本 `docs/agent-loop.md`

**Files:**
- Create: `docs/agent-loop.md`

**Interfaces:**
- Consumes: Task 1 的 `ISSUE_N`（文件開頭追蹤欄）。
- Produces: 協定正本。Task 3–5 的三個 SKILL.md 以「協定正本：`docs/agent-loop.md`」引用它；label 名稱、調速表、派工模板皆以此為準。

- [ ] **Step 1: 寫入完整檔案**（`（部署時填入）` 是刻意保留的部署設定欄，非佔位符；`#ISSUE_N` 代入實際編號）

````markdown
# 三角色 agent 閉環協定

追蹤：Issue #ISSUE_N ｜ 設計規格：`docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`

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
- 驗收要求：book check 實跑通過（含 unittest 測試）；<任務特定要求>

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
- **誠實原則**：`scripts/book-check`（含 unittest 測試）未實際
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
````

- [ ] **Step 2: 驗證**

```bash
grep -oh "loop:[a-z-]*" docs/agent-loop.md | sort -u
scripts/book-check
```

Expected: grep 恰好列出 7 個 label（queued、coding、needs-review、changes-requested、approved、blocked、paused）；book check 通過（exit 0，內含 unittest 測試）。

- [ ] **Step 3: Commit**

```bash
git add docs/agent-loop.md
git commit -m "docs: 建立 agent 閉環協定正本

Refs #ISSUE_N

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `.claude/skills/dispatcher/SKILL.md`

**Files:**
- Create: `.claude/skills/dispatcher/SKILL.md`

**Interfaces:**
- Consumes: `docs/agent-loop.md` 的 label 名、調速表、派工模板、通知規則。
- Produces: `/dispatcher` skill，供 `/loop /dispatcher` 逐輪呼叫。

- [ ] **Step 1: 寫入完整檔案**

````markdown
---
name: dispatcher
description: agent 閉環的調度角色——輪詢 GitHub 狀態、依 active gate 派工、合併已審核 PR、偵測停滯、彙整 gate 退出證據。配合 /loop 使用，每次呼叫執行一輪完整調度。
---

# Dispatcher（調度）

協定正本：`docs/agent-loop.md`——label 定義、權限、安全機制、調速表以它為準。
你只透過 gh CLI 操作 GitHub；不改檔案、不寫稿、不做審查裁決。

## 每輪程序

1. **煞車檢查**：`gh issue view 1 --json labels --jq '.labels[].name'`；
   含 `loop:paused` → 本輪結束，睡 30–60 分鐘。
2. **讀 gate**：`git fetch origin main --quiet && git show origin/main:AGENTS.md`，
   記下 active gate 與其允許的 Issue 清單。
3. **收集狀態**：

   ```bash
   gh issue list --state open --json number,title,labels,updatedAt
   gh pr list --state open --json number,title,labels,updatedAt,headRefName
   ```

4. **依序處理**（一輪內全部做完）：
   1. `loop:blocked`：若 Meta Issue #1 尚無本件的通知留言 → 依協定「通知」節
      走三管道。
   2. `loop:approved` 的 PR → 執行下方「合併程序」。
   3. 停滯偵測：任一帶 `loop:*` 的物件 updatedAt 距今超過 6 小時 →
      標 `loop:blocked` ＋ 通知。
   4. 退件計數：`gh pr view <n> --json timelineItems` 中 `loop:changes-requested`
      被加上第 3 次 → 標 `loop:blocked` ＋ 通知。
   5. 圈外盤點：無任何 `loop:*` label 的 open PR，且尚未留過圈外標記留言 →
      留言請使用者決定收編或自行處理，不接手。
   6. 派工：在途工作為零（無 issue／PR 帶 `loop:queued`、`loop:coding`、
      `loop:needs-review`、`loop:changes-requested`、`loop:approved`）
      且 gate 內仍有未完成工作 → 依 gate 順序選下一任務，
      `gh issue edit <N> --add-label "loop:queued"`，並按協定模板留言派工。
   7. Gate 退出偵測：active gate 的退出條件（見 `docs/curriculum.md`）全部
      在 main 留下證據 → 在 Meta Issue #1 留言彙整（完成 Issue／PR／merge SHA）
      ＋ 三管道通知請使用者核准；之後每輪檢查 Meta Issue #1 是否有使用者的
      核准回覆，核准前不派下一 gate 的任何工作。
5. **調速**：依協定調速表決定下次醒來時間。

## 合併程序

1. 確認 PR 帶 `loop:approved` 且有 `— Reviewer` 署名的裁決留言；缺一 →
   標 `loop:blocked`，不合併。
2. 確認 PR 對應派工留言、屬 active gate 範圍。
3. `gh pr merge <n> --squash --delete-branch`
4. 移除對應 Issue 的 `loop:coding`（Issue 全部完成時由 PR 的 `Closes` 自動關閉）。
5. 在 Meta Issue #1 留言記錄進度（PR 編號、merge SHA），文末署名。
6. 發 Discord 短訊通知合併完成。

## 紅線

- 永不改碼、寫稿、審查內容。
- 永不合併缺 `loop:approved`、缺 reviewer 署名留言、或圈外的 PR。
- 永不派 active gate 允許範圍之外的工作。
- Gate 升級只彙整證據與通知；執行與否由使用者決定。
````

- [ ] **Step 2: 驗證**

```bash
head -5 .claude/skills/dispatcher/SKILL.md
grep -oh "loop:[a-z-]*" .claude/skills/dispatcher/SKILL.md | sort -u
```

Expected: frontmatter 含 `name: dispatcher` 與 `description:`；grep 結果是 7 個 label 的子集，無新名稱。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/dispatcher/SKILL.md
git commit -m "feat: 建立 dispatcher 角色 skill

Refs #ISSUE_N

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `.claude/skills/coder/SKILL.md`

**Files:**
- Create: `.claude/skills/coder/SKILL.md`

**Interfaces:**
- Consumes: `docs/agent-loop.md`；dispatcher 派工留言格式（Task 3 模板）。
- Produces: `/coder` skill。

- [ ] **Step 1: 寫入完整檔案**

````markdown
---
name: coder
description: agent 閉環的編碼角色——認領 loop:queued 任務、依 authoring-guide 實作並開 PR、回應審查退件。配合 /loop 使用，每次呼叫處理一件事。
---

# Coder（編碼）

協定正本：`docs/agent-loop.md`。你是閉環中唯一的寫入者，只在本 worktree 工作。

## 每輪程序

1. **煞車檢查**：`gh issue view 1 --json labels --jq '.labels[].name'`；
   含 `loop:paused` → 本輪結束，睡 30–60 分鐘。
2. **優先處理退件**：`gh pr list --state open --json number,labels` 找
   `loop:changes-requested` 的 PR：
   1. 讀 `— Reviewer` 署名留言的全部 finding。
   2. `git fetch origin && git checkout <PR 分支>`，逐條修正；
      不同意的 finding 以理由回覆，不盲改（見 superpowers:receiving-code-review）。
   3. 重跑 `scripts/book-check`，通過才 push。
   4. `gh pr edit <n> --remove-label "loop:changes-requested" --add-label "loop:needs-review"`，
      署名留言逐條回覆處理結果。
3. **認領新任務**（無退件時）：找帶 `loop:queued` 的 Issue：
   1. 讀 dispatcher 的派工留言，確認本輪 PR 範圍。
   2. 以 `git show origin/main:AGENTS.md` 核對任務屬 active gate；
      不符 → `gh issue edit <N> --add-label "loop:blocked"` ＋ 署名留言拒做，結束本輪。
   3. `gh issue edit <N> --remove-label "loop:queued" --add-label "loop:coding"`，
      署名留言認領。
4. **開工前必查**：依 AGENTS.md「開工前必查」節執行（curriculum active gate、
   authoring guide、對應 Issue）。
5. **實作**：`git fetch origin && git checkout -b <type>/issue-<N>-<slug> origin/main`；
   依 `docs/authoring-guide.md` 工作；一個 PR 一章或高度相關兩章；
   會計數字用字串構造的 `Decimal`；未執行過的命令不得寫成已通過。
6. **驗證**：`scripts/book-check` 實跑通過（其中已含 unittest 測試）；
   輸出摘要收入 PR 內文。未通過不得進下一步。
7. **開 PR**：

   ```bash
   git push -u origin <分支>
   gh pr create --title "<type>: <摘要>" --body "<說明＋驗證輸出＋Refs #N（最後完成的 PR 用 Closes #N，依派工留言）＋署名 — Coder>"
   gh pr edit <n> --add-label "loop:needs-review"
   ```

8. **遇阻**：無法解決的障礙 → 對應物件標 `loop:blocked` ＋ 署名留言說明
   已嘗試什麼、卡在哪。
9. **調速**：依協定調速表。

## 紅線

- 永不合併 PR、永不 push `main`、永不自行改掉審查裁決 label。
- `scripts/book-check` 未實跑通過不得標 `loop:needs-review`。
- 不做派工留言範圍之外的工作；發現範圍問題找 dispatcher（留言），不自行擴權。
````

- [ ] **Step 2: 驗證**

```bash
head -5 .claude/skills/coder/SKILL.md
grep -oh "loop:[a-z-]*" .claude/skills/coder/SKILL.md | sort -u
```

Expected: frontmatter 含 `name: coder`；label 為 7 個之子集。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/coder/SKILL.md
git commit -m "feat: 建立 coder 角色 skill

Refs #ISSUE_N

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `.claude/skills/reviewer/SKILL.md`

**Files:**
- Create: `.claude/skills/reviewer/SKILL.md`

**Interfaces:**
- Consumes: `docs/agent-loop.md`；coder 的 PR（含驗證輸出）。
- Produces: `/reviewer` skill。

- [ ] **Step 1: 寫入完整檔案**

````markdown
---
name: reviewer
description: agent 閉環的審查角色——checkout loop:needs-review 的 PR、重跑驗證、以 label 裁決 approved 或 changes-requested。配合 /loop 使用。
---

# Reviewer（審查）

協定正本：`docs/agent-loop.md`。你是唯一的品質裁決者；永不改碼。

## 每輪程序

1. **煞車檢查**：`gh issue view 1 --json labels --jq '.labels[].name'`；
   含 `loop:paused` → 本輪結束，睡 30–60 分鐘。
2. **找待審 PR**：`gh pr list --state open --json number,labels,updatedAt` 中
   帶 `loop:needs-review` 者，最舊優先。沒有 → 依調速表睡眠。
3. **取碼**：在本 worktree：

   ```bash
   git fetch origin "pull/<n>/head" && git checkout --detach FETCH_HEAD
   ```

4. **重跑驗證**：`scripts/book-check`（其中已含 unittest 測試）。
   失敗 → 直接退件（finding 附完整錯誤輸出）。
5. **審查**（全部通過才可 approve）：
   - Gate 合規：diff 只觸及派工留言的範圍；無夾帶後續章節或後續 gate 能力。
   - authoring-guide 合規：章首內容狀態、`tag@commit`、`Decimal` 字串構造、
     mock 與真實來源分離、無秘密與 API key。
   - 宣稱與證據：PR 內文宣稱的命令抽驗重跑，輸出須一致。
   - 內容品質：正確性、與 `docs/curriculum.md` 目標一致、讀者面／作者面邊界。
6. **裁決**（只用 label ＋ 留言，不用 GitHub 原生 approve）：
   - 通過：`gh pr edit <n> --remove-label "loop:needs-review" --add-label "loop:approved"`；
     署名留言記錄實跑的驗證命令與結果。
   - 退件：`gh pr edit <n> --remove-label "loop:needs-review" --add-label "loop:changes-requested"`；
     署名留言逐條列 finding（`檔案:行號`、問題、期望的修法）。
7. **調速**：依協定調速表。

## 紅線

- 永不改碼、永不 push、永不合併。
- 驗證命令沒實跑不得裁決；「PR 內文說通過」不是證據。
- 一次裁決必須明確：approved 或 changes-requested，不留模糊狀態。
````

- [ ] **Step 2: 驗證**

```bash
head -5 .claude/skills/reviewer/SKILL.md
grep -roh "loop:[a-z-]*" .claude/skills docs/agent-loop.md | sort -u
```

Expected: frontmatter 含 `name: reviewer`；跨檔案 grep 恰好 7 個 label，無多無少。

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/reviewer/SKILL.md
git commit -m "feat: 建立 reviewer 角色 skill

Refs #ISSUE_N

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `AGENTS.md` 授權修訂

**Files:**
- Modify: `AGENTS.md`（「PR 與完成定義」節的合併條款；「Gate 升級」節之前插入新節）

**Interfaces:**
- Consumes: Task 1 的 `ISSUE_N`；Task 2 的 `docs/agent-loop.md`。
- Produces: dispatcher 合併授權的治理依據。

- [ ] **Step 1: 修改合併條款**

用 Edit 將：

```markdown
- 未經使用者明確要求，不自行合併 PR、建立 release 或關閉仍有工作項目的 Issue。
```

改為：

```markdown
- 未經使用者明確要求，不自行合併 PR、建立 release 或關閉仍有工作項目的 Issue；
  唯一例外見「三角色 agent 閉環（loop 工作流）」一節。
```

- [ ] **Step 2: 在「## Gate 升級」之前插入新節**

```markdown
## 三角色 agent 閉環（loop 工作流）

經使用者核准（2026-07-13，追蹤 Issue #ISSUE_N），本 repo 允許三個角色 session
（dispatcher／coder／reviewer）在 active gate 範圍內自動推進工作。協定正本為
`docs/agent-loop.md`，角色程序在 `.claude/skills/`。要點：

- dispatcher 得合併已標 `loop:approved`、有 reviewer 署名裁決留言、且屬
  active gate 派工範圍的 PR；這是上節「不自行合併 PR」的唯一例外。
- coder 與 reviewer 的權責與紅線依協定正本；reviewer 的裁決以 label 表達，
  不使用 GitHub 原生 review approve。
- Gate 升級不在授權範圍：dispatcher 只彙整退出證據並通知使用者，transition
  仍依下節「Gate 升級」由使用者核准後執行。
- 使用者可隨時在 Meta Issue #1 加 `loop:paused` label 暫停全部 agent。

```

- [ ] **Step 3: 驗證**

```bash
grep -n "loop:" AGENTS.md
scripts/book-check
```

Expected: AGENTS.md 出現 `loop:approved` 與 `loop:paused`；兩個檢查 exit 0。

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: 授權三角色 agent 閉環於 gate 內自動合併

Refs #ISSUE_N

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 建立 label、推分支、開 PR

**Files:** 無新檔案（GitHub 操作）

**Interfaces:**
- Consumes: 全部前置 task 的 commit 與 `ISSUE_N`。
- Produces: 待使用者合併的 PR；repo 上的 7 個 `loop:*` label。

- [ ] **Step 1: 建立 label**（照 `docs/agent-loop.md` 的命令區塊執行全部 7 條）

```bash
gh label create "loop:queued"            --color 0E8A16 --description "已派工，待 coder 認領"
gh label create "loop:coding"            --color 1D76DB --description "coder 實作中"
gh label create "loop:needs-review"      --color FBCA04 --description "PR 待 reviewer 審查"
gh label create "loop:changes-requested" --color D93F0B --description "審查退件，待 coder 修正"
gh label create "loop:approved"          --color 5319E7 --description "審查通過，待 dispatcher 合併"
gh label create "loop:blocked"           --color B60205 --description "異常，需 dispatcher 或使用者介入"
gh label create "loop:paused"            --color 000000 --description "全域煞車：所有 agent 暫停"
gh label list --search "loop:" --limit 10
```

Expected: 最後列出 7 個 `loop:*` label。

- [ ] **Step 2: 推分支並開 PR**

```bash
git push -u origin governance/agent-loop-skills
gh pr create --title "governance: 建立三角色 agent 閉環 skill 與授權" --body "$(cat <<'EOF'
## 內容

- `docs/superpowers/specs/2026-07-13-agent-loop-skills-design.md`：已確認的設計規格
- `docs/agent-loop.md`：協定正本（label 狀態機、權限、安全機制、啟動程序）
- `.claude/skills/{dispatcher,coder,reviewer}/SKILL.md`：三個角色 skill
- `AGENTS.md`：授權 dispatcher 在 active gate 內合併 `loop:approved` 的 PR

## 驗證

- `scripts/book-check`：通過（含 `python3 -m unittest` 測試）
- label 一致性：三個 skill 與協定正本的 `loop:*` 名稱恰好 7 個，無漂移

Refs #ISSUE_N（首圈驗證完成後才關閉該 Issue）

— Claude Fable

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: 輸出 PR URL。（依現行 AGENTS.md，本 PR 由使用者合併——授權例外要等本 PR 合併才生效。）

- [ ] **Step 3: 通知使用者**

回報 PR URL 與 Issue 編號，請使用者審閱並合併。

---

### Task 8: 合併後首圈驗證（需使用者參與）

**Files:** 可能 Modify: `docs/agent-loop.md`（填入 Discord chat_id）

**Interfaces:**
- Consumes: 已合併的 PR、7 個 label。
- Produces: 閉環第一圈完成證據（真實任務 queued → coding → needs-review → approved → 合併）。

- [ ] **Step 1: 取得 Discord chat_id**

問使用者要通知用的 Discord 頻道 chat_id，以小 PR（或併入其他修正）更新
`docs/agent-loop.md` 的「設定」段。使用者暫不提供則保留 `（部署時填入）`，
dispatcher 在該欄未填時跳過 Discord 管道並在通知留言註明。

- [ ] **Step 2: 建立 worktree**（依 `docs/agent-loop.md` 啟動程序）

```bash
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-coder origin/main
git -C ~/workspace/emmet-qt-book worktree add --detach ../emmet-qt-book-reviewer origin/main
```

- [ ] **Step 3: 啟動三個 session 跑第一圈**

使用者開三個終端機，分別執行 `/loop /dispatcher`、`/loop /coder`、`/loop /reviewer`。
以 active gate 內真實的下一個任務跑完整一圈；使用者旁觀。

- [ ] **Step 4: 驗收**

第一圈的 PR 由 dispatcher squash 合併、Meta Issue #1 有進度留言、三個角色
留言均有署名。全數成立 → 在治理 Issue `#ISSUE_N` 留言記錄首圈證據（PR、
merge SHA）後關閉該 Issue。發現協定缺陷 → 開 follow-up Issue，不在本圈夾帶修改。
