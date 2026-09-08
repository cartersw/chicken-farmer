# Reviewing competitive HUD captures

The review generator now creates every contact sheet in one FFmpeg pass and publishes a local `index.html`. The page links to every sheet, lists its frame range, and provides an original full-resolution TGA link for every frame. It never marks a frame reviewed or approves a capture.

```powershell
python -m cs2_data.hud_review `
  --render-dir data/rendered/your-capture `
  --out data/validation/your-fresh-hud-review `
  --ffmpeg .tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/ffmpeg.exe
```

Open the new directory's `index.html` locally. Follow the sheet navigation and inspect **every tile**, reading left to right and then top to bottom. Each sheet contains up to eight original-derived 640×360 images in a two-column, four-row grid. A partial final sheet has black padding; the page explicitly identifies how many slots are padding.

Confirm that the spectator name/avatar/weapon strip is absent, the player's ordinary HUD remains visible, and no console, menu or obvious capture corruption obstructs the scene. Record uncertain frames and failures rather than treating them as reviewed. The original links point to untouched TGA files; a browser without TGA support may download them for a local image viewer.

Actual approval still requires the separate source-bound review and registration described in [COMPETITIVE_ACCEPTANCE.md](COMPETITIVE_ACCEPTANCE.md). A complete index is a coverage checklist, not proof that someone inspected its images.

## Evidence and compatibility

`prepare_hud_review(...)` and `validate_hud_review_bundle(...)` retain their public APIs and the existing bundle-v1 fields. New bundles add versioned `generation` and `review_index` objects. Historical bundles without those additions remain valid under the original source and sheet checks.

Preparation checks every original image hash and the capture ledger, inventory and render manifest before publishing. Numeric staging links give FFmpeg an explicit, ordered image sequence; when a hard link is unavailable, the generator copies that verified original into its owned temporary directory. Cleanup is restricted to that directory. A failed run leaves no completed bundle and cannot overwrite an existing review directory.

New-bundle validation checks the FFmpeg executable hash, exact sheet grouping and dimensions, full once-only frame coverage, and every sheet/index hash. It also reconstructs the expected escaped HTML and compares its bytes, so editing the page and updating its ordinary hash cannot substitute a different index. Sheet paths stay inside the review directory; original links stay inside the capture's verified frame archive. The page contains no scripts or approval controls. Original TGA links require the capture archive to remain at its recorded location.

These checks preserve review material and its provenance. They do not establish visual correctness or replace the independently registered manual review.

## Measured improvement

The retained benchmark used the same 160-frame Dust2 capture for both implementations:

| Measurement | Previous generation | Single-pass generation |
| --- | ---: | ---: |
| FFmpeg processes | 20 | 1 |
| Contact sheets | 20 | 20 |
| Generation time | 5.508 seconds | 2.852 seconds |
| Matching sheet bytes | — | 20 of 20 |

This run was **1.93× faster**. New-bundle validation took 1.596 seconds. These are single local measurements, not a throughput guarantee. A separate real-FFmpeg regression verifies all eleven distinct-color frames, their row-major placement, and the five black padding slots in a partial final sheet.

Artifacts: [benchmark](../data/validation/hud-review-efficiency-v1/comparison.json), [new local review page](../data/validation/hud-review-efficiency-v1/sequence-final/index.html). Both the new bundle and the authentic historical benchmark bundle passed validation. Neither was approved by this benchmark.
