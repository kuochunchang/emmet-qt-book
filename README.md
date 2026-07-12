# Emmet 量化交易員養成

從可信研究、風險紀律到人與系統共同演進。

這是一套以新手為起點、以證據導向專業能力為目標的教材，配套專案為相鄰目錄
的 `../emmet-qt-bt1`。本書不另外發明簡化交易引擎；概念最終都要回到配套系統
的真實模型、資料、測試與正式操作入口。

## 寫作目標

- 讓沒有量化交易經驗的讀者理解市場、資料、訂單、撮合、會計與風控。
- 讓會寫一點 Python 的讀者能安全地安裝、檢查並操作配套系統。
- 訓練讀者建立假設、審核證據、作出 go／no-go 決定並留下專業紀錄。
- 解釋系統為何採用 Decimal、注入時鐘、事件全序、雙錢包與策略沙箱等設計。
- 由歷史資料與回測逐步前進，不把回測績效誤寫成未來獲利承諾。
- 讓讀者把交易觀察轉化成可重現的系統改善，而不只停留在操作層。

## 目前基線

本書第一批操作基線固定為 `emmet-qt-bt1`
`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`。可操作能力與後續 Phase 狀態以
[課程大綱的配套系統能力地圖](docs/curriculum.md#配套系統能力地圖)為準；目前允許
啟動的寫作工作以根目錄 `AGENTS.md` 的 active gate 為準。README 只提供入口摘要，
不取代這些權威來源。

> 本書是工程與研究教材，不構成投資建議。實盤交易可能損失全部本金；在讀者
> 完成資料驗證、回測、模擬盤、風控與操作演練前，不引導其投入真實資金。

## 導覽

- [全書目錄](manuscript/SUMMARY.md)
- [序章：怎麼使用這本書](manuscript/preface.md)
- [實作準備：建立版本固定的配套環境](manuscript/front-matter/setup.md)
- [權威課程大綱](docs/curriculum.md)
- [寫作指南與章節模板](docs/authoring-guide.md)
- [作者驗證台帳與重驗流程](docs/verification-ledger.md)

## 建議閱讀方式

全書使用序章定義的[七步證據閉環](manuscript/preface.md#七步證據閉環)，
把執行前預測、系統驗證、專業決定與系統回饋連成同一條學習路徑。第一次閱讀
可依序完成主線；已有交易經驗者可由
[課程大綱的能力地圖](docs/curriculum.md#配套系統能力地圖)直接跳到需要的主題。

## 書籍建置與閱讀

作者在 repository 根目錄只需執行一個品質入口：

```bash
./scripts/book-check
```

目前驗證基線為 Linux／Bash。

命令會以固定版本的 mdBook 建置並檢查書稿；成功後由 `book/index.html` 閱讀 HTML
版。尚未建置時，可由[全書目錄](manuscript/SUMMARY.md)開啟已有章稿；空 target
表示該章尚未撰寫。
目前正式支援範圍只有 HTML，PDF／EPUB 明確延後，不以瀏覽器列印或未驗證的轉檔
冒充正式產物。安裝條件、固定版本、檢查規則與格式決策見
[出版工具鏈與 book check](docs/publishing.md)。

## Repository 結構

```text
AGENTS.md                 Agent 寫作 gate 與工作規則
CLAUDE.md                 Claude Code 相容入口，匯入 AGENTS.md
README.md                 專案入口與目前基線
book.toml                 mdBook 出版設定
book-check.toml           metadata 與作者驗證台帳設定
docs/
├── curriculum.md         課程、章序、能力地圖與開發里程碑
├── authoring-guide.md    寫作、版本、驗證規範與章節模板
├── publishing.md         出版格式、工具版本與 book check 契約
└── verification-ledger.md
                          作者台帳 schema、邊界與更新流程
manuscript/
├── SUMMARY.md            讀者閱讀順序
├── preface.md            讀者契約、證據閉環與內容狀態正本
├── front-matter/         版本固定的實作準備
└── chapters/             正文章節
scripts/book-check        唯一本機建置／品質入口
tests/                    book check 正向與故障案例
verification/ledger.toml  唯一的 claim 級作者驗證台帳
```

教育目標、章序與寫作批次由 `docs/curriculum.md` 維護；README 不另行定義 agent
工作限制，目前限制以 `AGENTS.md` 與 curriculum 的 active gate 為準；`SUMMARY.md`
只負責讀者導航，避免出現彼此競爭的新舊大綱。
