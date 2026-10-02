# sample-product

localCIの製品CIモードを検証する、外部依存なしの最小Python製品です。

`.localci/sample-product-commands.json`からRouterへ渡す4段階のmanifestです。

- `install`: sourceを`.installed/`へ配置
- `test`: `unittest`を実行
- `typecheck`: Python sourceのコンパイル検査を実行
- `build`: `dist/sample_product.pyz`を生成

リポジトリルートで次を実行すると、実際のRouter→Executor経由で全段階を検査できます。

```bash
python3 scripts/validate_command_manifest.py .localci/sample-product-commands.json
./localci run --profile standard --backend host \
  --inventory .localci/sample-product-commands.json
```
