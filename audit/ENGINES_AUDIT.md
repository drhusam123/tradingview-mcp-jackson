# Engines Audit

**Generated:** 2026-06-15T20:06:36.049Z

## Core production engines

| Engine | Command | Layer | Output artifact | Last Run | Status |
|--------|---------|-------|-----------------|----------|--------|
| LRE 4.0 | `egx:lre:status` | client-shadow | `lre_4_0_status_last.json` | 2026-06-15T12:57:55.840706+00:00 | PASS |
| MED 0.3 | `egx:med:run` | client | `med_0_3_status_last.json` | 2026-06-15T12:52:24.791219+00:00 | PASS |
| MDE shadow | `egx:mde:shadow` | shadow | `mde_pilot_shadow_last.json` | 2026-06-15T12:57:05.518023+00:00 | PASS |
| Fabric | `egx:fabric:status` | research | `fabric_status_last.json` | — | NOT_RUN |
| P6 delivered | `egx:phase22:p6-delivered` | KPI | `phase22_p6_delivered_last.json` | 2026-06-15T12:57:09.324Z | PASS |
| LRE OOS acc | `lre_oos_accumulator.py` | shadow | `lre_oos_accumulator_last.json` | 2026-06-15T12:57:01.281153+00:00 | PASS |
| Graduation | `egx:graduation:complete` | ops | `graduation_final_last.json` | 2026-06-15T12:57:31.919Z | PASS |
| Score all | `egx:score:all` | scoring | `score_all_last.json` | — | NOT_RUN |
| Gates | `gate_doctor_audit.py` | gates | `gate_doctor_last.json` | — | NOT_RUN |

## Discovery / research registry

| Engine | Command | Layer | Runnable | Outputs |
|--------|---------|-------|----------|---------|
| opportunity_v2 | `egx:discovery:refresh` | daily | yes | opportunity_score_v2, final_signals.actionable |
| quant_rules | `egx:discover:quant` | weekly | yes | quant_discovery_rules |
| dmids | `egx:discover:rescore` | weekly | yes | structural_laws, dmids_profiles |
| strategy_sweep | `egx:discovery:strategy:sweep` | research | yes | param_sweep_results |
| strategy_wf | `egx:discovery:strategy:wf` | research | yes | walk_forward_results |
| strategy_ml | `egx:discovery:strategy:ml` | research | yes | ml_signal_candidates |
| strategy_patterns | `egx:discovery:strategy:patterns` | research | yes | egx_pattern_signals |
| promotion_audit | `egx:discovery:promotion:audit` | daily | yes | discovery_promotion_audit_last.json |
| closed_loop | `egx:closed:loop` | daily | no | p6_research_context.json, discovery_feedback_last.json |
| tv_microstructure | `egx:discovery:tv:micro` | daily | yes | tv_discovery_features, pine_analytics |
| counterfactual_atoms | `egx:discovery:counterfactual:atoms` | daily | yes | counterfactual_atoms_last.json |
| regime_conditional_sweep | `egx:discovery:regime:sweep` | research | yes | regime_conditional_sweep_last.json, regime_sweep_results |
| hypothesis_sandbox_bridge | `egx:discovery:hypothesis:bridge` | weekly | yes | hypothesis_sandbox_bridge_last.json |
| discovery_fabric | `egx:discovery:fabric` | daily | yes | discovery_atom_registry, discovery_ml_manifest.json |
| discovery_fabric_light | `egx:discovery:fabric:light` | daily | yes | discovery_atom_registry, discovery_ml_manifest.json |
| causal_discovery | `egx:causal78` | weekly | yes | causal_discovery_last.json |
| egx_x_pro | `egx:xpro` | daily | yes | egx_x_pro_daily, egx_signal_tracker |
| egx_market_discovery | `egx:mde` | daily | yes | egx_market_discovery_daily, mde_shadow_last.json, discovery_mde_manifest.json, mde_shadow_attribution_last.json |
| mde_signal_provider | `egx:mde:signal-provider` | daily | yes | mde_shadow_signals_daily, mde_signal_provider_last.json, gate_audit_snapshots.shadow_mde_* |
| egx_liquidity_rotation | `egx:lre:daily` | daily | yes | lre_explosion_events, lre_explosion_archaeology.json, lre_explosion_families.json, lre_pre_explosion_fingerprints.json, lre_radar_last.json, lre_daily_scores_last.json, lre_rotation_graph_last.json, lre_daily_scores, lre_market_daily |
| lre_signal_provider | `egx:lre:signal-provider` | daily | yes | lre_shadow_signals_daily, lre_signal_provider_last.json, gate_audit_snapshots.shadow_lre_* |
| lre_forward_paper | `egx:lre:paper-trading` | weekly | yes | lre_forward_paper_trades.json, lre_ignition_forward_monitor.json, lre_top_eps_special_track.json, lre_3_0_historical_replay.json, lre_client_grade_gate_status.json |
| lre_filter_tightening | `egx:lre:filter-tightening` | weekly | yes | lre_3_1_filter_replay.json, lre_3_1_sanitized_replay.json, lre_3_1_mode_comparison.json, lre_3_1_forward_candidates_last.json |
| lre_stage_rebuild | `egx:lre:stage-rebuild` | weekly | yes | lre_3_2_stage_rebuild_replay.json, lre_3_2_threshold_diagnostic.json, lre_3_2_entry_timing_diagnostic.json, lre_3_2_stop_diagnostic.json, lre_3_2_candidate_review.json |
| lre_dual_gate | `egx:lre:dual-gate` | weekly | yes | lre_mde_dual_gate_audit, lre_mde_dual_gate_audit_last.json, lre_mde_sequence_audit.json, lre_mde_oos_results.json, lre_mde_candidate_review_last.json |
| lre_confluence_robustness | `egx:lre:confluence-robustness` | weekly | yes | lre_3_4_confluence_dominance_detox.json, lre_3_4_leave_one_symbol_out.json, lre_3_4_leave_one_sector_out.json, lre_3_4_bootstrap_results.json, lre_3_4_entry_cost_stop_robustness.json, lre_3_4_candidate_review.json |
| lre_shadow_pilot | `egx:lre:shadow-pilot-design` | weekly | yes | lre_dual_gate_shadow_pilot, lre_3_5_pilot_caps_replay.json, lre_3_5_shadow_pilot_last.json, lre_3_5_bucket_distribution.json, lre_3_5_current_candidates_review.json, lre_3_5_forward_ledger_last.json |
| lre_walk_forward_pilot | `egx:lre:walk-forward-pilot` | weekly | yes | lre_walk_forward_shadow_pilot, lre_3_6a_walk_forward_results.json, lre_3_6a_walk_forward_ledger.json, lre_3_6a_caps_comparison.json, lre_3_6a_threshold_leakage_audit.json, lre_3_6a_bucket_results.json |
| lre_research_feed | `egx:lre:research-feed` | daily | yes | lre_research_feed_daily, lre_research_feed_last.json, discovery_lre_manifest.json, lre_learning_snapshot.json |
| lre_forward_shadow | `egx:lre:forward-shadow` | daily | yes | lre_forward_shadow_ledger, lre_3_6b_forward_shadow_last.json |
| lre_dual_gate_daily | `egx:lre:dual-gate-daily` | daily | yes | lre_mde_dual_gate_audit, lre_dual_gate_daily_last.json |
| lre_research_acceptance | `egx:lre:acceptance` | daily | yes | lre_4_0_acceptance_last.json |
| lre_research_status | `egx:lre:status` | daily | yes | lre_4_0_status_last.json |
| med_daily_chain | `egx:med:phase3` | daily | yes | med_daily_scores, med_research_feed, med_analogue_scores_daily, med_0_3_daily_chain_last.json, med_0_3_discovery_report.json, med_false_edge_feed_last.json, discovery_med_manifest.json |
| med_forward_shadow | `egx:med:forward-shadow` | daily | yes | med_forward_shadow_ledger, med_forward_shadow_last.json |
| med_research_status | `egx:med:phase3:verify` | daily | yes | med_0_3_status_last.json, med_0_3_acceptance_last.json |
