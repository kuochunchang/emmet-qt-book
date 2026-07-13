---
name: emmet-loop-coder
description: "Execute exactly one scoped coder iteration for the emmet-qt-book GitHub label loop. Use only when the user or a scheduled wake explicitly invokes $emmet-loop-coder to claim queued active-gate work, recover an interrupted coding task, or address changes requested on a loop PR; never invoke for unrelated coding."
---

# Emmet Loop Coder

處理一件 loop 工作後立即結束。不要 sleep、建立排程、合併 PR、push `main` 或設定
`loop:approved`。

## 每輪前置檢查

1. 先查 Meta Issue #1 的 `loop:paused`；存在就無副作用回報 paused 並結束。
2. 先 `git fetch origin main --prune --quiet`，記錄完整 `MAIN_SHA`，再從同一個
   `origin/main` snapshot 讀治理文件。
3. 完整讀取 [AGENTS.md](../../../AGENTS.md)、[loop 協定](../../../docs/agent-loop.md)、
   [curriculum](../../../docs/curriculum.md)、
   [authoring guide](../../../docs/authoring-guide.md) 與對應 GitHub Issue。
4. 將治理文件與 Meta Issue、live Issue 比對。任何不一致都加 blocked overlay、
   署名說明並結束。
5. 確認目前是控制檔與 `origin/main` 一致的 trusted runner；不得在 runner checkout
   task branch。建立或恢復另一個 task worktree，保留不屬本任務的既有變更；task
   worktree 不乾淨且不是可識別的同一任務時 fail closed。

## 選擇並完成一件工作

依序只選 unblocked 工作：

1. `loop:changes-requested` PR：讀最新 Reviewer 裁決及全部 findings，確認裁決的
   `Reviewed-Head` 與歷史一致，以 `gh pr checkout` 或 remote branch 恢復工作。
2. `loop:coding` Issue：若本角色先前已認領但 session 中斷，從派工留言、Issue 與 remote
   branch／PR 恢復；無法唯一恢復就標 blocked，不重做或另開任務。
3. `loop:queued` Issue：核對有效 Dispatcher 派工留言與精確 PR 範圍後，原子地把 primary
   state 從 queued 轉為 coding；認領留言以 `— Coder` 記錄 `Issue`、完整 `Branch`、
   `Claimed-Main` 與 slice，讓下一輪能唯一恢復。

完成所選工作時：

1. 在 task worktree 從最新 `origin/main` 建立或恢復聚焦分支；只修改派工範圍。
2. 對每個 finding 作技術判斷；不同意時以證據回覆，不盲改。
3. 執行派工列出的所有 task-specific oracle，再從 repo root 實跑 `./scripts/book-check`。
   任一未通過都不得交審。
4. 檢查完整 diff、未追蹤檔、秘密、範圍與 `Refs`／`Closes`；明確 stage、commit、push。
5. Push 後確認 GitHub `headRefOid` 等於本機完整 tested head，開立或更新 PR，記錄
   `Tested-Head`、`Based-On-Main` 與實跑命令／結果；`Based-On-Main` 是建立 tested
   head、或驗證前最後把它 rebase／merge 到其上的 main SHA，不是單純最後 fetch。
   重查 pause、最新 gate 與 PR head 後，讓新建或既有 PR 的唯一 primary state 成為
   `loop:needs-review`；中斷時下一輪依 handoff marker 補完，不重開 PR。
6. 無法安全繼續時保留目前 primary state、加 `loop:blocked` overlay，說明已嘗試事項與
   恢復條件。不要在 blocked 物件上繼續正常轉移。

未實際通過驗證不得宣稱通過；不得處理派工留言之外的工作。結束時摘要 `role`、
`result`、`object`、`main_sha`、`head_sha` 與 `mutations`；`result` 使用穩定 kebab-case。
