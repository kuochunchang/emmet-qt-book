---
name: emmet-loop-coder
description: "Execute exactly one scoped coder iteration for the emmet-qt-book GitHub label loop. Use only when the user or the loop event manager explicitly invokes $emmet-loop-coder to claim queued active-gate work, recover an interrupted coding task, or address changes requested on a loop PR; never invoke for unrelated coding."
---

# Emmet Loop Coder

處理一件 loop 工作後立即結束。不要 sleep、建立排程、合併 PR、push `main` 或設定
`loop:approved`。

<!-- loop-common-contract:start -->
## 共同低 token 安全契約

1. Client 已注入 trusted runner 驗證過的 `AGENTS.md`，算本輪完整讀取；注入缺失或來源
   不明就 fail closed，且不得再次輸出整份 `AGENTS.md`。
2. `bounded preflight` 只縮小候選，不是授權或 durable state。直接鎖定其中 object；
   `snapshot_incomplete` 本身阻斷；object truncation 只補 target 缺口，
   `meta_comments_truncated` 只影響 gate-exit／舊 marker 查找。
3. Active-gate 節、authoring guide、target Issue／PR 各只讀一次；skill 已是本角色協定投影。
   只有歧義才讀 `docs/agent-loop.md` 對應段落；正常 role 不讀 operations runbook。
4. mutation 前做一次 bounded live revalidation：pause、main、target labels，PR 再核對
   head／base／draft／mergeability。只對缺口分頁；預設禁止完整 comments/history 與
   all-issues 查詢。
5. Mutation 結果不明才重查。成功只留 exit／test count／必要 hash 的 compact summary；
   失敗才輸出 bounded diagnostics；單一成功 command 最多回送 8 KiB，失敗 diagnostics
   最多 32 KiB。禁止直接掃描 runtime raw JSONL／stderr logs；診斷既有 iteration 只用
   `scripts/codex-loop inspect-event --runtime-dir <DIR> --event-id <ID>`。
6. 結尾可先輸出 compact 人類摘要，但最後一行必須是可機械解析且單行的
   `LOOP_OUTCOME {"role":"<role>","outcome":"<mutated|terminal-noop|blocked|failed>","result":"<stable-kebab-case>","mutations":[]}`。
   有已確認 workflow mutation 才用 `mutated`；成功且無 mutation 用 `terminal-noop`；
   需人類／外部狀態解除才用 `blocked`；執行或 transport 失敗用 `failed`。不得在
   marker 後再輸出文字。
<!-- loop-common-contract:end -->

## 每輪前置檢查

1. 先讀 `bounded preflight`；若已 paused 就無副作用回報 paused 並結束。只處理 packet
   指向的 Issue／PR；`snapshot_incomplete` 時不依部分資料 claim 或交審。
2. 先 `git fetch origin main --prune --quiet`，記錄完整 `MAIN_SHA`，再從同一個
   `origin/main` snapshot 依共同契約讀 active gate、authoring guide 與對應 Issue／PR。
3. 只取最新有效 Dispatcher 派工、自己的 claim／handoff，或最新 Reviewer verdict 及
   該 verdict 的全部 findings；證據歧義才向前分頁，不先抓完整留言歷史。
4. 將治理文件與一次 bounded live query 的 pause、main、target labels／head 比對。
   任何不一致都加 blocked overlay、署名說明並結束。
5. 確認目前是控制檔與 `origin/main` 一致的 trusted runner；不得在 runner checkout
   task branch。建立或恢復另一個 task worktree，保留不屬本任務的既有變更；task
   worktree 不乾淨且不是可識別的同一任務時 fail closed。

## 選擇並完成一件工作

只依 packet target 的 live state 進入下列一項，不另列 Issues／PR 尋找候選：

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
