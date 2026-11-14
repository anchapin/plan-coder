# ast-grep for matchline — Phase 0 spike (issue #42)

Phase 0 is measurement only: no CI wiring, no workflow files, no matchline changes.  The deliverable is this report plus the rule files under `docs/spikes/ast-grep-matchline/`.

## 1. Binary pinning (STEP 0)

| parameter | value |
|---|---|
| binary | `/tmp/astgrep-venv/bin/ast-grep` |
| version | `ast-grep 0.51.2` |
| install | `python3 -m venv /tmp/astgrep-venv && /tmp/astgrep-venv/bin/pip install ast-grep-cli` |
| wheel | `ast_grep_cli-0.51.2-py3-none-manylinux_2_28_x86_64.whl` (8.9 MB) |
| pip install wall time | 2 s |
| venv size on disk | 88 MiB |
| `/usr/bin/sg` | `newgrp`, NOT ast-grep — never use it |

The venv lives under `/tmp`, so no node_modules / install footprint ever enters the plan-coder diff.  The egress proxy of this cell (`HTTP_PROXY=http://hatch-egress-proxy:3128`, `SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem`) has to be exported for pip to reach pypi.org; without it pip fails with `SSL: WRONG_VERSION_NUMBER`.

## 2. Scanned tree

| parameter | value |
|---|---|
| repository | matchline (read-only clone) |
| scanned commit | `522e18a38eed5b64cc29bcd8478d15599eb5a1dd` |
| tree | `/home/hatch/workspace/fixtrees/plan-coder-worker-42/matchline-ref` |
| clone | `git clone ~/workspace/matchline matchline-ref` + `git -C matchline-ref fetch ~/workspace/matchline refs/remotes/origin/develop:refs/scan/develop` + `git -C matchline-ref checkout refs/scan/develop` |

The scan target is the `refs/scan/develop` ref fetched from the source clone, NOT the source clone's local `develop` branch (which had diverged to 6b018fa and lacked files the issue cites).

## 3. Conventions, scoping and classification policy

| rule | convention | source | rule scope | scoping applied before classification |
|---|---|---|---|---|
| C1 | no `sys.path` manipulation in main-pipeline code | AGENTS.md:19, CONTRACT.md:30, docs/CODE-REVIEW.md:45 | every `sys.path` mutation (insert/append/extend) in every *.py | `detector/**` and `tests/**` excluded (issue-scope enumeration) |
| C2 | untrusted XML parsed only through `safe_xml` | safe_xml.py:1-7, CONTRACT.md:31, AGENTS.md:22 | every `etree.parse()` / `ET.parse()` call site, any arity | `tests/**` (test fixtures), `safe_xml.py` (the safe implementation) and call sites that already pass a safe parser excluded |
| C3 | no `print()` in library code | docs/CODE-REVIEW.md:44 | every `print()` call in every *.py | CLI/main scripts (`cli.py`, `run_*.py`, `scripts/**`, `detector/**`, `tests/**`, `review_classifier/**`, `synth/**`, `sim/**`) excluded |
| C4 | no hardcoded machine/absolute paths in code | AGENTS.md:19, CONTRACT.md:30, docs/CODE-REVIEW.md:47 | every string literal in every *.py containing `/home/`, `~/workspace` or `/Users/` | `tests/**`, `detector/**` (dataset-root convention) and docstring mentions excluded |

Classification vocabulary: **true violation** = the hit is what the convention forbids; **legitimate exception** = the hit sits in a scope the convention exempts (scoped out, listed for completeness); **false positive** = the rule fires where the convention is silent, so an unscoped CI job would raise a false alarm.

## 4. Raw table — every rule x the full tree

Counts are per rule over the whole scanned tree; the per-hit rows below list every single hit with its classification and a one-line reason.

| rule | hits | scoped hits | true violations | legitimate exceptions | false positives |
|---|---|---|---|---|---|
| C1 | 69 | 15 | 15 | 54 | 0 |
| C2 | 49 | 5 | 3 | 44 | 2 |
| C3 | 506 | 9 | 9 | 497 | 0 |
| C4 | 36 | 2 | 2 | 34 | 0 |

### C1 — c1-sys-path-manipulation.yml

| # | file:line | classification | reason | snippet |
|---|---|---|---|---|
| 1 | detector/check_dataset.py:9 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 2 | detector/convert_aec.py:25 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 3 | detector/convert_cubicasa.py:36 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 4 | detector/convert_floorplancad.py:25 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 5 | detector/eval_zero_shot.py:19 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 6 | detector/floorplancad_yolo_baseline.py:42 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` |
| 7 | detector/tests/test_convert_floorplancad.py:28 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, str(DETECTOR_DIR))` |
| 8 | detector/tests/test_eca.py:30 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` |
| 9 | detector/tests/test_floorplancad_yolo_baseline.py:15 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` |
| 10 | detector/tests/test_report_ab_stats.py:28 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 11 | detector/tests/test_train_scratch.py:27 | legitimate exception | detector/** excluded by the C1 scope (separate-venv track) (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 12 | scripts/archcad_smoke.py:24 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 13 | scripts/benchmark.py:57 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 14 | scripts/clinic_sheet_data.py:18 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 15 | scripts/eval_aec_bench.py:31 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 16 | scripts/eval_clinic_detector.py:33 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 17 | scripts/eval_schedule_noise.py:35 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 18 | scripts/eval_schedule_noise.py:36 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.append(str(ROOT / "tests"))` |
| 19 | scripts/perf_budget.py:44 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 20 | scripts/r1_vector_spike.py:115 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 21 | scripts/schedule_bench.py:57 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` |
| 22 | scripts/tempfit_baseline.py:76 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 23 | scripts/validate_clinic_hvac.py:33 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 24 | scripts/validate_clinic_hvac_detection.py:48 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 25 | scripts/validate_clinic_hvac_detection.py:185 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, det)` |
| 26 | scripts/validate_clinic_walls.py:27 | true violation | sys.path hack in a shipped script — AGENTS.md Never-list forbids sys.path hacks outside detector/** and tests/** | `sys.path.insert(0, str(ROOT))` |
| 27 | tests/integration/test_telemetry_feedback_roundtrip.py:20 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 28 | tests/test_bench_truth.py:14 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 29 | tests/test_benchmark.py:10 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 30 | tests/test_benchmark_simplify_tolerance.py:23 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 31 | tests/test_clinic_hvac_validation.py:9 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))` |
| 32 | tests/test_convert_synth.py:21 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 33 | tests/test_convert_synth.py:263 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 34 | tests/test_convert_synth.py:289 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 35 | tests/test_degenerate_inputs.py:17 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 36 | tests/test_degenerate_inputs.py:18 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).resolve().parent))` |
| 37 | tests/test_detection_provider.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 38 | tests/test_detection_provider.py:594 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 39 | tests/test_detection_provider.py:619 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 40 | tests/test_drawing_scale.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 41 | tests/test_elevation_sheets.py:11 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` |
| 42 | tests/test_elevation_sheets.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent))` |
| 43 | tests/test_grid_detect.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 44 | tests/test_hvac_legend.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 45 | tests/test_hvac_legend.py:13 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).resolve().parent))` |
| 46 | tests/test_hvac_legend_templates.py:14 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 47 | tests/test_hvac_legend_templates.py:15 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).resolve().parent))` |
| 48 | tests/test_legend_learning.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 49 | tests/test_matchlines.py:9 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 50 | tests/test_pdf_ingest.py:14 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 51 | tests/test_pdf_schedules.py:12 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 52 | tests/test_pdf_schedules.py:13 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).resolve().parent))` |
| 53 | tests/test_plan_walls.py:17 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 54 | tests/test_real_set.py:17 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 55 | tests/test_review_page_browser.py:173 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(tests_dir)` |
| 56 | tests/test_revisions.py:9 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 57 | tests/test_sahi_tile_merge.py:19 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 58 | tests/test_schedule_second_dataset.py:19 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 59 | tests/test_schedule_truth.py:13 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(ROOT))` |
| 60 | tests/test_scratch_loss.py:22 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 61 | tests/test_semi_exterior.py:10 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 62 | tests/test_sheet_index.py:14 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.append(str(Path(__file__).parent))` |
| 63 | tests/test_space_conditioning.py:8 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` |
| 64 | tests/test_synth_fonts.py:13 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` |
| 65 | tests/test_takeoff_diff.py:8 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 66 | tests/test_train_resume.py:19 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 67 | tests/test_wall_head.py:23 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(DETECTOR))` |
| 68 | tests/test_wall_types.py:8 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 69 | tests/test_zone_capacity.py:10 | legitimate exception | tests/** excluded by the C1 scope (scoped out) | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |

### C2 — c2-xml-parse-call-sites.yml

| # | file:line | classification | reason | snippet |
|---|---|---|---|---|
| 1 | bem/bem_export.py:1025 | legitimate exception | call site already passes a safe parser (scoped out) | `etree.parse(str(xsd_path), safe_parser)` |
| 2 | bem/buildingsync_export.py:482 | true violation | untrusted uploaded BuildingSync XML parsed with a bare etree.parse(): no entity-expansion protection, safe_xml.safe_xml_parse() required | `etree.parse(str(path))` |
| 3 | bem/buildingsync_export.py:485 | false positive | repo-local trusted Schematron schema (not untrusted input) — the rule fires but the convention does not forbid it | `etree.parse(str(schematron_path))` |
| 4 | bem/buildingsync_export.py:522 | true violation | untrusted uploaded BuildingSync XML parsed with stdlib ET.parse(): bypasses the safe_xml size/complexity limits | `ET.parse(path)` |
| 5 | bem/buildingsync_export.py:553 | true violation | untrusted uploaded BuildingSync XML parsed with stdlib ET.parse(): bypasses the safe_xml size/complexity limits | `ET.parse(str(path))` |
| 6 | bem/buildingsync_export.py:567 | legitimate exception | call site already passes a safe parser (scoped out) | `etree.parse(str(xsd_path), safe_parser)` |
| 7 | bem/buildingsync_export.py:568 | legitimate exception | call site already passes a safe parser (scoped out) | `etree.parse(str(path), safe_parser)` |
| 8 | detector/convert_aec.py:57 | legitimate exception | call site already passes a safe parser (scoped out) | `lxml.etree.parse(         os.path.join(args.src, "annotations_15_scoring_ready.xml"), safe` |
| 9 | detector/convert_cubicasa.py:228 | legitimate exception | call site already passes a safe parser (scoped out) | `lxml.etree.parse(svg_path, safe_xml_parser())` |
| 10 | safe_xml.py:61 | legitimate exception | safe_xml.py is the safe parser implementation itself (scoped out) | `etree.parse(path_str, safe_xml_parser())` |
| 11 | scripts/benchmark.py:444 | false positive | benchmark harness re-parses a gbXML file it exported itself (trusted local file) — the rule fires but the convention does not forbid it | `ET.parse(str(path))` |
| 12 | tests/test_air_loops.py:239 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 13 | tests/test_air_loops.py:267 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_gbxml(_bem(TZ), tmp_path / "n.xml"))` |
| 14 | tests/test_appendix_g_levels.py:36 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 15 | tests/test_appendix_g_split.py:150 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 16 | tests/test_atria.py:117 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(pa)` |
| 17 | tests/test_bem_loads.py:49 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 18 | tests/test_bem_multiring.py:62 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(str(path))` |
| 19 | tests/test_bem_shading.py:67 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 20 | tests/test_bem_shading.py:79 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 21 | tests/test_bem_skylights.py:111 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 22 | tests/test_bem_skylights.py:140 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 23 | tests/test_buildingsync_export.py:77 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(_tiny_model(), tmp_path / "a.xml"))` |
| 24 | tests/test_buildingsync_export.py:79 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(         write_buildingsync(_tiny_model(), tmp_path / "b.xml", version="2.7.0")  ` |
| 25 | tests/test_buildingsync_export.py:87 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(_tiny_model(), tmp_path / "out.xml"))` |
| 26 | tests/test_buildingsync_export.py:114 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(model, tmp_path / "out.xml"))` |
| 27 | tests/test_buildingsync_export.py:163 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(model, tmp_path / "out.xml"))` |
| 28 | tests/test_buildingsync_export.py:185 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 29 | tests/test_buildingsync_export.py:195 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(_tiny_model(), tmp_path / "out.xml"))` |
| 30 | tests/test_buildingsync_export.py:222 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_buildingsync(model, tmp_path / "out.xml"))` |
| 31 | tests/test_buildingsync_use_case.py:78 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 32 | tests/test_buildingsync_use_case.py:96 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 33 | tests/test_buildingsync_use_case.py:120 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 34 | tests/test_buildingsync_use_case.py:151 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 35 | tests/test_buildingsync_use_case.py:170 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 36 | tests/test_gbxml_per_space.py:64 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 37 | tests/test_gbxml_sloped_roofs.py:85 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 38 | tests/test_linked_gbxml_north_up.py:34 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 39 | tests/test_multistorey_export.py:66 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 40 | tests/test_opening_constructions.py:134 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 41 | tests/test_opening_constructions.py:186 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 42 | tests/test_openings_on_own_space.py:71 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 43 | tests/test_per_space_wall_u_export.py:29 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 44 | tests/test_roof_u.py:41 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 45 | tests/test_safe_xml.py:31 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `etree.parse(         BytesIO(             b"<?xml version='1.0'?><root>"             b"<sp` |
| 46 | tests/test_semi_exterior_floors.py:205 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 47 | tests/test_semi_exterior_walls.py:173 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(p)` |
| 48 | tests/test_slab_u.py:41 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(path)` |
| 49 | tests/test_zone_airflow.py:192 | legitimate exception | tests/** excluded: fixtures written by the test itself (scoped out) | `ET.parse(write_gbxml(bem, tmp_path / "m.xml"))` |

### C3 — c3-no-print-in-library-code.yml

Rows are grouped per file: every hit of a file is listed as `file:line` in the *hits* column, and the classification and reason are identical for all of them.

| file | hits | classification | reason |
|---|---|---|---|
| cli.py | 65, 79, 94, 97, 107, 124, 128, 231, 232, 234, 236, 238, 254, 267, 269, 274, 275, 285, 287, 302, 304, 305, 319, 322, 324, 325, 333, 340, 341, 350, 354, 359, 360, 368, 388, 389, 390, 391, 392, 397, 413, 422, 489, 503, 505, 510, 512, 514, 548, 563, 568, 595, 597, 599, 601, 604, 606, 621, 627, 629 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/check_dataset.py | 25, 49, 51, 53, 55 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/convert_aec.py | 107 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/convert_cubicasa.py | 354 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/convert_floorplancad.py | 86, 90 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/convert_synth.py | 394 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/eval_robustness.py | 279, 280, 287, 288, 295, 337 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/eval_zero_shot.py | 75, 79 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/floorplancad_yolo_baseline.py | 397, 402, 403, 409, 413, 420, 455, 461, 466, 477 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/report_ab_stats.py | 196, 197, 205, 206, 210, 212, 214, 215, 216, 217, 221, 222, 223, 224, 225, 230, 234, 239, 240, 241, 242, 245, 247, 248, 253, 254, 256, 257, 259, 261, 262, 263, 265, 266, 268, 269, 270, 273, 274, 275, 276, 277, 278, 279, 282, 283, 284, 285, 294, 296, 299, 300, 301, 303, 305, 306, 307, 308, 309, 311, 313, 316, 318, 321, 322, 324, 328, 330, 331, 332, 333, 334 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/sahi_infer.py | 150 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/train.py | 62, 67, 75, 97, 120, 159, 204, 230 | legitimate exception | CLI/main script or test surface (print() allowed) |
| detector/train_scratch.py | 727, 765, 809, 823, 922 | legitimate exception | CLI/main script or test surface (print() allowed) |
| hvac_trace.py | 1119, 1130, 1138, 1143, 1144, 1149, 1150 | true violation | library module prints (embedded WiSARD training experiment) — docs/CODE-REVIEW.md allows print() in CLI/main only |
| review_classifier/corpus/collect.py | 134, 140 | legitimate exception | CLI/main script or test surface (print() allowed) |
| review_classifier/laya_experiment.py | 64, 361, 362, 363, 364, 365, 416, 418, 423, 424, 437, 438, 439, 442, 445, 452, 460, 463, 477, 478, 479, 482, 485, 505, 506, 507, 510, 512, 518, 520, 523, 527, 530, 545, 546, 547, 548, 577 | legitimate exception | CLI/main script or test surface (print() allowed) |
| review_classifier/run_demo.py | 72, 86 | legitimate exception | CLI/main script or test surface (print() allowed) |
| review_classifier/train.py | 87, 88, 89, 90, 103, 114, 117, 123, 187 | legitimate exception | CLI/main script or test surface (print() allowed) |
| run_bem_export.py | 88, 89, 90, 91, 96, 101, 105, 107, 108, 110, 111 | legitimate exception | CLI/main script surface (print() allowed) |
| run_elevation_windows.py | 190, 205, 210, 217, 230, 247, 262, 266, 271, 280, 282, 284 | legitimate exception | CLI/main script surface (print() allowed) |
| run_facade_takeoff.py | 30, 31, 32, 44, 48, 53, 58, 67, 73, 77, 81, 82, 83, 91, 96, 107, 108, 109, 110, 111, 112, 127, 128, 129, 130, 131, 134, 143, 151, 168, 175 | legitimate exception | CLI/main script surface (print() allowed) |
| run_mnist.py | 29, 37, 48, 60, 65 | legitimate exception | CLI/main script surface (print() allowed) |
| run_multidiscipline.py | 118, 126, 131, 134, 137, 139, 140, 141 | legitimate exception | CLI/main script surface (print() allowed) |
| run_pipeline.py | 455, 458, 537, 591, 649, 881, 883, 953, 954, 955, 956, 957, 961, 1036, 1127, 1161, 1194, 1195, 1213, 1215 | legitimate exception | CLI/main script surface (print() allowed) |
| run_review.py | 203, 214, 224, 229, 255, 257, 259, 265, 266, 268, 269, 429, 460, 479, 499, 521, 526, 527, 528, 529, 537, 543, 549, 569, 585, 590, 591, 599, 605, 611, 625, 626, 647, 655, 657, 658, 659, 660, 667, 687, 692, 693, 697, 707, 712, 717, 719, 720, 721, 728, 731, 735, 736, 742, 747, 749, 757, 774, 782, 794 | legitimate exception | CLI/main script surface (print() allowed) |
| run_room_labels.py | 19, 25, 30, 48, 53, 58, 62, 66, 67, 71, 72, 89, 106, 130 | legitimate exception | CLI/main script surface (print() allowed) |
| run_symbols.py | 28, 38, 46, 47, 49, 54, 115, 120, 121, 123, 137 | legitimate exception | CLI/main script surface (print() allowed) |
| run_validation.py | 25, 26, 33, 38, 39, 40 | legitimate exception | CLI/main script surface (print() allowed) |
| scripts/archcad_smoke.py | 131 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/bench_truth.py | 720, 735, 739, 743, 744 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/benchmark.py | 427, 435, 508, 612, 635, 644 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/build_construction_library.py | 119 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/build_prm_library.py | 113 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/build_space_use_defaults.py | 218 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/check_release_licenses.py | 146, 155 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/cleanup_sweep.py | 167, 168, 174, 179, 185 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/clinic_sheet_data.py | 119 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/eval_aec_bench.py | 154, 158, 161, 163 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/eval_clinic_detector.py | 294, 296 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/eval_door_swings.py | 100, 105 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/eval_schedule_noise.py | 227, 229 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/generate_api_surface.py | 162 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/generate_repo_map.py | 210, 211 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/make_synthetic_schedule_workbook.py | 488, 493 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/measure_grid_detect.py | 233, 240 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/perf_budget.py | 321, 323, 326 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/r1_vector_spike.py | 138 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/schedule_bench.py | 737, 777, 779 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/schedule_truth.py | 258 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/synth_e2e_test.py | 182, 186, 193, 195, 196 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/synth_lighting_e2e_test.py | 167, 169, 170, 171 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/tempfit_baseline.py | 572, 574, 600, 601, 633, 634, 691, 696, 699, 717 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/triage_branches.py | 85 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/validate_clinic_hvac.py | 211 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/validate_clinic_hvac_detection.py | 484, 485, 487 | legitimate exception | CLI/main script or test surface (print() allowed) |
| scripts/validate_clinic_walls.py | 580 | legitimate exception | CLI/main script or test surface (print() allowed) |
| sim/cbecs_band.py | 108 | legitimate exception | CLI/main script or test surface (print() allowed) |
| sim_smoke.py | 272 | legitimate exception | CLI/main script or test surface (print() allowed) |
| synth/gap_experiment.py | 61, 65, 80, 87, 94, 105, 120 | legitimate exception | CLI/main script or test surface (print() allowed) |
| synth/make_dataset.py | 55, 59, 61, 67, 77 | legitimate exception | CLI/main script or test surface (print() allowed) |
| synth/mech.py | 1233 | legitimate exception | CLI/main script or test surface (print() allowed) |
| tests/make_goldens.py | 336, 339 | legitimate exception | CLI/main script or test surface (print() allowed) |
| tests/make_simplifier_oracle.py | 48 | legitimate exception | CLI/main script or test surface (print() allowed) |
| tests/test_ifc_roundtrip.py | 602, 604 | legitimate exception | CLI/main script or test surface (print() allowed) |
| tests/test_invariants.py | 58 | legitimate exception | CLI/main script or test surface (print() allowed) |
| validate/__init__.py | 270, 296 | true violation | library package prints error text to stderr — docs/CODE-REVIEW.md allows print() in CLI/main only |

### C4 — c4-no-hardcoded-absolute-paths.yml

| # | file:line | classification | reason | snippet |
|---|---|---|---|---|
| 1 | datasets_adapter.py:987 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Load AEC-geometric-bench sheets.      Returns (symbol_samples, takeoff). Takeoff areas ` |
| 2 | detector/convert_aec.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Convert AEC Geometric Bench annotations to YOLO format (eval / zero-shot set).  Reads d` |
| 3 | detector/convert_aec.py:37 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets/aec-geometric-bench/dataset"` |
| 4 | detector/convert_aec.py:39 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets/detector_yolo/aec"` |
| 5 | detector/convert_cubicasa.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Convert CubiCasa5K SVG annotations to Ultralytics YOLO detection format.  For each floo` |
| 6 | detector/convert_cubicasa.py:283 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets/cubicasa5k/cubicasa5k"` |
| 7 | detector/convert_cubicasa.py:286 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets/detector_yolo/cubicasa"` |
| 8 | detector/convert_floorplancad.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Convert FloorPlanCAD parquet (test split) to YOLO format.  The parquet has columns: ima` |
| 9 | detector/convert_floorplancad.py:49 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `'~/workspace/datasets/floorplancad/train-00000-of-00001.parquet'` |
| 10 | detector/convert_floorplancad.py:51 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `'~/workspace/datasets/detector_yolo/floorplancad'` |
| 11 | detector/convert_synth.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Convert synthetic sheet ground truth to Ultralytics YOLO format (#743).  Input: a synth` |
| 12 | detector/convert_synth.py:355 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets/detector_yolo/synth"` |
| 13 | detector/eval_zero_shot.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Zero-shot / cross-dataset eval: greedy IoU matching of predictions to GT.  Reads a pred` |
| 14 | detector/floorplancad_yolo_baseline.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""YOLO baseline precision/recall on FloorPlanCAD (corrected labels) — issue #1329.  Batch` |
| 15 | detector/floorplancad_yolo_baseline.py:144 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets"` |
| 16 | detector/floorplancad_yolo_baseline.py:153 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets"` |
| 17 | detector/floorplancad_yolo_baseline.py:165 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"~/workspace/datasets"` |
| 18 | detector/report_ab_stats.py:185 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `'~/workspace/datasets/detector_runs'` |
| 19 | detector/train.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""Config-driven YOLO fine-tuning harness for the matchline detector track.  Baseline: YOL` |
| 20 | detector/train.py:29 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `'~/workspace/datasets/detector_runs'` |
| 21 | detector/train_scratch.py:2 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `"""From-scratch PyTorch door/window detector for synthetic sheets (#743).  License-clean p` |
| 22 | detector/train_scratch.py:737 | legitimate exception | detector/** excluded: detector dataset-root convention (scoped out) | `f"~/workspace/datasets/detector_runs/scratch_{args.name}"` |
| 23 | elevation_windows.py:290 | true violation | machine-specific venv path baked into a runtime error message — AGENTS.md forbids machine paths in code | `"window detection needs OpenCV (cv2); e.g. run with ~/workspace/.venv-ocr/bin/python"` |
| 24 | review_classifier/laya_experiment.py:2 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Laya System 1 decision layer — spike experiment.  Investigates laya (https://laya.conva` |
| 25 | run_elevation_windows.py:1 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Validate exact window positioning from elevations.  For each synthetic building:   1. D` |
| 26 | scripts/benchmark.py:2 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Real-building benchmark harness (#748): scoreboard over licensed IFC buildings.  Usage:` |
| 27 | scripts/make_synthetic_schedule_workbook.py:2 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Generate a second schedule-truth benchmark dataset (#1296).  The schedule-truth benchma` |
| 28 | scripts/tempfit_baseline.py:2 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""TempFit-style scale/rotation template-fit baseline for the door/window taxonomy (issue ` |
| 29 | scripts/tempfit_baseline.py:88 | true violation | hardcoded ~/workspace/datasets fallback used at runtime — AGENTS.md forbids machine paths in code | `"~/workspace/datasets"` |
| 30 | synth/gap_experiment.py:2 | legitimate exception | docstring mention of a user-local path (documentation) (scoped out) | `"""Sim-to-real gap experiment.  Trains the WiSARD classifier (jesse.py) on SYNTHETIC symbo` |
| 31 | tests/test_detection_provider.py:16 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"/home/x/workspace/datasets/detector_runs/yolo11n_baseline_s0/weights/best.pt"` |
| 32 | tests/test_detection_provider.py:35 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"/home/alex/workspace/datasets/detector_runs/scratch-tile640v6ms-38530008572/ckpt.pt"` |
| 33 | tests/test_detection_provider.py:544 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"/home/alex/workspace/datasets/detector_runs/scratch-synth-38319984460/ckpt.pt"` |
| 34 | tests/test_feedback_capture.py:61 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"No such file or directory: /home/user/plans.pdf"` |
| 35 | tests/test_schedule_second_dataset.py:160 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"~/workspace/datasets/"` |
| 36 | tests/test_tempfit_baseline.py:30 | legitimate exception | tests/** excluded: fixture/assertion strings (scoped out) | `"/home/hatch"` |

## 5. Seeded-violation check (local, scratch copy only)

For every shipped rule the scanned tree is copied into a scratch directory, one synthetic violation is injected at the tree root (never inside `tests/` or `detector/`, so path scoping cannot hide it), the rule is re-run, and the scratch copy is deleted.

| rule | seeded file | baseline hits | hits after seed | seeded hits | caught |
|---|---|---|---|---|---|
| C1 | `seeded_violation_c1.py` | 69 | 70 | 1 (seeded_violation_c1.py:5) | yes |
| C2 | `seeded_violation_c2.py` | 49 | 50 | 1 (seeded_violation_c2.py:5) | yes |
| C3 | `seeded_violation_c3.py` | 506 | 507 | 1 (seeded_violation_c3.py:5) | yes |
| C4 | `seeded_violation_c4.py` | 36 | 37 | 1 (seeded_violation_c4.py:3) | yes |

## 6. Phase 0 gate

**Verdict: Proceed** — 3 rules survive the gate (C1, C3, C4): zero false positives after legitimate-exception scoping and each catches its seeded violation.  The gate needs >= 3.

| rule | false positives after scoping | seeded violation caught |
|---|---|---|
| C1 | 0 | yes |
| C2 | 2 | yes |
| C3 | 0 | yes |
| C4 | 0 | yes |

## 7. C5 — candidate rule, dropped on evidence

C5 (no literal-threshold confidence comparison bypassing `REVIEW_CONFIDENCE`, `building_model.py:51` = 0.80) was trialled as a candidate rule and is **not shipped**.  The candidate:

```yaml
id: c5-candidate-literal-confidence-threshold
language: python
severity: warning
message: "literal confidence threshold bypasses REVIEW_CONFIDENCE"
rule:
  any:
    - pattern: $X.confidence < $L
    - pattern: $X.confidence > $L
    - pattern: $X.confidence <= $L
    - pattern: $X.confidence >= $L
constraints:
  L:
    regex: ^0\.[0-9]+$
```

Measured over the scanned tree it fires 26 times:

| file:line | text | classification |
|---|---|---|
| tests/test_zone_airflow.py:180 | `items[0].confidence < 0.8` | legitimate exception — test assertion on a confidence threshold |
| run_pipeline.py:1448 | `ul.confidence < 0.80` | true violation — hardcoded 0.80 threshold bypassing REVIEW_CONFIDENCE (building_model.py:51) |
| tests/test_units.py:83 | `reg.confidence >= 0.85` | legitimate exception — test assertion on a confidence threshold |
| tests/test_units.py:96 | `reg.confidence < 0.80` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:482 | `schedule.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:502 | `schedule.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:514 | `schedule.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:522 | `entity.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:531 | `entity.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:553 | `table.rows[0].provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:580 | `c.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:583 | `tables[0].provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:607 | `tables[0].provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:624 | `t.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:650 | `tables[0].provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_schedule_parse.py:674 | `c.provenance.confidence <= 0.50` | legitimate exception — test assertion on a confidence threshold |
| tests/test_registration.py:198 | `reg.confidence >= 0.9` | legitimate exception — test assertion on a confidence threshold |
| tests/test_registration.py:242 | `reg.confidence < 0.9` | legitimate exception — test assertion on a confidence threshold |
| tests/test_real_set.py:831 | `d.provenance.confidence <= 0.6` | legitimate exception — test assertion on a confidence threshold |
| tests/test_drawing_scale.py:144 | `sc.confidence < 0.6` | legitimate exception — test assertion on a confidence threshold |
| tests/test_drawing_scale.py:152 | `sc.confidence < 0.6` | legitimate exception — test assertion on a confidence threshold |
| tests/test_drawing_scale.py:159 | `sc.confidence < 0.5` | legitimate exception — test assertion on a confidence threshold |
| tests/test_drawing_scale.py:190 | `sc.confidence > 0.9` | legitimate exception — test assertion on a confidence threshold |
| tests/test_ifc_import.py:317 | `w.provenance.confidence >= 0.9` | legitimate exception — test assertion on a confidence threshold |
| tests/test_ifc_roundtrip_fidelity.py:142 | `w.provenance.confidence <= 0.5` | legitimate exception — test assertion on a confidence threshold |
| tests/test_ifc_storey_fallback.py:67 | `e.provenance.confidence <= 0.6` | legitimate exception — test assertion on a confidence threshold |

The grep for the convention's literal-threshold form (`grep -rn -E '[<>]=?\s*0\.(8|85|7[0-9]|6[0-9])\b' --include='*.py'`) finds 4 confidence-relevant sites that the candidate rule does NOT catch:

| file:line | text | note |
|---|---|---|
| run_pipeline.py:1440 | `ls.label_confidence < 0.80` | true violation |
| run_pipeline.py:1448 | `ul.confidence < 0.80` | true violation |
| review_classifier/data.py:201 | `elif score < 0.8:` | label-noise urgency bucket (corpus labelling policy, not a review-queue threshold) |
| review_classifier/laya_experiment.py:183 | `if label == 0.0 and det_conf > 0.8:` | spike experiment gate (not the pipeline review queue) |

Evidence for dropping C5: the rule only matches comparisons against an attribute literally named `confidence`, so it misses the sibling violation `run_pipeline.py:1440` (`ls.label_confidence < 0.80`) and the differently named threshold comparisons in `review_classifier/data.py` and `review_classifier/laya_experiment.py`. A shipped rule that under-covers the convention would give false confidence, and loosening it to bare float comparisons (`$X < 0.8`) would fire on `rng.random() < 0.8`, geometry ratios and test assertions — false positives galore.  C5 is therefore dropped with this evidence; it can be re-opened in Phase 1 with a name-aware rule (`*_confidence`, `*_conf`) plus an explicit allowlist.

## 8. Re-running the spike

```sh
python3 -m venv /tmp/astgrep-venv
export HTTPS_PROXY=http://hatch-egress-proxy:3128 HTTP_PROXY=http://hatch-egress-proxy:3128
export SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem REQUESTS_CA_BUNDLE=/run/hatch/egress-tls/ca-bundle.pem
/tmp/astgrep-venv/bin/pip install ast-grep-cli

git clone ~/workspace/matchline matchline-ref
git -C matchline-ref fetch ~/workspace/matchline refs/remotes/origin/develop:refs/scan/develop
git -C matchline-ref checkout refs/scan/develop

python3 bin/ast_grep_phase0_scan.py --repo matchline-ref \
    --ast-grep /tmp/astgrep-venv/bin/ast-grep \
    --out docs/spikes/ast-grep-matchline-phase0.md
```

Single rule, no harness (raw ast-grep, what CI would run):

```sh
cd matchline-ref
/tmp/astgrep-venv/bin/ast-grep scan \
    --rule ../docs/spikes/ast-grep-matchline/c1-sys-path-manipulation.yml .
```
