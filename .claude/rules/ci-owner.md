# CI owner rule

- 完全ローカルCIは `/run-local-ci` または `scripts/run_ci_local_quiet.sh` から一度だけ実行する。
- exit 75 は後続runnerである。ownerを待機・ポーリング・再実行せず、ownerが結果を報告するまでCI成功とは言わない。
- 長いログ全文を会話へ読み込まず、失敗時だけ短い抜粋を確認する。
