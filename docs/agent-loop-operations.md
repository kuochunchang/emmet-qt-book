# 四角色 agent loop 操作指南

本專案用五個長生命週期 CLI component 推動三個交付角色，並在 gate checkpoint
啟動第四個 Gate Auditor。Gate Auditor 只有一項受限寫入權限：在有效事件與發佈前
重驗都通過時，於 Meta Issue #1 追加一則具冪等 marker 的稽核留言：

- `agent dispatcher`：等待 dispatcher 事件；每次事件啟動一次
  `$emmet-loop-dispatcher`。
- `agent coder`：等待 coder 事件；每次事件啟動一次 `$emmet-loop-coder`。
- `agent reviewer`：等待 reviewer 事件；每次事件啟動一次
  `$emmet-loop-reviewer`。
- `agent gate-auditor`：只在 main-bound gate-exit checkpoint 等待稽核事件；每次事件
  啟動一次 `$emmet-loop-gate-auditor`，最多發佈上述一則留言。
- `events`：定期 polling GitHub live state，依協定只通知目前負責的 agent。

本文件是操作者導覽，不是另一份狀態機。角色權限、label、routing、狀態轉移與
安全規則以 [`agent-loop.md`](agent-loop.md) 為正本；目前允許工作的 gate 以
[`AGENTS.md`](../AGENTS.md) 與 [`curriculum.md`](curriculum.md) 為準。
正常 role iteration 不載入本操作指南；只有操作者或 launcher lifecycle 診斷才讀。

## 執行模型

```text
                            Unix socket event
GitHub <- poll -- events ----------------------> current state owner
  ^                                                  |
  |                                                  | codex exec --json
  +----------- canonical role writes <---------------+
                  gate checkpoint --> Gate Auditor -- audit comment --> Meta #1 --> human
```

只有 `events` component 輪詢。四個 agent component 可以常駐，但只阻塞等待自己的
Unix socket；role skill 與每個 Codex child 仍只做一輪，不 sleep、不 poll、不啟動
下一輪。每個 child 結束後，agent 回到等待狀態。

GitHub Issue、PR、label、留言與完整 commit SHA 是跨重啟的唯一 durable workflow
state。Socket、event ID、fingerprint 與 manager 記憶體都只是喚醒機制；任一 component
中斷或重啟後，下一個 role iteration 仍先以 GitHub 做 reconciliation。

Event manager 不產生 GitHub mutation。它依 canonical routing 選出 dispatcher、
coder、reviewer，或在有效 gate-exit checkpoint 選出 gate-auditor；agent ACK 後，
同一 state 預設 30 分鐘才可能重送。已完成但沒有
durable 進度的 iteration 會改成 `stalled` 並只 escalation 一次，不按 heartbeat 反覆
喚醒。State 改變立即通知；agent 尚未啟動或 delivery 失敗則每次 poll 重試。任何
Codex child 執行期間不送新的 wake；在途 state 持續時，manager 每 30 分鐘先單獨喚醒
dispatcher 做 reconciliation 與停滯檢查，再回到 state owner。

## Gate transition：人類 checkpoint

Gate 退出不是一般 loop state transition。Dispatcher 彙整退出證據後停止派工，並在
Meta Issue #1 留下綁定目前 `main` 的
`emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA>` marker。沒有 loop WIP 時，
event manager 以 `reason=gate-audit-requested` 喚醒 Gate Auditor；Auditor 固定該
checkpoint comment ID，獨立重驗後最多追加一則綁定 gate、`MAIN_SHA`、checkpoint ID
與 verdict 的稽核留言。`not-ready` 讓 dispatcher 依明確缺口恢復目前 gate，`unknown`
fail closed，只有 `exit-ready` 會顯示 `health=awaiting-user`。這則留言不改 label、不
派工，也不構成 transition 核准。只有使用者明確核准、gate-transition PR 合併、
Meta Issue #1 完成同步，且三份治理真相一致後，下一 gate 才能開始。

Matching audit 是指以下 marker 與目前 checkpoint 完全一致的留言：

```text
<!-- emmet-loop:gate-auditor:audit:v1:gate=<GATE>:main=<SHA>:checkpoint=<ID>:verdict=<not-ready|unknown|exit-ready> -->
```

收到 matching audit 後，`not-ready` 交回 loop 修復；缺口解決且退出條件重新成立後，
dispatcher 才建立新的 checkpoint。`unknown` 先處理稽核所列證據缺口；只有
`exit-ready` 才繼續以下 transition checklist：

1. 確認 audit verdict 是 `exit-ready`，且綁定同一個 marker、完整 `MAIN_SHA` 與
   checkpoint comment ID，再停止 `events`；若其他 client 仍可能工作，由使用者在
   Meta Issue #1 加上 `loop:paused`。
2. 讀取 Gate Auditor 報告，並確認沒有半完成 label transaction、stale approval、
   blocked 或無法解釋的 WIP。
   有異常時，先以單輪 `codex-loop dispatcher` 做 reconciliation。
3. 使用者以獨立 Issue 明確核准 transition；Issue 連結 Dispatcher gate-exit marker、
   完整 `MAIN_SHA`、退出證據與 transition PR。未獲核准就停在 checkpoint。
4. 普通 session 依 `AGENTS.md` 建立圈外 transition PR，不呼叫任何 loop agent。
   同一個 PR 同步更新 `AGENTS.md` 與 curriculum 的 active gate、前一 gate 證據及
   允許／禁止範圍。
5. Transition PR 必須 base=`main`、非 draft、保持無 `loop:*` label，tracked diff
   只包含已核准治理範圍。它不得由 dispatcher 的自動合併例外合併，也不得使用
   auto-merge；使用者依目前完整 head SHA 重新確認後，才由 UI 或明確授權的普通
   session 以 head-match 保護合併。
6. 合併後立即更新 Meta Issue #1：記錄新 active gate、前一 gate 完成證據、
   transition PR merge SHA 與下一步，再依 repository 規則完成 transition Issue。
7. 從最新 `origin/main` 核對 `AGENTS.md`、curriculum 與 Meta Issue #1 三者完全
   一致；在此之前不得宣稱新 gate 生效或派下一 gate。
8. 依「Control 更新的自動換代與人工 fallback」移動 runners、重跑五項 dry-run。
9. 若使用了 `loop:paused`，由使用者移除。先保持 `events` 停止，手動執行一次
   dispatcher；確認它留下有效派工與唯一 `loop:queued` 後，才啟動完整五 component
   並人工觀察第一圈 dispatcher → coder → reviewer → dispatcher。

若自動 audit delivery 失敗，或需要對指定歷史 gate 做額外診斷，人類仍可明確呼叫
`$emmet-gate-auditor`；它的唯讀邊界與人類決策責任不變。

## 前置條件

開始前確認：

1. `origin/main` 上的 `AGENTS.md`、curriculum 與 Meta Issue #1 對 active gate 的描述
   一致。
2. `codex` 與 `gh` 已登入；帳號能 fetch repository、讀寫 loop labels／comments 及
   操作 PR。
3. Dedicated launcher control worktree 與四個 trusted runners 都是本 repository 的
   乾淨 linked worktree，不含候選 PR 內容，也不拿來 checkout task branch。
4. Meta Issue #1 沒有 `loop:paused`。
5. Lifecycle launcher 從最新 `origin/main` 的 dedicated control worktree 載入，五個
   component 再由同一版 trusted adapter 啟動；不要從主要 checkout 或候選 worktree
   載入 control inputs。

Repo 不安裝 cron、systemd unit 或其他主機 scheduler；此模型本身不需要定時器，
polling 由 `events` process 內建。

## tmux 一鍵生命週期（建議入口）

可從本 repository 任一 linked worktree 的空終端執行；以下仍以主要 checkout 為例：

```bash
cd /home/guojun/workspace/emmet-qt-book

./scripts/codex-loop tmux status
./scripts/codex-loop tmux start
```

`start`／`restart` 不會以呼叫端 worktree 作為 control source。它們先用 Git common-dir
找出 canonical checkout，建立或驗證同層的
`/home/guojun/workspace/emmet-qt-book-loop-control`，拒絕任何 tracked／untracked 變更，
將該 worktree detached 對齊最新 `origin/main`，再以其中的 launcher 重新執行命令。
因此主要 checkout 可停在正常 feature branch；不必為啟動 loop 而切換、merge 或 reset。
已建立 control worktree 後，也可直接從它執行相同命令。

`start` 只用於確認目前沒有 loop component 或同名 session 的首次啟動；任一 lock
或 session 已存在就 fail closed，不會偷偷啟動第二份。要把既有的手動五終端部署
換成 tmux，或在 control input 合併後更新並重開，使用：

```bash
./scripts/codex-loop tmux restart
```

Tmux 五個 pane 預設使用易讀的 pretty 顯示；Codex child 底層仍是
`codex exec --json`。若要讓整個 session 直接顯示原始 JSONL，以安全
restart 切換：

```bash
./scripts/codex-loop tmux restart --output-format jsonl
```

切回預設顯示同樣使用 restart：

```bash
./scripts/codex-loop tmux restart --output-format pretty
```

`--output-format auto` 也可明確指定；它在 TTY 選 pretty、pipe／redirect 選
JSONL。Direct agent、events 與 one-shot 預設是 `auto`，tmux launcher 則明確
預設 `pretty`。顯示格式不改變 role 權限、GitHub durable state 或啟停順序。

`restart` 先在不停止現有 component 的前提下完成 control worktree bootstrap 與驗證；
失敗時不碰既有 session。之後才停止 event manager，再停止 dispatcher、coder、
reviewer、gate-auditor，等待各自釋放 lock；接著清除本 launcher 擁有的舊 session、
建立缺少的 dedicated runner、拒絕不乾淨 runner，並把四個 runner 切到同一個
`origin/main`。五項預檢通過後才清掉 stale socket 並開 tmux；四個 agent socket 都
ready 之後，
右下角 event manager 才會啟動。

預設 session 名稱是 `emmet-qt-book-loop`，版面固定為：

| 位置 | component |
| --- | --- |
| 左上 | dispatcher agent |
| 左中 | coder agent |
| 左下 | reviewer agent |
| 右上 | Gate Auditor agent |
| 右下 | event manager |

每個 pane 的上邊框會持續顯示 component 名稱與目前狀態，不必先從捲動中的
pretty 事件或 JSONL 尋找最後一筆紀錄。例如：

- `dispatcher (等待事件)`：component 正常，尚未輪到 dispatcher。
- `coder (撰寫中：Issue #3)`：coder 的單輪 Codex child 正在處理該 Issue。
- `reviewer (審查中：PR #59)`：reviewer 正在審查該 PR。
- `gate-auditor (稽核中)`：Auditor 正在核對目前 gate checkpoint；有效事件通過發佈前
  重驗後，唯一可修改的 durable state 是 Meta Issue #1 的一則冪等稽核留言。
- `events (正常：coder 執行中／Issue #3)`：event manager 仍正常輪詢，流程 owner
  正在工作。
- `events (等待使用者：gate transition)`：目前 `main` 已有 gate-exit checkpoint、
  沒有 WIP 且已有 matching `exit-ready` audit；manager 只輪詢 durable state、不啟動
  角色。
- `events (停滯：coder／Issue #3)` 或 `events (阻斷：...)`：推進需要恢復或人工
  注意；搭配該 pane 最新的 `operator-alert` 查完整證據。

Agent child 成功結束後標題回到 `等待事件`；非零 exit 或 timeout 會保留在標題中，
直到下一輪開始。Component 正常停止會顯示 `已停止`；若 pane 內程序非預期退出，
tmux 邊框會自動附加 `[已退出]`。Pane title 是易讀的即時摘要，不是 durable state；
跨重啟仍以 GitHub Issue、PR、label、留言與完整 SHA 為準。

### 右上角：Gate Auditor 結果卡

Gate Auditor 完成一輪後，最後一則 agent message 會是固定結果卡，而不是只顯示
`verdict=exit-ready` 等機器欄位；其後仍可能出現 `turn completed` 與
`iteration-finished` lifecycle 記錄。先讀 `判定`、`Gate`、`問題` 與`下一步`，再依需要
開啟證據留言。下列是格式範例，不代表目前 live gate：

```text
Gate Auditor
判定：等待你決定
Gate：目前 <active>；稽核 <active>；後繼 <successor>（未生效）
問題：無
下一步（使用者）：決定是否啟動 transition；核准前停在 checkpoint。
本輪：已發佈 Meta #1 audit；只新增 report，未改 gate／label／PR／檔案
有效：檢查時 main@<12字元 SHA>；at=<ISO 8601 時間>
診斷：published / exit-ready / meta-comment-only / cache=git-fetch
證據：Meta #1 comment #<AUDIT_COMMENT_ID>（checkpoint #<CHECKPOINT_ID>）：<immutable permalink>
```

除 evidence permalink 可由 terminal 自行換行外，卡片每個 logical line 最多 80 個顯示
格；問題第一項最多 52 格，下一步最多 60 格，CJK 寬字以兩格計。

結果卡把 verdict、iteration outcome 與 freshness 分開：

- `尚未就緒`：至少一個退出條件明確失敗；看「問題」與 dispatcher 的最小恢復動作，
  不要核准 transition。
- `無法判定（安全停止）`：必要證據缺失、矛盾或無法綁定；先解決列出的 gap 並重跑
  fresh audit，之前不可 transition。
- `未稽核（安全停止）`：沒有完成 current-snapshot audit；從「本輪」與「問題」讀取
  stale、precondition 或 transport／snapshot 不完整的確切原因。
- `本輪不適用`：合法稽核已觀察到 transition state；沒有新增三選一 audit marker，交由
  使用者核對固定 transition 流程。
- `本輪：沿用既有 report，未重貼`：同一 gate／main／checkpoint 已有 durable report；
  「判定」仍顯示該 report 的真正 verdict，不會被 no-op 狀態蓋掉。
- `本輪：發佈結果未知`：不得假設有留言，也不得盲目重貼；恢復 GitHub 查詢後先搜尋
  exact marker。此時診斷的第二、三欄固定是 `none / unknown`。
- `無 durable 判定（report 未發佈）`：稽核已計算但 publication 明確失敗；「問題」若附
  `computed=<verdict>` 也只供除錯，不可沿用為 gate 判定。

`有效：過期` 會並列 bound 與 current 的短 SHA；即使舊 report 是 `exit-ready` 也
無效。不要直接建新 checkpoint：Dispatcher 先對 current main reconciliation，重驗
zero WIP、三方 gate 與退出證據；全部仍成立才建 fresh checkpoint 並重新稽核。

若結果卡顯示 GitHub 拒絕空白 body／stdin 關閉，表示 Auditor 把
`gh issue comment ... --body-file -` 啟動在沒有 live stdin 的一般 command execution。
修復後的角色程序會建立關閉 echo 的 interactive PTY／session，以 follow-up
`write_stdin` 傳入 report 並送 EOF；不得改用 inline body、heredoc、pipe 或暫存檔。
這種失敗沒有 durable verdict；先確認 exact marker 不存在，等 control inputs 合併並完成
trusted runner rotation 後，再由 Dispatcher 建立或重送 fresh audit event。

這張卡是 audit-time snapshot，不會在 main 日後移動時回頭改寫。要知道「此刻」是否仍
有效，對照右下角 Events pane 的 current `operator-status`。若 Events 顯示不同 main、
重新建立 checkpoint、
新的 WIP 或 blocking 狀態，以 Events 與 GitHub durable state 為準，舊卡只保留為歷史。

### 右下角：流程健康與下一步

先看右下角 pane title 判斷正常、暫停、停滯或阻斷；需要原因與恢復條件時，再看
event manager 每次 poll 輸出的 `operator-status`。Pretty 會優先顯示
`health`、`current`、`next` 與 `attention`；完整 raw 記錄的
`health`、`blocking`、`owner`、`current`、`next` 與 `attention` 都保留。要核對
這些原始欄位時，讀取 pretty 模式保留的 component JSONL trace，或切換到 JSONL 顯示。
這和 `tmux status` 不同：後者只證明 process、session 與 runner 版本健康，
不能證明 workflow 正在前進。

| 畫面值 | 操作者判讀 |
| --- | --- |
| `health=healthy` | state 合法；依 `owner`／`next` 等待下一個 transaction |
| `health=running` | 一個 role 正在執行；先等待，不手動啟動第二輪 |
| `health=draining` | control inputs 已更新；manager 停止派送並等待目前 child 結束 |
| `health=rotating` | detached rotator 正在驗證、同步 control worktree／runners、preflight 與重建 session |
| `health=awaiting-user` | gate exit 已綁定目前 `main`、沒有 WIP 且 matching audit verdict 是 `exit-ready`；等待使用者決定 transition |
| `health=paused` | 使用者的 durable brake 生效；確認安全後仍只由使用者移除 |
| `health=blocked` | 讀 `reason`／`attention`，修復 state、component 或 GitHub 讀取 |
| `health=stalled` | iteration 結束但 workflow fingerprint 未變，推進已實質停住 |

blocking 狀態第一次出現時，右下角會多一筆 `operator-alert`，並顯示簡短
`LOOP ALERT [warning|critical]`；warning／critical 同時送 terminal bell。相同
`alert_id` 持續時不重複響鈴或洗版；使用者設定的 pause 只顯示 notice、不響鈴。
問題確實解除時會出現一次 `operator-resolved`／`LOOP RESOLVED`。目前沒有內建桌面、
Email 或 Discord 通知；Meta Issue #1 才是需要跨終端保留的人類介入通知。

`health=stalled` 時先到 `attention` 指定的 role pane 看最後輸出，但不要手動啟動
下一輪。Manager 會為新的 no-progress alert 單獨喚醒一次 dispatcher；dispatcher 若能
機械恢復，只做一個 canonical transaction。若不能安全恢復，它會保留 primary state、
視情況加 `loop:blocked`，並在 Meta Issue #1 留含 alert ID、證據、解除條件與所需決定
的去重留言。照該留言補足授權或外部條件後，讓 GitHub durable state 改變；後續 poll
會自行輸出 resolved，不需要 restart loop，也不能換命令繞過 approval／安全政策。

component／socket 錯誤會是 `health=blocked`、`reason=delivery-failed` 與 critical
alert；依 `affected_role` 修復或重啟該 component，manager 下次 poll 會重送。
`github-poll-failed` 則先修復 `gh` authentication／network。一般 delivery、child
exit 或 no-progress alert 不會自動 restart process。唯一例外是 unpaused 狀態下
control inputs 與最新 `origin/main` 不同：manager 先 drain，再交給 detached rotator
執行 ownership／PID／lock／same-repo 驗證、同步、preflight 與 session 重建。
`loop:paused` 不會被自動移除，paused 期間也不換代。單看反覆出現的 routing decision
不代表已送達或有進度；換代細節看 `tmux status` 的 `rotation` 與 runtime directory
內的 `rotation.log`。

啟動成功會 attach session；在 tmux 按 `Ctrl-b d` 只會 detach，五個 component
繼續運作。重新觀看：

```bash
tmux attach-session -t emmet-qt-book-loop
```

在既有 tmux client 內執行會改用 `switch-client`；若只想背景啟動，加
`--no-attach`。啟動前只看計畫、不 fetch、不停止 process：

```bash
./scripts/codex-loop tmux restart --dry-run
```

其他生命週期命令：

```bash
# 純讀取：session ownership、active locks、control／runner HEAD 與乾淨狀態
./scripts/codex-loop tmux status

# 可重複執行：有序停止並只清除本 launcher 擁有的 session
./scripts/codex-loop tmux stop
```

`status`／`stop`／`--dry-run` 不 fetch 或移動 control worktree；`--dry-run` 只列出預計
使用的 control 路徑與 `control_bootstrap=true`。`stop` 與 `restart` 都先核對 lock
metadata、PID command identity 與 tmux
ownership marker；同名 session 若不是本 launcher 建立就拒絕處理，也不使用模糊的
`pkill` 或無條件 `kill -9`。正常停止會讓 busy agent 把 SIGTERM 轉給其 Codex
child process group。啟動中途失敗時，launcher 會有序停止已起來的 component、
移除 owned session 與 stale socket；無法驗證 identity 或 lock 未在 timeout 內
釋放時則保留現場並 fail closed。Pane 非預期退出後會保留畫面供檢查，下一次
`restart` 才清掉舊 session。

`tmux status`、start／restart／stop 的 launcher lifecycle stdout 本身仍輸出 JSON，
不經 pretty renderer；`--output-format` 只決定五個 component pane 的 operator
顯示與 pretty 模式的 local trace。

這四個命令只管理本機 process／tmux，不新增 durable workflow state、不安裝或啟用
主機 scheduler，也不新增／移除 `loop:paused`。若其他 client 仍可能 mutation，
先由使用者在 Meta Issue #1 加 `loop:paused`，完成重啟與 reconciliation 後再由
使用者移除。

四個 agent 每次收到事件才建立新的 `codex exec --ephemeral --json`。未指定 profile
時，launcher 先自動尋找 `$CODEX_HOME/loop-<role>.config.toml`，該角色檔不存在才找
共用的 `$CODEX_HOME/loop.config.toml`；兩者都不存在才使用 adapter 的 repo 角色預設：
dispatcher `gpt-5.6-sol/high`、coder
`gpt-5.6-sol/high`、reviewer 與 gate-auditor 都是 `gpt-5.6-sol/xhigh`
（Extra High），四者 verbosity 都是 low。
顯式 role profile 覆寫顯式共用 profile；顯式選擇又覆寫自動偵測。找到的 profile 在停止
舊 components 前與每次 wake 都必須存在且可解析，壞檔不會靜默退回。Repo 預設是最後的
trusted fallback，只有合併至 main 並完成換代後才生效。Codex 0.134.0 之後的 named profile 是獨立的
`~/.codex/NAME.config.toml`，其中可用 top-level `model` 與
`model_reasoning_effort` 同時固定模型與推理強度。

四個角色共用設定時沿用 `--profile`：

```bash
./scripts/codex-loop tmux restart --profile loop
```

模型或推理強度需要按角色分開時，建立四個 profile 檔，再分別指定：

```bash
./scripts/codex-loop tmux restart \
  --dispatcher-profile loop-dispatcher \
  --coder-profile loop-coder \
  --reviewer-profile loop-reviewer \
  --gate-auditor-profile loop-gate-auditor
```

`--dispatcher-profile`、`--coder-profile`、`--reviewer-profile` 與
`--gate-auditor-profile` 會覆寫共用
`--profile`；沒有 role-specific override 的角色仍使用共用 profile，兩者都沒有
才使用 repo 角色預設。Launcher 會對每個角色的最終選擇執行 adapter preflight，
並在停止舊 components 前確認每個 profile 檔存在且 TOML 可解析，再把 mapping
寫進 owned tmux session；之後不帶參數的 `tmux status` 會在
`codex_profiles` 顯示實際 profile 名稱，`codex_role_configuration` 顯示每個角色來自
`repo-default`、`profile` 或舊 generation 的 `inherited`。Launcher 啟動前與 adapter
每次 wake 都重驗 profile 檔存在且 TOML 可解析，避免檔案消失時靜默退回 user config；
實際 model entitlement 仍由 Codex 啟動時驗證。
右下角 `events` 不啟動 Codex，也不使用模型。

從舊三角色 session 執行新版 `tmux status` 時，launcher 會把缺少的
`gate-auditor` profile 顯示為 `None`、execution source 顯示為 `inherited`，並把
`codex_profile_source` 標成 `legacy-session`；這只是唯讀相容診斷。下一次安全
drain-and-rotate 會建立第四個 runner／agent 並寫入完整的新 generation metadata。

Profile 檔格式與優先序見
[Codex profiles 官方文件](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles)。

以下 dedicated runner、預檢與五終端步驟保留為底層手動操作與故障診斷；一般啟停
優先使用上述 tmux 入口。

## 建立 dedicated trusted runners

以下範例假設 canonical checkout 位於
`/home/guojun/workspace/emmet-qt-book`。Launcher control worktree
`emmet-qt-book-loop-control` 由 `tmux start/restart` 自動建立與同步；下列只是在缺少時
建立四個 role runners：

```bash
git -C /home/guojun/workspace/emmet-qt-book fetch origin main --prune

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-dispatcher origin/main

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-coder origin/main

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-reviewer origin/main

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-gate-auditor origin/main
```

路徑已存在時不要重建；先確認它確實是同一 repository 的乾淨 linked worktree。
Coder 的 task worktree 與 Reviewer 的 disposable candidate worktree 由各自 role
iteration 另外建立，不能把 trusted runner 當候選 worktree。

## 預檢

固定從 dispatcher runner 的 adapter 啟動五個 component，並把各角色指向自己的
trusted workdir：

```bash
ADAPTER=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop

"$ADAPTER" agent dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher --dry-run
"$ADAPTER" agent coder \
  --workdir /home/guojun/workspace/emmet-qt-book-coder --dry-run
"$ADAPTER" agent reviewer \
  --workdir /home/guojun/workspace/emmet-qt-book-reviewer --dry-run
"$ADAPTER" agent gate-auditor \
  --workdir /home/guojun/workspace/emmet-qt-book-gate-auditor --dry-run
"$ADAPTER" events \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher --once --dry-run
```

Agent dry-run 會 fetch、驗證 repository identity、origin、control inputs、role skill
與 Codex executable，列印 shell-safe `codex exec` command 及 socket path，但不取得
鎖、不建立 socket、不啟動 Codex。Events dry-run 會真的讀一次 GitHub，列出
`would-notify` event，但不連線 agent 或修改 GitHub。

任何 runner 不乾淨、control input 與 `origin/main` 不同、repo identity 不符或
GitHub polling 失敗，都要先修正；不得使用 dangerous bypass 或把 candidate branch
當控制來源。

## 啟動

開五個操作者可見終端。每個終端先設定相同的 `ADAPTER` 路徑；前四個先啟動
agent，最後才啟動 event manager。

```bash
# 每個終端先執行
ADAPTER=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop

# 終端一
"$ADAPTER" agent dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher

# 終端二
"$ADAPTER" agent coder \
  --workdir /home/guojun/workspace/emmet-qt-book-coder

# 終端三
"$ADAPTER" agent reviewer \
  --workdir /home/guojun/workspace/emmet-qt-book-reviewer

# 終端四
"$ADAPTER" agent gate-auditor \
  --workdir /home/guojun/workspace/emmet-qt-book-gate-auditor

# 終端五
"$ADAPTER" events \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher \
  --interval-seconds 60 \
  --retry-seconds 1800 \
  --dispatcher-heartbeat-seconds 1800
```

`--interval-seconds` 是 GitHub polling 間隔；`--retry-seconds` 是同一 state 已 ACK、
但 manager 尚無法確認 iteration 已完成時，重新通知 owner 的間隔；
`--dispatcher-heartbeat-seconds` 是在途 state 的 dispatcher oversight 間隔。已完成卻
沒有 durable 進度的 mutation iteration 會轉成 `stalled`。目前 `main` 的 gate-exit
checkpoint 會先喚醒 Gate Auditor；成功發佈的 matching audit comment 會推進正規化
workflow fingerprint。其 `exit-ready` verdict 轉成 `awaiting-user`，`unknown` 保持
fail-closed blocker，`not-ready` 則把目前 active gate 交回 dispatcher 修復。
`snapshot-incomplete` iteration 成功完成後也保持 blocker，直到快照改變；未完成或
非零退出才按各自的恢復規則處理。已被 durable state 吸收的成功結果不會按 retry
window 反覆喚醒原 owner。縮短 polling 會增加 GitHub API 使用量；
縮短 retry 或 oversight 間隔仍可能增加其他異常／在途狀態的 Codex 用量。若 socket
delivery 失敗，manager 不等 retry window，而是下一個 poll 再送。

每個角色只有一個常駐 agent。第二個同角色 process 會因 per-role `flock` 以 75
退出；第二個 event manager 也會因 `events` lock 以 75 退出。不同角色 socket 使用
同一個由 Git common-dir 派生的 runtime directory，因此從四個 linked worktree 啟動
仍能互相找到。

## Pretty 顯示與 raw trace

Agent 以 `codex exec --ephemeral --json` 啟動每次 iteration；`--json` 仍是底層
machine-readable contract。Agent、events 與 one-shot 共用：

| `--output-format` | operator 顯示 | local raw trace |
| --- | --- | --- |
| `auto` | TTY 用 pretty；pipe／redirect 用 JSONL | 只在解析為 pretty 時建立 |
| `pretty` | 易讀的 lifecycle、message、command、tool、file change、error 與 usage | 建立三個分流 trace |
| `jsonl` | 原始 JSONL stdout；stderr 仍走 stderr | 不由 renderer 另建檔 |

Direct 入口預設 `auto`，tmux 五個 pane 則明確預設 `pretty`。Pretty 是
bounded operator projection：已知 `thread.started`、`turn.*`、`item.*` 與 component
事件會轉成易讀文字；未識別 event、malformed JSON、無效 UTF-8、過長或缺少
結尾換行的記錄會顯示安全警示並引導查看 raw log，不會把不受信任的
terminal control sequence 直接送到畫面。這些顯示層差異不改變 child exit code、
timeout 或 signal forwarding。

若 pretty 的私有 trace 目錄或 display worker 無法安全初始化，入口會在 stderr
警告並 fail open 回 JSONL；child 仍照常執行，且不留下半套 generation files。
若已啟動 generation 的 raw sink 在 iteration 中失效或長時間無法完成，adapter 會先
保留 child exit／timeout 清理，再停止該 renderer 並讓後續輸出退回 JSONL；此時該
generation 可能不完整，應依 diagnostic 修復 runtime filesystem 後重啟 components。

`--dry-run` 不啟動 child，也不建立 raw trace；`--print-command` 的 shell-safe wrapper
command 只顯示在 terminal，實際啟動後的 child／component 事件才進 trace。真正的
pretty one-shot 會先顯示三個 raw path，方便直接定位。即使明確指定 pretty，agent
或 events dry-run 仍只顯示 shell-safe command／JSONL preflight，不啟動 renderer。

Pretty 模式在現有 Git common-dir 派生的 repo 外 runtime directory 下建立
`logs/`；每個 component generation 各有 Codex `.stdout.jsonl`、child `.stderr.log`
與 `.component.jsonl`。Codex stdout bytes 先原樣寫入 stdout 檔再投影；component 的
waiting、event、delivery、exit code 與 timeout 寫入獨立 component trace，不能插入
child 的 partial record。`.stderr.log` 只保存 Codex child stderr，component preflight
error 與 `LOOP ALERT`／`LOOP RESOLVED` 仍直接顯示在 terminal。三個 stream 不混檔。
Runtime `logs/` 為 mode `0700`，generation files 為 mode `0600`。
`tmux status` 的 `runtime_dir` 可用來定位這個 `logs/` 目錄；它不代表 launcher
把個別 raw-log path 當成 session 狀態。

這些 generation files 只是 local operator trace，不是 GitHub durable workflow
state。Role、manager 與 rotator 不得從它們 routing、reconciliation、推定 mutation
成功或恢復跨重啟狀態；真相仍只來自 GitHub Issue、PR、label、comment 與完整
SHA。Log 可能包含 private repository 路徑、Issue／PR 內容、prompt、tool 參數
或命令輸出；不得提交 repository，分享前先檢查秘密與 private data。

Renderer 不自動刪除 generation trace，也不把 retention 當 workflow 動作；長駐
component 的檔案會持續成長。操作者須監看 runtime filesystem 容量並制定本機保留期。
清理前先以 `./scripts/codex-loop tmux stop` 停止全部 component、確認 locks 已釋放，
再依組織的稽核與
秘密處理政策封存或刪除舊 generation；不得在 agent 寫入中清檔，也不得讓清理結果
影響 GitHub routing 或 reconciliation。

若 direct 入口的 stdout 要送進其他工具，可明確要求 JSONL 並將 stderr 分開保存：

```bash
"$ADAPTER" agent dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher \
  --output-format jsonl \
  >/tmp/emmet-loop-dispatcher.stdout.jsonl \
  2>/tmp/emmet-loop-dispatcher.stderr.log
```

## 手動單輪診斷

事件架構之外仍保留 one-shot 相容入口，供部署前或故障時明確執行一輪：

```bash
"$ADAPTER" dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher
```

使用前先停止對應 agent，否則同一把 role lock 會回傳 75。不要用 cron、systemd
timer 或 App Scheduled Tasks 重複呼叫這個相容入口；連續運作只用 `agent` + `events`。

Gate Auditor 也保留唯讀 one-shot fallback：

```bash
"$ADAPTER" gate-auditor \
  --workdir /home/guojun/workspace/emmet-qt-book-gate-auditor
```

這個手動入口沒有合法 `gate-audit-requested` event packet，因此即使載入
`$emmet-loop-gate-auditor` 也只輸出診斷、不發佈留言。若要稽核指定歷史 gate，仍由
人類明確呼叫既有 `$emmet-gate-auditor`；它始終是完全唯讀工具。

## Control 更新的自動換代與人工 fallback

一般圈外內容合併只讓 runner HEAD 暫時落後，不會觸發 restart；`tmux status` 的
`control_inputs_match=true` 表示 long-lived generation 仍安全。Control inputs
改變時，正常路徑會自動顯示 `draining`／`rotating`，完成後從 GitHub durable
state 恢復，不需人工先改 label 或重送事件。

Handoff 期間 event manager 會繼續持有 `events` lock，直到 detached rotator 已驗證
parent PID／lock 並以 matching PID 寫入 `waiting-for-manager` ACK。Manager 收到 ACK 後
才退出並釋放 lock；rotator 隨後停止 components。ACK 前 rotator 退出或逾時會被停止並
記為 `rotation.state=failed`，不得靠排程競速或重複 spawn 繞過 parent identity 驗證。

Launcher 會把目前 `--output-format` 傳給 event manager 與 detached rotator，因此
後續的自動換代不會意外從 pretty 切回 JSONL，或反向切換。Renderer 程式本身
屬 control inputs；它在 `main` 改變時也必須走相同 drain-and-rotate。Pretty
generation logs 保留在 repo 外 runtime `logs/`，不會被同步進 control／runner
worktree，也不得用來取代換代後的 GitHub reconciliation。

下列手動程序只用於 gate transition 的人工 checkpoint，或 `rotation.state=failed`
且已依 `detail` 排除原因後：

每次 gate transition，或 `AGENTS.md`、`.agents/`、`.claude/`、`.codex/`、loop
協定、curriculum、authoring guide、adapter 等 control inputs 在 `main` 改變後：

1. 先停止 `events`，再以 Ctrl-C／SIGTERM 停止四個 agents。若 child 正在執行，
   agent 會把 signal 轉給整個 child process group，等待退出並釋放 lock。
2. 若仍可能有其他 client 工作，由使用者加 `loop:paused`。
3. 確認 runners 乾淨，再移到最新 `origin/main`：

   ```bash
   git -C /home/guojun/workspace/emmet-qt-book fetch origin main --prune
   git -C /home/guojun/workspace/emmet-qt-book-dispatcher switch --detach origin/main
   git -C /home/guojun/workspace/emmet-qt-book-coder switch --detach origin/main
   git -C /home/guojun/workspace/emmet-qt-book-reviewer switch --detach origin/main
   git -C /home/guojun/workspace/emmet-qt-book-gate-auditor switch --detach origin/main
   ```

4. 不乾淨或無法 switch 時停止，不得以 `reset --hard` 掩蓋未知變更。
5. 重跑五項 dry-run。Gate transition 依 checkpoint 程序先完成人工 Dispatcher 派工與
   唯一 `loop:queued` 核對；其他 control-input 更新則完成必要 reconciliation。
6. 準備重開完整 components 時執行 `tmux restart`；bootstrap 會以相同規則建立或同步
   `/home/guojun/workspace/emmet-qt-book-loop-control`。Control worktree 不乾淨或不屬於
   same-repo 時，launcher 會在停止既有 components 前 fail closed。

## 暫停、恢復與停止

全域 durable brake：

```bash
gh issue edit 1 --add-label loop:paused
```

Manager 看到 paused 後會通知四個 agents；paused event 本身不啟動 Codex。已在執行的
role 仍須依協定在任何 mutation 前重查 pause。確認安全後只由使用者恢復：

```bash
gh issue edit 1 --remove-label loop:paused
```

停止 `events` 只阻止這台主機送新事件，不是跨 client brake；`loop:paused` 才是所有
client 共用的 GitHub durable brake。完全停止時先終止 manager，再終止 agents。

## Exit、錯誤與恢復

| 狀況／code | 意義 | 處理 |
| --- | --- | --- |
| component 持續執行 | 正常等待／polling | 先看 pretty pane；需完整欄位時查看 raw trace |
| `0` | 正常手動停止、dry-run 或有限測試完成 | 正常 |
| `75` | 同角色 agent／one-shot 已持鎖 | 不再啟動第二份；核對 holder PID |
| child `124` | 單次 iteration timeout | 停止 manager，檢查 durable state 後 reconciliation |
| component `2` | worktree、origin、control input、executable、socket 或 polling 預檢失敗 | 停止 loop 並修正部署 |
| `delivery-failed` | agent 不在線、拒絕或無 ACK | 啟動／修復 agent；manager 下次 poll 重試 |
| 其他 child exit | Codex 原始 exit code | 保存 log；下一 event 先 reconciliation |

Push、label、comment 或 merge 結果不明時，不靠 event delivery 成功推定 mutation 成功；
下一個 role iteration 必須先讀 GitHub durable state，不能盲目重試。
