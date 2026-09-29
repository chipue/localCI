# 共有CIポリシー

このリポジトリは共有CIポリシーの導入用スキャフォールドである。

- CIは `scripts/run_ci_local_quiet.sh` を入口にする。直接の重複実行、ポーリング、長いログの読み込みをしない。
- 作業は `main → integration/* → feature/*` の順に行い、`main`へ直接pushしない。
- subagent分割は、token削減の見込みを検証できる場合だけ行う。未測定なら単独で進める。
- 未設定の製品テストを成功と見なさない。現在はポリシー検査のみが実行可能である。
- 詳細は `.github/CI_POLICY.md`、`WORKFLOW.md`、`.agent-ci-policy.yml` を参照する。
