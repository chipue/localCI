# CI Router仕様

## 目的

CI Routerは、製品コマンド・変更内容・実行環境・ユーザーが選んだprofileを照合し、実行可能な最小のCI計画を作る。Router自身は製品コマンドを実行せず、計画をLocal Executorへ渡す。

```text
入力
  ↓
Discovery（製品コマンド、変更、環境）
  ↓
Policy（profileと安全制約）
  ↓
Plan（selected / excluded / blocked）
  ↓
Local Executor
```

## 入力

- `.agent-ci-policy.yml`: 実行する製品コマンド
- `.localci/product-commands.json`: 実製品のコマンド能力・対応OS・分類情報
- Gitの変更ファイル
- 利用可能なbackendとその能力（`host`、`docker`、`act`、`wsl`、`windows`）
- `quick`、`standard`、`full`、`delivery`のprofile

`delivery`はtriggerに応じて、pushでは差分チェック、nightlyでは重いfull test、release PRでは明示承認付きgateとfull testを実行し、同じ結果をGitHub status report形式へ変換する。

入力にない製品コマンドを推測して実行してはならない。

### 実行中CIの制御

`localci run --max-jobs N`は、依存関係を満たしたコマンドを最大`N`個まで並列実行する。`LOCALCI_MAX_JOBS`、`LOCALCI_CPU_LIMIT`、`LOCALCI_MEMORY_LIMIT_MB`でも既定値を指定できる。コマンドごとの`resources.exclusive`は名前付きのファイルロックとして扱われ、同じリソースを使うコマンドは直列化される。`resources.cpu`はhost backendのCPU秒数、`resources.memory_mb`はhost backendの仮想メモリ上限になり、Docker backendでは対応するコンテナ制限へ変換される。

各runは`~/.localci/runs/<execution_id>/state.json`に状態を公開する。別端末から`localci cancel <execution_id>`を実行するとキャンセルマーカーを作成し、実行中プロセスを終了して`cancelled`として記録する。`localci status <execution_id>`は状態確認専用である。

### backendの分離

`host`はlocalCI本体に含まれる必須backendである。`docker`、`act`、`wsl`、`windows`は任意adapterとして扱い、利用環境に存在しなくてもlocalCI本体の導入やhost実行を妨げない。`localci doctor`はadapterの能力検出に加えて、Gitの作業ツリー、command manifestのschema、Docker daemon、ディスク空き容量、直近のlocalCI履歴を一度に診断する。通常は利用可能なbackendだけを表示し、`--all`では未利用可能なadapterと不足理由も表示する。

```bash
localci doctor
localci doctor --all
localci doctor --all --json
localci doctor --root /path/to/product --manifest .localci/product-commands.json \
  --history-file ~/.localci/history.jsonl --min-free-gb 2
```

人間向け出力は`[ok]`、`[warn]`、`[fail]`ごとに原因候補と次の確認方法を表示する。JSONでは`checks`配列と`summary`を機械的に利用できる。Gitの未コミット変更、Docker daemon未起動、CI履歴の失敗は警告として案内し、manifest不正または設定した最低空き容量未満は終了コード1になる。`--min-free-gb`の既定値は1GBである。

doctorおよび`auto`の能力検出はbackendの実行環境を必要に応じて検査するが、製品コマンドやCI workflowは起動しない。DockerコンテナやWSLコマンドを起動するのは、Routerがそのbackendを選択してExecutorへ渡した後だけである。

## 任意深度のbranch階層

branch policyは`main`以外の任意深度のbranchを扱う。各branchには直属の親を1つだけ持たせる。`.localci/branch-parents.json`へ登録するか、親が一意に推測できない場合だけ`LOCALCI_PARENT_BRANCH`で指定する。

```bash
localci branch register --parent integration/example --branch feature/example/router
localci branch register --parent feature/example/router --branch feature/example/router/graph
```

hookと`run_ci_scoped.py`は現在のbranchと直属の親の差分（`parent...HEAD`）だけをDiscoveryへ渡す。子孫branchを親へ統合した後は、親branch側のhookが親の親との差分を検査するため、同じ製品コマンドを全履歴に対して毎回やり直す必要はない。

### 正式worktreeへの昇格

稼働履歴を確認して安定したcheckoutは、次のコマンドで正式worktreeへ昇格できる。

```bash
localci worktree status --json
localci worktree promote --min-successes 3
```

`promote`はmainを対象外とし、成功履歴数と親branchの存在を確認する。未コミットのtracked/untracked変更がある場合は、一時indexを使ったsnapshot commitを作成するため、現在のindex・作業ツリーを変更せずに変更内容を新worktreeへ移せる。既存checkoutは削除せず、`git worktree add`で`worktree/<branch>`を作成する。作成後はそのworktreeへ親マッピングを書き込み、hooksを有効化し、registryへ昇格記録を保存する。

`localci worktree list`は、branch階層だけでなくGitのworktree実体とregistryを照合する。dirtyまたは利用不能なworktree、registry内の重複branch・formal branch・path、Gitから外れたstale registryを`diagnostics`として返し、警告表示する。統合済み正式worktreeは削除候補としてチェックサマリーと再検査警告を含めて表示する。

統合済みの正式worktreeは`localci worktree retire`で整理する。候補判定は、親branchに子branchのtipが含まれること、正式worktreeとしてregistryに登録されていること、現在のcheckoutではないことを条件とする。標準動作はpreviewであり、`--apply`を付けた場合だけworktreeを削除する。dirty worktreeは通常スキップし、`--force`を明示した場合だけ強制削除する。正式branchの削除も`--delete-branch`を別途指定しない限り実行しない。

候補の親branchにさらに上流の親がある場合、retireは`localci merge-check --base <上流branch> --profile full`という再検査計画を生成する。`--apply`時に再検査計画が残っていれば削除を止め、`--run-rechecks`で親worktree上の計画を実行する。全再検査が成功した場合だけ整理を続行し、親worktreeが存在しない・CIが失敗した場合は`recheck_failed`として候補を保持する。

`--apply`または`--run-rechecks`で実行したretire処理は、履歴storeへ`kind=worktree_retire`として保存する。記録には候補のチェックサマリー、実行した再検査コマンドと結果、実際に削除されたformal worktreeが含まれるため、後から整理の根拠を追跡できる。

履歴は`--branch`または`--worktree`で対象を絞り込める。`--summary --json`では`rechecks.total`、`success`、`failed`、`blocked`、`success_rate`を返すため、worktree整理前の再検査品質を集計できる。

`--since`と`--until`でISO日時または日付による期間指定ができる。`--branch-parents-file`を指定すると、summaryの`branch_hierarchy`に親branchと世代を付加し、`by_worktree`では昇格・整理件数とworktree単位の再検査成功率を比較できる。`worktree_promote`も履歴へ保存するため、昇格から整理までの運用状態を同じstoreで追跡できる。

### manifestの要件

各コマンドは、少なくとも次の要件を宣言する。

```json
{
  "name": "integration-test",
  "stage": "test",
  "command": "python3 -m pytest tests/integration",
  "requirements": {
    "os": ["linux"],
    "docker": true,
    "services": ["postgres"],
    "tools": ["python3", "pytest"]
  },
  "profiles": ["standard", "full"],
  "timeout_seconds": 600
}
```

### サーバー統合テストのpreflight

Node/Pythonパッケージや外部サービス設定を使うコマンドは、`requirements.packages`と`requirements.env`で実行条件を宣言する。host backendではRouterがruntime packageと環境変数を検査し、不足時は`blocked`にする。Docker・act・WSLなどinstall段階で依存を導入するbackendでは、Executorが宣言を保持したままinstall後の実行へ渡す。

```json
{
  "name": "server-tests",
  "stage": "integration_test",
  "command": "node server/test/index.js",
  "requirements": {
    "os": ["linux", "macos", "windows"],
    "docker": false,
    "services": [],
    "tools": ["node"],
    "packages": ["npm:sharp", "npm:google-auth-library"],
    "env": ["GOOGLE_APPLICATION_CREDENTIALS", "BRAVE_API_KEY"]
  },
  "preflight": ["node scripts/check-server-config.js"],
  "profiles": ["standard", "full"],
  "timeout_seconds": 900
}
```

`preflight`は製品固有の設定契約を検査するためのshell commandである。API quotaのレスポンスがnullでないこと、認証設定が読み込めること、外部参照が存在することなどをここで明示的に検査できる。preflightが失敗した場合は製品コマンドを起動せず、実行結果へ`preflight_then_*`として記録する。

Node.jsサーバー用には、localCIに依存ゼロの`.localci/preflight/node-server-preflight.example.js`を同梱している。JSON設定の`packages`は`require.resolve`、`env`は空値検査、`http`は標準`http`/`https`モジュールで検査する。`http[].json_paths`へ`{"path":"brave_monthly_quota_202609.used","not_null":true,"type":"number"}`を指定すれば、`null.used`を本体テスト開始前に検出できる。外部APIのsecret値は出力せず、URLとフィールド名だけをエラーに表示する。

同じ設定体系のPython・HTTP・PostgreSQLテンプレートも`.localci/preflight/`に置く。PythonはimportとHTTP JSON契約、HTTPテンプレートは複数endpointのstatus/JSONフィールド、PostgreSQLテンプレートは`pg_isready`と`psql -v ON_ERROR_STOP=1`による接続・query検査を担当する。PostgreSQLは`host`/`port`/`database`/`user`と`PG*`形式の設定キーを受け付ける。passwordやAPI secretをテンプレートのJSONへ保存せず、`password_env`で指定した環境変数や`PGPASSWORD`などCI secretから渡す。`sslmode`、`connect_timeout`、`application_name`も利用できる。

テンプレートのコピーとmanifest登録は次で行う。

```bash
localci preflight init --kind node --command server-tests
localci preflight init --kind python
localci preflight init --kind http
localci preflight init --kind postgres
```

`--command NAME`を付けると既存コマンドの`preflight`へ接続し、付けない場合は`preflight_<kind>`というstandalone commandをmanifestへ追加する。既存ファイルや同名manifest commandは安全のため拒否し、意図的な置換にだけ`--force`を使う。

導入済みtemplateの診断は`localci preflight doctor`で行う。doctorは設定JSON、runtime tool、package/env、HTTPまたはPostgreSQL接続をtimeout付きで確認する。

```bash
localci preflight doctor
localci preflight doctor --kind node --json
localci preflight doctor --root /path/to/product --timeout 10
localci preflight doctor --watch --kind http --interval 5
localci preflight doctor --watch --kind postgres --iterations 12 --interval 10 --json
python3 scripts/postgres_integration_check.py --json
python3 scripts/postgres_integration_check.py --provider docker --json
python3 scripts/postgres_integration_check.py --history-file /tmp/localci-history.jsonl --json
```

結果は`passed`、`blocked`、`failed`、`timeout`、`config_error`、`not_configured`に分類される。`--watch`はサーバー起動後のHTTP・PostgreSQL接続状態を繰り返し診断する。Ctrl-Cで停止でき、`--iterations`または`--duration`を指定すれば自動化用に有限回で終了する。`--json --watch`は1診断につき1行のJSON Linesを出力する。doctorは導入確認の診断用であり、preflight対象が未設定でもlocalCI本体の導入を失敗扱いにはしない。
`scripts/postgres_integration_check.py`は、Homebrew/nativeの`initdb`・`pg_ctl`・`pg_isready`・`psql`が揃っていれば一時クラスタを優先して使用する。揃わない場合はDocker daemonと準備済みPostgreSQL imageを検査し、利用可能なら一時コンテナを使用する。いずれも利用できない場合は`skipped`を返す。検査は停止・復旧を含む`passed → failed → passed`で、Docker imageの自動pullは行わない。
`--history-file`を指定すると`postgres_integration`として履歴へ記録し、`localci history --kind postgres_integration --summary --json`でprovider別に比較できる。

外部サービスは共通adapter契約（`available`、`start`、`wait_ready`、`stop`）でオンデマンド起動できる。

```bash
localci service integration --kind http --json
localci service integration --kind redis --json
localci service integration --kind mysql --json
localci service integration --kind postgres --json
```

adapter registryの読み込みはmetadataのみで、サービスを起動しない。HTTPはnative、一時Redis/MySQLは準備済みDocker imageを使用し、必要なadapterだけが実行時に生成される。
`--history-file`を指定したservice integrationは`service_integration`として履歴へ保存され、`localci history --kind service_integration --summary --json`でサービス別の成功数、skip数、平均実行時間、平均復旧時間を集計できる。

`requirements`は実行可否の判定に使い、コマンド文字列から推測して補完しない。実製品manifestは`.localci/product-commands.json`、導入用の雛形は`.localci/product-commands.example.json`に置く。検証は`python3 scripts/validate_command_manifest.py <manifest>`で行う。

検証では、schemaだけでなく`depends_on`の未知コマンド、stageをまたぐ依存循環、OSとtool要件の矛盾、空の`command`を実行前に拒否する。Routerが現在のbackendで実行対象を選んだ後は、shell commandの実行ファイルも確認し、存在しない場合はExecutorへ渡さず`blocked`にする。Windows専用コマンドなど、現在のbackendでは選択されないコマンドは、そのOSでの実行時に検証する。

backendごとに起動方法が異なる製品コマンドは、任意の`backend_commands`で上書きできる。

```json
{
  "command": "npm test",
  "backend_commands": {
    "act": "act workflow_dispatch -W .github/workflows/product.yml --pull=false",
    "windows": "pwsh -File scripts/test.ps1"
  }
}
```

Executorは選択backendの上書きを優先し、結果の`execution_mode`へ`backend_override`を記録する。hostの標準コマンドは`native`、act／Windowsのbackend専用コマンドは`act_native`／`windows_native`、上書きがないportableコマンドは`portable_shell_fallback`として区別する。fallbackは実行可能性を維持する互換経路だが、履歴には残り、Routerの自動選択スコアで不利に扱われる。

コマンドに`paths`を指定すると、変更ファイルがglobに一致した場合だけ候補になる。`always: true`は変更内容に関係なく候補にする。profileに含まれないコマンド、backendの対象OSに対応しないコマンド、変更範囲に一致しないコマンドは`excluded`として理由を表示する。

差分テストの安全網を使う場合は`localci plan --diff`または`localci run --diff`を指定する。Routerはテスト系コマンドの`paths`に一致する対象を選び、`depends_on`で宣言されたinstallなどの依存だけを追加する。共有CI設定・lockfile・runtime設定の変更、変更ファイルを発見できない場合、対象テストの`paths`が未宣言の場合、依存先が解決できない場合は安全のため`full` profileへフォールバックする。計画の`differential.mode`と`differential.reasons`に判定結果を記録する。静音入口では`AGENT_CI_DIFF=1`で同じ経路を有効化できる。

backend capabilityは、OS、Docker daemon、利用可能サービス、必要ツールを検査する。要件が満たされないコマンドは`blocked`へ分け、成功や単なる除外として扱わない。追加サービスは`LOCALCI_SERVICES=postgres,redis`のように明示する。Docker daemonが利用可能な場合は`docker`サービスとして自動登録する。`docker` backendは既定で`catthehacker/ubuntu:act-24.04`へworkspaceをmountしてmanifestコマンドを直接実行し、`LOCALCI_DOCKER_IMAGE`でimageを変更できる。`wsl` backendはWindows上でWSLとLinux側の`python3`・`sh`が利用できる場合だけ実行可能になる。

`blocked`を含む計画は`execution_allowed: false`になる。Local Executorは実行前に`scripts/plan_gate.py`の`ensure_runnable`を通し、blocked計画を受け取った場合はコマンドを1つも起動せず、backend不足の理由を返す。各blocked項目には他のbackend（`host`、`docker`、`act`、`wsl`、`windows`）の評価も付き、`available`な候補または候補側の不足要件を表示する。これは自動的なbackend切替ではなく、利用者が次の実行経路を選べるようにする診断情報である。

## 出力

計画は次の4つに分ける。

```text
selected: 実行対象
excluded: 条件により実行しない対象と理由
blocked: 必要能力が不足し、実行できない対象と理由
backend: 選択した実行backendと選択理由
execution_allowed: Local Executorへ渡してよいか。`blocked`が1件でもあれば`false`
blocked[].alternatives: 他backendごとの`available`/`blocked`状態と不足要件
```

計画表示は製品コマンドを実行しない。実行機能は計画の`selected`だけをLocal Executorへ渡す。

Local Executorの入口は`scripts/local_executor.py`（CLIでは`localci execute PLAN.json`）である。入口の最初に`plan_gate.ensure_runnable()`を呼び出すため、`blocked`計画は製品コマンドを起動せずに終了する。実行結果はコマンド単位の`success`、`failed`、`timeout`として統合する。

計画生成から実行までを一度に行う場合は`localci run --profile standard --backend host`を使う。`localci run`はRouterの`make_plan`結果をそのままLocal Executorへ渡し、blockedなら実行せず拒否理由と代替backendを表示する。`--json`では`plan`、`result`、`error`をまとめて返す。

`--result-file PATH`を指定すると、同じ完全なJSON結果をファイルにも保存できる。このファイルを`localci retry`や`localci graph`へ渡すことで、計画・実行・再実行・可視化をシェルのログ解析なしで接続できる。実行時にはログディレクトリへ`execution-record.json`も自動保存され、結果JSONの`record_file`から参照できる。

実行結果を共有する場合は`--report-json PATH`または`--report-html PATH`を指定する。JSONには`status`、実際に使った`backend`、stageごとの状態・所要時間・コマンド数、失敗したコマンドの抜粋、`rerun_candidates`を含む。HTMLは同じ情報を表形式で表示し、失敗抜粋を折りたたみ可能な形で保存する。`--result-file`にも同じ`report`オブジェクトが含まれる。

実行記録の`reproducibility`には、HEAD commit、branch、dirty状態と作業ツリー差分のハッシュ、manifestの絶対パス・SHA-256・内容、OS・machine・Python・shell、backend capabilities、`.localci/backend.lock`の内容とハッシュ、関連toolの実行ファイル・版を保存する。Routerが`auto`でbackendを選んだ場合は、候補ごとの実行可否・履歴・scoreと`selection.reason`も保存する。`result.results`にはコマンドごとの実際のcommand、backend execution mode、終了値、所要時間、ログパスを残すため、過去の条件を記録JSONだけで確認できる。manifestやコマンドに秘密情報を含めないこと。

`localci run --backend auto`ではmatrixと同じ能力判定を使い、host、docker、act、wsl、windowsの順で`execution_allowed`なbackendを選ぶ。能力検出は候補ごとに行うが、製品コマンドを起動するのは選択したbackendだけである。選択結果と各候補のblocked理由はJSONの`route`に保存する。全候補がblockedの場合は実行せず、host計画と全候補の診断を返す。

auto選択時は履歴ストアのbackend別失敗率、portable fallback率、平均実行時間も候補評価へ使う。スコアは失敗率、fallback率、平均実行時間の順に重み付けし、同条件ならhost→docker→act→wsl→windowsの順で決める。候補ごとの`history`と`score`を`route.candidates`へ保存し、履歴がない場合も既定順で決定する。

実行結果は選択されたbackendを`route`と`result.backend`に保持する。`localci retry`は元のrouteを`route.source`に残し、実際にretryしたbackendを`route.selected`へ記録する。`localci graph`もroute情報を表示するため、計画・実行・再実行・可視化でbackend選択を追跡できる。

各結果JSONには`execution_id`と`history`を保存する。履歴にはroute選択、実行開始、完了、blocked、retry開始・完了を時系列で記録し、retryは元の`source_execution_id`を保持する。`localci graph`はこの履歴も表示する。

実行ごとのcompactな記録は既定で`~/.localci/history.jsonl`へ追加する。`localci history`でbackend、status、kind（run/retry）、件数、failure summaryを検索でき、`--history-file`、`--backend`、`--status`、`--kind`、`--limit`、`--json`で絞り込める。詳細ログやplan本体は履歴ストアへ複製せず、結果JSONと`log_path`を参照する。

`localci history --summary`ではbackend別の実行回数、成功・失敗・blocked件数、平均実行時間、失敗stageを全履歴から集計する（`--limit`指定時だけ対象を絞る）。過去のroute選択と実行結果を比較し、backend選択や製品CIのボトルネックを判断するために使う。

製品ルートがまだGit管理下にない場合、Discoveryは`source: non-git-root`として変更ファイルを空に返す。`always: true`のコマンドは実行対象にできるが、paths条件だけのコマンドは変更なしとして除外される。これにより、展開直後の一時ディレクトリやパッケージ検証でもmanifestの能力判定と実行を行える。

製品コマンドのstdout/stderrは標準出力へ流さず、コマンド単位のログへ保存する。既定の保存先は`~/.localci/logs/<run-id>/`で、`--log-dir`で変更できる。成功時の通常表示は実行結果・所要時間・ログディレクトリだけの要約とし、JSON結果には各コマンドの`log_path`を含める。

### 秘密情報の実行境界

manifestの`security.env_allowlist`は、製品コマンドへ追加で渡す環境変数の許可リストである。Executorは`PATH`などの安全な基本変数、localCI内部変数、manifestの`requirements.env`とこの許可リストに含まれる変数だけを子プロセスへ渡し、親プロセスの環境全体は継承しない。`security.secret_env`と`requirements.env`の値は秘密として収集し、stdout/stderr、保存ログ、失敗時の`error_excerpt`および`error_highlights`からマスクする。`secret_log_policy`は`redact`（既定、マスク済みログだけを保存）または`discard`（秘密を含む実行のログを保存しない）を指定できる。結果JSONの`secrets_detected`、`log_redacted`、`log_discarded`で適用結果を確認できる。

共有入口`scripts/run_ci_local_quiet.sh`は、ポリシー検査後に`.localci/product-commands.json`を読み、`localci run`へ接続する。`AGENT_CI_BACKEND`、`AGENT_CI_PROFILE`、`AGENT_CI_MANIFEST`で実行経路を固定・変更できる。製品コマンドの内部から同じquiet wrapperを検査する場合は、Executor配下の再帰実行を抑止して現在のCIへ委譲する。

コマンドが失敗またはタイムアウトした場合だけ、ログ末尾20行を`error_excerpt`として通常表示とJSON結果に含める。全ログは引き続き`log_path`から確認できるため、成功時の出力を抑えつつ、失敗原因の初動確認ができる。

さらに抜粋全体から`Traceback`、`ERROR`、`failed`/`failure`、`warning`を分類し、重要度順の`error_highlights`として最大10行を表示する。分類は文字列の大文字小文字を区別せず、`Traceback`、`error`、`failed`、`warning`の順で優先する。

実行結果にはCI全体の`failure_summary`も含める。失敗した最初のコマンドについて、`stage`、コマンド名、実行状態、終了コード、エラー分類、重要行、ログパスをまとめる。これにより、個別ログを探す前に失敗段階と原因候補を確認できる。全コマンド成功時の`failure_summary`は`null`とする。

複数コマンドの実行中に失敗またはタイムアウトした場合、後続コマンドは安全のため起動せず`not_run`として記録する。`execution_summary`には全体件数、完了件数、`success`/`failed`/`timeout`/`not_run`の件数と、コマンドごとのstage・状態を含める。

manifestのコマンドには任意で`depends_on`（依存コマンド名）と`retryable`（再実行可否、既定値`true`）を指定できる。`not_run`の理由は`dependency_failed`、`dependency_not_run`、`policy_stop_after_failure`、`policy_not_retryable`に分類し、`blocked_by`と`rerunnable`を付ける。`execution_summary.rerun_candidates`には安全に再実行候補と判定されたコマンド名を列挙する。

`localci retry RESULT.json`は`localci run --json`の結果を入力にし、`execution_summary.rerun_candidates`に含まれるコマンドだけを元のplanから抽出して再実行する。成功済み、依存失敗、`retryable: false`のコマンドは対象外である。候補がない場合はコマンドを起動せず`no_candidates`を返す。

Windows実機がない環境でWindows専用コマンドがblockedになった場合も、結果JSONのトップレベル`rerun_candidates`へ対象名を保存する。`localci retry RESULT.json`はこの計画を復元して要件不足を再表示し、製品コマンドは起動せず`blocked_retry`として返す。Windowsが利用可能になった後に同じ計画を再実行できるよう、`retry_plan`にはmanifest要件と依存関係を保持する。

act backendのコマンドは、アーキテクチャ・runner image・pull方針をmanifestで明示し、イメージ選択プロンプトを発生させない。通常の製品コマンドは一時workflowへ変換し、`--bind`で現在の未コミット作業ツリーをactコンテナへ渡す。Dockerソケット共有は既定で無効（`LOCALCI_ACT_DAEMON_SOCKET=-`）とし、Docker-in-Dockerが必要なworkflowだけ明示的に変更する。

Windows backendはWindows上でPowerShell（`pwsh`または`powershell`）が利用可能な場合だけ`runtime_available: true`になる。Windows専用コマンドは`windows_native`または`backend_commands.windows`で実行し、macOS/LinuxからWindows実行済みとは扱わない。

`localci matrix --profile standard`はhost・docker・act・wsl・windowsの実行可否を同じmanifestで比較する。現在のOSで実行できないWindowsまたはWSL backendは`runtime_available: false`として扱い、実行済みとは見なさない。JSONではbackendごとのselected・blocked・excluded・execution_allowedを確認できる。

`--failed-only`を付けると、候補のうち前回の状態が`failed`または`timeout`のコマンドだけを再実行する。`--stage test`のように指定すると、指定stageのコマンドだけを再実行する。両方を指定した場合はAND条件で絞り込む。

再実行候補は`depends_on`を使って安定トポロジカル順に並べ替える。候補内に依存先があれば依存先を先に実行し、依存関係のない候補は元のmanifest順を維持する。候補内に循環依存がある場合は、コマンドを起動せずエラーにする。適用後の順序は`retry_plan.retry_order`に記録する。

`localci graph RESULT.json`で、前回planのコマンド、stage、状態、依存関係、再実行候補を確認できる。blocked計画ではmanifestからノードを復元し、`blocked_candidates`と`rerun_candidates`を同じグラフ上に表示する。`retry order`は`localci retry`と同じ依存関係順で表示し、`--json`では`nodes`、`edges`、`rerun_candidates`、`blocked_candidates`、`retry_order`を返す。

`localci graph RESULT.json --mermaid`ではMermaidの`flowchart TD`を出力する。成功は緑、失敗は赤、blockedは黄・橙の点線、タイムアウトは橙、未実行は灰色の点線、不明状態は紫で表示する。再実行候補は同じ状態色の太枠で強調し、plan外の依存先は赤い点線で表示する。出力はMermaid対応のMarkdownやドキュメントへそのまま貼り付けられる。

ノードはstageごとのMermaid `subgraph`（例：`install`、`test`、`typecheck`、`build`、`full_ci`）にまとめる。stageをまたぐ依存関係もsubgraph間のエッジとして表示されるため、処理段階と実行順序を同時に確認できる。

各subgraphのラベルには、そのstageに属するコマンドの合計実行時間と`success`、`failed`、`blocked`、`timeout`、`not_run`の件数を表示する。実行時間が結果にない場合は`time unknown`とし、JSON出力の`stage_stats`でも同じ集計を確認できる。

stageの合計実行時間を比較し、最長のstageを`bottleneck_stage`として表示する。Mermaidでは該当subgraphに`BOTTLENECK`を付け、通常表示ではstage名と秒数を表示する。実行時間が揃わないstageは比較対象から除外し、比較可能なstageがない場合は`unknown`とする。

ボトルネックstage内で同一stageの依存先を持たないコマンドを`parallel_candidates`として抽出する。これは実行を自動変更する機能ではなく、並列化による短縮を検討できる候補の表示である。

依存エッジには、再実行候補内での実行順序と遷移先コマンドの所要時間を`#順序 / 時間`形式で表示する。候補外の依存先は`dependency / time unknown`などのラベルになる。JSONの`edges`には`from_stage`、`to_stage`、`order`、`duration_seconds`、`label`を含める。

## 選択順

1. 明示されたprofileを確認する。
2. 製品policyに設定されたコマンドだけを候補にする。
3. コマンドのOS・Docker・サービス・ツール要件を確認する。
4. 要件を満たすbackendだけを候補にする。
5. 変更内容とprofileに応じて不要な検査を除外する。
6. 候補がない場合は成功にせず、`blocked`または`excluded`として理由を表示する。

LLMによる推測は必須経路に入れない。将来追加する場合も、宣言済みpolicyの候補を並べ替える補助に限定する。

## Discovery

`scripts/discover_changes.py`は、製品コマンドを実行せずにGitの変更を収集する。デフォルトでは作業ツリーの変更、`--base REF`指定時は`REF...HEAD`のコミット済み変更も含める。

```bash
python3 scripts/discover_changes.py
python3 scripts/discover_changes.py --base main --json
```

収集対象はtrackedの変更・追加・削除、rename/copy、未追跡ファイルで、`.gitignore`で無視されたファイルは含めない。出力は後続Routerが利用できる`changed_files`配列とする。

## 実装段階

1. Router契約とprofileを固定する
2. 製品コマンドmanifestを拡張する
3. 変更ファイルDiscoveryを追加する
4. Discovery結果をplanへ接続する
5. backend capability検出を追加する（完了）
6. route選択とblocked判定を追加する（完了）
7. Local Executorと結果統合を接続する（完了）
8. キャッシュ・並列実行・履歴による最適化を追加する
