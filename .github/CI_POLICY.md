# 共有CIポリシー

このリポジトリでは、ローカルCIを先に実行し、GitHub Actionsは最終マージ前の環境差確認に限定する。

## ローカルCI

```bash
scripts/run_ci_local_quiet.sh
```

- runnerは同じ作業フォルダで一つだけ実行する。
- 後続runnerはownerを再監視せず、exit 75で終了する。
- 成功時は件数と所要時間だけを表示し、全文ログは一時領域へ保存する。
- 失敗時だけ短いエラー周辺を確認する。
- 現在は製品コマンドが未設定なので、ポリシー検査のみを実行する。

## token削減

- subagent分割は、過去の品質合格済み実測があり、単独より削減できる場合だけ許可する。
- 長いログ、同じ状態のポーリング、同一入力のCI再実行を避ける。
- 未知・欠測のtoken値を削減実績として扱わない。

## GitHub Actions

- workflowは手動起動、1 workflow・1 job・直列stepsを基本とする。
- 短い検査を複数jobへ分割しない。
- 第三者Actionは固定SHAを使う。
- GitHubが未設定の間は `github.enabled: false` を維持する。
- GitHub Actionsを直接起動せず、将来は `scripts/run_pre_merge.sh` を入口にする。
