# 未來轉運站 · 音檔內容閱讀版

臺大 TPIDC《未來轉運站》Podcast 129 集的個人閱讀整理（非官方）。內容依完整官方音檔機器轉錄後逐段整理，機器辨識仍可能有誤，各集另列校對註記；策略管理關聯為整理者解讀。音檔與影片連結指向官方 SoundOn 與 YouTube。

本 repo 只包含建置後的 `index.html`；來源筆記與逐字稿不在此處。更新方式：在工作目錄執行 `.venv/bin/python build_reader.py`，再 commit 本 repo 的 `index.html`。

## 自動更新（此 fork）

`.github/workflows/update.yml` 每週六 09:00（台灣時間）檢查官方 SoundOn RSS，若有新集數就下載音檔、以 faster-whisper 轉錄、交給 Claude 依既有格式整理，再寫回 `index.html` 並 commit。需在 repo 的 Settings → Secrets and variables → Actions 設定 `ANTHROPIC_API_KEY`；也可在 Actions 頁面手動執行。腳本為 `scripts/update_episodes.py`（`--from-json DIR` 可併入人工整理好的集數）。
