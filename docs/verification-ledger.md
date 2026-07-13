# 作者驗證台帳

> 台帳 schema：2
> 適用範圍：W1 起的章稿宣稱與重驗追蹤

## 權威位置與維護方式

[結構化台帳](../verification/ledger.toml)是作者驗證 claim 的唯一中央索引，位置固定，
不開放設定。不得再維護另一份試算表、Markdown 台帳或只存在 GitHub comment 的
紀錄；Issue 與 PR 負責工作狀態，不能取代已提交的證據索引。

一筆 `[[records]]` 對應一項可以獨立重驗的 claim，同一章可以有多筆。所有
`metadata.required` 範圍內的書稿都必須至少有一筆紀錄；因此新增正文或操作型
front matter 時，不需要再維護第二份 coverage 清單，但必須在同一個 PR 補台帳。
這只是 document-level 結構門檻，不能證明正文的每項 claim 都已完成 gate 所需
審核。台帳不加入 `manuscript/SUMMARY.md`，也不出現在讀者版 HTML。

## 前置條件：必須有配套 repo

book check **必須**能存取配套 repository，否則以 exit 2 中止。依序解析
`$EMMET_QT_BT1_DIR`（見[實作準備](../manuscript/front-matter/setup.md)）與
`../emmet-qt-bt1`。

這是刻意的硬失敗。台帳的身分驗證無法離線進行，若允許「找不到就跳過」，
book check 的綠色就會有兩種含義——「驗過了」與「沒驗」——而後者會迅速變成
常態。`AGENTS.md` 的開工前必查本來就要求作者持有固定版本的隔離 worktree，
所以「沒有配套 repo 卻在跑 book check」本身就是違規狀態。

checker 對配套 repo **只讀**，且只用三個 git plumbing 命令：確認是 git
repository、把 tag 解析成 commit、確認某個路徑存在於某個 commit。它永遠不會
`checkout`、`fetch` 或 `worktree add`，不會切換你正在開發的工作樹。

## 三層檢查

| 層 | 內容 | 何時執行 |
|---|---|---|
| tier-1 | schema、coverage、章首／章末／台帳的交叉一致性 | 一律 |
| tier-2 | `[baselines]` 的 tag 真的解析到宣告的 commit；每筆 `repo:` evidence 真的存在於該 commit | 一律（預設） |
| tier-3 | 實際執行 `verification_commands` 並比對結構化 oracle | `--execute`，**尚未實作** |

tier-2 只花毫秒，因為它是純 git plumbing。**它對 commit 驗證，不是對工作樹**：
配套 repo 現在的工作樹上有某個檔案，不代表它在台帳宣告的那個 commit 存在過。

`url:` evidence 仍然只驗格式，checker 不連外。

### 誠實邊界

schema 2 **沒有**讓「假裝重驗」變成不可能。台帳裡的 SHA 從 24 個收斂成 1 個，
但章首的 `配套基線` 仍然必須帶 SHA（那是給讀者看的資訊，拿不掉），所以一次
`sed` 掃過 `manuscript/` 依然能偽造一場沒發生過的重驗。

真正讓 `result = "pass"` 由機器判定的是 tier-3，不是 schema 2。schema 2 買到的
是：遷移變成改一行、而且會大聲爆掉；冗餘抄本消失；tag 被移動會被抓到。

`executable = false` 的紀錄（例如 setup 的 worktree 建立）連 tier-3 都不會執行，
它們的 `pass` 永遠只是作者宣稱。這寫在各自的 `executable_note` 裡。

## 三種紀錄的邊界

| 紀錄 | 擁有者與用途 | 保存位置 |
|---|---|---|
| 讀者專業紀錄 | 讀者保存自己的環境、預測、結果、決定與回饋，可能包含本機路徑或個人判斷 | 讀者自己的工作區，不提交到本 repository |
| 章末作者驗證紀錄 | 給讀者看的單章摘要，支持章首狀態與基線 | 書稿的「作者驗證紀錄」章末小節 |
| 中央作者台帳 | 給作者與 checker 使用的 claim 級索引，保存基線、命令、oracle、差異與重驗條件 | `verification/ledger.toml` |

中央台帳不保存讀者答案、API key、帳戶資料、私人輸出或機器絕對路徑。它只索引
證據，不取代配套原始碼、測試、資料檔、golden 或第一手外部來源。章首 metadata、
章末摘要與中央台帳必須一致，不能各自形成不同的完成宣稱。

## Schema

根節點 `schema_version` 固定為 `2`。

### `[baselines]`

每個寫作批次宣告一次基線，值為 `tag@40 字元 SHA`：

```toml
[baselines]
W1 = "v0.3.0@c999965e5cc923281541409cda9502beb93b8a60"
```

批次與已發布版本的對應由[課程大綱](curriculum.md)決定（W1 → `v0.3.0`）。
每個宣告都會對配套 repo 核對：tag 必須存在，且必須解析到宣告的那個 commit。
[實作準備](../manuscript/front-matter/setup.md)早就要求讀者手動做這個核對，
因為 **tag 可能被移動**；工具現在自己也做。

基線壞掉時，依賴它的每一筆 record 都會連帶失敗。根因報一次還不夠——不能讓
其餘紀錄在一個已知壞掉的基線上靜默通過。

### `[[records]]`

每筆 record 必須包含下列欄位；未知或拼錯的欄位會使 check 失敗。

| 欄位 | 規則 |
|---|---|
| `id` | 全域唯一、穩定的小寫 kebab-case claim ID |
| `batch` | 寫作批次，例如 `W1`；基線由 `[baselines]` 的同名鍵解析 |
| `document`、`chapter`、`claim` | repository 相對章稿路徑、與 H1 相同的章名、可獨立重驗的宣稱 |
| `content_state` | 必須來自序章，且與章首一致 |
| `data_checksums` | 有資料時使用 `<logical-id>=sha256:<64 hex>`；沒有資料時用空陣列 |
| `data_checksum_note` | 說明 checksum 範圍；空陣列時必須以「不適用：」說明具體原因，有 checksum 時不得標不適用 |
| `formal_entrypoints`、`schemas`、`interface_note` | 支持 claim 的核准讀者／產品 CLI、API 與 DTO／資料／報告契約；Git、uv、pytest 與 package 路徑是驗證工具或 evidence，不得冒充正式入口／schema；兩個陣列皆空時，note 必須明示不適用原因 |
| `evidence_refs` | 至少一項；使用 `repo:emmet-qt-bt1:<path>`（**不帶 SHA**，commit 由有效基線解析）、可版本化且未被忽略的 `book:<path>#<fragment>`，或不含認證資訊的 `url:https://...` |
| `verification_commands` | 實際重現命令 |
| `executable` | tier-3 是否可自動重跑該命令組 |
| `oracle_exit_code`、`oracle_stdout_contains` | 執行前即能判斷成敗的結構化標準；`oracle_stdout_contains` 可為空陣列（判準純粹是 exit code 時） |
| `result`、`observed` | `pass`／`needs-revalidation` 與精簡實際結果；不保存整段 stdout |
| `known_differences` | 已知限制；確認沒有差異時使用空陣列，不寫模糊 placeholder |
| `verified_on` | `YYYY-MM-DD`，必須與章首最後驗證日期一致；checker 不用牆鐘推定作者是否真的重跑 |
| `revalidation_triggers` | 會使本 claim 失效的版本、資料、入口、schema、命令或預期結果變更 |

### 條件欄位

這兩個欄位只允許在特定情境出現。不該出現卻出現、該出現卻缺席，**兩個方向都
失敗**。

| 欄位 | 只允許出現於 | 出現時 |
|---|---|---|
| `verified_against` | `result = "needs-revalidation"` | 必填；記錄最後一次通過的（現已過期的）基線 |
| `executable_note` | `executable = false` | 必填；必須以「不適用：」＋具體原因開頭 |

`needs-revalidation` 只能用於內容狀態已改為「需重驗」的文件；相反地，「需重驗」
文件也必須至少有一筆相同結果的紀錄。沒有資料、正式入口或 schema 是合法狀態，
但必須明示不適用，不能虛構 checksum 或未發布能力來填欄位。

### 有效基線

```text
有效基線 = verified_against          （result 為 needs-revalidation 時）
         = baselines[batch]          （其餘情況）
```

有效基線決定三件事：evidence 對哪個 commit 驗證、章首 `配套基線` 要跟什麼比對、
章末「對照 tag／commit」要跟什麼比對。

遷移期間那個「舊基線」**不是免驗區**：`verified_against` 與 `[baselines]` 走
完全相同的驗證。

## 新增與重驗流程

1. 先依目前 active gate 判斷該章是否允許工作。
2. 在隔離 worktree 固定配套 `tag@commit`，先寫 oracle，再執行命令。
3. 更新章稿的 metadata 與章末作者驗證摘要。
4. 在台帳新增或更新 claim；同一 claim 沿用原 `id`，不要用新 ID 隱藏舊差異。
5. 從 repository 根目錄執行 `./scripts/book-check`，把通過證據連回對應 Issue／PR。

## 遷移儀式：換基線

當一個批次要改用新的配套版本（例如 P1 的[全書版本重驗](curriculum.md)）：

1. **改一行**：`[baselines]` 的 `W1 = "v0.4.0@<new-sha>"`。
2. book-check 立刻對**每一筆** `pass` 紀錄失敗——章首仍是舊 SHA，與新基線不符。
3. 每一筆只能二選一：
   - **真的重跑**：更新章首 SHA、`observed`、`verified_on`，維持 `pass`。
   - **誠實標記**：`result = "needs-revalidation"`、`content_state = "需重驗"`、
     `verified_against = "v0.3.0@<old-sha>"`，章首改「需重驗」。

沒有中間地帶，也沒有一次改完就全過的捷徑。若任一 `revalidation_triggers` 發生
（不只是換基線），也走同一條路：先標成需重驗，重跑之後才能恢復 `pass`。

## W1-G0 首批紀錄

目前台帳只建立骨架所需的四項 claim：setup 的 tag／隔離 worktree、locked
environment smoke，以及第 1 章的系統邊界與訂單模型 smoke。這不代表第 1 章已
完成 W1-G1 的全面內容驗收；後續章節 PR 必須依 active gate 隨章擴充。
