# 三角色 agent loop 操作指南

本專案用四個長生命週期 CLI component 推動三個短生命週期 Codex role：

- `agent dispatcher`：等待 dispatcher 事件；每次事件啟動一次
  `$emmet-loop-dispatcher`。
- `agent coder`：等待 coder 事件；每次事件啟動一次 `$emmet-loop-coder`。
- `agent reviewer`：等待 reviewer 事件；每次事件啟動一次
  `$emmet-loop-reviewer`。
- `events`：定期 polling GitHub live state，依協定只通知目前負責的 agent。

本文件是操作者導覽，不是第四份狀態機。角色權限、label、routing、狀態轉移與
安全規則以 [`agent-loop.md`](agent-loop.md) 為正本；目前允許工作的 gate 以
[`AGENTS.md`](../AGENTS.md) 與 [`curriculum.md`](curriculum.md) 為準。
正常 role iteration 不載入本操作指南；只有操作者或 launcher lifecycle 診斷才讀。

## 執行模型

```text
                         Unix socket event
GitHub <- poll -- events --------------------> responsible agent
  ^                                               |
  |                                               | codex exec --json
  +-------- role iteration mutations <------------+
```

只有 `events` component 輪詢。三個 agent component 可以常駐，但只阻塞等待自己的
Unix socket；role skill 與每個 Codex child 仍只做一輪，不 sleep、不 poll、不啟動
下一輪。每個 child 結束後，agent 回到等待狀態。

GitHub Issue、PR、label、留言與完整 commit SHA 是跨重啟的唯一 durable workflow
state。Socket、event ID、fingerprint 與 manager 記憶體都只是喚醒機制；任一 component
中斷或重啟後，下一個 role iteration 仍先以 GitHub 做 reconciliation。

Event manager 不產生 GitHub mutation。它只依 canonical routing 選出 dispatcher、
coder 或 reviewer；agent ACK 後，同一 state 預設 30 分鐘才可能重送。已完成但沒有
durable 進度的 iteration 會改成 `stalled` 並只 escalation 一次，不按 heartbeat 反覆
喚醒。State 改變立即通知；agent 尚未啟動或 delivery 失敗則每次 poll 重試。任何
Codex child 執行期間不送新的 wake；在途 state 持續時，manager 每 30 分鐘先單獨喚醒
dispatcher 做 reconciliation 與停滯檢查，再回到 state owner。

## Gate transition：人類 checkpoint

Gate 退出不是一般 loop state transition。Dispatcher 彙整退出證據後停止派工，並在
Meta Issue #1 留下綁定目前 `main` 的
`emmet-loop:dispatcher:gate-exit:<GATE>:main=<MAIN_SHA>` marker。沒有 loop WIP 時，
event manager 會顯示 `health=awaiting-user`，且在 `main` 或 durable state 改變前不再
啟動 Codex。只有使用者明確核准、gate-transition PR 合併、Meta Issue #1 完成同步，
且三份治理真相一致後，下一 gate 才能開始。

Checkpoint 期間：

1. 先停止 `events`，避免產生新 wake；若其他 client 仍可能工作，由使用者在 Meta
   Issue #1 加上 `loop:paused`。
2. 確認沒有半完成 label transaction、stale approval、blocked 或無法解釋的 WIP。
   有異常時，先以單輪 `codex-loop dispatcher` 做 reconciliation。
3. 使用者以獨立 Issue 明確核准 transition；Issue 連結 Dispatcher gate-exit marker、
   完整 `MAIN_SHA`、退出證據與 transition PR。未獲核准就停在 checkpoint。
4. 普通 session 依 `AGENTS.md` 建立圈外 transition PR，不呼叫三個 loop role。
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
8. 依「Control 更新的自動換代與人工 fallback」移動 runners、重跑四項 dry-run。
9. 若使用了 `loop:paused`，由使用者移除。先保持 `events` 停止，手動執行一次
   dispatcher；確認它留下有效派工與唯一 `loop:queued` 後，才啟動完整四 component
   並人工觀察第一圈 dispatcher → coder → reviewer → dispatcher。

需要逐項唯讀核對時，由人類明確呼叫 `$emmet-gate-auditor`；它不留言、不改 label、
不派工，也不替人類核准 transition。

## 前置條件

開始前確認：

1. `origin/main` 上的 `AGENTS.md`、curriculum 與 Meta Issue #1 對 active gate 的描述
   一致。
2. `codex` 與 `gh` 已登入；帳號能 fetch repository、讀寫 loop labels／comments 及
   操作 PR。
3. Dedicated launcher control worktree 與三個 trusted runners 都是本 repository 的
   乾淨 linked worktree，不含候選 PR 內容，也不拿來 checkout task branch。
4. Meta Issue #1 沒有 `loop:paused`。
5. Lifecycle launcher 從最新 `origin/main` 的 dedicated control worktree 載入，四個
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
或 session 已存在就 fail closed，不會偷偷啟動第二份。要把既有的手動四終端部署
換成 tmux，或在 control input 合併後更新並重開，使用：

```bash
./scripts/codex-loop tmux restart
```

`restart` 先在不停止現有 component 的前提下完成 control worktree bootstrap 與驗證；
失敗時不碰既有 session。之後才停止 event manager，再停止 dispatcher、coder、
reviewer，等待各自釋放 lock；接著清除本 launcher 擁有的舊 session、建立缺少的
dedicated runner、拒絕不乾淨 runner，並把三個 runner 切到同一個 `origin/main`。
四項預檢通過後才清掉 stale socket 並開 tmux；三個 agent socket 都 ready 之後，
右下角 event manager 才會啟動。

預設 session 名稱是 `emmet-qt-book-loop`，版面固定為：

| 位置 | component |
| --- | --- |
| 左上 | dispatcher agent |
| 右上 | coder agent |
| 左下 | reviewer agent |
| 右下 | event manager |

每個 pane 的上邊框會持續顯示 component 名稱與目前狀態，不必先從捲動中的 JSONL
尋找最後一筆紀錄。例如：

- `dispatcher (等待事件)`：component 正常，尚未輪到 dispatcher。
- `coder (撰寫中：Issue #3)`：coder 的單輪 Codex child 正在處理該 Issue。
- `reviewer (審查中：PR #59)`：reviewer 正在審查該 PR。
- `events (正常：coder 執行中／Issue #3)`：event manager 仍正常輪詢，流程 owner
  正在工作。
- `events (等待使用者：gate transition)`：目前 `main` 已有 gate-exit checkpoint，
  沒有 WIP，manager 只輪詢 durable state、不啟動角色。
- `events (停滯：coder／Issue #3)` 或 `events (阻斷：...)`：推進需要恢復或人工
  注意；搭配該 pane 最新的 `operator-alert` 查完整證據。

Agent child 成功結束後標題回到 `等待事件`；非零 exit 或 timeout 會保留在標題中，
直到下一輪開始。Component 正常停止會顯示 `已停止`；若 pane 內程序非預期退出，
tmux 邊框會自動附加 `[已退出]`。Pane title 是易讀的即時摘要，不是 durable state；
跨重啟仍以 GitHub Issue、PR、label、留言與完整 SHA 為準。

### 右下角：流程健康與下一步

先看右下角 pane title 判斷正常、暫停、停滯或阻斷；需要原因與恢復條件時，再看
event manager 每次 poll 輸出的完整 `operator-status`。其中先讀
`health`、`blocking`、`owner`，再看 `current`、`next` 與 `attention`；
這和 `tmux status` 不同：後者只證明 process、session 與 runner 版本健康，
不能證明 workflow 正在前進。

| 畫面值 | 操作者判讀 |
| --- | --- |
| `health=healthy` | state 合法；依 `owner`／`next` 等待下一個 transaction |
| `health=running` | 一個 role 正在執行；先等待，不手動啟動第二輪 |
| `health=draining` | control inputs 已更新；manager 停止派送並等待目前 child 結束 |
| `health=rotating` | detached rotator 正在驗證、同步 control worktree／runners、preflight 與重建 session |
| `health=awaiting-user` | gate exit 已綁定目前 `main` 且沒有 WIP；等待使用者核准 transition |
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

啟動成功會 attach session；在 tmux 按 `Ctrl-b d` 只會 detach，四個 component
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

這四個命令只管理本機 process／tmux，不新增 durable workflow state、不安裝或啟用
主機 scheduler，也不新增／移除 `loop:paused`。若其他 client 仍可能 mutation，
先由使用者在 Meta Issue #1 加 `loop:paused`，完成重啟與 reconciliation 後再由
使用者移除。

三個 agent 每次收到事件才建立新的 `codex exec --ephemeral --json`。未指定 profile
時，launcher 先自動尋找 `$CODEX_HOME/loop-<role>.config.toml`，該角色檔不存在才找
共用的 `$CODEX_HOME/loop.config.toml`；兩者都不存在才使用 adapter 的 repo 角色預設：
dispatcher `gpt-5.6-sol/high`、coder
`gpt-5.6-sol/high`、reviewer `gpt-5.6-sol/xhigh`（Extra High），三者 verbosity 都是 low。
顯式 role profile 覆寫顯式共用 profile；顯式選擇又覆寫自動偵測。找到的 profile 在停止
舊 components 前與每次 wake 都必須存在且可解析，壞檔不會靜默退回。Repo 預設是最後的
trusted fallback，只有合併至 main 並完成換代後才生效。Codex 0.134.0 之後的 named profile 是獨立的
`~/.codex/NAME.config.toml`，其中可用 top-level `model` 與
`model_reasoning_effort` 同時固定模型與推理強度。

三個角色共用設定時沿用 `--profile`：

```bash
./scripts/codex-loop tmux restart --profile loop
```

模型或推理強度需要按角色分開時，建立三個 profile 檔，再分別指定：

```bash
./scripts/codex-loop tmux restart \
  --dispatcher-profile loop-dispatcher \
  --coder-profile loop-coder \
  --reviewer-profile loop-reviewer
```

`--dispatcher-profile`、`--coder-profile` 與 `--reviewer-profile` 會覆寫共用
`--profile`；沒有 role-specific override 的角色仍使用共用 profile，兩者都沒有
才使用 repo 角色預設。Launcher 會對每個角色的最終選擇執行 adapter preflight，
並在停止舊 components 前確認每個 profile 檔存在且 TOML 可解析，再把 mapping
寫進 owned tmux session；之後不帶參數的 `tmux status` 會在
`codex_profiles` 顯示實際 profile 名稱，`codex_role_configuration` 顯示每個角色來自
`repo-default`、`profile` 或舊 generation 的 `inherited`。Launcher 啟動前與 adapter
每次 wake 都重驗 profile 檔存在且 TOML 可解析，避免檔案消失時靜默退回 user config；
實際 model entitlement 仍由 Codex 啟動時驗證。
右下角 `events` 不啟動 Codex，也不使用模型。

Profile 檔格式與優先序見
[Codex profiles 官方文件](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles)。

以下 dedicated runner、預檢與四終端步驟保留為底層手動操作與故障診斷；一般啟停
優先使用上述 tmux 入口。

## 建立 dedicated trusted runners

以下範例假設 canonical checkout 位於
`/home/guojun/workspace/emmet-qt-book`。Launcher control worktree
`emmet-qt-book-loop-control` 由 `tmux start/restart` 自動建立與同步；下列只是在缺少時
建立三個 role runners：

```bash
git -C /home/guojun/workspace/emmet-qt-book fetch origin main --prune

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-dispatcher origin/main

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-coder origin/main

git -C /home/guojun/workspace/emmet-qt-book worktree add --detach \
  /home/guojun/workspace/emmet-qt-book-reviewer origin/main
```

路徑已存在時不要重建；先確認它確實是同一 repository 的乾淨 linked worktree。
Coder 的 task worktree 與 Reviewer 的 disposable candidate worktree 由各自 role
iteration 另外建立，不能把 trusted runner 當候選 worktree。

## 預檢

固定從 dispatcher runner 的 adapter 啟動四個 component，並把各角色指向自己的
trusted workdir：

```bash
ADAPTER=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop

"$ADAPTER" agent dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher --dry-run
"$ADAPTER" agent coder \
  --workdir /home/guojun/workspace/emmet-qt-book-coder --dry-run
"$ADAPTER" agent reviewer \
  --workdir /home/guojun/workspace/emmet-qt-book-reviewer --dry-run
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

開四個操作者可見終端。每個終端先設定相同的 `ADAPTER` 路徑；前三個先啟動
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
"$ADAPTER" events \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher \
  --interval-seconds 60 \
  --retry-seconds 1800 \
  --dispatcher-heartbeat-seconds 1800
```

`--interval-seconds` 是 GitHub polling 間隔；`--retry-seconds` 是同一 state 已 ACK、
但 manager 尚無法確認 iteration 已完成時，重新通知 owner 的間隔；
`--dispatcher-heartbeat-seconds` 是在途 state 的 dispatcher oversight 間隔。已完成卻
沒有 durable 進度的 iteration 會轉成 `stalled`，而目前 `main` 的 gate-exit checkpoint
會轉成 `awaiting-user`；`snapshot-incomplete` iteration 成功完成後也保持 blocker，直到
快照改變，未完成或非零退出才按 retry window 重試。三者都不會在成功後按 retry window
反覆喚醒原 owner。縮短 polling 會增加 GitHub API 使用量；
縮短 retry 或 oversight 間隔仍可能增加其他異常／在途狀態的 Codex 用量。若 socket
delivery 失敗，manager 不等 retry window，而是下一個 poll 再送。

每個角色只有一個常駐 agent。第二個同角色 process 會因 per-role `flock` 以 75
退出；第二個 event manager 也會因 `events` lock 以 75 退出。不同角色 socket 使用
同一個由 Git common-dir 派生的 runtime directory，因此從三個 linked worktree 啟動
仍能互相找到。

## 觀看完整訊息

Agent 以 `codex exec --ephemeral --json` 啟動每次 iteration。Codex JSONL stdout 與
stderr 直接繼承到目前終端，不經 buffer、摘要或檔案轉送；因此能即時看到
`thread.started`、`turn.*`、`item.*`、tool／command、agent message、error 與 usage。
角色 contract 只限制送回 model context 的 command output：成功用 compact summary，
失敗用 bounded diagnostics；不裁掉上述 JSONL lifecycle stream。Agent 自身也用 JSONL
記錄 waiting、收到的 event、child exit code 與 timeout。

需要同時保存畫面時，可由操作者在 repo 外寫 log：

```bash
"$ADAPTER" agent dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher \
  2>&1 | tee /tmp/emmet-loop-dispatcher.jsonl
```

Log 可能包含 private repository 路徑、Issue／PR 內容或命令輸出；不得提交 repository，
分享前先檢查秘密與 private data。

## 手動單輪診斷

事件架構之外仍保留 one-shot 相容入口，供部署前或故障時明確執行一輪：

```bash
"$ADAPTER" dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher
```

使用前先停止對應 agent，否則同一把 role lock 會回傳 75。不要用 cron、systemd
timer 或 App Scheduled Tasks 重複呼叫這個相容入口；連續運作只用 `agent` + `events`。

## Control 更新的自動換代與人工 fallback

一般圈外內容合併只讓 runner HEAD 暫時落後，不會觸發 restart；`tmux status` 的
`control_inputs_match=true` 表示 long-lived generation 仍安全。Control inputs
改變時，正常路徑會自動顯示 `draining`／`rotating`，完成後從 GitHub durable
state 恢復，不需人工先改 label 或重送事件。

下列手動程序只用於 gate transition 的人工 checkpoint，或 `rotation.state=failed`
且已依 `detail` 排除原因後：

每次 gate transition，或 `AGENTS.md`、`.agents/`、`.claude/`、`.codex/`、loop
協定、curriculum、authoring guide、adapter 等 control inputs 在 `main` 改變後：

1. 先停止 `events`，再以 Ctrl-C／SIGTERM 停止三個 agents。若 child 正在執行，
   agent 會把 signal 轉給整個 child process group，等待退出並釋放 lock。
2. 若仍可能有其他 client 工作，由使用者加 `loop:paused`。
3. 確認 runners 乾淨，再移到最新 `origin/main`：

   ```bash
   git -C /home/guojun/workspace/emmet-qt-book fetch origin main --prune
   git -C /home/guojun/workspace/emmet-qt-book-dispatcher switch --detach origin/main
   git -C /home/guojun/workspace/emmet-qt-book-coder switch --detach origin/main
   git -C /home/guojun/workspace/emmet-qt-book-reviewer switch --detach origin/main
   ```

4. 不乾淨或無法 switch 時停止，不得以 `reset --hard` 掩蓋未知變更。
5. 重跑四項 dry-run。Gate transition 依 checkpoint 程序先完成人工 Dispatcher 派工與
   唯一 `loop:queued` 核對；其他 control-input 更新則完成必要 reconciliation。
6. 準備重開完整 components 時執行 `tmux restart`；bootstrap 會以相同規則建立或同步
   `/home/guojun/workspace/emmet-qt-book-loop-control`。Control worktree 不乾淨或不屬於
   same-repo 時，launcher 會在停止既有 components 前 fail closed。

## 暫停、恢復與停止

全域 durable brake：

```bash
gh issue edit 1 --add-label loop:paused
```

Manager 看到 paused 後會通知三個 agents；paused event 本身不啟動 Codex。已在執行的
role 仍須依協定在任何 mutation 前重查 pause。確認安全後只由使用者恢復：

```bash
gh issue edit 1 --remove-label loop:paused
```

停止 `events` 只阻止這台主機送新事件，不是跨 client brake；`loop:paused` 才是所有
client 共用的 GitHub durable brake。完全停止時先終止 manager，再終止 agents。

## Exit、錯誤與恢復

| 狀況／code | 意義 | 處理 |
| --- | --- | --- |
| component 持續執行 | 正常等待／polling | 查看 JSONL log |
| `0` | 正常手動停止、dry-run 或有限測試完成 | 正常 |
| `75` | 同角色 agent／one-shot 已持鎖 | 不再啟動第二份；核對 holder PID |
| child `124` | 單次 iteration timeout | 停止 manager，檢查 durable state 後 reconciliation |
| component `2` | worktree、origin、control input、executable、socket 或 polling 預檢失敗 | 停止 loop 並修正部署 |
| `delivery-failed` | agent 不在線、拒絕或無 ACK | 啟動／修復 agent；manager 下次 poll 重試 |
| 其他 child exit | Codex 原始 exit code | 保存 log；下一 event 先 reconciliation |

Push、label、comment 或 merge 結果不明時，不靠 event delivery 成功推定 mutation 成功；
下一個 role iteration 必須先讀 GitHub durable state，不能盲目重試。
