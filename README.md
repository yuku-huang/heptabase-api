# heptabase-api
Get heptabase data

## Auto Download Images To Local

Run locally:

```bash
python scripts/download_heptabase_images.py \
  --whiteboard-id "YOUR_WHITEBOARD_SECRET" \
  --output-dir heptabase-assets \
  --manifest heptabase-images-manifest.json \
  --rewrite-output data.with_local_images.json \
  --local-prefix "./heptabase-assets"
```

Use local `data.json` as input:

```bash
python scripts/download_heptabase_images.py \
  --from-local-json \
  --input-json data.json \
  --output-dir heptabase-assets
```

GitHub Actions workflow:

- File: `.github/workflows/syncHeptabaseImages.yml`
- Required secret: `HEPTABASE_WHITEBOARD_ID`
