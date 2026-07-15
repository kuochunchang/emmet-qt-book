---
name: emmet-loop-dispatcher
description: "Execute exactly one idempotent dispatcher iteration for the emmet-qt-book GitHub label loop. Use only when the user or the loop event manager explicitly invokes $emmet-loop-dispatcher to reconcile loop state, merge an approved PR, dispatch active-gate work, detect a gate exit, or report a block; never use for ordinary repository work."
---

# Emmet Loop Dispatcher

執行一次調度循環後立即結束。不要 sleep、建立排程、開始第二輪、改碼、寫稿或做內容審查。
只從與最新 `origin/main` 一致的 trusted runner 載入本 skill 與治理指令。

## 每輪固定開場

1. 先查 Meta Issue #1 的 `loop:paused`。存在就無副作用回報 paused 並結束。
2. 先 `git fetch origin main --prune --quiet`，記錄完整 `MAIN_SHA`；一律以該
   `origin/main` snapshot 上的治理文件判斷授權。
3. 完整讀取 [AGENTS.md](../../../AGENTS.md)、[loop 協定](../../../docs/agent-loop.md)、
   [curriculum](../../../docs/curriculum.md) 的 active gate 與
   [authoring guide](../../../docs/authoring-guide.md)。
4. 讀取 Meta Issue #1、active-gate Issues、open loop PR 的 live body、comments、labels、
   head SHA、base、draft 與 mergeability。三份治理真相不一致時 fail closed。

## 執行一個邏輯動作

1. 先做 reconciliation，再做任何新派工：
   - 強制每個 Issue／PR 最多一個 primary state label；把 `loop:blocked` 當暫停 overlay，
     所有正常選件與合併都排除 blocked。
   - 補齊已合併 PR 遺留的 Issue label、Meta 進度與 merge SHA。
   - queued 但缺有效派工留言、coding 但無可恢復分支／PR、互斥 labels 或其他半完成
     transaction，一律標 blocked 並留下可恢復說明。
   - 若 approved 的 `Reviewed-Head` 與目前 `headRefOid` 不同，機械性撤銷 stale approval，
     先留含 reviewed／current SHA 的穩定 reconciliation marker，再以期望的完整 label
     set 轉回 `loop:needs-review` 並重查；不要自行做品質裁決或在同輪處理第二件事。
2. 在派工前判斷 active gate 退出條件。證據已齊就彙整完成 Issue／PR／merge SHA、通知
   使用者並結束；核准 transition 前不得派下一 gate。
3. 若有 unblocked `loop:approved` PR，執行合併前檢查：
   - 最新 Reviewer 裁決留言含目前完整 `Reviewed-Head` SHA，且 `Reviewed-Base` 等於
     最新 `origin/main`；任一 SHA 過期都轉回 `loop:needs-review`；
   - PR base 是 `main`、非 draft、可合併，且只有 `loop:approved` 這個 primary label；
   - PR、派工留言與 live Issue 都屬目前 active gate；
   - 再查一次 pause 與 head SHA，兩者都未改變。
   全部成立才 squash merge；否則 fail closed 或依協定恢復狀態。
4. 沒有在途或 blocked 工作、gate 尚未退出且仍有 gate-scoped 工作時，才讀完整 Issue
   與既有證據，先留下署名派工留言，再加 `loop:queued`。跨 gate bundle Issue 不能以
   open／closed 判定本輪範圍是否完成。
5. 依協定處理停滯、退件上限與圈外 PR。以 label timeline 的狀態時間判定停滯，
   不使用整個物件的 `updatedAt` 代替。

每輪只完成一個可稽核的邏輯動作；留言使用 `— Dispatcher`。Gate transition 只通知，
永不代替使用者核准或執行。結束時摘要 `role`、`result`、`object`、`main_sha`、
`head_sha` 與 `mutations`；`result` 使用穩定 kebab-case，供 agent JSONL log 稽核。
