# 實作準備：建立版本固定的配套環境

> 配套基線：`emmet-qt-bt1 v0.3.0@c999965e5cc9`
> 內容狀態：可操作
> 最後驗證日期：2026-07-12

這份準備不編入正文章號，也不依賴尚未完成的 Foundation 入口。凡是需要執行
`emmet-qt-bt1` 指令的章節，都先使用這裡建立的隔離環境，避免正在開發的系統
工作樹悄悄改變書中結果。

> 本書第一批操作基線：`emmet-qt-bt1 v0.3.0@c999965e5cc9`

## 你需要準備什麼

- 本版驗證環境為 Linux 與 Bash。後續 Live 部署也使用 Linux 專屬能力；其他
  作業系統不是目前的驗證基線，不能直接把 Bash 指令翻成 PowerShell 後宣稱得到
  相同證據。
- Git。
- 可存取配套 private repository 的 GitHub 帳號與 SSH 設定。
- uv；安裝方式以 [uv 官方安裝文件](https://docs.astral.sh/uv/getting-started/installation/)
  為準。
- 可以下載 Python 與 locked dependencies 的網路環境。

先確認 Git 和 uv 可以執行：

```bash
git --version
uv --version
```

配套專案固定使用 Python `3.12.*`。uv 可以依專案的 `.python-version` 自動取得
相容版本，也可以先明確安裝：

```bash
uv python install 3.12
```

Git 可由 [Git 官方安裝頁](https://git-scm.com/install/)取得。若尚未設定 GitHub
SSH，先依 [GitHub 官方 SSH 指南](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
完成驗證；private repository 的存取權仍須由 repository 所有者授予。

## 為什麼不用現有的開發目錄

相鄰的 `../emmet-qt-bt1` 可能正在 Phase 4 或後續分支開發。直接在該目錄執行
書中命令，今天與下週可能得到不同介面或結果；直接 `git switch --detach` 又會
打斷既有工作。因此本書一律從它建立版本化的獨立 worktree。

以下假設書籍與配套 repository 位於同一層：

```text
workspace/
├── emmet-qt-book/
└── emmet-qt-bt1/
```

如果尚未取得配套 repository，從書籍根目錄執行：

```bash
cd ..
git clone git@github.com:kuochunchang/emmet-qt-bt1.git
cd emmet-qt-book
```

出現 `Repository not found` 或 `Permission denied (publickey)` 時先停止：前者通常
表示帳號沒有 private repository 權限，後者表示 SSH key 尚未正確連到 GitHub。
不能改抓來路不明的副本繼續操作。

## 建立 v0.3.0 隔離 worktree

從書籍根目錄執行：

```bash
git -C ../emmet-qt-bt1 fetch --tags
git -C ../emmet-qt-bt1 rev-parse 'v0.3.0^{commit}'
git -C ../emmet-qt-bt1 worktree add --detach \
  ../emmet-qt-bt1-v0.3.0 \
  c999965e5cc923281541409cda9502beb93b8a60
```

`rev-parse` 必須先輸出完整的
`c999965e5cc923281541409cda9502beb93b8a60`，證明 tag 與本書 commit 一致。worktree
動作不會切換 `../emmet-qt-bt1` 的分支。若目錄已存在，不要覆蓋它；先用下節的
命令確認它是否正是本書要求的版本。

## 驗證版本與乾淨狀態

```bash
export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"
git -C "$EMMET_QT_BT1_DIR" rev-parse HEAD
git -C "$EMMET_QT_BT1_DIR" status --short
```

第一個命令必須輸出：

```text
c999965e5cc923281541409cda9502beb93b8a60
```

第二個命令應沒有輸出。SHA 不符或工作樹不乾淨時先停止；不能把不同版本產生
的結果標成 `v0.3.0` 證據。

## 同步 locked environment

```bash
cd "$EMMET_QT_BT1_DIR"
uv lock --check
uv sync --locked --dev
uv run python --version
```

Python 應為 `3.12.*`。`uv sync` 會建立或更新此隔離 worktree 的 `.venv` 並下載
依賴，所以本書不會在讀者毫無預期時用 `uv run` 偷做第一次環境同步。

最後執行一個固定的 smoke test：

```bash
uv run pytest tests/unit/test_models_orders.py -q
```

在本書基線上，預期結果為 `32 passed`。

## 常見準備問題

| 現象 | 先檢查什麼 | 處置 |
|---|---|---|
| 找不到 `uv` | `uv --version` | 回到官方安裝文件，不要跳過 locked sync |
| 找不到 Python 3.12 | `uv python list` | 執行 `uv python install 3.12` |
| worktree 目錄已存在 | `git -C ../emmet-qt-bt1 worktree list` | 核對既有路徑與 SHA，不覆蓋、不刪除未知工作 |
| tag 解析成不同 SHA | `git -C ../emmet-qt-bt1 rev-parse 'v0.3.0^{commit}'` | 停止並回報；tag 可能已被移動，不繼續建立 worktree |
| HEAD 與章首不同 | `git -C "$EMMET_QT_BT1_DIR" rev-parse HEAD` | 停止操作，建立正確版本的另一個 worktree |
| `status --short` 有輸出 | 檢查變更來源 | 不把該結果當成本書基線證據 |
| dependency sync 失敗 | 網路、lockfile 與完整錯誤 | 保留錯誤訊息，不改 lockfile 規避問題 |

## 後續章節的版本規則

每個含操作的章節都會標示 `tag@commit`。開始操作前：

1. 使用對應版本的隔離 worktree，而不是開發目錄。
2. 核對 `git rev-parse HEAD` 與章首 commit。
3. 確認 `git status --short` 沒有輸出。
4. 依 lockfile 同步環境後才執行章中命令。
5. 將實際 tag、commit、工具版本與結果寫入自己的環境／實驗紀錄。章末作者驗證
   紀錄是書稿稽核證據，不由讀者改寫。

只要配套基線、正式入口、命令或預期輸出改變，章節就先標為「需重驗」，直到
相同操作在新基線上重新通過。

## 保存你的環境與版本檢查紀錄

完成準備後保存以下資料；後續每份研究都要能指出它使用哪個環境：

```text
配套路徑：
tag@commit：v0.3.0@c999965e5cc923281541409cda9502beb93b8a60
Git 版本：
uv 版本：
Python 版本：
工作樹是否乾淨：
lock check／sync 結果：
smoke test 結果：32 passed
檢查日期：
```

## 作者驗證紀錄

- 驗證對象：tag、隔離 worktree、locked environment、Python 與 smoke test 全流程
- 對照 tag／commit：`v0.3.0@c999965e5cc923281541409cda9502beb93b8a60`
- 驗證環境：Linux／Bash、Git 2.43.0、uv 0.10.4、Python 3.12.3
- 驗證命令：
  - `git -C ../emmet-qt-bt1 rev-parse 'v0.3.0^{commit}'`
  - `git -C ../emmet-qt-bt1 worktree add --detach ../emmet-qt-bt1-v0.3.0 c999965e5cc923281541409cda9502beb93b8a60`
  - `export EMMET_QT_BT1_DIR="$(cd ../emmet-qt-bt1-v0.3.0 && pwd)"`
  - `git -C "$EMMET_QT_BT1_DIR" rev-parse HEAD`
  - `git -C "$EMMET_QT_BT1_DIR" status --short`
  - `cd "$EMMET_QT_BT1_DIR"`
  - `uv lock --check`
  - `uv sync --locked --dev`
  - `uv run python --version`
  - `uv run pytest tests/unit/test_models_orders.py -q`
- 通過結果：tag 與 HEAD 均為固定 SHA；locked sync 成功；`32 passed`
- 待處理差異：其他作業系統與 shell 尚未列入驗證基線
