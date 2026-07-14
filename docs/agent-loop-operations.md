# 三角色 agent loop 操作指南

本專案提供三個 Codex 一次性角色 skill：

- `$emmet-loop-dispatcher`：reconciliation、派工、合併已核准 PR，以及彙整 gate
  退出證據。
- `$emmet-loop-coder`：認領或恢復一件派工、實作、驗證並交審。
- `$emmet-loop-reviewer`：獨立驗證 integration candidate，發布綁定 head／main SHA
  的裁決。

本文件是給操作者使用的部署與喚醒指南，不是第四份狀態機。角色權限、label、
狀態轉移與安全規則以 [`agent-loop.md`](agent-loop.md) 為正本；目前允許
工作的 gate 以 [`AGENTS.md`](../AGENTS.md) 與
[`curriculum.md`](curriculum.md) 為準。

## 執行模型

每次 role wake 只執行一輪，最多完成一個主要狀態轉移，然後退出。連續運作由角色
外部的 scheduler 反覆喚醒：

```text
external scheduler
  -> dispatcher
  -> coder
  -> reviewer
  -> dispatcher
  -> 等待後重複
```

角色內不得使用 `sleep`、輪詢、遞迴啟動或常駐 shell loop。沒有符合條件的工作時，
角色回報 no-op 並退出；GitHub Issue、PR、label、留言與完整 commit SHA 保存跨輪狀態。

自動閉環只在目前 active gate 內運作。Dispatcher 發現 gate 退出證據已齊時會停止派工
並通知使用者；只有使用者明確核准、gate-transition PR 合併、Meta Issue #1 完成同步，
且三份治理真相一致後，下一 gate 才能開始。

## Gate transition：人類 checkpoint

Gate 退出不是一般 loop state transition，而是刻意保留給使用者的控制點。Dispatcher
在 Meta Issue #1 留下 gate 退出證據後停止派工；此時沒有 `loop:*` primary state 通常
是正常的等待狀態，不代表 coder 或 reviewer 故障。新 gate 只有在 `main` 的
`AGENTS.md`、curriculum 與 Meta Issue #1 三者一致後才生效。

```text
active-gate loop
  -> dispatcher 彙整退出證據並停止派工
  -> 人類 checkpoint：核准 transition
  -> 圈外 transition Issue／PR
  -> transition PR 合併至 main
  -> Meta #1 同步 + 三方一致性核對
  -> trusted runners 同步至新 main
  -> dispatcher 派出新 gate 的第一個 slice
  -> coder -> reviewer -> dispatcher
```

### 如何辨識 checkpoint

同時符合下列條件時，把目前狀態視為 gate checkpoint：

1. Dispatcher 已在 Meta Issue #1 留下綁定完整 `main` SHA 的 gate-exit 證據。
2. Curriculum 的目前 gate 退出條件已由合併到 `main` 的 Issue／PR／驗證證據滿足。
3. 沒有任何未完成或 blocked 的 loop Issue／PR，也沒有殘留 primary／blocked labels；
   下一 gate 的工作尚未被派工。
4. `AGENTS.md`、curriculum 與 Meta Issue #1 仍宣告舊 gate，或 transition 尚未完成。

若有半完成 transaction、互斥 labels、stale approval 或無法解釋的在途工作，這不是
checkpoint；先喚醒 dispatcher 做 reconciliation，不能直接進行 gate transition。

### 通用處理程序

1. 停止外部 timer 或停用 App Scheduled Tasks，避免 checkpoint 期間反覆 no-op 與
   浪費額度。若仍可能有其他 client 喚醒角色，由使用者在 Meta Issue #1 加上
   `loop:paused` 作全域煞車。
2. 使用者明確核准從 `<OLD_GATE>` 前進到 `<NEW_GATE>`，並以獨立 Issue 追蹤
   transition。Issue 應連結 Dispatcher gate-exit marker、完整 `MAIN_SHA`、退出證據與
   transition PR；未獲核准就停在 checkpoint。
3. 使用普通、明確授權的 Codex／Claude session 建立或完成圈外 transition PR；不要
   呼叫 dispatcher／coder／reviewer。該 PR 依 `AGENTS.md` 同步更新
   `AGENTS.md` 與 curriculum 的 active gate、前一 gate 證據及允許／禁止範圍。
4. Transition PR 必須 base=`main`、非 draft、保持無 `loop:*` label，且 tracked diff
   只包含使用者核准的治理範圍。使用者確認核准仍適用於目前完整 head SHA，並核對
   驗證與 mergeability 後，可在最後一次重查 head 後立即由 GitHub UI 合併，或明確
   授權普通 session 使用：

   ```bash
   gh pr merge <TRANSITION_PR> --squash --delete-branch \
     --match-head-commit <FULL_HEAD_SHA>
   ```

   不得使用 auto-merge。Dispatcher 不得替 transition PR 加 `loop:approved`，也不得
   利用 loop 的自動合併例外合併它。
5. 合併後立即更新 Meta Issue #1：寫入新 active gate、前一 gate 完成證據、
   transition PR merge SHA 與新 gate 的下一步。完成 transition Issue 的 checklist、
   留下三方核對證據，再依 repository 規則關閉 transition Issue。
6. 從最新 `origin/main` 核對 `AGENTS.md`、curriculum 與 Meta Issue #1 完全一致；在此
   之前不得宣稱新 gate 生效，也不得派下一 gate。
7. 依下方「Gate 或治理文件更新後同步 runner」停止喚醒、將所有 detached trusted
   runners 移到最新 `origin/main`，確認乾淨與 control inputs 一致，再對三個角色
   重跑 `--dry-run`。
8. 若使用了 `loop:paused`，由使用者移除；先保持 timer 或 Scheduled Tasks 停止，
   單獨喚醒 dispatcher。只有看到署名派工留言，且目標 Issue 的唯一 primary state 是
   無 `loop:blocked` 的 `loop:queued`，才可喚醒 coder。若 dispatcher 本輪只完成
   reconciliation 或安全 no-op，先處理或確認該結果，再只喚醒 dispatcher；不得跳到
   coder。
9. 第一個 slice 依序手動觀察 `dispatcher -> coder -> reviewer -> dispatcher`。Reviewer
   退件時回到 `coder -> reviewer`；只有 `loop:approved` 才由 dispatcher 合併。第一圈
   durable state 與合併結果都正確後，才恢復 timer 或 Scheduled Tasks。

### W1-G0 到 W1-G1 對照

| 通用項目 | 本次對照 |
| --- | --- |
| 舊 gate | `W1-G0` |
| 退出證據 | Issue #8／PR #37 與 Issue #7／PR #39；Meta #1 的 Dispatcher gate-exit 留言 |
| Transition 追蹤 | Issue #43 |
| 圈外 transition PR | PR #44；不得加 `loop:*` label，也不得由 dispatcher 合併 |
| 新 gate | `W1-G1`，必須等 PR #44 合併及 Meta #1 三方同步後才生效 |
| 新 gate 第一個 bundle | Issue #2；Issue #7 只隨章更新並保持開啟 |

在 PR #44 與 Meta #1 transition 尚未完成前，重複喚醒 dispatcher 只應安全停止或
no-op；coder 與 reviewer 沒有工作。三方一致且 runners 同步後，第一個 dispatcher
才可依 `W1-G1` 從 Issue #2 派出一個受限 slice，而不是一次派完整 gate。

不要用下列捷徑跨過 checkpoint：把 feature branch 的 gate 文字視為已生效、替
transition PR 補 loop labels、直接喚醒 coder、預先 queue 下一 gate，或在 trusted
runners 尚未同步時恢復 scheduler。

## 前置條件

開始前確認：

1. `origin/main` 上的 `AGENTS.md`、curriculum 與 Meta Issue #1 對 active gate 的描述
   一致。
2. `codex` 與 `gh` 已登入，且執行帳號能 fetch repository、讀寫 loop labels／comments
   及操作 PR。
3. Trusted runners 是本 repository 的 linked worktree，不含候選 PR 內容，也不拿來
   checkout task branch。
4. Meta Issue #1 沒有 `loop:paused`。
5. 主機排程由使用者另行核准、建立與啟用；repository 不會自行安裝 scheduler。

## 建立 dedicated trusted runners

以下範例假設主要 checkout 位於 `/home/guojun/workspace/emmet-qt-book`。三個 runner
都使用 detached `origin/main`，讓操作者平常使用的 checkout 不會成為候選內容或控制
指令來源：

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

## 預檢與手動執行

從 dispatcher runner 的 adapter 指定每個角色的 trusted workdir：

```bash
ADAPTER=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop

"$ADAPTER" dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher --dry-run
"$ADAPTER" coder \
  --workdir /home/guojun/workspace/emmet-qt-book-coder --dry-run
"$ADAPTER" reviewer \
  --workdir /home/guojun/workspace/emmet-qt-book-reviewer --dry-run
```

若符合上方 gate checkpoint 條件，不要接著照抄完整四步週期。先完成 transition、
三方同步、runner 更新與 dry-run；保持 scheduler 停止，且只先喚醒 dispatcher。等它
留下有效派工與 `loop:queued` 後，才喚醒 coder。

三項都通過後，可先手動執行一個完整週期：

```bash
"$ADAPTER" dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher
"$ADAPTER" coder \
  --workdir /home/guojun/workspace/emmet-qt-book-coder
"$ADAPTER" reviewer \
  --workdir /home/guojun/workspace/emmet-qt-book-reviewer
"$ADAPTER" dispatcher \
  --workdir /home/guojun/workspace/emmet-qt-book-dispatcher
```

Timer 啟用後不要另外手動喚醒角色；需要手動測試時，先停止 timer 與正在執行的
cycle，避免繞過 systemd 提供的跨角色順序化。

Adapter 會先 fetch `origin/main`，驗證 repository identity、origin、control inputs 與
role skill，再啟動一次 `codex exec --ephemeral`。它不使用 dangerous bypass。

## 以 user-level systemd 連續喚醒

下列配置是操作者可選擇安裝的範例，不是 repository 安裝程序。它把四次 one-shot wake
放在同一個 `Type=oneshot` service，確保同一 cycle 依序完成；timer 只會在前一 cycle
結束後再次觸發。

建立 `~/.config/systemd/user/emmet-loop-cycle.service`：

```ini
[Unit]
Description=Emmet book Codex agent loop cycle

[Service]
Type=oneshot
Environment=HOME=/home/guojun
Environment=PATH=/home/guojun/.local/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=/home/guojun/workspace/emmet-qt-book-dispatcher

ExecStart=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop dispatcher --workdir /home/guojun/workspace/emmet-qt-book-dispatcher
ExecStart=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop coder --workdir /home/guojun/workspace/emmet-qt-book-coder
ExecStart=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop reviewer --workdir /home/guojun/workspace/emmet-qt-book-reviewer
ExecStart=/home/guojun/workspace/emmet-qt-book-dispatcher/scripts/codex-loop dispatcher --workdir /home/guojun/workspace/emmet-qt-book-dispatcher

SuccessExitStatus=75
TimeoutStartSec=infinity
```

建立 `~/.config/systemd/user/emmet-loop-cycle.timer`：

```ini
[Unit]
Description=Periodically wake the Emmet Codex agent loop

[Timer]
OnBootSec=2min
OnUnitInactiveSec=30min
Unit=emmet-loop-cycle.service

[Install]
WantedBy=timers.target
```

先驗證 unit，再由使用者明確啟用：

```bash
systemd-analyze --user verify \
  ~/.config/systemd/user/emmet-loop-cycle.service \
  ~/.config/systemd/user/emmet-loop-cycle.timer

systemctl --user daemon-reload
systemctl --user enable --now emmet-loop-cycle.timer
systemctl --user start emmet-loop-cycle.service
```

檢查 timer 與執行紀錄：

```bash
systemctl --user list-timers emmet-loop-cycle.timer
journalctl --user -u emmet-loop-cycle.service -f
```

`OnUnitInactiveSec=30min` 是起始建議，不是協定要求。頻繁 no-op 仍會消耗 Codex 額度；
可依任務量調整，但不得讓不同 cycle 或同一角色重疊。

## Codex App Scheduled Tasks

若使用 Codex App Scheduled Tasks，每個角色建立一個錯開時間的 task，prompt 直接明示：

```text
Use $emmet-loop-<role> to execute exactly one idempotent iteration for this
repository, then stop. Do not sleep, poll, schedule another run, or start a
second iteration. If no safe action is available, report no-op and exit.
```

App 已負責喚醒時，不要在 scheduled task 裡再執行 `scripts/codex-loop`。只有在 App
明確保證同一 task 不重疊時才直接排程 skill；否則使用 CLI adapter 與外部 scheduler。
Scheduled Task 必須指向 dedicated trusted runner，不得指向候選 PR worktree。

OpenAI 官方說明：

- [Scheduled tasks](https://developers.openai.com/codex/app/automations)
- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)

## Gate 或治理文件更新後同步 runner

Adapter 會 fetch，但不會自動移動 detached runner。每次 gate transition，或
`AGENTS.md`、`.agents/`、`.claude/`、`.codex/`、loop 協定、curriculum、authoring
guide、adapter 等 control inputs 在 `main` 改變後，先停止 timer，再同步三個 runner：

```bash
systemctl --user stop \
  emmet-loop-cycle.timer emmet-loop-cycle.service

git -C /home/guojun/workspace/emmet-qt-book fetch origin main --prune
git -C /home/guojun/workspace/emmet-qt-book-dispatcher switch --detach origin/main
git -C /home/guojun/workspace/emmet-qt-book-coder switch --detach origin/main
git -C /home/guojun/workspace/emmet-qt-book-reviewer switch --detach origin/main
```

任一 runner 不乾淨或無法 switch 時停止處理，不得以 `reset --hard` 掩蓋未知變更。

同步後先保持 scheduler 停止，重跑「預檢與手動執行」的三項 dry-run。Gate transition
依上方 checkpoint 流程手動驗證第一圈後才恢復排程；其他 control-input 更新也要在
確認三個 runners 與最新 `origin/main` 一致後，才由使用者重啟 timer 或 Scheduled
Tasks。

## 暫停、恢復與停用

使用者可用 Meta Issue #1 的全域煞車阻止所有角色產生副作用：

```bash
gh issue edit 1 --add-label loop:paused
```

確認安全後由使用者恢復：

```bash
gh issue edit 1 --remove-label loop:paused
```

停用主機排程與正在執行的 cycle：

```bash
systemctl --user disable --now emmet-loop-cycle.timer
systemctl --user stop emmet-loop-cycle.service
```

`loop:paused` 與停止 timer 的效果不同：前者是所有 client 共用的 GitHub durable brake；
後者只停止這台主機的 systemd 喚醒。

## Exit codes 與告警

| Exit code | 意義 | Scheduler 處理 |
| --- | --- | --- |
| `0` | 完成一輪或安全 no-op | 正常 |
| `75` | 同角色已有 worker 執行 | 正常跳過，不告警 |
| `124` | 超過單輪 timeout | 告警、停用 timer 並檢查 durable state |
| `2` | worktree、origin、control input、Codex executable 等預檢失敗 | 告警、停用 timer 並修正部署 |
| 其他 | Codex child 原始 exit code | 保留 log 並調查 |

Adapter 只有 per-role `flock`；systemd 範例另外用單一 oneshot cycle 避免不同角色跨
cycle 重疊。遇到不明 push、label、merge 或 timeout 結果時，下一輪必須先依 GitHub
durable state reconciliation，不得盲目重試 mutation。
