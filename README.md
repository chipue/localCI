# localCI

ローカル検査を本体にし、GitHubでは安全な受け渡しと最小限の統合だけを行うための共有CIポリシーです。

製品コマンドは`.localci/product-commands.json`へ登録します。manifestがある場合、静音CI入口からRouterがbackendを選択し、Local Executorが製品CIを実行します。manifestがない場合は製品CI未設定として終了コード2になり、成功扱いしません。

汎用のOSS公開部品と導入手順は[`oss/README.md`](oss/README.md)に分離しています。ライセンスはMITです。

対応OSはmacOS 12以降、Linux、Windows 10/11です。WindowsではGit for WindowsのGit BashとPowerShellを使用します。

## localCIの差別化

localCIは、クラウド上で検査を自動実行するだけのCIではありません。開発中の反復、同時実行の制御、main統合前の意思決定までを一つの運用として扱います。差別化の核は次の3点です。

| 差別化の核 | 一般的なCIで起きやすい問題 | localCIの対応 |
| --- | --- | --- |
| ローカル高速実行 | 小さな変更でもrunner起動、checkout、依存導入を待つ | `scripts/run_ci_local_quiet.sh`を開発者のPCで実行し、重い検査を先に完了させる |
| 重複実行防止 | 手動実行、pre-push hook、pushやPR更新で同じ検査が重なる | リポジトリ単位の共有ロックで同時実行を1つに制限し、後続実行は`exit 75`で終了する |
| 統合前の明示承認 | 通常のpushやPR更新でも外部CIが起動し、統合意図と無関係にコストが発生する | `scripts/run_pre_merge.sh`をmain統合直前だけ使い、対象commit・ブランチ・ローカルCI結果・明示承認を確認する |

この設計では、GitHub Actionsを置き換えるのではなく、役割を限定します。開発中の検査はローカルで素早く行い、GitHub Actionsはmain統合直前の環境差確認と受け渡しの検証だけに使います。

### 利用シナリオ

通常のfeature開発では、変更ごとに次を実行します。

```text
編集 → ローカルCI → 修正 → ローカルCI
```

push前にはhookから同じローカルCIが起動します。手動実行とhookが重なっても、共有ロックによって同じリポジトリのCIは一つだけが実行されます。

複数のfeatureをintegrationへまとめるときは、各featureのローカルCIに加えて、統合後のintegrationブランチでもローカルCIを実行します。

mainへ統合するときだけ、ユーザーの明示指示を受けて次を実行します。

```bash
scripts/run_pre_merge.sh <pull-request-number>
```

この入口は、PRのtargetが`main`であること、sourceが`main`ではないこと、ローカルのHEADとPRのcommitが一致すること、ローカルCI成功と統合承認が指定されていることを確認してから、GitHub Actionsを1回だけ起動します。

## 外部CI backend

localCI本体はMIT Licenseで提供し、GitHub Actions互換実行は外部backendとして扱います。最初の対応backendは[`nektos/act`](https://github.com/nektos/act)のMIT License版`v0.2.89`（commit `4f41128`）です。act本体は同梱せず、利用者の環境へ別途インストールします。

Dockerを起動したうえで、次のように実行します。

```bash
AGENT_CI_BACKEND=act scripts/run_ci_local_quiet.sh
```

既定では`ubuntu-24.04`に`catthehacker/ubuntu:act-24.04`を割り当てます。別のrunner imageを検証する場合は`AGENT_CI_ACT_PLATFORM_IMAGE`で指定できます。

通常のポリシーrunnerを使う場合は、環境変数を指定せずに実行します。

実行せずに、選択されるCI経路だけを確認する場合は次を使います。

```bash
./localci plan
./localci plan --profile quick
./localci plan --backend act
```

`plan`は製品コマンドを実行せず、選択されたprofile・backend・実行対象・除外理由だけを表示します。

変更ファイルに応じてテストを絞り込み、判定不能時は全体へ戻す安全網は`--diff`で有効化できます。

```bash
./localci plan --diff
./localci run --diff
AGENT_CI_DIFF=1 ./scripts/run_ci_local_quiet.sh
```

テスト系コマンドにはmanifestの`paths`と`depends_on`を宣言してください。設定変更、依存関係不明、差分未取得、対象不一致では自動的に`full` profileを選びます。

計画を表示したうえで、そのまま実行する場合は`run`を使います。

```bash
./localci run
./localci run --profile quick
./localci run --backend act
```

実行結果を共有するには、`localci run`へ`--report-json PATH`または`--report-html PATH`を追加します。JSON/HTMLには、stageごとの状態と所要時間、失敗抜粋、再実行候補、実際に使ったbackendをまとめます。既存の`--result-file`にも同じ`report`オブジェクトが含まれます。

重いテスト、依存環境の準備、検査ログの確認は開発者のPCで行います。GitHub Actionsは、子孫ブランチから`main`へ統合する直前にだけ、最終的な環境差確認として実行します。feature・integrationでの通常pushや作業中のPR更新では起動しません。

## 方針

- `main`への直接pushを禁止する
- `main`への統合はPull Request経由にする
- ローカルCIを高速な完全検査の本体にする
- 同じリポジトリのCIを同時に複数実行しない
- main統合前のGitHub Actions実行には明示承認を要求する
- PRには検査対象のコミットとローカルCI結果を紐付ける
- GitHub Actionsでは軽量な整合性確認だけを行う
- ポリシー本体は固定版として利用し、lockファイルでバージョンを固定する

## 利用側リポジトリへの導入

利用側リポジトリには、ポリシーの固定版とプロジェクト固有設定だけを置きます。

```text
project/
├── AGENTS.md
├── WORKFLOW.md
├── .agent-ci-policy.yml
├── .agent-ci-policy.lock
└── .github/workflows/pre-merge.yml
```

`.agent-ci-policy.lock`には、利用するポリシーのタグまたはcommit SHAを記録します。ポリシーを更新するときは、lockの値を変更し、ローカルCIと評価テストを実行してから変更を統合します。

## ローカルで実行する

導入後は、リポジトリのルートで次を実行します。

```bash
scripts/run_ci_local_quiet.sh
python3 -m unittest discover -s eval -p 'test_*.py'
```

`AGENT_CI_BACKEND=host|docker|act|wsl|windows`でbackendを固定でき、省略時は`auto`で過去履歴も考慮して選択します。`AGENT_CI_MANIFEST`と`AGENT_CI_PROFILE`でmanifest/profileも上書きできます。`auto`は全backendを起動せず能力検出だけを行い、選択したbackendだけを起動します。`docker`はLinuxコンテナ内で、`wsl`はWindows上のWSL Linux環境で製品コマンドを実行します。

backendの導入状況は`localci doctor`で確認できます。通常は利用可能なbackendだけを表示し、`localci doctor --all`で未導入adapterと不足理由も表示します。JSON連携には`localci doctor --json`または`localci doctor --all --json`を使います。`host`はlocalCI本体に含まれる必須backendで、Docker・act・WSL・Windowsは任意adapterです。

## ブランチ階層と統合CI

作業ブランチは`main → integration/* → feature/*`の順に作成します。初回だけhookを有効化します。

```bash
localci hooks install
```

この階層は固定3層ではなく、ひ孫以下も作成できます。直属の親を明示登録する場合は次のようにします。

```bash
localci branch register --parent integration/example --branch feature/example/router
localci branch register --parent feature/example/router --branch feature/example/router/graph
```

各ブランチのcommit/pushでは直属の親との差分だけをlocalCIへ渡します。親の自動判定が曖昧な場合は`LOCALCI_PARENT_BRANCH`を指定するか、`localci branch register`で`.localci/branch-parents.json`へ登録します。mainへの直接作業・直接pushはbranch policyで拒否します。

作業規模の判定は読み取り専用の`localci mode --json`で確認できます。小規模変更は現在のcheckoutで`quick` profile（install・test・typecheck・build）を使う軽量モード、共有設定・外部service・大規模変更は下位feature branch、必要なworktree、`full` profile、`merge-check`を使う統合モードとして提案されます。`localci mode`はbranchやworktreeを自動変更しません。

判定からCI実行までを一度に行う場合は`localci start --mode auto`を使います。軽量モードではfetch、worktree作成、追加branch作成を行わず、現在のcheckoutでquick profileだけを実行します。`--dry-run --json`を付けると実行せず、選択結果とGit変更なしの契約を確認できます。

CI失敗以外の原因候補をまとめて確認するには、利用者向け診断を実行します。Gitの作業ツリー、manifest、backend依存、Docker接続、ディスク容量、直近のCI履歴を読み取り専用で検査します。

```bash
localci doctor
localci doctor --all --json
```

integrationからmainへ統合する前は、統合後のintegrationブランチ上で全差分をfull profileで確認します。

```bash
localci merge-check --base main --profile full
```

### 正式worktreeへの昇格

localCIが安定してから、現在のcheckoutを残したまま正式worktreeへ昇格できます。成功履歴が1回以上あり、未コミット変更がある場合は一時snapshot commitへ保存してから新しいworktreeへ引き継ぎます。

```bash
localci worktree status --json
localci worktree promote --min-successes 3
```

昇格先は`worktree/<現在のbranch>`として作成され、親branch登録とhooks導入も自動で行われます。既存checkoutは削除せず、作成したworktreeの一覧は`~/.localci/worktrees.json`へ保存します。履歴ファイルやregistryの場所は`--history-file`、`--registry`または対応する環境変数で変更できます。

worktree構成を確認するときは、`list`がGitの実worktree状態とregistryを照合します。各branchの世代・配置・統合状態に加えて、dirty worktree、重複registry branch/formal branch/path、Gitから見えなくなったstale registryをJSONと警告で確認できます。統合済み正式worktreeは削除候補としてチェックサマリー付きで表示されます。

```bash
localci worktree list
localci worktree list --json
```

統合済みの正式worktreeは、まず候補だけを確認し、内容を確認してから明示的に整理します。

```bash
localci worktree retire
localci worktree retire --apply
localci worktree retire --apply --delete-branch
localci worktree retire --apply --run-rechecks
```

標準ではpreviewのみで、未統合・未登録・dirty worktreeは削除対象になりません。統合後の再検査が必要な候補を`--apply`で整理する場合は、生成された`merge-check`を`--run-rechecks`で先に実行します。`--force`はdirty worktreeも削除するため、必要な場合だけ使用してください。

実行した再検査と整理結果は通常のlocalCI履歴へ`worktree_retire`として保存されます。`localci history --kind worktree_retire --json`で、通過したmerge-checkと削除結果を確認できます。

branchやworktree単位の絞り込みと、再検査成功率の集計も利用できます。

```bash
localci history --branch feature/example --json
localci history --worktree /path/to/formal-worktree --json
localci history --kind worktree_retire --summary --json
localci history --summary --since 2026-09-01 --until 2026-09-30 \
  --branch-parents-file .localci/branch-parents.json --json
```

summaryにはbranch世代、親branch、worktreeごとの昇格・整理件数、再検査成功率が含まれます。`--until`へ日付だけを指定した場合は、その日を含む範囲として扱います。

共通runnerは、同じ作業フォルダでの重複実行を検出し、全文ログを一時領域へ保存します。成功時は要約だけを表示し、失敗時に必要なログを確認します。

`localci run`は実行ごとに`execution-record.json`をログディレクトリへ保存します。記録にはcommit、dirty状態、manifestのSHA-256と内容、OS・Python、backend lockとtoolの版、backend選択理由、コマンドごとの実行command・終了値・所要時間・ログパスを含めます。結果JSONの`record_file`から同じ記録を参照できます。再現性を保つため、manifestやコマンドへ秘密情報を直接書かないでください。

### 実行単位の隔離と後始末

`localCI run`の各manifestコマンドは、固有の実行スコープで起動します。スコープごとに`LOCALCI_RUN_ID`、`LOCALCI_EXECUTION_ID`、`LOCALCI_RUNTIME_DIR`、`LOCALCI_TEMP_DIR`、`LOCALCI_PORT_NAMESPACE`を注入し、実行中の一時ファイルとポート割当を他のコマンドから分離します。既存の環境変数や秘密情報は互換性のため引き継ぎますが、localCIが追加する値は実行IDで名前空間化されます。

Docker backendでは、コンテナに実行ID付きの名前とlabelを付け、`--rm`、専用runtime volume、`/tmp`のtmpfsを使います。成功・失敗・タイムアウト・Ctrl-Cによる中断のいずれでも、コマンドのプロセスグループ、生成workflow、Dockerコンテナ、一時runtimeディレクトリを後始末します。サービスアダプタへスコープを渡す場合も、ポートとコンテナ名が同じ実行IDで分離され、`finally`で停止されます。

デバッグが必要な場合だけ`LOCALCI_RUNTIME_DIR=/path/to/runtime`で一時runtimeの親ディレクトリを指定できます。実行終了後は個別スコープが削除され、コマンドログだけが`AGENT_CI_LOG_DIR`または`--log-dir`に残ります。

初回はローカルhookを有効化します。以後、push前にローカルCIが自動実行され、失敗したpushは止まります。

```bash
scripts/install_local_hooks.sh
```

CI以外の出力が長くなり得るコマンドは、全文ログを一時領域へ保存するwrapperを使います。

```bash
scripts/run_command_quiet.py -- git status
```

ログと共有状態の保存先は、必要に応じて次の環境変数で変更できます。

```bash
export AGENT_CI_LOG_DIR=/path/to/ci-logs
export AGENT_CI_SHARED_DIR=/path/to/ci-shared
```

## 製品CIを設定する

製品コードを持つリポジトリでは、`.agent-ci-policy.yml`の`commands`に、依存導入・テスト・型検査・buildを設定します。

```yaml
commands:
  install: <依存導入コマンド>
  test: <テストコマンド>
  typecheck: <型検査コマンド>
  build: <buildコマンド>
  full_ci: null  # test/typecheck/build の代わりに総合検査を使う場合に設定
```

ローカルCIは、設定された`install`を先に実行します。`full_ci`を設定した場合は、それを`test`・`typecheck`・`build`の代わりに実行します。`full_ci`が未設定なら、設定された3コマンドを順に実行します。未設定のコマンドは成功扱いにせず、実行対象外として表示します。

### CIキャッシュ

依存関係やビルド成果物を再利用するコマンドには、manifestの`cache`を指定します。`paths`が保存・復元対象、`key_files`がキー計算対象です。コマンド、backend、OS、指定ファイルの内容、cacheの`version`からキーを作るため、lockfileをキーに含めると依存関係の変更時だけ再実行できます。

```json
"cache": {
  "version": 1,
  "paths": [".venv", "build"],
  "key_files": ["requirements.lock", "pyproject.toml"]
}
```

キャッシュは`~/.localci/cache`（`LOCALCI_CACHE_DIR`で変更可能）に保存します。アーカイブのチェックサムまたは内容が壊れている場合は自動的に無効化してコマンドを再実行します。強制再構築は`./localci run --invalidate-cache`、キャッシュを使わない検証は`./localci run --no-cache`、手動削除は`./localci cache clear`です。cache hitは実行結果で`status: cached`として表示されます。

最小構成の実行例は[`examples/sample-product/`](examples/sample-product/)にあります。評価テストはこの設定を一時的にlocalCIへ適用し、4段階の実行とbuild成果物の生成を確認します。

## GitHubとの受け渡し

ローカルCIが成功したコミットをfeatureまたはintegrationブランチへ進めます。main宛てPull Requestでは`shared-ci` status checkが共有CI入口を実行し、手動の最終確認が必要な場合はユーザーの明示指示のもと`scripts/run_pre_merge.sh`を入口にworkflow_dispatchを1回実行します。

GitHub連携では`.agent-ci-policy.yml`の`github.enabled`を有効にし、workflow内のActionをcommit SHAで固定しています。`main`は保護し、直接pushではなくPull Requestからのみ更新できるようにします。

## delivery profile

`delivery`は、変更関連チェックのローカル実行、夜間の重いテスト、release PR gate、GitHub status reportを同じ実行profileへ束ねます。通常のpushでは差分付き`standard`、夜間は`full`、release PRでは明示承認を確認した後に`full`を実行します。

```bash
AGENT_CI_PROFILE=delivery LOCALCI_DELIVERY_EVENT=push scripts/run_ci_local_quiet.sh
AGENT_CI_PROFILE=delivery LOCALCI_DELIVERY_EVENT=nightly scripts/run_ci_local_quiet.sh
LOCAL_CI_PASSED=true MERGE_AUTHORIZED=true SOURCE_BRANCH=integration/example TARGET_BRANCH=main \
  python3 scripts/delivery_profile.py --event release-pr --json
```

300 push/24hの再現可能な測定は、実テストを300回起動せず、固定seedのスケジューリングシミュレーションで行います。待ち時間、検知遅延、nightly fallback件数、runner分数、入力したrunner単価からの推定費用をJSONへ出力します。

```bash
python3 scripts/delivery_load_test.py --pushes 300 --window-hours 24 --output delivery-load.json
```

## ポリシーの更新

ポリシーの更新は、タグまたはcommit SHAで公開します。利用側はlockを更新してから、次を実行します。

```bash
python3 scripts/policy_verify.py
python3 -m unittest discover -s eval -p 'test_*.py'
```

現時点ではGitHub remoteを前提にしていません。後からremoteを追加しても、初期のローカルCI運用はそのまま継続できます。

## キャンセルと並列制御

依存関係のないコマンドは、`--max-jobs`または`LOCALCI_MAX_JOBS`で指定した上限まで並列実行できます。manifestのコマンドには、CPU・メモリ制限と名前付き排他リソースを宣言できます。

```json
{
  "name": "integration-test",
  "depends_on": ["install"],
  "resources": {"exclusive": ["postgres"], "cpu": 2, "memory_mb": 1024}
}
```

host backendではCPU秒数と仮想メモリをOSのプロセス制限で、Docker backendでは`--cpus`と`--memory`で適用します。CLIの`--cpu-limit`、`--memory-limit-mb`、環境変数の`LOCALCI_CPU_LIMIT`、`LOCALCI_MEMORY_LIMIT_MB`は全コマンドの既定値です。同じ`exclusive`名を使うコマンドは、実行間で排他されます。

実行中のJSONに表示された`execution_id`を使って、別の端末からキャンセルできます。キャンセルは現在のコマンドのプロセスグループを終了し、後続コマンドを起動しません。

```bash
localci cancel run-20261003T120000Z-1234-abcd1234
localci status run-20261003T120000Z-1234-abcd1234 --json
```

CI Routerの契約と段階的な実装方針は[`docs/CI_ROUTER.md`](docs/CI_ROUTER.md)にまとめています。

## 製品コマンド

現在のmanifestには、基本CIに加えて品質・配布・運用確認を登録しています。

| コマンド | 主な目的 | profile |
| --- | --- | --- |
| `lint` | Pythonソースの静的検査 | standard / full |
| `format_check` | 末尾空白・改行などの形式検査 | standard / full |
| `security` | 秘密情報らしき文字列の検出 | standard / full |
| `package` | クロスプラットフォーム配布ZIPの生成 | full |
| `coverage` | 標準ライブラリtraceによるテスト計測 | full |
| `integration_test` | plan→run→graphの接続検査 | full |
| `smoke_test` | CLIサブコマンドの起動確認 | standard / full |
| `delivery-profile` | push/nightly/release-prごとの統合実行とstatus report | delivery |

実製品の定義は[`.localci/product-commands.json`](.localci/product-commands.json)が正本です。追加コマンドは、実装・要件・profile・依存関係をmanifestへ同時に登録し、未実装の名前だけを登録して成功扱いにしないでください。

サーバー統合テストでは、実行前の依存確認もmanifestへ宣言できます。

```json
{
  "requirements": {
    "os": ["linux", "macos", "windows"],
    "docker": false,
    "services": [],
    "tools": ["node"],
    "packages": ["npm:sharp", "npm:google-auth-library"],
    "env": ["GOOGLE_APPLICATION_CREDENTIALS", "BRAVE_API_KEY"]
  },
  "preflight": ["node scripts/check-external-config.js"]
}
```

`packages`は`npm:`、`python:`などのruntime packageを実行前に確認し、不足時は`blocked`として扱います。`env`は必須設定値を確認し、`preflight`はnull設定・quota・認証情報など製品固有の契約検査に使います。これにより依存読み込みエラーや`null.used`のような実行途中の失敗を、製品コマンド起動前に検出できます。

manifestの検証では、schemaに加えて依存コマンド名の存在、stageをまたぐ依存循環、OSとtool要件の矛盾、空のコマンドを検出します。Routerは選択したbackendの実行対象について、shell commandに含まれる実行ファイルの存在も確認し、見つからない場合はExecutorを起動せず`blocked`として返します。検証は次で単独実行できます。

```bash
python3 scripts/validate_command_manifest.py .localci/product-commands.json
```

Node.jsサーバー向けの依存ゼロテンプレートを[`.localci/preflight/node-server-preflight.example.js`](.localci/preflight/node-server-preflight.example.js)と[設定例](.localci/preflight/node-server-preflight.example.json)に用意しています。製品側へコピーし、`packages`、`env`、`http[].json_paths`を編集してmanifestの`preflight`から呼び出してください。

テンプレートの導入とmanifest登録はCLIで行えます。

```bash
# 既存のserver-testsへpreflightを接続
localci preflight init --kind node --command server-tests

# 専用のpreflight_<kind>コマンドを追加
localci preflight init --kind python
localci preflight init --kind http
localci preflight init --kind postgres
```

既存ファイルを置き換える場合だけ`--force`を指定します。対象製品が別ディレクトリにある場合は`--root`を指定できます。

導入後の設定・ツール・接続確認は`doctor`で行えます。

```bash
localci preflight doctor
localci preflight doctor --kind postgres --json
localci preflight doctor --root /path/to/product --timeout 10

# サーバー起動後の接続状態を継続監視（Ctrl-Cで停止）
localci preflight doctor --watch --kind http --interval 5
localci preflight doctor --watch --kind postgres --interval 10

# 自動検証用の有限監視（JSONLを1回の診断ごとに1行出力）
localci preflight doctor --watch --kind http --iterations 12 --interval 5 --json

# PostgreSQL実環境評価（Homebrew/native、または準備済みDocker imageを自動選択）
python3 scripts/postgres_integration_check.py --json
# Docker経路を明示する場合
python3 scripts/postgres_integration_check.py --provider docker --json
# 評価結果を履歴ストアへ保存する場合
python3 scripts/postgres_integration_check.py --history-file /tmp/localci-history.jsonl --json
```

doctorはテンプレートの設定JSONを読み込み、必要なruntime toolを確認した後、timeout付きでpreflightを実行します。未設定templateは`not_configured`、依存tool不足は`blocked`、接続や契約検査の失敗は`failed`として表示します。`--watch`を付けるとHTTP・PostgreSQLなどの接続状態を繰り返し診断し、`--interval`で間隔を指定できます。`--iterations`または`--duration`を指定しない場合はCtrl-Cまで継続します。`--json`のwatch出力はJSON Lines形式で、各行に`iteration`、UTC `timestamp`、前回からの状態変化を示す`changed`を含みます。
`scripts/postgres_integration_check.py`は一時クラスタまたは一時Dockerコンテナを使い、停止・復旧を含む`passed → failed → passed`を検査します。利用可能なPostgreSQL実行基盤がない場合は理由付きで`skipped`になり、通常のCIを失敗させません。Dockerはデーモンと`postgres:16-alpine`（または`LOCALCI_POSTGRES_IMAGE`で指定したimage）がローカルに準備済みの場合だけ選択します。
`--history-file`を指定すると`postgres_integration`として履歴へ保存できます。`localci history --kind postgres_integration --summary --json`でhost/Dockerの評価結果と所要時間を比較できます。

外部サービスadapterは共通のライフサイクルでオンデマンド実行できます。

```bash
localci service integration --kind http --json
localci service integration --kind redis --json
localci service integration --kind mysql --json
localci service integration --kind postgres --json
```

HTTPは一時nativeサーバー、Redis/MySQLは準備済みDocker imageを必要時だけ起動します。imageやDocker daemonがない場合は`skipped`になり、adapterのimportや一覧表示だけではサービスを起動しません。
`--history-file`を追加すると共通履歴へ保存できます。`localci history --kind service_integration --summary --json`でサービス別の実行数、成功・失敗・skip、平均実行時間、平均復旧時間を比較できます。

同じ形式でPython、HTTPサービス、PostgreSQLのテンプレートも用意しています。

```text
.localci/preflight/python-service-preflight.example.py
.localci/preflight/http-service-preflight.example.py
.localci/preflight/postgres-preflight.example.py
```

PythonとHTTPは標準ライブラリのみ、PostgreSQLは`pg_isready`と`psql`を使用します。PostgreSQL設定は`host`/`port`/`database`/`user`（または`PG*`形式）を受け付けます。passwordは設定ファイルへ直接書かず、`password_env`で指定した環境変数やCI secretから渡してください。`sslmode`、`connect_timeout`、`application_name`も設定できます。

## GitHubを追加するとき

1. `pre-merge.yml` のActionを確認済みcommit SHAへ固定する
2. GitHub側でmainへの直接pushを禁止する
3. `shared-ci` status checkをmain保護の必須検査に登録する

ポリシー検査、評価テスト、利用側のローカルCIが成功した状態で、更新をPull Requestへ提出します。
