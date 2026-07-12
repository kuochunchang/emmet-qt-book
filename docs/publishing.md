# 出版工具鏈與 book check

> 決策日期：2026-07-12
> 適用範圍：W1 起的本機出版與書稿品質入口

## 輸出範圍

| 格式 | 目前狀態 | 產物／處理方式 |
|---|---|---|
| HTML | 支援，為目前唯一正式產物 | mdBook 0.5.4；輸出到 repository 根目錄的 `book/` |
| PDF | 延後 | 不把 mdBook 的瀏覽器列印頁宣稱為已驗證 PDF |
| EPUB | 延後 | 待正文、篇章結構與電子書 metadata 穩定後另行選擇並驗收 |

W1 選擇 [mdBook 0.5.4](https://github.com/rust-lang/mdBook/releases/tag/v0.5.4)，
因為它直接以既有的 `manuscript/SUMMARY.md` 決定章序、篇章與來源，不需要再維護
第二份導航設定。尚未撰寫的正文章節使用 mdBook 官方支援的
[draft chapter](https://rust-lang.github.io/mdBook/format/summary.html#structure)，只在
目錄保留空 target，不建立空白章稿；附錄在實際建立檔案前只保留來源端的延伸
閱讀清單，避免被 mdBook 錯編成第 51–64 章。這些 standalone empty suffix targets
刻意只顯示於 Markdown 來源，不出現在 HTML，也不是可閱讀附錄；實際附錄章稿
建立後，必須連同「專業延伸附錄」part title 重新設計並實測導航。不能只替目前
的 empty target 填入 `.md` 路徑，因為 mdBook 會在 part title 後忽略這種 suffix。

Quarto 與 Pandoc 能涵蓋更多輸出格式，但現階段會增加另一份 book 結構設定，PDF
還需要固定繁中字型與排版引擎。PDF／EPUB 會在內容與版面需求形成後另行決策；
成功產生檔案本身不構成格式已受支援的證據。

## 唯一品質入口

在 repository 根目錄執行：

```bash
./scripts/book-check
```

第一次執行時，Linux x86_64／aarch64 會下載官方預編譯的 mdBook 0.5.4，先核對
release asset 的 SHA-256，再安裝到已忽略的 `.cache/book-tools/`。快取建立後不需
網路；每次使用快取前仍會核對固定的 executable SHA-256。也可以把使用者自行
信任、已安裝的相同版本指定給命令：

```bash
MDBOOK_BIN=/path/to/mdbook ./scripts/book-check
```

自動安裝需要 Bash、Python 3.11 以上、`curl`、`sha256sum`、`tar` 與 `install`。
目前實際驗證基線是 Linux／Bash；`MDBOOK_BIN` 只略過自動安裝，不代表原生
Windows 已受支援。檢查不讀取 `../emmet-qt-bt1`，不需要 private repository、
API key 或其他秘密。

命令依序執行：

1. 執行 checker 的正向與故障案例 self-tests；
2. 核對 `book.toml` 的書名、說明、`zh-TW`、來源與唯一 output；作者署名待專案
   所有者另行確認，不由工具鏈代填；
3. 將 tracked 與未忽略的 untracked repository Markdown 放入隔離的 synthetic
   mdBook，使用相同 renderer 實際驗證本機 file／fragment／reference／raw HTML
   links；
4. 驗證 `SUMMARY.md` target 存在、不重複、不逃出來源，並找出孤兒書稿；
5. 對 `book-check.toml` 指定的正文與操作型 front matter 驗證章首 metadata、內容
   狀態和章末作者驗證紀錄；
6. 確認來源不會被 `.gitignore` 靜默排除，且 `book/` 已忽略、沒有 tracked 產物；
7. 在暫存 staging 以固定版本 mdBook 建置 HTML；只有舊 `book/` 帶有本工具
   ownership marker 時才安全替換，未標記的既有資料不刪除；
8. 從實際 HTML 驗證已發布章頁、`lang`、本機檔案、圖片、資源與 fragment；
9. 輸出所有產物的組合 SHA-256 manifest，供跨路徑重現性比對。

若出現 `OUTPUT_UNOWNED`，先檢查並自行移走或備份既有 `book/`，再重新執行；不要
只手動偽造 marker。Marker 是防止工具誤刪資料的 sentinel，不是安全或真實所有權
證明。

任一步失敗都回傳非零 exit code。外部 `http`／`https` URL 不在 deterministic gate
中發出網路請求；交易所、API、法規與其他時效性來源仍依寫作指南在發布前另行
查證。

書稿使用 Markdown code fence 表達程式與原始片段。為避免輸出的離線 HTML 執行
未受控內容，manuscript 禁止 `script`、`iframe`、`object`、`embed`、`base`、表單、
raw `pre`／`style` 等 active 或會干擾解析的 HTML，也禁止 `on*`、`srcdoc` 與 inline
style attribute。讀者連結的 `href` 只接受本機、`http`、`https`、`mailto`；圖片、
script、poster 與其他 reader resource 必須實際存在於 HTML output，不遠端載入。

## 新增書稿時

- 實際要出版的 Markdown 必須直接且只出現一次於 `SUMMARY.md`；只從另一章連到
  它仍然是孤兒。
- 未完成正文章節只保留 `- [章名]()` draft navigation，不建立空檔案。
- `manuscript/chapters/` 下的正文自動需要 metadata。新增具操作性的 front matter
  時，也要把相對路徑加入 `book-check.toml` 的 `metadata.required`。
- 內容狀態允許值直接讀取序章「內容狀態」小節；checker 不建立另一份狀態定義。
- 建置後的讀者入口是 `book/index.html`。`book/` 是可重建產物，不提交 Git。

mdBook 官方也建議在 CI 固定版本並另外執行連結或自訂品質檢查；目前 Issue #8 的
範圍是本機入口，沒有同時新增部署或 CI。版本固定方式可對照
[官方 CI 指南](https://rust-lang.github.io/mdBook/continuous-integration.html)。
