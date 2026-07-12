# 作者驗證台帳

> 台帳 schema：1
> 適用範圍：W1 起的章稿宣稱與重驗追蹤

## 權威位置與維護方式

[結構化台帳](../verification/ledger.toml)是作者驗證 claim 的唯一中央索引，位置由
[book-check 設定](../book-check.toml)固定。不得再維護另一份試算表、Markdown
台帳或只存在 GitHub comment 的紀錄；Issue 與 PR 負責工作狀態，不能取代已提交
的證據索引。

一筆 `[[records]]` 對應一項可以獨立重驗的 claim，同一章可以有多筆。所有
`metadata.required` 範圍內的書稿都必須至少有一筆紀錄；因此新增正文或操作型
front matter 時，不需要再維護第二份 coverage 清單，但必須在同一個 PR 補台帳。
這只是 document-level 結構門檻，不能證明正文的每項 claim 都已完成 gate 所需
審核。台帳不加入 `manuscript/SUMMARY.md`，也不出現在讀者版 HTML。

## 三種紀錄的邊界

| 紀錄 | 擁有者與用途 | 保存位置 |
|---|---|---|
| 讀者專業紀錄 | 讀者保存自己的環境、預測、結果、決定與回饋，可能包含本機路徑或個人判斷 | 讀者自己的工作區，不提交到本 repository |
| 章末作者驗證紀錄 | 給讀者看的單章摘要，支持章首狀態與基線 | 書稿的「作者驗證紀錄」章末小節 |
| 中央作者台帳 | 給作者與 checker 使用的 claim 級索引，保存版本、命令、oracle、差異與重驗條件 | `verification/ledger.toml` |

中央台帳不保存讀者答案、API key、帳戶資料、私人輸出或機器絕對路徑。它只索引
證據，不取代配套原始碼、測試、資料檔、golden 或第一手外部來源。章首 metadata、
章末摘要與中央台帳必須一致，不能各自形成不同的完成宣稱。

## Schema

根節點 `schema_version` 目前固定為 `1`，`records` 使用 TOML table array。每筆
record 必須包含下列欄位；未知或拼錯的欄位會使 check 失敗。

| 欄位 | 規則 |
|---|---|
| `id` | 全域唯一、穩定的小寫 kebab-case claim ID |
| `batch` | 寫作批次，例如 `W1` |
| `document`、`chapter`、`claim` | repository 相對章稿路徑、與 H1 相同的章名、可獨立重驗的宣稱 |
| `content_state` | 必須來自序章，且與章首一致 |
| `tag_commit`、`full_commit` | `tag@40 字元 SHA` 與獨立完整 SHA；兩者及章首、章末必須一致 |
| `data_checksums` | 有資料時使用 `<logical-id>=sha256:<64 hex>`；沒有資料時用空陣列 |
| `data_checksum_note` | 說明 checksum 範圍；空陣列時必須以「不適用：」說明具體原因，有 checksum 時不得標不適用 |
| `formal_entrypoints`、`schemas`、`interface_note` | 支持 claim 的核准讀者／產品 CLI、API 與 DTO／資料／報告契約；Git、uv、pytest 與 package 路徑是驗證工具或 evidence，不得冒充正式入口／schema；兩個陣列皆空時，note 必須明示不適用原因 |
| `evidence_refs` | 至少一項；使用 `repo:emmet-qt-bt1@<完整 SHA>:<path>`、可版本化且未被忽略的 `book:<path>#<fragment>`，或不含認證資訊的 `url:https://...` |
| `verification_commands`、`oracle` | 實際重現命令與執行前即能判斷成敗的標準 |
| `result`、`observed` | `pass`／`needs-revalidation` 與精簡實際結果；不保存整段 stdout |
| `known_differences` | 已知限制；確認沒有差異時使用空陣列，不寫模糊 placeholder |
| `verified_on` | `YYYY-MM-DD`，必須與章首最後驗證日期一致；checker 不用牆鐘推定作者是否真的重跑 |
| `revalidation_triggers` | 會使本 claim 失效的版本、資料、入口、schema、命令或預期結果變更 |

`needs-revalidation` 只能用於內容狀態已改為「需重驗」的文件；相反地，「需重驗」
文件也必須至少有一筆相同結果的紀錄。沒有資料、正式入口或 schema 是合法狀態，
但必須明示不適用，不能虛構 checksum 或未發布能力來填欄位。

## 新增與重驗流程

1. 先依目前 active gate 判斷該章是否允許工作。
2. 在隔離 worktree 固定配套 `tag@commit`，先寫 oracle，再執行命令。
3. 更新章稿的 metadata 與章末作者驗證摘要。
4. 在台帳新增或更新 claim；同一 claim 沿用原 `id`，不要用新 ID 隱藏舊差異。
5. 從 repository 根目錄執行 `./scripts/book-check`，把通過證據連回對應 Issue／PR。

若任一 `revalidation_triggers` 發生，先把章首改為「需重驗」、相關 record 改為
`needs-revalidation`，再更新文字或操作宣稱。完成重跑後才能恢復適當內容狀態與
`pass`。checker 只離線核對 schema、coverage 與交叉一致性；它不讀取
`../emmet-qt-bt1`、不替作者執行台帳命令，也不連外驗證 URL。

## W1-G0 首批紀錄

目前台帳只建立骨架所需的四項 claim：setup 的 tag／隔離 worktree、locked
environment smoke，以及第 1 章的系統邊界與訂單模型 smoke。這不代表第 1 章已
完成 W1-G1 的全面內容驗收；後續章節 PR 必須依 active gate 隨章擴充。
