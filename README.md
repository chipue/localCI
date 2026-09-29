# localCI

共有CIポリシー `0.1.0` の初期配布物です。GitHubがまだない空のプロジェクトへ導入しても、ポリシー自身の検査をすぐに実行できます。

この版は製品固有のテストや依存関係を持ちません。製品CIを未設定のまま成功扱いしないため、初期状態では `product checks: not configured` と表示します。

## すぐに実行する

```bash
scripts/run_ci_local_quiet.sh
python3 -m unittest discover -s eval -p 'test_*.py'
```

現在はポリシー検査のみが設定されています。製品コードとテストを追加した後、`.agent-ci-policy.yml` の `commands` を設定してください。

## 固定版として配布する

このリポジトリを共通ポリシーの正本にし、利用側ではタグまたはcommit SHAを `.agent-ci-policy.lock` に記録します。更新時は、lockの版を変更してからポリシー検査と評価テストを実行します。

```bash
python3 scripts/policy_verify.py
python3 -m unittest discover -s eval -p 'test_*.py'
```

現時点ではGitHub remoteを前提にしていません。後からremoteを追加しても、初期のローカルCI運用はそのまま継続できます。

## GitHubを追加するとき

1. `.agent-ci-policy.yml` の `github.enabled` を `true` にする
2. `scripts/run_pre_merge.sh` にPR検査を接続する
3. `pre-merge.yml` のActionを確認済みcommit SHAへ固定する
4. GitHub側でmainへの直接pushを禁止し、最終検査を必須にする
