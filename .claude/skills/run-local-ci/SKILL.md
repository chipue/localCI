---
name: run-local-ci
description: 共有CIポリシーに従ってローカルCIを単一ownerで実行する。
disable-model-invocation: true
---

```bash
scripts/run_ci_local_quiet.sh
```

ownerになったrunnerだけが完了まで待ち、後続runnerはexit 75で終了する。後続runnerは待機、ポーリング、再実行、共有ログの読み込みをしない。
