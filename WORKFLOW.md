# 作業手順

## 初期状態

このプロジェクトには製品コードがないため、最初に実行できるのは共有CIポリシー自身の検査だけである。

```bash
scripts/run_ci_local_quiet.sh
python3 scripts/policy_verify.py
python3 -m unittest discover -s eval -p 'test_*.py'
```

`policy checks passed` はポリシー検査の成功を意味し、製品テストの成功を意味しない。

## ブランチ

`main → integration/<目的> → feature/<変更>` の順に進める。featureの作業中はPRを作らず、完全ローカルCI成功後にintegrationへ統合する。

## 製品CIの追加

製品コードを追加したら `.agent-ci-policy.yml` の `commands` に、依存導入・テスト・型検査・buildを設定する。設定後も、ポリシー検査と製品検査を同じ静音runnerから実行する。
