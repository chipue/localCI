# localCI OSS bundle

このディレクトリは、localCIから利用側リポジトリへ配布する汎用部品と設定の公開境界です。

## 公開部品

- `scripts/run_ci_local_quiet.sh`: ローカルCIの単一入口
- `scripts/run_ci_local_quiet.ps1`: Windows PowerShell用のローカルCI入口
- `scripts/ci_local.sh`と`localci`: Routerから製品manifestを選択し、plan gate経由でExecutorへ渡す入口
- `scripts/run_command_quiet.py`: CI以外の長いコマンドを要約実行するwrapper
- `scripts/ci_log_excerpt.py`: 失敗したCIログから安全なエラー周辺を抜粋
- `scripts/reproducibility.py`: commit、manifest、環境、backend/tool版、選択理由、コマンドログを実行記録へ保存
- `scripts/install_local_hooks.sh`: `.githooks`をGitへ登録するinstaller
- `scripts/install_local_hooks.ps1`: Windows PowerShell用のinstaller
- `.localci/hooks/pre-commit`・`pre-push`: commit/push前に親branchとの差分CIを実行するhook
- `.agent-ci-policy.example.yml`: 利用側設定のテンプレート
- `.localci/backend.lock`: 外部backendの名前、version、commit、ライセンスの固定情報
- `scripts/check_act_command_coverage.py`: 製品コマンドのact対応分類
- `.localci/product-commands.example.json`: 製品コマンド一覧の例

上記の実行部品はリポジトリ直下の同名ファイルを正本とします。`oss/`には公開対象と導入方法をまとめ、製品固有の設定や個人用エージェント指示とは分離します。コマンド定義は`.agent-ci-policy.yml`ではなく、`.localci/product-commands.json`のmanifest schemaを使用します。

## `act` backend

GitHub Actions互換の実行には、外部インストールした`nektos/act` `v0.2.89`（commit `4f41128`）を使えます。localCIはactを同梱せず、`.localci/backend.lock`で対応版を固定します。actは自動で常時起動せず、planで選択され要件を満たすときだけ呼び出します。

```bash
AGENT_CI_BACKEND=act scripts/run_ci_local_quiet.sh
```

既定のrunner imageは`catthehacker/ubuntu:act-24.04`です。別のimageを使う場合は`AGENT_CI_ACT_PLATFORM_IMAGE`で指定します。

actはMIT Licenseです。localCI本体のMIT Licenseとは別の外部ソフトウェアとして扱い、actのLICENSE表示を保持してください。

## 対応OS

- macOS 12以降（Bash、Python 3.10以降）
- Linux（Bash、Python 3.10以降）
- Windows 10/11（Git for Windows、Git Bash、PowerShell、Python 3.10以降）

WindowsではGit for Windowsの`bash.exe`を使います。外部Pythonパッケージは使用しません。

## 導入

```bash
cp oss/.agent-ci-policy.example.yml .agent-ci-policy.yml
cp .localci/product-commands.example.json .localci/product-commands.json
./localci hooks install
```

Windows PowerShellでは次を実行します。

```powershell
.\scripts\install_local_hooks.ps1
```

利用側では`.localci/product-commands.json`に製品stage、必要OS・tools・services、依存関係、profileを記述します。通常の入口は次のとおりです。

manifestの`security`では、子プロセスへ渡す追加環境変数を`env_allowlist`、秘密として扱う変数を`secret_env`で宣言します。Executorは安全な基本変数と宣言済み変数だけを渡し、stdout/stderrと失敗抜粋の秘密値をマスクします。秘密が検出された実行ログは既定でマスク済みログだけを保存し、`secret_log_policy: "discard"`ならログファイル自体を保存しません。ログディレクトリとファイルは所有者だけが読める権限で作成されます。

```bash
scripts/run_ci_local_quiet.sh
```

別のmanifestを使う場合は`--inventory`で指定します。act backendのルーティングはlocalCI本体が行います。

依存関係や成果物を再利用する場合は、各commandに`cache.paths`と`cache.key_files`を追加します。破損したキャッシュは自動的に破棄して通常実行へ戻り、`localci run --invalidate-cache`で強制再構築できます。

```bash
./localci run --profile standard --backend auto \
  --inventory .localci/product-commands.json
```

分類は`ubuntu_standard`（Ubuntu標準で実行可能）、`dependency_required`（依存導入が必要）、`act_out_of_scope`（actのUbuntu runner対象外）です。分類は実行環境内での静的な事前判定であり、最終的な製品CIの成否は実コマンドの終了コードで判定します。

Windows PowerShellでは次を実行します。

```powershell
.\scripts\run_ci_local_quiet.ps1
```

sample productでinstall・test・typecheck・buildを通す評価は次のとおりです。

```bash
python3 scripts/validate_command_manifest.py .localci/sample-product-commands.json
./localci run --profile standard --backend host \
  --inventory .localci/sample-product-commands.json
```

manifest検証はschemaだけでなく、`depends_on`の未知コマンド、stage依存の循環、OSとtool要件の矛盾、空の`command`も検出します。Routerは選択されたbackendで実行するコマンドの実行ファイルも確認し、存在しない場合はExecutorを起動せずblockedにします。

ライセンスはリポジトリルートの`LICENSE`にあるMIT Licenseです。
