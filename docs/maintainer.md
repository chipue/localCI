# メンテナー向け情報

この文書は、共有CIポリシー本体を開発・配布するメンテナー向けの補足です。一般利用者向けの導入手順は、リポジトリ直下の`README.md`と`WORKFLOW.md`を参照してください。

OSSとして配布する汎用部品と設定テンプレートは`oss/`にまとめています。公開ライセンスはリポジトリルートの`LICENSE`にあるMIT Licenseです。

対応OSはmacOS 12以降、Linux、Windows 10/11です。WindowsではGit for Windowsの`bash.exe`を使用します。

## 配布物の初期状態

- ポリシー版は`0.1.0`。
- `.localci/product-commands.json`にlocalCI自身の評価コマンドを登録する。
- 導入先の製品コマンドは`.localci/product-commands.example.json`を複製して定義する。
- GitHub workflowは、GitHub側の連携を有効化するまで手動起動に限定する。

## メンテナー検査

```bash
python3 scripts/policy_verify.py
python3 -m unittest discover -s eval -p 'test_*.py'
scripts/run_ci_local_quiet.sh
```

sample productだけを個別に実行する場合は次を使います。

```bash
./localci run --profile standard --backend host \
  --inventory .localci/sample-product-commands.json
```

未設定のコマンドを成功扱いにしないでください。backend lock、対応OS、GitHub workflowの手動起動条件も評価テストで確認します。

## リリース前の確認

1. ポリシー版を更新する。
2. lockファイルと設定ファイルの整合性を確認する。
3. ポリシー検査と評価テストを実行する。
4. workflowのAction参照が固定SHAであることを確認する。
5. タグを作成し、利用側がlockで固定できる状態にする。
