# 作業手順

## 初期状態

このリポジトリ自身の検査は `.localci/product-commands.json` に登録し、Router→Executorを通して実行する。導入先では同じschemaのmanifestに製品コマンドを登録する。

```bash
scripts/run_ci_local_quiet.sh
python3 scripts/policy_verify.py
python3 -m unittest discover -s eval -p 'test_*.py'
```

ポリシー検査だけの成功を製品テストの成功と混同しない。登録されたコマンドと実行結果はCI summaryで確認する。

## ブランチ

`main → integration/<目的> → feature/<変更>` の順に進める。featureの作業中はPRを作らず、完全ローカルCI成功後にintegrationへ統合する。

1回の作業は、同じ目的・責任範囲の実装、テスト、ドキュメント、運用確認までをまとめて進める。細かな次作業ごとに停止せず、直接関係する不足機能や回帰修正も安全な範囲で同じfeature内に取り込み、ローカルCIまで完了させる。目的や責任範囲が変わる作業だけは、下位feature branchまたは別worktreeへ分離する。

「次にするべきこと」の提案も同じ単位で行う。提案には、直後に行う作業、続けて行う作業、その次の作業、完了条件を実行順に含める。単発の編集やテストだけではなく、現在の目的に直接関係する実装・テスト・ドキュメント・運用確認を連続した作業パッケージとして示し、可能な限り同じfeature内でローカルCIまで完了させる。

作業パッケージの完了後は、そこから直接つながる次の作業、その次の作業も、目的達成または安全な区切りまで連続して進める。承認不要の後続実装、回帰テスト、ドキュメント更新、運用確認を細かな報告単位で止めない。ユーザー判断、外部承認、目的変更、破壊的変更、または安全に進める情報不足がある場合だけ停止して確認する。

`localci hooks install`でpre-commit/pre-push hookを有効化する。ブランチは任意の深さで作成でき、各ブランチの直属の親を`localci branch register --parent PARENT --branch CHILD`で管理する。各commit/pushでは直属の親との差分だけをlocalCIへ渡す。複数の子孫ブランチを親へ順次統合した後、親側でその上流との差分を再検査する。integrationからmainへ統合する前は`localci merge-check --base main --profile full`を実行する。

## 作業モード

作業規模に応じて、軽量モードと統合モードを使い分ける。判定は`localci mode --json`で読み取り専用に行い、worktreeやbranchを自動作成・削除しない。

判定とCI実行を一度に行う場合は`localci start`を使う。軽量モードではfetch・worktree作成・追加branch作成を行わず、現在のcheckoutでquick profileだけを実行する。

```bash
localci start --mode auto
localci start --mode lightweight --dry-run --json
```

軽量モードは小規模変更に適用し、現在のcheckoutとfeature branchで`quick` profile（install・test・typecheck・build）を実行する。専用worktreeや追加branchは必須にしない。

統合モードは、20ファイル超の変更、`integration/*`または`release/*` branch、共有CI設定・lockfile・Dockerfileの変更、Docker・外部service要求、製品コードからintegration/package/coverage/full CIへ影響する変更に適用する。下位feature branch、必要に応じた正式worktree、`full` profile、`plan → execute → merge-check`を使用する。

ローカルCIが安定したcheckoutは、`localci worktree promote --min-successes N`で正式worktreeへ昇格できる。未コミット変更は一時snapshot commitへ保存され、元checkoutを残したまま`worktree/<branch>`が作成される。親branch登録・hooks導入・registry保存まで完了したworktreeを以後の標準作業場所とする。

統合済みworktreeの整理は`localci worktree retire`で候補を確認してから行う。実際の削除には`--apply`が必要で、正式branchを削除する場合だけ追加で`--delete-branch`を指定する。dirty worktreeへの強制削除は`--force`を明示した場合だけ許可する。

整理前は`localci worktree list --json`でdirty状態、重複branch、stale registry、削除候補のチェックサマリーと警告を確認する。dirtyまたはstaleな候補は、原因を解消してから`retire`へ進む。

候補に統合後の再検査警告がある場合、`--apply`だけでは整理を実行しない。表示された`localci merge-check`を`--run-rechecks`で実行し、全件成功した場合だけworktreeを整理する。

## 製品CIの追加

製品コードを追加したら `.agent-ci-policy.yml` の `commands` に、依存導入・テスト・型検査・buildを設定する。設定後も、ポリシー検査と製品検査を同じ静音runnerから実行する。

## OSS配布・導入

汎用配布物、対応OS、MITライセンス、act backendの固定版は [`oss/README.md`](oss/README.md) と [`LICENSE`](LICENSE) を参照する。localCI本体はMITで配布し、actは同梱せず任意依存として扱う。actを利用する環境では `.localci/backend.lock` の版と一致することを検査し、Docker daemonが使えるときだけ選択する。

導入先では `.localci/product-commands.example.json` をコピーして製品manifestを作成し、`.agent-ci-policy.yml`にはRouterのprofileやGitHub連携などポリシー設定を記述する。sample productの4段階を試す場合は、専用manifestを使う。

```bash
python3 scripts/validate_command_manifest.py .localci/sample-product-commands.json
./localci run --profile standard --backend host --inventory .localci/sample-product-commands.json
```

macOS/Linuxは `scripts/run_ci_local_quiet.sh`、Windowsは `scripts/run_ci_local_quiet.ps1` を入口とする。hookは `./localci hooks install` で `.localci/hooks` に設定する。GitHub Actionsはmain宛てPull Requestとworkflow_dispatchで起動し、`shared-ci`をmain保護の必須status checkにする。通常のfeature/integration pushでは起動しない。
